"""Tests for upload retry logic (Story 2.3).

Validates:
* Success after N transient failures
* Exhausting retries → segment marked ``failed`` (file preserved on disk)
* Permanent error → ``failed`` immediately without any retries
* ``attempts`` and ``last_error`` are persisted per-attempt in SQLite
* ``upload_retry_count`` / ``upload_failed_count`` metrics are correct
* The backoff sleep in ``@with_retry`` is mocked so tests run instantly

Implementation notes
--------------------
``patch("utils.retry.asyncio.sleep")`` patches ``asyncio.sleep`` **globally**
because ``utils.retry.asyncio`` refers to the same module object as
``asyncio`` everywhere.  The wait loop in ``_run_queue`` therefore saves a
reference to the real ``asyncio.sleep`` *before* patching so it can still
yield the event loop to the worker task.

DB state is read by reopening the SQLite file after ``q.stop()`` closes it.
"""

from __future__ import annotations

import asyncio
import asyncio as _asyncio  # used to save real sleep before patching
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from storage.db import SegmentDB
from upload.queue import UploadQueue
from upload.s3_client import S3Uploader
from utils.errors import S3PermanentError, S3TransientError

_CREATED_AT = "2026-06-08T12:00:00.000Z"
_S3_KEY = "user-abc123/router-def456/cam-test/segment_00001.ts"
_DB_NAME = "queue.db"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ts(tmp_path: Path, name: str = "segment_00001.ts") -> Path:
    cam_dir = tmp_path / "cam-test"
    cam_dir.mkdir(parents=True, exist_ok=True)
    p = cam_dir / name
    p.write_bytes(b"fake-ts-content")
    return p


def _failing_uploader(
    failures: int,
    error_type: type[Exception] = S3TransientError,
    success_key: str = _S3_KEY,
) -> S3Uploader:
    """Return an uploader mock that raises ``failures`` times, then succeeds."""
    uploader = MagicMock(spec=S3Uploader)
    call_count = [0]

    async def _side_effect(camera_id: str, path: Path) -> str:
        call_count[0] += 1
        if call_count[0] <= failures:
            raise error_type(f"simulated failure #{call_count[0]}")
        return success_key

    uploader.upload_segment = AsyncMock(side_effect=_side_effect)
    return uploader


def _permanent_uploader() -> S3Uploader:
    """Return an uploader mock that always raises S3PermanentError."""
    uploader = MagicMock(spec=S3Uploader)
    uploader.upload_segment = AsyncMock(
        side_effect=S3PermanentError("403 Access Denied")
    )
    return uploader


async def _run_queue(
    tmp_path: Path,
    uploader: S3Uploader,
    ts: Path,
    max_retries: int,
    *,
    wait_for_error: bool = False,
    timeout_s: float = 5.0,
) -> UploadQueue:
    """Start a queue, enqueue one segment, wait for completion, stop.

    IMPORTANT: saves ``asyncio.sleep`` BEFORE patching so the wait loop can
    still yield to the event loop even though the retry backoff sleep is mocked.
    """
    db_path = tmp_path / _DB_NAME
    # Save the real coroutine function before we patch it globally
    _real_sleep = _asyncio.sleep

    with patch("upload.queue._WORKER_POLL_INTERVAL", 0.01), patch(
        "utils.retry.asyncio.sleep"
    ):  # mock retry backoff so tests run instantly
        q = UploadQueue(
            uploader=uploader,
            db_path=db_path,
            max_retries=max_retries,
        )
        await q.start()
        await q.enqueue("cam-test", ts, _CREATED_AT)

        # Poll using the *real* asyncio.sleep so the event loop actually runs
        deadline_iterations = int(timeout_s / 0.02) + 1
        for _ in range(deadline_iterations):
            done = (
                q.upload_success_count >= 1
                if not wait_for_error
                else q.upload_error_count >= 1
            )
            if done:
                break
            await _real_sleep(0.02)

        await q.stop()

    return q


async def _open_db(tmp_path: Path) -> SegmentDB:
    """Reopen the queue DB for post-stop assertions."""
    db = SegmentDB(tmp_path / _DB_NAME)
    await db.open()
    return db


# ── Success after N transient failures ────────────────────────────────────────

