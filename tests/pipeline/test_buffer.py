"""Tests for BufferManager (Story 2.4).

The golden rule under test: the buffer NEVER deletes a segment that is not
confirmed ``uploaded`` in SQLite.  Disk usage is mocked via
``shutil.disk_usage`` so no real full disk is needed.
"""

from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.buffer import BufferManager
from storage.db import SegmentDB

_CREATED_BASE = "2026-06-08T10:00:0{n}.000Z"

_DiskUsage = namedtuple("DiskUsage", "total used free")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _usage(total: int, used: int) -> "_DiskUsage":
    """Build a shutil.disk_usage-like named tuple (total, used, free)."""
    return _DiskUsage(total, used, total - used)


async def _make_db(tmp_path: Path) -> SegmentDB:
    db = SegmentDB(tmp_path / "queue.db")
    await db.open()
    return db


def _write_segment(buffer_dir: Path, camera: str, name: str, size: int) -> Path:
    cam_dir = buffer_dir / camera
    cam_dir.mkdir(parents=True, exist_ok=True)
    p = cam_dir / name
    p.write_bytes(b"\x00" * size)
    return p


async def _add(
    db: SegmentDB, camera: str, path: Path, state: str, created_at: str, size: int
) -> int:
    """Insert a row and drive it to the requested state."""
    row_id = await db.add_segment(camera, path, size, created_at)
    if state == "pending":
        return row_id
    await db.mark_uploading(row_id)
    if state == "uploading":
        return row_id
    if state == "uploaded":
        await db.mark_uploaded(row_id, f"key/{path.name}")
    elif state == "failed":
        await db.mark_failed(row_id, "boom")
    return row_id


def _buffer(db: SegmentDB, buffer_dir: Path) -> BufferManager:
    return BufferManager(db=db, buffer_dir=buffer_dir, segment_duration_s=4)


# ── Disk introspection ──────────────────────────────────────────────────────────

