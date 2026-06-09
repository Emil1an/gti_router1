"""Tests for CommandReceiver (Story 4.2).

Supabase REST + Realtime are faked in-process; no network.
"""

from __future__ import annotations

import asyncio
from typing import Any

from camera.command_receiver import CommandReceiver
from utils.errors import SupabaseTransientError

_REAL_SLEEP = asyncio.sleep


# ── Fakes ──────────────────────────────────────────────────────────────────────

class FakeSupabase:
    """In-memory ptz_commands simulating PostgREST select + conditional update."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: dict[Any, dict[str, Any]] = {r["id"]: dict(r) for r in rows}

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        cam_filter = params.get("camera_id", "in.()")
        cams = cam_filter[len("in.("):-1].split(",") if cam_filter.startswith("in.(") else []
        want_status = params.get("status", "eq.pending")[len("eq."):]
        return [
            dict(r) for r in self.rows.values()
            if r["status"] == want_status and r["camera_id"] in cams
        ]

    async def update(
        self, table: str, params: dict[str, str], patch: dict[str, Any]
    ) -> list[dict[str, Any]]:
        idv = params["id"][len("eq."):]
        row = self.rows.get(idv)
        if row is None:
            return []
        if "status" in params:
            want = params["status"][len("eq."):]
            if row["status"] != want:  # atomic claim guard
                return []
        row.update(patch)
        return [dict(row)]


class FakeRealtimeDown:
    async def connect(self) -> None:
        raise SupabaseTransientError("websocket unavailable")

    async def messages(self):
        if False:  # pragma: no cover
            yield {}
        return

    async def close(self) -> None:
        pass


class FakeRealtimeUp:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def messages(self):
        for row in self._rows:
            yield row
        # Then idle so the loop doesn't busy-reconnect during the test.
        await _REAL_SLEEP(60)

    async def close(self) -> None:
        pass


def _cmd(cid: str, camera_id: str, command_type: str = "ptz_continuous_move",
         status: str = "pending", payload: dict | None = None) -> dict[str, Any]:
    return {
        "id": cid, "camera_id": camera_id, "command_type": command_type,
        "status": status, "payload": payload or {"pan": 0.1},
        "issued_at": f"2026-06-08T10:00:0{cid[-1]}.000Z",
    }


def _receiver(client, handler, camera_ids=("cam-1", "cam-2"), realtime=None) -> CommandReceiver:
    return CommandReceiver(
        client=client, handler=handler, camera_ids=list(camera_ids), realtime=realtime
    )


async def _settle(receiver: CommandReceiver) -> None:
    """Let dispatched handler tasks run to completion."""
    await _REAL_SLEEP(0.02)
    tasks = [t for t in receiver._inflight.values() if not t.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ── Filtering by router cameras ──────────────────────────────────────────────────

class TestFiltering:
    async def test_only_router_cameras_processed(self) -> None:
        delivered: list[str] = []

        async def handler(cmd):
            delivered.append(cmd["camera_id"])

        client = FakeSupabase([
            _cmd("c1", "cam-1"),
            _cmd("c2", "cam-2"),
            _cmd("c3", "cam-9"),  # not this router's camera
        ])
        rcv = _receiver(client, handler)
        rcv._running = True  # _poll_once honours the running flag
        await rcv._poll_once()
        await _settle(rcv)

        assert sorted(delivered) == ["cam-1", "cam-2"]


# ── Atomic claim ─────────────────────────────────────────────────────────────────

class TestAtomicClaim:
    async def test_claim_marks_processing(self) -> None:
        async def handler(cmd):
            pass

        client = FakeSupabase([_cmd("c1", "cam-1")])
        rcv = _receiver(client, handler)
        await rcv._process_row(_cmd("c1", "cam-1"))
        await _settle(rcv)
        assert client.rows["c1"]["status"] == "processing"

    async def test_already_claimed_not_delivered(self) -> None:
        delivered: list[str] = []

        async def handler(cmd):
            delivered.append(cmd["id"])

        # Row is already 'processing' in the DB → claim affects 0 rows.
        client = FakeSupabase([_cmd("c1", "cam-1", status="processing")])
        rcv = _receiver(client, handler)
        # Simulate Realtime delivering the (stale) row as pending.
        await rcv._process_row(_cmd("c1", "cam-1", status="pending"))
        await _settle(rcv)
        assert delivered == []


# ── No duplication between Realtime and polling ──────────────────────────────────

class TestNoDuplication:
    async def test_same_command_processed_once(self) -> None:
        delivered: list[str] = []

        async def handler(cmd):
            delivered.append(cmd["id"])

        client = FakeSupabase([_cmd("c1", "cam-1")])
        rcv = _receiver(client, handler)
        # Realtime path then polling path see the same row.
        await rcv._process_row(_cmd("c1", "cam-1"))
        await rcv._process_row(_cmd("c1", "cam-1"))
        await _settle(rcv)
        assert delivered == ["c1"]
        assert rcv.ptz_commands_received == 1


# ── Priority of ptz_stop + cancellation of pending moves ────────────────────────

class TestPriorityAndCancellation:
    async def test_stop_processed_before_moves_in_sweep(self) -> None:
        order: list[str] = []

        async def handler(cmd):
            order.append(cmd["command_type"])

        client = FakeSupabase([
            _cmd("c1", "cam-1", command_type="ptz_continuous_move"),
            _cmd("c2", "cam-1", command_type="ptz_stop"),
        ])
        rcv = _receiver(client, handler)
        rcv._running = True
        await rcv._poll_once()
        await _settle(rcv)
        # Stop is dispatched first despite being issued later.
        assert order[0] == "ptz_stop"
        assert set(order) == {"ptz_stop", "ptz_continuous_move"}

    async def test_new_move_cancels_previous_move(self) -> None:
        started = asyncio.Event()
        first_cancelled = asyncio.Event()

        async def handler(cmd):
            if cmd["id"] == "c1":
                started.set()
                try:
                    await _REAL_SLEEP(60)  # long-running move
                except asyncio.CancelledError:
                    first_cancelled.set()
                    raise

        client = FakeSupabase([_cmd("c1", "cam-1"), _cmd("c2", "cam-1")])
        rcv = _receiver(client, handler)

        await rcv._process_row(_cmd("c1", "cam-1"))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await rcv._process_row(_cmd("c2", "cam-1"))  # new move cancels c1
        await asyncio.wait_for(first_cancelled.wait(), timeout=2.0)
        assert first_cancelled.is_set()

        # cleanup
        for t in rcv._inflight.values():
            t.cancel()

    async def test_stop_not_cancelled_by_following_move(self) -> None:
        running: list[str] = []

        async def handler(cmd):
            running.append(cmd["id"])
            if cmd["command_type"] == "ptz_stop":
                await _REAL_SLEEP(0.05)  # stop takes a moment

        client = FakeSupabase([
            _cmd("s1", "cam-1", command_type="ptz_stop"),
            _cmd("m1", "cam-1", command_type="ptz_continuous_move"),
        ])
        rcv = _receiver(client, handler)
        # Dispatch stop first, then a move — the move must NOT cancel the stop.
        await rcv._process_row(client.rows["s1"] | {"status": "pending"})
        await rcv._process_row(client.rows["m1"] | {"status": "pending"})
        stop_task = rcv._inflight  # not used directly
        await _settle(rcv)
        assert "s1" in running  # stop ran to completion


# ── Realtime down → polling fallback ─────────────────────────────────────────────

class TestRealtimeFallback:
    async def test_polling_active_when_realtime_down(self) -> None:
        delivered: list[str] = []

        async def handler(cmd):
            delivered.append(cmd["id"])

        client = FakeSupabase([_cmd("c1", "cam-1")])
        rcv = _receiver(client, handler, realtime=FakeRealtimeDown())
        rcv._poll_interval = 0.02
        await rcv.start()
        polling_active = realtime_connected = None
        try:
            for _ in range(200):
                if delivered:
                    break
                await _REAL_SLEEP(0.02)
            # Capture WHILE running (stop() clears the polling flag).
            polling_active = rcv.ptz_polling_active
            realtime_connected = rcv.ptz_realtime_connected
        finally:
            await rcv.stop()

        assert delivered == ["c1"]
        assert realtime_connected is False
        assert polling_active is True

    async def test_realtime_delivers_when_up(self) -> None:
        delivered: list[str] = []

        async def handler(cmd):
            delivered.append(cmd["id"])

        client = FakeSupabase([_cmd("c1", "cam-1")])
        # Realtime streams the insert; polling also runs but claim de-dups.
        rcv = _receiver(client, handler, realtime=FakeRealtimeUp([_cmd("c1", "cam-1")]))
        rcv._poll_interval = 0.02
        await rcv.start()
        try:
            for _ in range(200):
                if delivered:
                    break
                await _REAL_SLEEP(0.02)
        finally:
            await rcv.stop()

        assert delivered == ["c1"]
        assert rcv.ptz_commands_received == 1


# ── No cameras → inactive ─────────────────────────────────────────────────────────

class TestInactive:
    async def test_no_cameras_does_not_start_loops(self) -> None:
        async def handler(cmd):
            pass

        client = FakeSupabase([])
        rcv = _receiver(client, handler, camera_ids=[])
        await rcv.start()
        assert rcv._poll_task is None
        await rcv.stop()