class TestSuccessAfterRetries:
    async def test_success_after_1_failure(self, tmp_path: Path) -> None:
        """Segment must be marked ``uploaded`` when a transient error precedes success."""
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=1)

        q = await _run_queue(tmp_path, uploader, ts, max_retries=3)

        assert q.upload_success_count == 1
        assert q.upload_retry_count == 1
        # 1 failure + 1 success = 2 total calls
        assert uploader.upload_segment.call_count == 2

    async def test_success_after_2_failures(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=2)

        q = await _run_queue(tmp_path, uploader, ts, max_retries=5)

        assert q.upload_success_count == 1
        assert q.upload_retry_count == 2

    async def test_uploaded_state_in_db(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=1)

        await _run_queue(tmp_path, uploader, ts, max_retries=3)

        db = await _open_db(tmp_path)
        counts = await db.counts()
        await db.close()
        assert counts["uploaded"] == 1
        assert counts["failed"] == 0


# ── Retry exhaustion → failed (file preserved) ────────────────────────────────

class TestRetryExhaustion:
    async def test_exhausted_retries_marks_failed(self, tmp_path: Path) -> None:
        """When max_retries is exhausted the segment must be in ``failed`` state."""
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=999)

        q = await _run_queue(
            tmp_path, uploader, ts, max_retries=2, wait_for_error=True
        )

        assert q.upload_error_count == 1
        assert q.upload_failed_count == 1
        assert q.upload_success_count == 0

    async def test_exhausted_retries_preserves_file(self, tmp_path: Path) -> None:
        """The ``.ts`` file must NOT be deleted when all retries are exhausted."""
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=999)

        await _run_queue(tmp_path, uploader, ts, max_retries=1, wait_for_error=True)

        assert ts.exists(), "Segment file must be preserved on exhaustion"

    async def test_exhausted_attempt_count(self, tmp_path: Path) -> None:
        """``upload_retry_count`` must equal max_retries + 1 (all attempts intercepted)."""
        ts = _make_ts(tmp_path)
        max_retries = 3
        uploader = _failing_uploader(failures=999)

        q = await _run_queue(
            tmp_path, uploader, ts, max_retries=max_retries, wait_for_error=True
        )

        # with_retry calls _attempt (max_retries+1) times total, each raises,
        # each increments upload_retry_count before re-raise.
        assert q.upload_retry_count == max_retries + 1

    async def test_failed_state_in_db(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=999)

        await _run_queue(tmp_path, uploader, ts, max_retries=1, wait_for_error=True)

        db = await _open_db(tmp_path)
        counts = await db.counts()
        await db.close()
        assert counts["failed"] == 1
        assert counts["uploaded"] == 0


# ── Permanent error → failed immediately ──────────────────────────────────────

class TestPermanentError:
    async def test_permanent_error_marks_failed(self, tmp_path: Path) -> None:
        """S3PermanentError must set state to ``failed`` without any retries."""
        ts = _make_ts(tmp_path)
        uploader = _permanent_uploader()

        q = await _run_queue(
            tmp_path, uploader, ts, max_retries=10, wait_for_error=True
        )

        assert q.upload_failed_count == 1
        # Exactly one call — permanent errors are never retried
        assert uploader.upload_segment.call_count == 1

    async def test_permanent_error_zero_retries(self, tmp_path: Path) -> None:
        """``upload_retry_count`` must be 0 for a permanent error."""
        ts = _make_ts(tmp_path)
        uploader = _permanent_uploader()

        q = await _run_queue(
            tmp_path, uploader, ts, max_retries=10, wait_for_error=True
        )

        assert q.upload_retry_count == 0

    async def test_permanent_error_preserves_file(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        uploader = _permanent_uploader()

        await _run_queue(
            tmp_path, uploader, ts, max_retries=5, wait_for_error=True
        )

        assert ts.exists()

    async def test_permanent_error_state_in_db(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        uploader = _permanent_uploader()

        await _run_queue(tmp_path, uploader, ts, max_retries=5, wait_for_error=True)

        db = await _open_db(tmp_path)
        counts = await db.counts()
        await db.close()
        assert counts["failed"] == 1


# ── Attempt tracking in SQLite ─────────────────────────────────────────────────

class TestAttemptTracking:
    async def test_attempts_recorded_per_transient_failure(
        self, tmp_path: Path
    ) -> None:
        """Each transient failure must increment ``attempts`` in SQLite."""
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=2, success_key=_S3_KEY)

        await _run_queue(tmp_path, uploader, ts, max_retries=5)

        # The DB should show attempts = 2 (each transient failure was recorded)
        db = await _open_db(tmp_path)

        def _fetch() -> int:
            conn = db._conn
            assert conn is not None
            row = conn.execute(
                "SELECT attempts FROM upload_queue WHERE state='uploaded'"
            ).fetchone()
            return int(row["attempts"]) if row else 0

        attempts = await asyncio.to_thread(_fetch)
        await db.close()
        assert attempts == 2

    async def test_last_error_recorded(self, tmp_path: Path) -> None:
        """``last_error`` must be populated in the DB after a transient failure."""
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=999)

        await _run_queue(tmp_path, uploader, ts, max_retries=1, wait_for_error=True)

        db = await _open_db(tmp_path)

        def _fetch() -> str | None:
            conn = db._conn
            assert conn is not None
            row = conn.execute(
                "SELECT last_error FROM upload_queue WHERE state='failed'"
            ).fetchone()
            return row["last_error"] if row else None

        last_error = await asyncio.to_thread(_fetch)
        await db.close()
        assert last_error is not None
        assert len(last_error) > 0


