"""SQLite-backed index for the HLS segment upload queue (Story 2.2).

This is the **single authorised source of upload-state persistence** in the
project.  All other modules must use this API — never write to or read from
the SQLite file directly.

Design notes
------------
* ``asyncio.to_thread`` is used for every SQL I/O call so the event loop is
  never blocked (stdlib ``sqlite3`` is synchronous).
* An ``asyncio.Lock`` serialises all callers through a single
  ``check_same_thread=False`` connection, eliminating concurrent-write issues
  while keeping connection overhead near zero.
* WAL journal + ``synchronous=NORMAL`` provides crash-safe durability without
  the full-sync overhead of ``synchronous=FULL``.

Schema
------
``upload_queue`` table columns:

+---------------+---------+----------------------------------------------------+
| column        | type    | notes                                              |
+===============+=========+====================================================+
| id            | INTEGER | PK AUTOINCREMENT                                   |
| camera_id     | TEXT    | owning camera                                      |
| segment_path  | TEXT    | absolute local path (UNIQUE)                       |
| s3_key        | TEXT    | set on successful upload                           |
| state         | TEXT    | pending → uploading → uploaded | failed            |
| size_bytes    | INTEGER | local file size at enqueue time                    |
| created_at    | TEXT    | ISO-8601 UTC timestamp from the pipeline callback  |
| enqueued_at   | TEXT    | ISO-8601 UTC timestamp when row was inserted       |
| uploaded_at   | TEXT    | ISO-8601 UTC timestamp when upload succeeded       |
| attempts      | INTEGER | total upload attempts made                         |
| last_error    | TEXT    | last error message (any state)                     |
+---------------+---------+----------------------------------------------------+
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Schema DDL ─────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS upload_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id     TEXT    NOT NULL,
    segment_path  TEXT    NOT NULL,
    s3_key        TEXT,
    state         TEXT    NOT NULL DEFAULT 'pending',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    enqueued_at   TEXT    NOT NULL,
    uploaded_at   TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    UNIQUE(segment_path),
    CHECK(state IN ('pending','uploading','uploaded','failed'))
);
CREATE INDEX IF NOT EXISTS idx_upload_queue_state  ON upload_queue(state);
CREATE INDEX IF NOT EXISTS idx_upload_queue_camera ON upload_queue(camera_id);
"""


