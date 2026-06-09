"""Tests for SegmentDB (Story 2.2).

All tests use a temporary SQLite file (tmp_path) and do NOT require any
external services.  The autouse _storage_config fixture from conftest.py
ensures get_config() works in every test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storage.db import SegmentDB

# ── Helpers ────────────────────────────────────────────────────────────────────

_CREATED_AT = "2026-06-08T10:00:00.000Z"


async def _make_db(tmp_path: Path) -> SegmentDB:
    db = SegmentDB(tmp_path / "queue.db")
    await db.open()
    return db


def _fake_path(tmp_path: Path, name: str = "segment_00001.ts") -> Path:
    p = tmp_path / "cam-test" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake")
    return p


# ── Open / close ───────────────────────────────────────────────────────────────

class TestOpenClose:
    async def test_open_creates_schema(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        counts = await db.counts()
        await db.close()
        assert counts == {"pending": 0, "uploading": 0, "uploaded": 0, "failed": 0}

    async def test_close_is_idempotent(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        await db.close()
        await db.close()  # must not raise


# ── add_segment / idempotency ──────────────────────────────────────────────────

class TestAddSegment:
    async def test_insert_returns_id(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.close()
        assert isinstance(row_id, int)
        assert row_id > 0

    async def test_initial_state_is_pending(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        await db.add_segment("cam-test", p, 4, _CREATED_AT)
        counts = await db.counts()
        await db.close()
        assert counts["pending"] == 1

    async def test_duplicate_path_is_idempotent(self, tmp_path: Path) -> None:
        """Inserting the same segment_path twice must not create a duplicate row."""
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        id1 = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        id2 = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        counts = await db.counts()
        await db.close()
        assert id1 == id2
        assert counts["pending"] == 1

    async def test_multiple_different_segments(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        for i in range(3):
            p = _fake_path(tmp_path, f"segment_{i:05d}.ts")
            await db.add_segment("cam-test", p, i * 100, _CREATED_AT)
        counts = await db.counts()
        await db.close()
        assert counts["pending"] == 3


# ── State transitions ──────────────────────────────────────────────────────────

class TestStateTransitions:
    async def test_pending_to_uploading(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        result = await db.mark_uploading(row_id)
        counts = await db.counts()
        await db.close()
        assert result is True
        assert counts["uploading"] == 1
        assert counts["pending"] == 0

    async def test_mark_uploading_non_pending_returns_false(
        self, tmp_path: Path
    ) -> None:
        """mark_uploading on a non-pending row must return False."""
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.mark_uploading(row_id)
        # Second call: row is already uploading
        result = await db.mark_uploading(row_id)
        await db.close()
        assert result is False

    async def test_uploading_to_uploaded(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.mark_uploading(row_id)
        await db.mark_uploaded(row_id, "user/router/cam-test/segment_00001.ts")
        counts = await db.counts()
        await db.close()
        assert counts["uploaded"] == 1
        assert counts["uploading"] == 0

    async def test_uploading_to_failed(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.mark_uploading(row_id)
        await db.mark_failed(row_id, "timeout")
        counts = await db.counts()
        await db.close()
        assert counts["failed"] == 1
        assert counts["uploading"] == 0


# ── record_attempt ─────────────────────────────────────────────────────────────

class TestRecordAttempt:
    async def test_record_attempt_updates_counter_and_error(
        self, tmp_path: Path
    ) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.record_attempt(row_id, 2, "connection reset")
        # Verify via next_pending (attempts are returned)
        item = await db.next_pending()
        await db.close()
        assert item is not None
        assert item["attempts"] == 2


# ── reset_uploading_to_pending ─────────────────────────────────────────────────

class TestCrashRecovery:
    async def test_uploading_rows_are_reset_to_pending(
        self, tmp_path: Path
    ) -> None:
        """Simulate a crash: rows stuck in 'uploading' must reset to 'pending'."""
        db = await _make_db(tmp_path)
        p1 = _fake_path(tmp_path, "segment_00001.ts")
        p2 = _fake_path(tmp_path, "segment_00002.ts")

        id1 = await db.add_segment("cam-test", p1, 4, _CREATED_AT)
        id2 = await db.add_segment("cam-test", p2, 4, _CREATED_AT)
        await db.mark_uploading(id1)
        await db.mark_uploading(id2)

        reset_count = await db.reset_uploading_to_pending()
        counts = await db.counts()
        await db.close()

        assert reset_count == 2
        assert counts["pending"] == 2
        assert counts["uploading"] == 0

    async def test_uploaded_rows_are_not_reset(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.mark_uploading(row_id)
        await db.mark_uploaded(row_id, "some/key.ts")

        reset_count = await db.reset_uploading_to_pending()
        counts = await db.counts()
        await db.close()

        assert reset_count == 0
        assert counts["uploaded"] == 1


# ── next_pending ───────────────────────────────────────────────────────────────

class TestNextPending:
    async def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        result = await db.next_pending()
        await db.close()
        assert result is None

    async def test_returns_oldest_pending(self, tmp_path: Path) -> None:
        """FIFO: next_pending must return the item with the smallest id."""
        db = await _make_db(tmp_path)
        p1 = _fake_path(tmp_path, "seg_1.ts")
        p2 = _fake_path(tmp_path, "seg_2.ts")
        id1 = await db.add_segment("cam-test", p1, 4, _CREATED_AT)
        await db.add_segment("cam-test", p2, 4, _CREATED_AT)

        item = await db.next_pending()
        await db.close()

        assert item is not None
        assert item["id"] == id1

    async def test_does_not_return_uploading_rows(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p = _fake_path(tmp_path)
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.mark_uploading(row_id)

        item = await db.next_pending()
        await db.close()

        assert item is None


# ── counts ─────────────────────────────────────────────────────────────────────

class TestCounts:
    async def test_all_states_present_in_counts(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        counts = await db.counts()
        await db.close()
        assert set(counts.keys()) == {"pending", "uploading", "uploaded", "failed"}

    async def test_counts_reflect_mixed_states(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)

        p1 = _fake_path(tmp_path, "s1.ts")
        p2 = _fake_path(tmp_path, "s2.ts")
        p3 = _fake_path(tmp_path, "s3.ts")

        id1 = await db.add_segment("c", p1, 1, _CREATED_AT)
        id2 = await db.add_segment("c", p2, 1, _CREATED_AT)
        id3 = await db.add_segment("c", p3, 1, _CREATED_AT)

        await db.mark_uploading(id1)
        await db.mark_uploaded(id1, "k1")
        await db.mark_uploading(id2)
        await db.mark_failed(id2, "err")
        # id3 stays pending

        counts = await db.counts()
        await db.close()

        assert counts["uploaded"] == 1
        assert counts["failed"] == 1
        assert counts["pending"] == 1
        assert counts["uploading"] == 0


# ── all_segment_paths ──────────────────────────────────────────────────────────

class TestAllSegmentPaths:
    async def test_returns_all_paths(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        p1 = _fake_path(tmp_path, "s1.ts")
        p2 = _fake_path(tmp_path, "s2.ts")
        await db.add_segment("c", p1, 1, _CREATED_AT)
        await db.add_segment("c", p2, 1, _CREATED_AT)

        paths = await db.all_segment_paths()
        await db.close()

        assert str(p1) in paths
        assert str(p2) in paths


# ── Persistence across re-open ─────────────────────────────────────────────────

class TestPersistence:
    async def test_data_survives_close_and_reopen(self, tmp_path: Path) -> None:
        """SQLite data must persist across close/open (simulates a service restart)."""
        db_path = tmp_path / "queue.db"
        p = _fake_path(tmp_path)

        # First session: insert a segment
        db = SegmentDB(db_path)
        await db.open()
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.close()

        # Second session: data must still be there
        db2 = SegmentDB(db_path)
        await db2.open()
        item = await db2.next_pending()
        await db2.close()

        assert item is not None
        assert item["id"] == row_id
        assert item["camera_id"] == "cam-test"

    async def test_uploaded_state_persists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "queue.db"
        p = _fake_path(tmp_path)

        db = SegmentDB(db_path)
        await db.open()
        row_id = await db.add_segment("cam-test", p, 4, _CREATED_AT)
        await db.mark_uploading(row_id)
        await db.mark_uploaded(row_id, "some/key.ts")
        await db.close()

        db2 = SegmentDB(db_path)
        await db2.open()
        counts = await db2.counts()
        await db2.close()

        assert counts["uploaded"] == 1
        assert counts["pending"] == 0