class TestDiskIntrospection:
    async def test_usage_percent(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buf = _buffer(db, tmp_path / "hls")
        (tmp_path / "hls").mkdir()
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 800)):
            pct = await buf.usage_percent()
        await db.close()
        assert pct == pytest.approx(80.0)

    async def test_buffer_size_bytes(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"
        _write_segment(buffer_dir, "cam-a", "segment_00001.ts", 100)
        _write_segment(buffer_dir, "cam-a", "segment_00002.ts", 250)
        buf = _buffer(db, buffer_dir)
        size = await buf.buffer_size_bytes()
        await db.close()
        assert size == 350

    async def test_segment_count(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"
        _write_segment(buffer_dir, "cam-a", "s1.ts", 10)
        _write_segment(buffer_dir, "cam-b", "s2.ts", 10)
        buf = _buffer(db, buffer_dir)
        count = await buf.segment_count()
        await db.close()
        assert count == 2


# ── Retention (FR5: ≥4h) ────────────────────────────────────────────────────────

class TestRetention:
    async def test_retention_target_is_configured(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buf = _buffer(db, tmp_path / "hls")
        await db.close()
        assert buf.retention_target_hours >= 4

    async def test_estimated_retention_hours_meets_4h(self, tmp_path: Path) -> None:
        """With ample free space and small segments, retention must exceed 4h."""
        db = await _make_db(tmp_path)
        (tmp_path / "hls").mkdir()
        buf = _buffer(db, tmp_path / "hls")
        # 10 GB free, 1 MB avg segment, 4s each →
        # (10e9 / 1e6) * 4 / 3600 ≈ 11.1h  ⇒  ≥ 4h
        with patch(
            "pipeline.buffer.shutil.disk_usage",
            return_value=_usage(20_000_000_000, 10_000_000_000),
        ):
            hours = await buf.estimated_retention_hours(avg_segment_bytes=1_000_000)
            meets = await buf.meets_retention_target(avg_segment_bytes=1_000_000)
        await db.close()
        assert hours >= 4.0
        assert meets is True

    async def test_estimated_retention_below_target(self, tmp_path: Path) -> None:
        """Tiny free space must report retention under the 4h target."""
        db = await _make_db(tmp_path)
        (tmp_path / "hls").mkdir()
        buf = _buffer(db, tmp_path / "hls")
        with patch(
            "pipeline.buffer.shutil.disk_usage",
            return_value=_usage(20_000_000_000, 19_999_000_000),  # ~1 MB free
        ):
            meets = await buf.meets_retention_target(avg_segment_bytes=1_000_000)
        await db.close()
        assert meets is False


# ── Alert at 80% ────────────────────────────────────────────────────────────────

class TestAlert:
    async def test_alert_raised_at_threshold(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        (tmp_path / "hls").mkdir()
        buf = _buffer(db, tmp_path / "hls")
        assert buf.alert_active is False
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 800)):
            result = await buf.enforce()
        await db.close()
        assert result["alert_active"] is True
        assert buf.alert_active is True

    async def test_no_alert_below_threshold(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        (tmp_path / "hls").mkdir()
        buf = _buffer(db, tmp_path / "hls")
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 500)):
            result = await buf.enforce()
        await db.close()
        assert result["alert_active"] is False
        assert buf.alert_active is False


# ── FIFO deletes only uploaded, oldest-first ───────────────────────────────────

class TestFifoCleanup:
    async def test_deletes_only_uploaded_oldest_first(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"

        # Three uploaded segments of different ages (oldest = ...01).
        p1 = _write_segment(buffer_dir, "cam-a", "segment_00001.ts", 300)
        p2 = _write_segment(buffer_dir, "cam-a", "segment_00002.ts", 300)
        p3 = _write_segment(buffer_dir, "cam-a", "segment_00003.ts", 300)
        await _add(db, "cam-a", p1, "uploaded", _CREATED_BASE.format(n=1), 300)
        await _add(db, "cam-a", p2, "uploaded", _CREATED_BASE.format(n=2), 300)
        await _add(db, "cam-a", p3, "uploaded", _CREATED_BASE.format(n=3), 300)

        buf = _buffer(db, buffer_dir)
        # total 1000, used 900 (90% > cleanup 85%); target = 80% → 800 used.
        # Need to free ≥100 bytes → delete the single oldest (300) suffices.
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 900)):
            result = await buf.enforce()

        counts = await db.counts()
        await db.close()

        assert result["deleted_count"] == 1
        assert not p1.exists()          # oldest deleted
        assert p2.exists() and p3.exists()
        assert counts["uploaded"] == 2  # one row reconciled away

    async def test_never_deletes_non_uploaded(self, tmp_path: Path) -> None:
        """pending / uploading / failed segments must survive even when disk full."""
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"

        p_pending = _write_segment(buffer_dir, "cam-a", "segment_00001.ts", 300)
        p_uploading = _write_segment(buffer_dir, "cam-a", "segment_00002.ts", 300)
        p_failed = _write_segment(buffer_dir, "cam-a", "segment_00003.ts", 300)
        await _add(db, "cam-a", p_pending, "pending", _CREATED_BASE.format(n=1), 300)
        await _add(db, "cam-a", p_uploading, "uploading", _CREATED_BASE.format(n=2), 300)
        await _add(db, "cam-a", p_failed, "failed", _CREATED_BASE.format(n=3), 300)

        buf = _buffer(db, buffer_dir)
        # Disk is 99% full but NOTHING is uploaded → must delete nothing.
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 990)):
            result = await buf.enforce()

        counts = await db.counts()
        await db.close()

        assert result["deleted_count"] == 0
        assert result["backpressure"] is True
        assert p_pending.exists()
        assert p_uploading.exists()
        assert p_failed.exists()
        assert counts["pending"] == 1
        assert counts["uploading"] == 1
        assert counts["failed"] == 1

    async def test_mixed_states_only_uploaded_recycled(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"

        p_up1 = _write_segment(buffer_dir, "cam-a", "segment_00001.ts", 400)
        p_pending = _write_segment(buffer_dir, "cam-a", "segment_00002.ts", 400)
        p_up2 = _write_segment(buffer_dir, "cam-a", "segment_00003.ts", 400)
        await _add(db, "cam-a", p_up1, "uploaded", _CREATED_BASE.format(n=1), 400)
        await _add(db, "cam-a", p_pending, "pending", _CREATED_BASE.format(n=2), 400)
        await _add(db, "cam-a", p_up2, "uploaded", _CREATED_BASE.format(n=3), 400)

        buf = _buffer(db, buffer_dir)
        # 95% used; free down to 80%. Delete uploaded oldest-first until satisfied.
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 950)):
            result = await buf.enforce()

        await db.close()
        # pending must never be touched
        assert p_pending.exists()
        # at least the oldest uploaded recycled
        assert not p_up1.exists()
        assert result["deleted_count"] >= 1

    async def test_no_cleanup_when_below_cleanup_threshold(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"
        p1 = _write_segment(buffer_dir, "cam-a", "segment_00001.ts", 300)
        await _add(db, "cam-a", p1, "uploaded", _CREATED_BASE.format(n=1), 300)

        buf = _buffer(db, buffer_dir)
        # 70% used < cleanup 85% → nothing deleted even though uploaded exists.
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 700)):
            result = await buf.enforce()
        await db.close()
        assert result["deleted_count"] == 0
        assert p1.exists()


# ── File ↔ index consistency ────────────────────────────────────────────────────

class TestConsistency:
    async def test_row_removed_when_file_deleted(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        buffer_dir = tmp_path / "hls"
        p1 = _write_segment(buffer_dir, "cam-a", "segment_00001.ts", 500)
        row_id = await _add(db, "cam-a", p1, "uploaded", _CREATED_BASE.format(n=1), 500)

        buf = _buffer(db, buffer_dir)
        with patch("pipeline.buffer.shutil.disk_usage", return_value=_usage(1000, 950)):
            await buf.enforce()

        # The row must be gone (reconciled) and so must the file.
        remaining = await db.all_segment_paths()
        await db.close()
        assert str(p1) not in remaining
        assert not p1.exists()
        assert row_id  # sanity: we did insert something