def _utc_now() -> str:
    """Return current UTC time as ISO-8601 with trailing Z."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ── SegmentDB ──────────────────────────────────────────────────────────────────

class SegmentDB:
    """Transactional SQLite index for upload queue state.

    Example usage::

        db = SegmentDB(Path("/var/lib/gti-router/queue.db"))
        await db.open()

        row_id = await db.add_segment("cam-1", path, size, created_at)
        await db.mark_uploading(row_id)
        await db.mark_uploaded(row_id, s3_key)

        counts = await db.counts()   # {"pending": 0, "uploading": 0, ...}
        await db.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def open(self) -> None:
        """Open (and initialise) the SQLite database.

        Creates the schema if it does not yet exist and applies WAL + normal
        synchronous settings for crash-safe durability.
        """
        def _open() -> sqlite3.Connection:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            for stmt in _SCHEMA_SQL.split(";"):
                stripped = stmt.strip()
                if stripped:
                    conn.execute(stripped)
            conn.commit()
            return conn

        async with self._lock:
            self._conn = await asyncio.to_thread(_open)

    async def close(self) -> None:
        """Close the database connection."""
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    # ── Write operations ───────────────────────────────────────────────────────

    async def add_segment(
        self,
        camera_id: str,
        segment_path: Path,
        size_bytes: int,
        created_at: str,
    ) -> int:
        """Insert a new segment as ``pending``.

        Idempotent: if ``segment_path`` already exists, the existing row's id is
        returned without creating a duplicate (UNIQUE constraint / ON CONFLICT DO
        NOTHING).

        Returns:
            The id of the inserted or pre-existing row.
        """
        now = _utc_now()

        def _run() -> int:
            assert self._conn is not None
            cursor = self._conn.execute(
                """
                INSERT INTO upload_queue
                    (camera_id, segment_path, size_bytes, created_at, enqueued_at, state)
                VALUES (?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(segment_path) DO NOTHING
                """,
                (camera_id, str(segment_path), size_bytes, created_at, now),
            )
            self._conn.commit()
            if cursor.rowcount and cursor.lastrowid:
                return cursor.lastrowid
            # Row already existed — fetch its id
            row = self._conn.execute(
                "SELECT id FROM upload_queue WHERE segment_path = ?",
                (str(segment_path),),
            ).fetchone()
            return int(row["id"])

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def mark_uploading(self, item_id: int) -> bool:
        """Transition state ``pending`` → ``uploading``.

        Returns:
            ``True`` if the row was updated; ``False`` if it was already in
            another state (e.g. a concurrent worker already claimed it).
        """
        def _run() -> bool:
            assert self._conn is not None
            cursor = self._conn.execute(
                "UPDATE upload_queue SET state='uploading' WHERE id=? AND state='pending'",
                (item_id,),
            )
            self._conn.commit()
            return cursor.rowcount == 1

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def mark_uploaded(self, item_id: int, s3_key: str) -> None:
        """Transition state to ``uploaded``, recording ``s3_key`` and ``uploaded_at``."""
        now = _utc_now()

        def _run() -> None:
            assert self._conn is not None
            self._conn.execute(
                """
                UPDATE upload_queue
                SET state='uploaded', s3_key=?, uploaded_at=?
                WHERE id=?
                """,
                (s3_key, now, item_id),
            )
            self._conn.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def mark_failed(self, item_id: int, error: str) -> None:
        """Transition state to ``failed``, recording the error message."""
        def _run() -> None:
            assert self._conn is not None
            self._conn.execute(
                "UPDATE upload_queue SET state='failed', last_error=? WHERE id=?",
                (error, item_id),
            )
            self._conn.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def record_attempt(
        self, item_id: int, attempts: int, last_error: str
    ) -> None:
        """Update ``attempts`` count and ``last_error`` for an in-progress item."""
        def _run() -> None:
            assert self._conn is not None
            self._conn.execute(
                "UPDATE upload_queue SET attempts=?, last_error=? WHERE id=?",
                (attempts, last_error, item_id),
            )
            self._conn.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def reset_uploading_to_pending(self) -> int:
        """Reset all ``uploading`` rows back to ``pending`` (crash recovery).

        Call this at service startup to recover items that were in-flight when
        the process was interrupted.

        Returns:
            Number of rows reset.
        """
        def _run() -> int:
            assert self._conn is not None
            cursor = self._conn.execute(
                "UPDATE upload_queue SET state='pending' WHERE state='uploading'"
            )
            self._conn.commit()
            return cursor.rowcount

        async with self._lock:
            return await asyncio.to_thread(_run)

    # ── Read operations ────────────────────────────────────────────────────────

    async def next_pending(self) -> dict[str, Any] | None:
        """Return the oldest ``pending`` item as a dict, or ``None`` if empty."""
        def _run() -> dict[str, Any] | None:
            assert self._conn is not None
            row = self._conn.execute(
                """
                SELECT id, camera_id, segment_path, state, size_bytes,
                       created_at, attempts
                FROM upload_queue
                WHERE state = 'pending'
                ORDER BY id ASC
                LIMIT 1
                """,
            ).fetchone()
            return dict(row) if row is not None else None

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def counts(self) -> dict[str, int]:
        """Return item counts grouped by state.

        Returns a dict with keys ``pending``, ``uploading``, ``uploaded``,
        ``failed`` (all present, defaulting to 0).
        """
        def _run() -> dict[str, int]:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS cnt FROM upload_queue GROUP BY state"
            ).fetchall()
            result: dict[str, int] = {
                "pending": 0, "uploading": 0, "uploaded": 0, "failed": 0
            }
            for row in rows:
                result[row["state"]] = row["cnt"]
            return result

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def all_segment_paths(self) -> set[str]:
        """Return all ``segment_path`` values in the index.

        Used by :class:`~upload.queue.UploadQueue` at startup to detect orphan
        ``.ts`` files on disk that are not yet tracked.
        """
        def _run() -> set[str]:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT segment_path FROM upload_queue"
            ).fetchall()
            return {row["segment_path"] for row in rows}

        async with self._lock:
            return await asyncio.to_thread(_run)

    # ── Buffer FIFO support (Story 2.4) ─────────────────────────────────────────

    async def oldest_uploaded(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return ``uploaded`` rows ordered oldest-first (FIFO delete candidates).

        Only rows whose ``state`` is ``uploaded`` are returned — these are the
        **only** segments the buffer is ever allowed to delete (FR5).  Ordering
        is by ``created_at`` then ``id`` so the oldest segment is recycled first.
        """
        def _run() -> list[dict[str, Any]]:
            assert self._conn is not None
            sql = (
                "SELECT id, camera_id, segment_path, size_bytes, created_at "
                "FROM upload_queue WHERE state='uploaded' "
                "ORDER BY created_at ASC, id ASC"
            )
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            rows = self._conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def delete_uploaded(self, item_id: int) -> bool:
        """Delete an index row, but **only** if its state is ``uploaded``.

        This is the index-side reconciliation that accompanies a buffer file
        deletion.  The ``state='uploaded'`` guard makes it impossible to evict
        a ``pending`` / ``uploading`` / ``failed`` segment by mistake.

        Returns:
            ``True`` if a row was deleted; ``False`` otherwise.
        """
        def _run() -> bool:
            assert self._conn is not None
            cursor = self._conn.execute(
                "DELETE FROM upload_queue WHERE id=? AND state='uploaded'",
                (item_id,),
            )
            self._conn.commit()
            return cursor.rowcount == 1

        async with self._lock:
            return await asyncio.to_thread(_run)

    # ── Priority classification support (Story 2.5) ─────────────────────────────

    async def pending_cameras_for_class(
        self, klass: str, cutoff_iso: str
    ) -> list[str]:
        """Return camera_ids that have ``pending`` items of the given class.

        ``klass`` is ``"realtime"`` (``created_at >= cutoff_iso``) or
        ``"backlog"`` (``created_at < cutoff_iso``).  Cameras are returned in a
        **stable** order (by ``camera_id``) so the worker's round-robin pointer
        rotates fairly and deterministically even as items drain.
        """
        op = ">=" if klass == "realtime" else "<"

        def _run() -> list[str]:
            assert self._conn is not None
            rows = self._conn.execute(
                f"""
                SELECT DISTINCT camera_id
                FROM upload_queue
                WHERE state='pending' AND created_at {op} ?
                ORDER BY camera_id ASC
                """,
                (cutoff_iso,),
            ).fetchall()
            return [r["camera_id"] for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def next_pending_for_camera_class(
        self, camera_id: str, klass: str, cutoff_iso: str
    ) -> dict[str, Any] | None:
        """Return the oldest ``pending`` item for one camera within a class."""
        op = ">=" if klass == "realtime" else "<"

        def _run() -> dict[str, Any] | None:
            assert self._conn is not None
            row = self._conn.execute(
                f"""
                SELECT id, camera_id, segment_path, state, size_bytes,
                       created_at, attempts
                FROM upload_queue
                WHERE state='pending' AND camera_id=? AND created_at {op} ?
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (camera_id, cutoff_iso),
            ).fetchone()
            return dict(row) if row is not None else None

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def class_counts(self, cutoff_iso: str) -> dict[str, int]:
        """Return pending counts split into ``realtime`` and ``backlog``."""
        def _run() -> dict[str, int]:
            assert self._conn is not None
            row = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS realtime,
                    SUM(CASE WHEN created_at <  ? THEN 1 ELSE 0 END) AS backlog
                FROM upload_queue
                WHERE state='pending'
                """,
                (cutoff_iso, cutoff_iso),
            ).fetchone()
            return {
                "realtime": int(row["realtime"] or 0),
                "backlog": int(row["backlog"] or 0),
            }

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def oldest_backlog_created_at(self, cutoff_iso: str) -> str | None:
        """Return the ``created_at`` of the oldest ``backlog`` pending item."""
        def _run() -> str | None:
            assert self._conn is not None
            row = self._conn.execute(
                """
                SELECT MIN(created_at) AS oldest
                FROM upload_queue
                WHERE state='pending' AND created_at < ?
                """,
                (cutoff_iso,),
            ).fetchone()
            return row["oldest"] if row and row["oldest"] is not None else None

        async with self._lock:
            return await asyncio.to_thread(_run)
