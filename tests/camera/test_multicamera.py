"""Multicamera fault-isolation test (Story 5.4).

Two cameras share one UploadQueue. One camera's FFmpeg fails to launch (crash),
the other stays connected and produces a segment — we assert the healthy camera
keeps capturing and uploading and that only the failed camera's per_camera entry
goes down. FFmpeg/S3 are mocked; no hardware.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pipeline.ffmpeg_hls as hls_mod
import upload.queue as queue_mod
from camera.sources.base import StreamMetadata, VideoSource
from health.state import AppState
from pipeline.ffmpeg_hls import HLSPipeline
from storage.db import SegmentDB
from upload.queue import UploadQueue
from upload.s3_client import S3Uploader

_REAL_SLEEP = asyncio.sleep


class _FakeSource(VideoSource):
    def __init__(self, camera_id: str) -> None:
        self._camera_id = camera_id

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def ffmpeg_input_args(self) -> list[str]:
        return ["-i", f"fake://{self._camera_id}"]

    async def probe(self) -> StreamMetadata:
        return StreamMetadata("h264", 640, 480, 25.0, self._camera_id)


class _StallProcess:
    """A 'connected' FFmpeg that stays alive until terminated."""

    def __init__(self) -> None:
        self.pid = 1
        self._rc: int | None = None
        self._term = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return self._rc

    def terminate(self) -> None:
        if self._rc is None:
            self._rc = -15
        self._term.set()

    def kill(self) -> None:
        if self._rc is None:
            self._rc = -9
        self._term.set()

    async def wait(self) -> int:
        await self._term.wait()
        return self._rc if self._rc is not None else -15

    async def communicate(self) -> tuple[None, bytes]:
        await self._term.wait()
        return None, b""


async def _wait(predicate, timeout_s: float = 5.0) -> None:
    for _ in range(int(timeout_s / 0.02) + 1):
        if predicate():
            return
        await _REAL_SLEEP(0.02)


def _pipe(source, queue, tmp_path, state, **kw) -> HLSPipeline:
    defaults = dict(
        on_segment=queue.enqueue, output_base_dir=str(tmp_path), app_state=state,
        input_type="rtsp_ip", max_failures=2,
        reconnect_base_delay=0.001, reconnect_max_delay=0.002, reconnect_idle_s=0.03,
        segment_timeout_s=30,
    )
    defaults.update(kw)
    return HLSPipeline(source=source, **defaults)


class TestMulticameraIsolation:
    async def test_one_camera_crash_does_not_stop_the_other(self, tmp_path) -> None:
        db = SegmentDB(tmp_path / "q.db")
        await db.open()

        uploaded: list[str] = []
        uploader = MagicMock(spec=S3Uploader)

        async def _upload(camera_id, path):
            uploaded.append(camera_id)
            return f"key/{camera_id}/{path.name}"

        uploader.upload_segment = AsyncMock(side_effect=_upload)
        queue = UploadQueue(uploader=uploader, db=db, max_retries=0)

        state = AppState()
        pipe_a = _pipe(_FakeSource("cam-a"), queue, tmp_path, state)  # will crash
        pipe_b = _pipe(_FakeSource("cam-b"), queue, tmp_path, state)  # stays up

        # One module-level exec, dispatched by camera_id in the command.
        async def _exec(*args, **_kw):
            cmd = " ".join(str(a) for a in args)
            if "cam-a" in cmd:
                raise FileNotFoundError("ffmpeg missing for cam-a")
            return _StallProcess()

        with patch.object(hls_mod.asyncio, "create_subprocess_exec", new=_exec), \
                patch.object(hls_mod, "_SEGMENT_POLL_INTERVAL", 0.02), \
                patch.object(queue_mod, "_WORKER_POLL_INTERVAL", 0.02):
            await queue.start()
            await pipe_a.start()
            await pipe_b.start()

            # Produce a segment for the healthy camera so it enqueues + uploads.
            (tmp_path / "cam-b" / "segment_00000.ts").write_bytes(b"data")

            await _wait(
                lambda: pipe_a.camera_unavailable
                and pipe_b.rtsp_connected
                and queue.upload_success_count >= 1,
                timeout_s=5.0,
            )

            # Capture scalar values WHILE running (stop() resets connected flags).
            a_unavailable = pipe_a.camera_unavailable
            b_connected = pipe_b.rtsp_connected
            uploads = queue.upload_success_count
            cam_a_connected = state.per_camera["cam-a"].connected
            cam_a_error = state.per_camera["cam-a"].error
            cam_b_connected = state.per_camera["cam-b"].connected

            await pipe_a.stop()
            await pipe_b.stop()
            await queue.stop(drain_timeout_s=2.0)

        # cam-a crashed and is unavailable…
        assert a_unavailable is True
        assert cam_a_connected is False
        assert cam_a_error is not None
        # …but cam-b kept capturing AND uploading through the shared queue.
        assert b_connected is True
        assert cam_b_connected is True
        assert uploads >= 1
        assert "cam-b" in uploaded
        assert "cam-a" not in uploaded  # the crashed camera produced nothing
