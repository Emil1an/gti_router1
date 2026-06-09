"""Upload queue — durable producer/consumer with 3:1 priority scheduling.

Stories
-------
* **2.2** — durable SQLite-backed queue, decoupled producer/consumer, crash
  recovery + orphan scan.
* **2.3** — per-segment retry of transient errors via the single ``@with_retry``.
* **2.5** — realtime/backlog classification with a configurable 3:1 scheduler and
  fair round-robin across cameras (all derived from the same SQLite index — no
  second source of truth).
* **2.6** — ``upload_latency_seconds`` metric and a graceful shutdown that drains
  in-flight uploads within a configurable timeout before cancelling.

Architecture
------------
::

    HLSPipeline                         UploadQueue (worker)
    ───────────                         ─────────────────────────────────────
    on_segment callback  ──enqueue()──▶  SQLite (pending) ──▶ S3Uploader
                                         SQLite (uploaded / failed)

Scheduling (Story 2.5)
----------------------
A pending segment is ``realtime`` while ``now - created_at <=
upload.backlog_age_threshold_s`` and ``backlog`` once it is older.  When both
classes have work the worker serves ``priority_ratio`` realtime segments per
backlog segment (default 3:1).  If one class is empty the other is drained
without waiting.  Within a class, cameras are served round-robin for fairness.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config.loader import get_config
from storage.db import SegmentDB
from upload.s3_client import S3Uploader
from utils.errors import S3PermanentError, S3TransientError, S3UploadError
from utils.logging import get_logger
from utils.retry import with_retry

# Worker poll interval when no items are pending (seconds)
_WORKER_POLL_INTERVAL: float = 1.0

_ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 ``...Z`` timestamp; return ``None`` if unparseable."""
    try:
        return datetime.strptime(value, _ISO_FMT).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


