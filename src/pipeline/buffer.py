"""Local buffer and disk-space management (Story 2.4).

The ``BufferManager`` owns the *files* on disk; the SQLite index
(:class:`~storage.db.SegmentDB`, Story 2.2) owns the *state*.  These are two
separate, consistent boundaries: the buffer asks the index which segments are
safely ``uploaded`` and only ever deletes those.

The golden rule
---------------
**A segment that is not confirmed ``uploaded`` in SQLite is NEVER deleted.**
If the disk fills with ``pending`` / ``uploading`` / ``failed`` segments, the
manager raises an alert (and applies back-pressure) rather than lose unsent
video (FR5 — buffer ≥4 h).

FIFO recycling
--------------
When disk usage crosses ``buffer.cleanup_threshold_percent`` the manager deletes
already-uploaded segments oldest-first (true FIFO) until usage drops back to the
``buffer.alert_threshold_percent`` water-mark or no uploaded segments remain.

Alerting
--------
When usage crosses ``buffer.alert_threshold_percent`` an ``alert_active`` flag is
set (a WARNING is logged with ``camera_id`` context).  The Health Reporter
(Epic 3) reads this flag.

All disk I/O is offloaded with ``asyncio.to_thread`` so the event loop is never
blocked.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from config.loader import get_config
from storage.db import SegmentDB
from utils.errors import RouterError
from utils.logging import get_logger


class BufferError(RouterError):
    """Raised for unrecoverable buffer / disk-management problems."""


class BufferManager:
    """Per-device buffer that recycles only already-uploaded segments.

    Args:
        db:           the shared :class:`~storage.db.SegmentDB` index.
        buffer_dir:   root directory holding per-camera ``.ts`` segments.
                      Defaults to ``config.hls.output_dir``.
        segment_duration_s: HLS segment length in seconds (defaults to
                      ``config.hls.segment_duration``).  Used for retention math.
    """

    def __init__(
        self,
        db: SegmentDB,
        buffer_dir: Path | None = None,
        segment_duration_s: int | None = None,
    ) -> None:
        cfg = get_config()
        self._db = db
        self._base_dir: Path = Path(buffer_dir) if buffer_dir else Path(cfg.hls.output_dir)
        self._segment_duration_s: int = (
            segment_duration_s if segment_duration_s is not None
            else cfg.hls.segment_duration
        )

        self._retention_hours: int = cfg.buffer.retention_hours
        self._alert_threshold: float = cfg.buffer.alert_threshold_percent
        self._cleanup_threshold: float = cfg.buffer.cleanup_threshold_percent

        self._alert_active: bool = False
        self._logger = get_logger(__name__)

    # ── Public state (read by the Health Reporter in Epic 3) ────────────────────

    @property
    def alert_active(self) -> bool:
        """``True`` when disk usage is at/over ``alert_threshold_percent``."""
        return self._alert_active

    @property
    def retention_target_hours(self) -> int:
        """Configured minimum retention window (hours)."""
        return self._retention_hours

    # ── Disk / size introspection ───────────────────────────────────────────────

    def _disk_usage(self) -> tuple[int, int, int]:
        """Return ``(total, used, free)`` bytes for the buffer filesystem.

        Falls back to the parent directory if ``_base_dir`` does not exist yet.
        """
        target = self._base_dir if self._base_dir.exists() else self._base_dir.parent
        usage = shutil.disk_usage(str(target))
        return usage.total, usage.used, usage.free

    async def disk_usage(self) -> tuple[int, int, int]:
        """Async wrapper around :func:`shutil.disk_usage`."""
        return await asyncio.to_thread(self._disk_usage)

    async def usage_percent(self) -> float:
        """Return current filesystem usage as a percentage (0–100)."""
        total, used, _free = await self.disk_usage()
        if total <= 0:
            return 0.0
        return used / total * 100.0

    def _scan_buffer(self) -> list[tuple[Path, int]]:
        """Return ``(path, size_bytes)`` for every ``.ts`` file under the buffer."""
        if not self._base_dir.exists():
            return []
        out: list[tuple[Path, int]] = []
        for p in self._base_dir.rglob("*.ts"):
            try:
                out.append((p, p.stat().st_size))
            except OSError:
                continue
        return out

    async def buffer_size_bytes(self) -> int:
        """Total bytes occupied by ``.ts`` segments in the buffer."""
        files = await asyncio.to_thread(self._scan_buffer)
        return sum(size for _p, size in files)

    async def segment_count(self) -> int:
        """Number of ``.ts`` segments currently buffered on disk."""
        files = await asyncio.to_thread(self._scan_buffer)
        return len(files)

    async def average_segment_bytes(self) -> float | None:
        """Average ``.ts`` size in bytes, or ``None`` if the buffer is empty."""
        files = await asyncio.to_thread(self._scan_buffer)
        if not files:
            return None
        return sum(size for _p, size in files) / len(files)

    # ── Retention math (FR5: ≥4 h) ──────────────────────────────────────────────

    async def estimated_retention_hours(
        self, avg_segment_bytes: float | None = None
    ) -> float:
        """Estimate how many more hours of video can be buffered in free space.

        ``retention = (free_bytes / avg_segment_bytes) * segment_duration_s / 3600``

        Args:
            avg_segment_bytes: override for the average segment size.  When
                ``None`` the value is derived from segments already on disk;
                if the buffer is empty this returns ``inf`` (unknown/unbounded).
        """
        _total, _used, free = await self.disk_usage()
        if avg_segment_bytes is None:
            avg_segment_bytes = await self.average_segment_bytes()
        if not avg_segment_bytes or avg_segment_bytes <= 0:
            return float("inf")
        segments_that_fit = free / avg_segment_bytes
        seconds = segments_that_fit * self._segment_duration_s
        return seconds / 3600.0

    async def meets_retention_target(
        self, avg_segment_bytes: float | None = None
    ) -> bool:
        """``True`` if free space can hold at least ``retention_target_hours``."""
        return await self.estimated_retention_hours(avg_segment_bytes) >= self._retention_hours

    # ── FIFO cleanup (only uploaded segments) ───────────────────────────────────

    async def enforce(self) -> dict[str, object]:
        """Evaluate disk usage and recycle uploaded segments if necessary.

        Returns a summary dict::

            {
              "used_percent": float,
              "alert_active": bool,
              "deleted_count": int,
              "freed_bytes": int,
              "backpressure": bool,   # disk over cleanup but nothing deletable
            }

        Only ``uploaded`` segments are ever deleted, oldest-first, until usage
        drops to ``alert_threshold_percent`` or no uploaded segments remain.
        """
        total, used, _free = await self.disk_usage()
        used_percent = (used / total * 100.0) if total > 0 else 0.0

        # ── Alert flag (read by health report) ──────────────────────────────────
        self._alert_active = used_percent >= self._alert_threshold
        if self._alert_active:
            self._logger.warning(
                "Buffer usage high",
                extra={
                    "used_percent": round(used_percent, 1),
                    "alert_threshold_percent": self._alert_threshold,
                },
            )

        deleted_count = 0
        freed_bytes = 0
        backpressure = False

        if used_percent >= self._cleanup_threshold:
            # Free down to the alert water-mark.
            target_used = (self._alert_threshold / 100.0) * total
            candidates = await self._db.oldest_uploaded()

            if not candidates and used > target_used:
                # Disk is over the cleanup threshold but nothing is safely
                # deletable (everything is pending/uploading/failed).  We must
                # NOT delete unsent video — raise back-pressure instead.
                backpressure = True
                self._logger.warning(
                    "Buffer over cleanup threshold but no uploaded segments to "
                    "recycle — applying back-pressure (never deleting unsent video)",
                    extra={"used_percent": round(used_percent, 1)},
                )

            for row in candidates:
                if used <= target_used:
                    break
                seg_path = Path(row["segment_path"])
                size = int(row["size_bytes"] or 0)
                deleted = await self._delete_segment(seg_path, int(row["id"]))
                if deleted:
                    used -= size
                    freed_bytes += size
                    deleted_count += 1

            if deleted_count:
                self._logger.info(
                    "Buffer FIFO cleanup freed space",
                    extra={
                        "deleted_count": deleted_count,
                        "freed_bytes": freed_bytes,
                    },
                )

        return {
            "used_percent": used_percent,
            "alert_active": self._alert_active,
            "deleted_count": deleted_count,
            "freed_bytes": freed_bytes,
            "backpressure": backpressure,
        }

    async def _delete_segment(self, seg_path: Path, item_id: int) -> bool:
        """Delete one uploaded segment file and reconcile its index row.

        The index row is removed **only** via
        :meth:`~storage.db.SegmentDB.delete_uploaded`, which itself guards on
        ``state='uploaded'`` — a second layer of protection against evicting
        unsent video.
        """
        # Reconcile the index first; its state guard is authoritative.
        removed = await self._db.delete_uploaded(item_id)
        if not removed:
            # Row was not 'uploaded' (race / state changed) — do NOT touch file.
            self._logger.debug(
                "Skipped buffer delete — row not in 'uploaded' state",
                extra={"segment": seg_path.name, "id": item_id},
            )
            return False

        def _unlink() -> None:
            try:
                seg_path.unlink(missing_ok=True)
            except OSError as exc:
                raise BufferError(
                    f"Failed to delete buffered segment '{seg_path}': {exc}"
                ) from exc

        try:
            await asyncio.to_thread(_unlink)
        except BufferError as exc:
            # File deletion failed after the row was removed — log and continue.
            self._logger.error("%s", exc, extra={"segment": seg_path.name})
            return False

        self._logger.debug(
            "Recycled uploaded segment",
            extra={"segment": seg_path.name, "id": item_id},
        )
        return True
