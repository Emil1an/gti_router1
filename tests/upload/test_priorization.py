"""Tests for realtime/backlog 3:1 priority scheduling (Story 2.5).

Classification is derived from ``created_at`` age vs ``upload.backlog_age_threshold_s``
(default 60 s).  To make tests independent of the wall clock we use timestamps
far in the past (→ always ``backlog``) and far in the future (→ always
``realtime``).

The single worker processes items sequentially, so the order in which the mock
uploader is called equals the scheduler's selection order — that's what we assert.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from storage.db import SegmentDB
from upload.queue import UploadQueue
from upload.s3_client import S3Uploader

# Far past → backlog;  far future → realtime (relative to any real "now").
_PAST = "2000-01-01T00:00:0{n}.000Z"
_FUTURE = "2099-01-01T00:00:0{n}.000Z"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_db(tmp_path: Path) -> SegmentDB:
    db = SegmentDB(tmp_path / "queue.db")
    await db.open()
    return db


def _seg(tmp_path: Path, camera: str, name: str) -> Path:
    cam = tmp_path / camera
    cam.mkdir(parents=True, exist_ok=True)
    p = cam / name
    p.write_bytes(b"x")
    return p


def _recording_uploader(order: list[str]) -> S3Uploader:
    """Uploader mock that records the basename of each uploaded segment."""
    uploader = MagicMock(spec=S3Uploader)

    async def _side_effect(camera_id: str, path: Path) -> str:
        order.append(path.name)
        return f"key/{camera_id}/{path.name}"

    uploader.upload_segment = AsyncMock(side_effect=_side_effect)
    return uploader


async def _drain(queue: UploadQueue, expected: int, timeout_s: float = 5.0) -> None:
    """Run the worker until ``expected`` items are uploaded (or timeout)."""
    await queue.start()
    deadline = int(timeout_s / 0.02) + 1
    for _ in range(deadline):
        if queue.upload_success_count >= expected:
            break
        await asyncio.sleep(0.02)
    await queue.stop(drain_timeout_s=2.0)


# ── 3:1 ratio with both classes full ────────────────────────────────────────────

class TestRatio:
    async def test_three_to_one_pattern(self, tmp_path: Path) -> None:
        """With both classes full, the first 8 picks must follow 3 RT : 1 BL."""
        db = await _make_db(tmp_path)
        # 6 realtime (future) + 6 backlog (past), single camera.
        for i in range(6):
            p = _seg(tmp_path, "cam-a", f"rt_{i}.ts")
            await db.add_segment("cam-a", p, 1, _FUTURE.format(n=i))
        for i in range(6):
            p = _seg(tmp_path, "cam-a", f"bl_{i}.ts")
            await db.add_segment("cam-a", p, 1, _PAST.format(n=i))

        order: list[str] = []
        uploader = _recording_uploader(order)
        q = UploadQueue(uploader=uploader, db=db, max_retries=0)
        await _drain(q, expected=12)
        await db.close()

        assert len(order) == 12
        classes = ["rt" if n.startswith("rt_") else "bl" for n in order]
        # First cycle: RT RT RT BL, second cycle: RT RT RT BL → first 8 fixed.
        assert classes[:8] == ["rt", "rt", "rt", "bl", "rt", "rt", "rt", "bl"]
        # After realtime is exhausted, the rest are all backlog.
        assert classes[8:] == ["bl", "bl", "bl", "bl"]

    async def test_realtime_served_before_backlog(self, tmp_path: Path) -> None:
        """The very first segment uploaded must be realtime when both exist."""
        db = await _make_db(tmp_path)
        p_rt = _seg(tmp_path, "cam-a", "rt_0.ts")
        p_bl = _seg(tmp_path, "cam-a", "bl_0.ts")
        await db.add_segment("cam-a", p_rt, 1, _FUTURE.format(n=0))
        await db.add_segment("cam-a", p_bl, 1, _PAST.format(n=0))

        order: list[str] = []
        q = UploadQueue(uploader=_recording_uploader(order), db=db, max_retries=0)
        await _drain(q, expected=2)
        await db.close()

        assert order[0] == "rt_0.ts"


# ── Draining when one class is empty ────────────────────────────────────────────

class TestDrainNonEmpty:
    async def test_only_backlog_present(self, tmp_path: Path) -> None:
        """With no realtime items, the worker drains backlog without stalling."""
        db = await _make_db(tmp_path)
        for i in range(4):
            p = _seg(tmp_path, "cam-a", f"bl_{i}.ts")
            await db.add_segment("cam-a", p, 1, _PAST.format(n=i))

        order: list[str] = []
        q = UploadQueue(uploader=_recording_uploader(order), db=db, max_retries=0)
        await _drain(q, expected=4)
        await db.close()

        assert len(order) == 4
        assert all(n.startswith("bl_") for n in order)

    async def test_only_realtime_present(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        for i in range(4):
            p = _seg(tmp_path, "cam-a", f"rt_{i}.ts")
            await db.add_segment("cam-a", p, 1, _FUTURE.format(n=i))

        order: list[str] = []
        q = UploadQueue(uploader=_recording_uploader(order), db=db, max_retries=0)
        await _drain(q, expected=4)
        await db.close()

        assert len(order) == 4
        assert all(n.startswith("rt_") for n in order)


# ── Fair round-robin across cameras ─────────────────────────────────────────────

class TestMultiCameraFairness:
    async def test_two_cameras_share_fairly(self, tmp_path: Path) -> None:
        """Two cameras each with realtime items must be served round-robin."""
        db = await _make_db(tmp_path)
        for i in range(4):
            pa = _seg(tmp_path, "cam-a", f"rt_a{i}.ts")
            pb = _seg(tmp_path, "cam-b", f"rt_b{i}.ts")
            await db.add_segment("cam-a", pa, 1, _FUTURE.format(n=i))
            await db.add_segment("cam-b", pb, 1, _FUTURE.format(n=i))

        order: list[str] = []
        q = UploadQueue(uploader=_recording_uploader(order), db=db, max_retries=0)
        await _drain(q, expected=8)
        await db.close()

        cams = ["a" if "_a" in n else "b" for n in order]
        # Each camera served exactly 4 times — fair split.
        assert cams.count("a") == 4
        assert cams.count("b") == 4
        # And they alternate (round-robin), not all-A-then-all-B.
        assert cams[0] != cams[1]


# ── Metrics ────────────────────────────────────────────────────────────────────

class TestMetrics:
    async def test_queue_size_metrics(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        for i in range(3):
            p = _seg(tmp_path, "cam-a", f"rt_{i}.ts")
            await db.add_segment("cam-a", p, 1, _FUTURE.format(n=i))
        for i in range(2):
            p = _seg(tmp_path, "cam-a", f"bl_{i}.ts")
            await db.add_segment("cam-a", p, 1, _PAST.format(n=i))

        # Do not start the worker — inspect classification directly.
        uploader = MagicMock(spec=S3Uploader)
        q = UploadQueue(uploader=uploader, db=db, max_retries=0)

        assert await q.realtime_queue_size() == 3
        assert await q.backlog_queue_size() == 2
        await db.close()

    async def test_backlog_oldest_age_seconds(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _seg(tmp_path, "cam-a", "bl_0.ts")
        await db.add_segment("cam-a", p, 1, _PAST.format(n=0))  # year 2000

        uploader = MagicMock(spec=S3Uploader)
        q = UploadQueue(uploader=uploader, db=db, max_retries=0)
        age = await q.backlog_oldest_age_seconds()
        await db.close()

        # The 2000-01-01 segment is decades old → a very large positive number.
        assert age > 10 * 365 * 24 * 3600  # > 10 years in seconds

    async def test_backlog_age_zero_when_empty(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _seg(tmp_path, "cam-a", "rt_0.ts")
        await db.add_segment("cam-a", p, 1, _FUTURE.format(n=0))  # realtime only

        uploader = MagicMock(spec=S3Uploader)
        q = UploadQueue(uploader=uploader, db=db, max_retries=0)
        age = await q.backlog_oldest_age_seconds()
        await db.close()
        assert age == 0.0
