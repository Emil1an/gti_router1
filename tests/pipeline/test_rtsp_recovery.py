"""Tests for RTSP auto-recovery (Story 3.4).

Exercises stall detection, reconnect metrics, per-camera health publication,
multi-camera fault isolation, and buffer preservation across reconnects.
FFmpeg is fully mocked — no hardware.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from camera.sources.base import StreamMetadata, VideoSource
from health.state import AppState
from pipeline.ffmpeg_hls import HLSPipeline


# ── Fakes ──────────────────────────────────────────────────────────────────────

class _FakeSource(VideoSource):
    def __init__(self, camera_id: str) -> None:
        self._camera_id = camera_id

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def ffmpeg_input_args(self) -> list[str]:
        return ["-i", "fake"]

    async def probe(self) -> StreamMetadata:
        return StreamMetadata(
            codec="h264", width=640, height=480, framerate=25.0,
            camera_id=self._camera_id,
        )


class _ExitProcess:
    """Process that exits immediately with a given return code."""

    def __init__(self, returncode: int = 1, stderr: bytes = b"err") -> None:
        self.pid = 111
        self._exit = returncode
        self._stderr = stderr
        self._returncode: int | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        if self._returncode is None:
            self._returncode = -15

    def kill(self) -> None:
        if self._returncode is None:
            self._returncode = -9

    async def wait(self) -> int:
        return self._returncode if self._returncode is not None else self._exit

    async def communicate(self) -> tuple[None, bytes]:
        self._returncode = self._exit
        return None, self._stderr


class _StallProcess:
    """Process that stays alive (communicate blocks) until terminated."""

    def __init__(self) -> None:
        self.pid = 222
        self._returncode: int | None = None
        self._term = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        if self._returncode is None:
            self._returncode = -15
        self._term.set()

    def kill(self) -> None:
        if self._returncode is None:
            self._returncode = -9
        self._term.set()

    async def wait(self) -> int:
        await self._term.wait()
        return self._returncode if self._returncode is not None else -15

    async def communicate(self) -> tuple[None, bytes]:
        await self._term.wait()  # alive until terminated (live but stalled stream)
        return None, b""


def _pipeline(tmp_path: Path, source: _FakeSource, state: AppState | None = None,
              **kw) -> HLSPipeline:
    defaults = dict(
        output_base_dir=str(tmp_path),
        reconnect_base_delay=0.001,
        reconnect_max_delay=0.002,
        reconnect_idle_s=0.02,
    )
    defaults.update(kw)
    return HLSPipeline(source=source, app_state=state, **defaults)


async def _wait(predicate, timeout_s: float = 5.0) -> None:
    for _ in range(int(timeout_s / 0.01) + 1):
        if predicate():
            return
        await asyncio.sleep(0.01)


# ── Stall detection ──────────────────────────────────────────────────────────────

class TestStallDetection:
    async def test_no_segments_triggers_reconnect(self, tmp_path: Path) -> None:
        """If no segment appears within the timeout, FFmpeg is restarted."""
        launches = [0]

        async def _exec(*_a, **_kw):
            launches[0] += 1
            return _StallProcess()

        pipe = _pipeline(
            tmp_path, _FakeSource("cam-test"),
            segment_timeout_s=5,   # config min; override the effective value below
            max_failures=20,
        )
        # Force a very short stall window for the test.
        pipe._segment_timeout_s = 0.05
        pipe._stall_check_interval = 0.02

        with patch("pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_exec):
            await pipe.start()
            await _wait(lambda: pipe.rtsp_reconnect_count >= 2, timeout_s=5.0)
            await pipe.stop()

        assert pipe.rtsp_reconnect_count >= 2
        assert launches[0] >= 3  # initial + ≥2 reconnects


# ── Reconnect metrics + per_camera health ──────────────────────────────────────

class TestMetrics:
    async def test_metrics_published_to_per_camera(self, tmp_path: Path) -> None:
        connected = asyncio.Event()

        async def _exec(*_a, **_kw):
            # First call exits with error; later calls stay connected.
            if not connected.is_set():
                connected.set()
                return _ExitProcess(returncode=1)
            return _StallProcess()

        state = AppState()
        pipe = _pipeline(tmp_path, _FakeSource("cam-test"), state=state, max_failures=20)

        with patch("pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_exec):
            await pipe.start()
            await _wait(lambda: pipe.rtsp_connected and pipe.rtsp_reconnect_count >= 1)
            await pipe.stop()

        assert pipe.rtsp_reconnect_count >= 1
        assert pipe.rtsp_last_connected is not None
        # per_camera block exists and reflects the camera identity/state.
        cam = state.per_camera["cam-test"]
        assert cam.camera_id == "cam-test"
        assert cam.input_type == "rtsp_ip"


# ── Multi-camera fault isolation ────────────────────────────────────────────────

class TestIsolation:
    async def test_one_camera_failure_does_not_affect_other(
        self, tmp_path: Path
    ) -> None:
        """cam-a (broken) goes unavailable while cam-b stays connected."""
        # Both pipelines share the module-level create_subprocess_exec, so a
        # single fake dispatches by camera_id (present in the ffmpeg command):
        # cam-a always fails, cam-b stays connected.
        async def _exec(*args, **_kw):
            cmd = " ".join(str(a) for a in args)
            if "cam-a" in cmd:
                raise FileNotFoundError("no ffmpeg")
            return _StallProcess()

        state = AppState()
        pipe_a = _pipeline(
            tmp_path, _FakeSource("cam-a"), state=state, max_failures=2
        )
        pipe_b = _pipeline(
            tmp_path, _FakeSource("cam-b"), state=state, max_failures=2
        )

        with patch("pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_exec):
            await pipe_a.start()
            await pipe_b.start()
            await _wait(
                lambda: pipe_a.camera_unavailable and pipe_b.rtsp_connected,
                timeout_s=5.0,
            )
            # Capture state WHILE running (stop() resets connected flags).
            a_unavailable = pipe_a.camera_unavailable
            b_connected = pipe_b.rtsp_connected
            cam_a_connected = state.per_camera["cam-a"].connected
            cam_b_connected = state.per_camera["cam-b"].connected
            await pipe_a.stop()
            await pipe_b.stop()

        # cam-a failed and is unavailable; cam-b is unaffected and connected.
        assert a_unavailable is True
        assert b_connected is True
        assert cam_a_connected is False
        assert cam_b_connected is True


# ── Buffer / queue preserved across reconnect ───────────────────────────────────

class TestBufferPreserved:
    async def test_existing_segments_not_deleted_on_reconnect(
        self, tmp_path: Path
    ) -> None:
        """Reconnection must never delete already-captured segments."""
        out_dir = tmp_path / "cam-test"
        out_dir.mkdir()
        existing = out_dir / "segment_00000.ts"
        existing.write_bytes(b"captured")

        async def _exec(*_a, **_kw):
            return _ExitProcess(returncode=1)  # always crash → keep reconnecting

        pipe = _pipeline(tmp_path, _FakeSource("cam-test"), max_failures=20)

        with patch("pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_exec):
            await pipe.start()
            await _wait(lambda: pipe.rtsp_reconnect_count >= 2, timeout_s=5.0)
            await pipe.stop()

        # The previously-captured segment is untouched.
        assert existing.exists()
        assert existing.read_bytes() == b"captured"