class UploadQueue:
    """Durable, async upload queue backed by a SQLite index.

    Args:
        uploader:    ``S3Uploader`` instance (started/stopped by the caller).
        buffer_dir:  directory the HLS pipeline writes ``.ts`` files into; scanned
                     for orphans at startup.  ``None`` skips orphan scanning.
        db_path:     SQLite path (used only when ``db`` is not supplied).
                     Defaults to ``/var/lib/gti-router/upload_queue.db``.
        db:          a shared :class:`~storage.db.SegmentDB`.  When provided the
                     queue does **not** open or close it — the owner does (used by
                     the integrating service in Story 2.6 so the buffer can share
                     the same index).  When ``None`` the queue owns its own DB.
        max_retries: additional upload attempts for transient errors.  Defaults to
                     ``aws.upload_max_retries``.
    """

    def __init__(
        self,
        uploader: S3Uploader,
        buffer_dir: Path | None = None,
        db_path: Path | None = None,
        db: SegmentDB | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._uploader = uploader
        self._buffer_dir = buffer_dir

        cfg = get_config()
        if db is not None:
            self._db = db
            self._owns_db = False
        else:
            resolved_db_path = db_path or Path("/var/lib/gti-router/upload_queue.db")
            self._db = SegmentDB(resolved_db_path)
            self._owns_db = True

        self._max_retries: int = (
            max_retries if max_retries is not None else cfg.aws.upload_max_retries
        )

        # ── Scheduling parameters (Story 2.5) ───────────────────────────────────
        self._priority_ratio: int = cfg.upload.priority_ratio
        self._backlog_age_threshold_s: int = cfg.upload.backlog_age_threshold_s
        self._shutdown_timeout_s: int = cfg.upload.shutdown_timeout_s
        self._rt_served: int = 0  # consecutive realtime served in current cycle
        self._rr_index: dict[str, int] = {"realtime": 0, "backlog": 0}

        self._logger = get_logger(__name__)
        self._worker_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._running = False

        # ── Metrics ──────────────────────────────────────────────────────────────
        self._upload_success_count: int = 0
        self._upload_error_count: int = 0
        self._upload_retry_count: int = 0
        self._upload_failed_count: int = 0
        self._items_processed: int = 0
        self._upload_latency_seconds_last: float = 0.0

    # ── Public lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Open the database (if owned), recover in-progress items, scan for
        orphans, and start the background upload worker.
        """
        if self._owns_db:
            await self._db.open()

        # Crash recovery: items stuck in 'uploading' → 'pending'
        recovered = await self._db.reset_uploading_to_pending()
        if recovered:
            self._logger.info("Crash recovery: reset %d uploading→pending", recovered)

        # Orphan detection: .ts files on disk that aren't in the index
        await self._scan_orphans()

        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="upload-worker"
        )
        self._logger.info(
            "UploadQueue started",
            extra={
                "max_retries": self._max_retries,
                "priority_ratio": self._priority_ratio,
                "backlog_age_threshold_s": self._backlog_age_threshold_s,
            },
        )

    async def stop(self, drain_timeout_s: float | None = None) -> None:
        """Gracefully stop the worker, waiting for in-flight uploads (Story 2.6).

        The worker stops claiming new items immediately; an upload already in
        progress is allowed to finish for up to ``drain_timeout_s`` seconds
        (default ``upload.shutdown_timeout_s``).  After the timeout the worker is
        cancelled — the in-flight item stays ``uploading`` in SQLite and is
        recovered to ``pending`` on next start.  Nothing un-uploaded is lost.
        """
        if not self._running and self._worker_task is None:
            if self._owns_db:
                await self._db.close()
            return

        timeout = (
            drain_timeout_s if drain_timeout_s is not None else self._shutdown_timeout_s
        )
        self._running = False
        self._wake.set()  # unblock the worker if it is sleeping

        task = self._worker_task
        if task is not None and not task.done():
            try:
                # Wait for the worker to finish the in-flight upload and exit.
                # On timeout, wait_for cancels the task for us.
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Shutdown drain exceeded %.0fs — cancelling worker; in-flight "
                    "item stays recoverable in SQLite",
                    timeout,
                )
            except (asyncio.CancelledError, Exception):
                pass
        self._worker_task = None

        if self._owns_db:
            await self._db.close()

        self._logger.info(
            "UploadQueue stopped",
            extra={
                "upload_success_count": self._upload_success_count,
                "upload_error_count": self._upload_error_count,
                "upload_retry_count": self._upload_retry_count,
                "upload_failed_count": self._upload_failed_count,
            },
        )

    # ── Public producer API ──────────────────────────────────────────────────────

    async def enqueue(
        self,
        camera_id: str,
        segment_path: Path,
        created_at: str,
    ) -> None:
        """Enqueue a segment for upload (the HLS-pipeline callback contract).

        ``async (camera_id: str, segment_path: Path, created_at: str) -> None``

        Idempotent: enqueueing the same ``segment_path`` twice is safe.
        """
        try:
            size_bytes = segment_path.stat().st_size
        except OSError:
            size_bytes = 0

        await self._db.add_segment(camera_id, segment_path, size_bytes, created_at)
        self._wake.set()  # wake the worker without delay
        self._logger.debug(
            "Segment enqueued",
            extra={
                "camera_id": camera_id,
                "segment": segment_path.name,
                "size_bytes": size_bytes,
                "created_at": created_at,
            },
        )

    # ── Metrics ──────────────────────────────────────────────────────────────────

    @property
    def items_processed(self) -> int:
        """Total segments that reached ``uploaded`` state since start."""
        return self._items_processed

    @property
    def upload_success_count(self) -> int:
        return self._upload_success_count

    @property
    def upload_error_count(self) -> int:
        return self._upload_error_count

    @property
    def upload_retry_count(self) -> int:
        return self._upload_retry_count

    @property
    def upload_failed_count(self) -> int:
        return self._upload_failed_count

    @property
    def upload_latency_seconds_last(self) -> float:
        """Latency (s) of the most recently confirmed upload (created_at→S3)."""
        return self._upload_latency_seconds_last

    async def queue_size(self) -> int:
        """Number of items currently in ``pending`` or ``uploading`` state."""
        c = await self._db.counts()
        return c["pending"] + c["uploading"]

    async def items_pending(self) -> int:
        """Number of items in ``pending`` state."""
        c = await self._db.counts()
        return c["pending"]

    async def realtime_queue_size(self) -> int:
        """Number of pending ``realtime`` segments (Story 2.5 metric)."""
        c = await self._db.class_counts(self._cutoff_iso())
        return c["realtime"]

    async def backlog_queue_size(self) -> int:
        """Number of pending ``backlog`` segments (Story 2.5 metric)."""
        c = await self._db.class_counts(self._cutoff_iso())
        return c["backlog"]

    async def backlog_oldest_age_seconds(self) -> float:
        """Age (s) of the oldest pending ``backlog`` segment, or ``0.0`` if none."""
        oldest = await self._db.oldest_backlog_created_at(self._cutoff_iso())
        if oldest is None:
            return 0.0
        ts = _parse_iso(oldest)
        if ts is None:
            return 0.0
        return max(0.0, (datetime.now(tz=UTC) - ts).total_seconds())

    # ── Internal: classification helpers ─────────────────────────────────────────

    def _cutoff_iso(self) -> str:
        """ISO cutoff: segments older than this are ``backlog``, newer ``realtime``."""
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=self._backlog_age_threshold_s)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _choose_class(self, realtime_available: bool, backlog_available: bool) -> str | None:
        """Pick the next class honouring the 3:1 ratio and draining the non-empty
        class when the other is empty.
        """
        if realtime_available and backlog_available:
            return "realtime" if self._rt_served < self._priority_ratio else "backlog"
        if realtime_available:
            return "realtime"
        if backlog_available:
            return "backlog"
        return None

    # ── Worker ────────────────────────────────────────────────────────────────────

    async def _claim_next(self) -> dict[str, Any] | None:
        """Select, claim (mark ``uploading``) and return the next item, or ``None``.

        Applies the 3:1 realtime/backlog ratio and round-robin camera fairness.
        """
        cutoff = self._cutoff_iso()
        rt_cameras = await self._db.pending_cameras_for_class("realtime", cutoff)
        bl_cameras = await self._db.pending_cameras_for_class("backlog", cutoff)

        klass = self._choose_class(bool(rt_cameras), bool(bl_cameras))
        if klass is None:
            return None

        cameras = rt_cameras if klass == "realtime" else bl_cameras
        # Round-robin across cameras within the chosen class.
        idx = self._rr_index[klass] % len(cameras)
        camera_id = cameras[idx]
        self._rr_index[klass] = self._rr_index[klass] + 1

        item = await self._db.next_pending_for_camera_class(camera_id, klass, cutoff)
        if item is None:
            return None

        claimed = await self._db.mark_uploading(item["id"])
        if not claimed:
            return None

        # Update the ratio counter only once we've actually claimed an item.
        if klass == "realtime":
            self._rt_served += 1
        else:
            self._rt_served = 0

        item["_class"] = klass
        return item

    async def _worker_loop(self) -> None:
        """Background task: claim → upload → repeat; sleep when idle."""
        try:
            while self._running:
                try:
                    item = await self._claim_next()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # never let a claim error kill the worker
                    self._logger.error("Error selecting next item: %s", exc)
                    item = None

                if item is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(
                            self._wake.wait(), timeout=_WORKER_POLL_INTERVAL
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue

                try:
                    await self._process_item(item)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._logger.error("Error processing item: %s", exc)
        except asyncio.CancelledError:
            pass

    async def _process_item(self, item: dict[str, Any]) -> None:
        """Upload one segment, applying ``@with_retry`` for transient failures."""
        item_id: int = item["id"]
        camera_id: str = item["camera_id"]
        segment_path = Path(item["segment_path"])
        created_at: str = item.get("created_at", "")
        attempt_counter = [item.get("attempts", 0)]

        self._logger.debug(
            "Processing segment",
            extra={
                "camera_id": camera_id,
                "segment": segment_path.name,
                "class": item.get("_class"),
            },
        )

        # Guard: file must still be on disk
        if not segment_path.exists():
            self._logger.warning(
                "Segment file missing on disk — marking failed",
                extra={"camera_id": camera_id, "path": str(segment_path)},
            )
            await self._db.mark_failed(item_id, "segment file not found on disk")
            self._upload_error_count += 1
            self._upload_failed_count += 1
            return

        async def _attempt() -> str:
            try:
                return await self._uploader.upload_segment(camera_id, segment_path)
            except S3TransientError as exc:
                attempt_counter[0] += 1
                await self._db.record_attempt(item_id, attempt_counter[0], str(exc))
                self._upload_retry_count += 1
                self._logger.warning(
                    "Transient upload error (attempt %d): %s",
                    attempt_counter[0],
                    exc,
                    extra={"camera_id": camera_id, "segment": segment_path.name},
                )
                raise  # let @with_retry handle the backoff + retry
            # S3PermanentError is NOT retryable → propagates immediately

        _attempt_with_retry = with_retry(
            max_retries=self._max_retries,
            retryable=(S3TransientError,),
        )(_attempt)

        try:
            s3_key: str = await _attempt_with_retry()
            await self._db.mark_uploaded(item_id, s3_key)
            self._upload_success_count += 1
            self._items_processed += 1
            latency = self._latency_seconds(created_at)
            self._upload_latency_seconds_last = latency
            self._logger.info(
                "Segment uploaded (confirmed)",
                extra={
                    "camera_id": camera_id,
                    "segment": segment_path.name,
                    "s3_key": s3_key,
                    "upload_latency_seconds": round(latency, 3),
                },
            )
        except S3PermanentError as exc:
            self._logger.error(
                "Permanent upload failure — not retrying: %s",
                exc,
                extra={"camera_id": camera_id, "segment": segment_path.name},
            )
            await self._db.mark_failed(item_id, f"permanent: {exc}")
            self._upload_error_count += 1
            self._upload_failed_count += 1
        except S3TransientError as exc:
            self._logger.error(
                "Upload retries exhausted (%d attempts): %s",
                attempt_counter[0],
                exc,
                extra={"camera_id": camera_id, "segment": segment_path.name},
            )
            await self._db.mark_failed(item_id, f"retries_exhausted: {exc}")
            self._upload_error_count += 1
            self._upload_failed_count += 1
        except S3UploadError as exc:
            self._logger.error(
                "Upload error: %s",
                exc,
                extra={"camera_id": camera_id, "segment": segment_path.name},
            )
            await self._db.mark_failed(item_id, str(exc))
            self._upload_error_count += 1

    @staticmethod
    def _latency_seconds(created_at: str) -> float:
        """Seconds elapsed from ``created_at`` to now (S3 confirmation)."""
        ts = _parse_iso(created_at)
        if ts is None:
            return 0.0
        return max(0.0, (datetime.now(tz=UTC) - ts).total_seconds())

    # ── Orphan scanning ──────────────────────────────────────────────────────────

    async def _scan_orphans(self) -> None:
        """Add any ``.ts`` files in ``buffer_dir`` that are not in the index."""
        if self._buffer_dir is None or not self._buffer_dir.exists():
            return

        def _find_ts() -> list[Path]:
            return sorted(self._buffer_dir.rglob("*.ts"))  # type: ignore[union-attr]

        ts_files = await asyncio.to_thread(_find_ts)
        if not ts_files:
            return

        known = await self._db.all_segment_paths()
        orphan_count = 0

        for ts_path in ts_files:
            if str(ts_path) in known:
                continue
            camera_id = ts_path.parent.name or "unknown"
            try:
                size = ts_path.stat().st_size
            except OSError:
                size = 0
            await self._db.add_segment(camera_id, ts_path, size, _utc_now())
            orphan_count += 1

        if orphan_count:
            self._logger.info(
                "Orphan scan: added %d untracked segment(s) as pending",
                orphan_count,
                extra={"buffer_dir": str(self._buffer_dir)},
            )