# ── Metrics ────────────────────────────────────────────────────────────────────

class TestMetrics:
    async def test_success_count_increments(self, tmp_path: Path) -> None:
        ts = _make_ts(tmp_path)
        uploader = _mock_uploader_ok()

        q = await _run_queue(tmp_path, uploader, ts, max_retries=0)

        assert q.upload_success_count == 1
        assert q.upload_error_count == 0
        assert q.upload_retry_count == 0

    async def test_retry_count_increments_per_attempt(
        self, tmp_path: Path
    ) -> None:
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=3)

        q = await _run_queue(tmp_path, uploader, ts, max_retries=5)

        assert q.upload_retry_count == 3
        assert q.upload_success_count == 1

    async def test_failed_count_increments_on_exhaustion(
        self, tmp_path: Path
    ) -> None:
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=999)

        q = await _run_queue(
            tmp_path, uploader, ts, max_retries=2, wait_for_error=True
        )

        assert q.upload_failed_count == 1
        assert q.upload_success_count == 0


# ── Backoff is mocked (no real sleep) ─────────────────────────────────────────

class TestBackoffMocked:
    async def test_retry_does_not_actually_sleep(self, tmp_path: Path) -> None:
        """``utils.retry.asyncio.sleep`` must be called for each retry interval,
        but the mock returns immediately so the test runs fast.
        """
        ts = _make_ts(tmp_path)
        uploader = _failing_uploader(failures=2)
        sleep_calls: list[float] = []
        _real_sleep = _asyncio.sleep  # save real sleep before patching

        async def _fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            # Yield to event loop without actually sleeping
            f: asyncio.Future[None] = asyncio.get_event_loop().create_future()
            asyncio.get_event_loop().call_soon(f.set_result, None)
            await f

        with patch("upload.queue._WORKER_POLL_INTERVAL", 0.01), patch(
            "utils.retry.asyncio.sleep", new=_fake_sleep
        ):
            q = UploadQueue(
                uploader=uploader,
                db_path=tmp_path / _DB_NAME,
                max_retries=5,
            )
            await q.start()
            await q.enqueue("cam-test", ts, _CREATED_AT)

            deadline = int(5.0 / 0.02) + 1
            for _ in range(deadline):
                if q.upload_success_count >= 1:
                    break
                await _real_sleep(0.02)

            await q.stop()

        assert q.upload_success_count == 1
        # 2 failures → 2 backoff sleep calls before the third (successful) attempt
        assert len(sleep_calls) == 2


# ── Helper ─────────────────────────────────────────────────────────────────────

def _mock_uploader_ok(key: str = _S3_KEY) -> S3Uploader:
    uploader = MagicMock(spec=S3Uploader)
    uploader.upload_segment = AsyncMock(return_value=key)
    return uploader
