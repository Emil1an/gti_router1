"""End-to-end integration tests for the capture→upload subsystem (Story 2.6).

These tests exercise the real wiring — ``HLSPipeline`` callback → ``UploadQueue``
→ ``S3Uploader`` (mocked) → SQLite confirmation — and the ``UploadService``
graceful shutdown.  They do not require FFmpeg or AWS:

* The S3 client is an ``AsyncMock`` (``moto`` + ``aiobotocore`` cannot mock the
  async request body, so we mock at the client boundary — consistent with the
  Story 2.1–2.3 suites).
* Segment production is simulated by either driving ``HLSPipeline``'s segment
  watcher with hand-written ``.ts`` files, or by calling the callback directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pipeline.ffmpeg_hls as _hls_mod
from camera.sources.base import StreamMetadata, VideoSource
from pipeline.ffmpeg_hls import HLSPipeline
from storage.db import SegmentDB
from upload.queue import UploadQueue
from upload.s3_client import S3Uploader
from upload.service import UploadService

_NOW_ISO = "2026-06-08T10:00:00.000Z"


# ── Helpers ────────────────────────────────────────────────────────────────────

class _FakeSource(VideoSource):
    """Minimal VideoSource stub (no real FFmpeg needed for these tests)."""

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


def _ok_uploader(order: list[str] | None = None) -> S3Uploader:
    uploader = MagicMock(spec=S3Uploader)
    uploader.start = AsyncMock()
    uploader.stop = AsyncMock()

    async def _side_effect(camera_id: str, path: Path) -> str:
        if order is not None:
            order.append(path.name)
        return f"key/{camera_id}/{path.name}"

    uploader.upload_segment = AsyncMock(side_effect=_side_effect)
    return uploader


def _write_ts(buffer_dir: Path, camera: str, name: str) -> Path:
    cam = buffer_dir / camera
    cam.mkdir(parents=True, exist_ok=True)
    p = cam / name
    p.write_bytes(b"fake-ts")
    return p


# ── Callback wiring: HLSPipeline → UploadQueue.enqueue ──────────────────────────

class TestCallbackWiring:
    async def test_pipeline_callback_enqueues_segment(self, tmp_path: Path) -> None:
        """A new .ts detected by the pipeline watcher must land in the queue."""
        db = SegmentDB(tmp_path / "q.db")
        await db.open()
        uploader = _ok_uploader()
        queue = UploadQueue(uploader=uploader, db=db, max_retries=0)

        source = _FakeSource("cam-test")
        pipe = HLSPipeline(
            source=source,
            on_segment=queue.enqueue,  # ← the Story 2.6 wiring
            output_base_dir=str(tmp_path),
        )

        out_dir = tmp_path / "cam-test"
        out_dir.mkdir()

        enqueued = asyncio.Event()
        orig_enqueue = queue.enqueue

        async def _tracking_enqueue(cam, p, ts):
            await orig_enqueue(cam, p, ts)
            enqueued.set()

        pipe._on_segment = _tracking_enqueue

        with patch.object(_hls_mod, "_SEGMENT_POLL_INTERVAL", 0.02):
            pipe._running = True
            pipe._watcher_task = asyncio.create_task(pipe._segment_watcher())
            await asyncio.sleep(0.02)
            _write_ts(tmp_path, "cam-test", "segment_00000.ts")
            await asyncio.wait_for(enqueued.wait(), timeout=3.0)
            pipe._running = False
            pipe._watcher_task.cancel()
            try:
                await pipe._watcher_task
            except asyncio.CancelledError:
                pass

        counts = await db.counts()
        await db.close()
        assert counts["pending"] == 1


# ── Full E2E flow ───────────────────────────────────────────────────────────────

class TestE2EFlow:
    async def test_segment_flows_to_s3_and_confirmed(self, tmp_path: Path) -> None:
        """created → enqueued → uploaded → confirmed in SQLite."""
        db = SegmentDB(tmp_path / "q.db")
        await db.open()
        uploader = _ok_uploader()
        queue = UploadQueue(uploader=uploader, db=db, max_retries=0)

        seg = _write_ts(tmp_path, "cam-test", "segment_00001.ts")

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.02):
            await queue.start()
            # Simulated pipeline producer emitting the callback contract:
            await queue.enqueue("cam-test", seg, _NOW_ISO)
            for _ in range(200):
                if queue.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.02)
            await queue.stop(drain_timeout_s=2.0)

        counts = await db.counts()
        await db.close()
        assert queue.upload_success_count == 1
        assert counts["uploaded"] == 1
        uploader.upload_segment.assert_called_once_with("cam-test", seg)

    async def test_upload_latency_seconds_emitted(self, tmp_path: Path) -> None:
        """upload_latency_seconds must be computed from created_at to confirmation."""
        db = SegmentDB(tmp_path / "q.db")
        await db.open()
        queue = UploadQueue(uploader=_ok_uploader(), db=db, max_retries=0)

        seg = _write_ts(tmp_path, "cam-test", "segment_00001.ts")

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.02):
            await queue.start()
            await queue.enqueue("cam-test", seg, _NOW_ISO)
            for _ in range(200):
                if queue.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.02)
            await queue.stop(drain_timeout_s=2.0)
        await db.close()

        # created_at is 2026-06-08; real "now" is later → positive latency.
        assert queue.upload_latency_seconds_last > 0.0


# ── Graceful shutdown ────────────────────────────────────────────────────────────

class TestGracefulShutdown:
    async def test_shutdown_waits_for_in_flight_upload(self, tmp_path: Path) -> None:
        """stop() must let an in-progress upload finish before returning."""
        db = SegmentDB(tmp_path / "q.db")
        await db.open()

        upload_started = asyncio.Event()
        release = asyncio.Event()
        finished = []

        uploader = MagicMock(spec=S3Uploader)

        async def _slow_upload(camera_id: str, path: Path) -> str:
            upload_started.set()
            await release.wait()       # block until the test allows completion
            finished.append(path.name)
            return f"key/{path.name}"

        uploader.upload_segment = AsyncMock(side_effect=_slow_upload)

        queue = UploadQueue(uploader=uploader, db=db, max_retries=0)
        seg = _write_ts(tmp_path, "cam-test", "segment_00001.ts")

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.02):
            await queue.start()
            await queue.enqueue("cam-test", seg, _NOW_ISO)
            await asyncio.wait_for(upload_started.wait(), timeout=3.0)

            # Begin graceful shutdown with a generous drain window; release the
            # upload shortly after so it completes within the window.
            async def _release_soon():
                await asyncio.sleep(0.05)
                release.set()

            asyncio.create_task(_release_soon())
            await queue.stop(drain_timeout_s=5.0)

        counts = await db.counts()
        await db.close()
        # The in-flight upload was allowed to finish, not abandoned.
        assert finished == ["segment_00001.ts"]
        assert counts["uploaded"] == 1

    async def test_shutdown_timeout_leaves_item_recoverable(self, tmp_path: Path) -> None:
        """If an upload exceeds the drain timeout it is cancelled but not lost."""
        db = SegmentDB(tmp_path / "q.db")
        await db.open()

        upload_started = asyncio.Event()
        uploader = MagicMock(spec=S3Uploader)

        async def _hang(camera_id: str, path: Path) -> str:
            upload_started.set()
            await asyncio.sleep(3600)  # never completes within the test
            return "never"

        uploader.upload_segment = AsyncMock(side_effect=_hang)
        queue = UploadQueue(uploader=uploader, db=db, max_retries=0)
        seg = _write_ts(tmp_path, "cam-test", "segment_00001.ts")

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.02):
            await queue.start()
            await queue.enqueue("cam-test", seg, _NOW_ISO)
            await asyncio.wait_for(upload_started.wait(), timeout=3.0)
            # Drain timeout is tiny → worker gets cancelled mid-upload.
            await queue.stop(drain_timeout_s=0.1)

        counts = await db.counts()
        # Item stays 'uploading' in SQLite (recoverable on restart), never lost.
        await db.close()
        assert counts["uploading"] == 1
        assert counts["uploaded"] == 0


# ── Restart recovery ────────────────────────────────────────────────────────────

class TestRestartRecovery:
    async def test_restart_resumes_pending_without_duplicates(
        self, tmp_path: Path
    ) -> None:
        """After a crash, a restart resumes pending uploads and never re-uploads."""
        db_path = tmp_path / "q.db"
        seg1 = _write_ts(tmp_path, "cam-test", "segment_00001.ts")
        seg2 = _write_ts(tmp_path, "cam-test", "segment_00002.ts")

        # ── First run: upload seg1, then "crash" with seg2 stuck uploading ──────
        db = SegmentDB(db_path)
        await db.open()
        id1 = await db.add_segment("cam-test", seg1, 7, _NOW_ISO)
        await db.mark_uploading(id1)
        await db.mark_uploaded(id1, "key/seg1")
        id2 = await db.add_segment("cam-test", seg2, 7, _NOW_ISO)
        await db.mark_uploading(id2)  # crashed mid-upload
        await db.close()

        # ── Restart: queue must reset seg2→pending and upload only it ───────────
        db2 = SegmentDB(db_path)
        await db2.open()
        order: list[str] = []
        queue = UploadQueue(uploader=_ok_uploader(order), db=db2, max_retries=0)

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.02):
            await queue.start()
            for _ in range(200):
                if queue.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.02)
            await queue.stop(drain_timeout_s=2.0)

        counts = await db2.counts()
        await db2.close()

        # Only seg2 was (re)uploaded; seg1 was already uploaded and untouched.
        assert order == ["segment_00002.ts"]
        assert counts["uploaded"] == 2


# ── UploadService orchestration ──────────────────────────────────────────────────

class TestUploadService:
    async def test_service_start_stop_uploads_segment(self, tmp_path: Path) -> None:
        """UploadService wires the queue worker under its own start/stop lifecycle."""
        order: list[str] = []
        uploader = _ok_uploader(order)

        service = UploadService(
            sources=None, db_path=tmp_path / "svc.db", uploader=uploader
        )
        seg = _write_ts(tmp_path, "cam-test", "segment_00001.ts")

        with patch("upload.service._BUFFER_ENFORCE_INTERVAL", 0.05), patch(
            "upload.queue._WORKER_POLL_INTERVAL", 0.02
        ):
            await service.start()
            await service.queue.enqueue("cam-test", seg, _NOW_ISO)
            for _ in range(200):
                if service.queue.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.02)
            await service.stop(drain_timeout_s=2.0)

        assert order == ["segment_00001.ts"]
        uploader.start.assert_awaited_once()
        uploader.stop.assert_awaited_once()

    async def test_service_stop_is_safe_without_pipelines(self, tmp_path: Path) -> None:
        uploader = _ok_uploader()
        service = UploadService(sources=None, db_path=tmp_path / "svc.db", uploader=uploader)
        with patch("upload.service._BUFFER_ENFORCE_INTERVAL", 0.05), patch(
            "upload.queue._WORKER_POLL_INTERVAL", 0.02
        ):
            await service.start()
            await service.stop(drain_timeout_s=1.0)
        uploader.stop.assert_awaited_once()
