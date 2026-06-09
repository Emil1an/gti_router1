"""Tests for UploadQueue (Story 2.2).

Uses a mocked S3Uploader to avoid real network calls.  The SQLite DB uses a
temporary file (tmp_path) so tests are isolated and stateless.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from storage.db import SegmentDB
from upload.queue import UploadQueue, _WORKER_POLL_INTERVAL
from upload.s3_client import S3Uploader
from utils.errors import S3PermanentError, S3TransientError

_CREATED_AT = "2026-06-08T10:00:00.000Z"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ts(tmp_path: Path, name: str = "segment_00001.ts") -> Path:
    cam_dir = tmp_path / "cam-test"
    cam_dir.mkdir(parents=True, exist_ok=True)
    p = cam_dir / name
    p.write_bytes(b"fake-ts")
    return p


def _mock_uploader(success_key: str = "user/router/cam/seg.ts") -> S3Uploader:
    """Return an S3Uploader mock that succeeds immediately."""
    uploader = MagicMock(spec=S3Uploader)
    uploader.upload_segment = AsyncMock(return_value=success_key)
    uploader.upload_playlist = AsyncMock(return_value=success_key)
    return uploader


async def _make_queue(
    tmp_path: Path,
    uploader: S3Uploader | None = None,
    buffer_dir: Path | None = None,
    max_retries: int = 0,
) -> UploadQueue:
    if uploader is None:
        uploader = _mock_uploader()
    db_path = tmp_path / "queue.db"
    q = UploadQueue(
        uploader=uploader,
        buffer_dir=buffer_dir,
        db_path=db_path,
        max_retries=max_retries,
    )
    return q


# ── enqueue / basic flow ───────────────────────────────────────────────────────

class TestEnqueue:
    async def test_enqueue_adds_pending_item(self, tmp_path: Path) -> None:
        q = await _make_queue(tmp_path)
        await q._db.open()
        ts = _make_ts(tmp_path)
        await q.enqueue("cam-test", ts, _CREATED_AT)
        counts = await q._db.counts()
        await q._db.close()
        assert counts["pending"] == 1

    async def test_enqueue_is_idempotent(self, tmp_path: Path) -> None:
        q = await _make_queue(tmp_path)
        await q._db.open()
        ts = _make_ts(tmp_path)
        await q.enqueue("cam-test", ts, _CREATED_AT)
        await q.enqueue("cam-test", ts, _CREATED_AT)  # duplicate
        counts = await q._db.counts()
        await q._db.close()
        assert counts["pending"] == 1

    async def test_enqueue_wakes_worker(self, tmp_path: Path) -> None:
        q = await _make_queue(tmp_path)
        q._wake.clear()
        ts = _make_ts(tmp_path)
        await q._db.open()
        await q.enqueue("cam-test", ts, _CREATED_AT)
        await q._db.close()
        assert q._wake.is_set()


# ── Worker: happy path ─────────────────────────────────────────────────────────

class TestWorkerHappyPath:
    async def test_worker_uploads_segment_and_marks_uploaded(
        self, tmp_path: Path
    ) -> None:
        ts = _make_ts(tmp_path)
        uploader = _mock_uploader("u/r/cam-test/segment_00001.ts")

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = await _make_queue(tmp_path, uploader=uploader)
            await q.start()
            await q.enqueue("cam-test", ts, _CREATED_AT)

            # Wait for worker to process
            for _ in range(40):
                if q.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.05)

            await q.stop()

        assert q.upload_success_count == 1
        uploader.upload_segment.assert_called_once_with("cam-test", ts)

    async def test_worker_marks_uploaded_state_in_db(
        self, tmp_path: Path
    ) -> None:
        ts = _make_ts(tmp_path)
        uploader = _mock_uploader("u/r/cam-test/segment_00001.ts")

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = await _make_queue(tmp_path, uploader=uploader)
            await q.start()
            await q.enqueue("cam-test", ts, _CREATED_AT)

            for _ in range(40):
                if q.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.05)

            counts = await q._db.counts()
            await q.stop()

        assert counts["uploaded"] == 1
        assert counts["pending"] == 0

    async def test_worker_processes_multiple_segments(
        self, tmp_path: Path
    ) -> None:
        uploader = _mock_uploader()
        segments = [_make_ts(tmp_path, f"segment_{i:05d}.ts") for i in range(5)]

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = await _make_queue(tmp_path, uploader=uploader)
            await q.start()
            for seg in segments:
                await q.enqueue("cam-test", seg, _CREATED_AT)

            for _ in range(100):
                if q.upload_success_count >= 5:
                    break
                await asyncio.sleep(0.05)

            await q.stop()

        assert q.upload_success_count == 5


# ── Worker: missing file ───────────────────────────────────────────────────────

class TestWorkerMissingFile:
    async def test_missing_file_is_marked_failed(self, tmp_path: Path) -> None:
        """A segment that no longer exists on disk must be marked failed."""
        ghost = tmp_path / "cam-test" / "segment_ghost.ts"
        ghost.parent.mkdir(parents=True, exist_ok=True)
        uploader = _mock_uploader()

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = await _make_queue(tmp_path, uploader=uploader)
            await q.start()
            # enqueue a path that does NOT exist on disk
            await q._db.add_segment("cam-test", ghost, 0, _CREATED_AT)
            q._wake.set()

            for _ in range(40):
                if q.upload_error_count >= 1:
                    break
                await asyncio.sleep(0.05)

            counts = await q._db.counts()
            await q.stop()

        assert counts["failed"] == 1
        uploader.upload_segment.assert_not_called()


# ── Worker: crash recovery ─────────────────────────────────────────────────────

class TestCrashRecovery:
    async def test_uploading_items_recovered_on_start(
        self, tmp_path: Path
    ) -> None:
        """Items stuck in 'uploading' state must be reset to 'pending' at startup."""
        db_path = tmp_path / "queue.db"
        ts = _make_ts(tmp_path)

        # Simulate a crash: open DB, insert and mark uploading, close without finishing
        db = SegmentDB(db_path)
        await db.open()
        row_id = await db.add_segment("cam-test", ts, 4, _CREATED_AT)
        await db.mark_uploading(row_id)
        await db.close()

        uploader = _mock_uploader()
        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = UploadQueue(
                uploader=uploader,
                db_path=db_path,
                max_retries=0,
            )
            await q.start()

            for _ in range(40):
                if q.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.05)

            await q.stop()

        assert q.upload_success_count == 1


# ── Orphan scanning ────────────────────────────────────────────────────────────

class TestOrphanScanning:
    async def test_orphan_ts_files_are_enqueued(self, tmp_path: Path) -> None:
        """`.ts` files in buffer_dir not in the DB must be added as pending."""
        buffer_dir = tmp_path / "hls"
        cam_dir = buffer_dir / "cam-orphan"
        cam_dir.mkdir(parents=True)
        orphan = cam_dir / "segment_00099.ts"
        orphan.write_bytes(b"orphan")

        uploader = _mock_uploader()
        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = UploadQueue(
                uploader=uploader,
                buffer_dir=buffer_dir,
                db_path=tmp_path / "q.db",
                max_retries=0,
            )
            await q.start()

            for _ in range(40):
                if q.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.05)

            await q.stop()

        assert q.upload_success_count == 1

    async def test_non_orphan_files_are_not_duplicated(
        self, tmp_path: Path
    ) -> None:
        """Files already in the DB must not be added again."""
        buffer_dir = tmp_path / "hls"
        cam_dir = buffer_dir / "cam-test"
        cam_dir.mkdir(parents=True)
        ts = cam_dir / "segment_00001.ts"
        ts.write_bytes(b"fake")

        db_path = tmp_path / "q.db"
        db = SegmentDB(db_path)
        await db.open()
        await db.add_segment("cam-test", ts, 4, _CREATED_AT)
        await db.close()

        uploader = _mock_uploader()
        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = UploadQueue(
                uploader=uploader,
                buffer_dir=buffer_dir,
                db_path=db_path,
                max_retries=0,
            )
            await q.start()

            for _ in range(40):
                if q.upload_success_count >= 1:
                    break
                await asyncio.sleep(0.05)

            counts = await q._db.counts()
            await q.stop()

        # Still exactly 1 row — no duplicate
        assert counts["uploaded"] == 1
        total = sum(counts.values())
        assert total == 1


# ── Metrics ────────────────────────────────────────────────────────────────────

class TestMetrics:
    async def test_items_processed_increments(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = await _make_queue(tmp_path)
            await q.start()
            await q.enqueue("cam-test", ts, _CREATED_AT)

            for _ in range(40):
                if q.items_processed >= 1:
                    break
                await asyncio.sleep(0.05)

            await q.stop()

        assert q.items_processed == 1

    async def test_queue_size_reflects_pending_count(self, tmp_path: Path) -> None:
        q = await _make_queue(tmp_path)
        await q._db.open()
        ts1 = _make_ts(tmp_path, "s1.ts")
        ts2 = _make_ts(tmp_path, "s2.ts")
        await q.enqueue("cam-test", ts1, _CREATED_AT)
        await q.enqueue("cam-test", ts2, _CREATED_AT)
        size = await q.queue_size()
        await q._db.close()
        assert size == 2


# ── Stop behaviour ─────────────────────────────────────────────────────────────

class TestStopBehaviour:
    async def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.05):
            q = await _make_queue(tmp_path)
            await q.start()
            await q.stop()
            await q.stop()  # second call must not raise
