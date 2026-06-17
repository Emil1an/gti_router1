"""Tests for the read-only ptz_get_position command (Story 4.6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from camera.command_executor import CommandExecutor
from camera.command_receiver import CommandReceiver
from camera.ptz_control import PTZController
from health.supabase_client import SupabaseClient
from utils.errors import PTZCommandError

_REAL_SLEEP = asyncio.sleep


def _controller() -> MagicMock:
    c = MagicMock(spec=PTZController)
    c.continuous_move = AsyncMock(return_value="ok")
    c.stop = AsyncMock(return_value="ok")
    c.get_position = AsyncMock(
        return_value={"pan": 0.25, "tilt": -0.5, "zoom": 0.1, "preset": "preset-1"}
    )
    return c


def _client() -> MagicMock:
    client = MagicMock(spec=SupabaseClient)
    client.update = AsyncMock(return_value=[{"id": "c1"}])
    return client


def _cmd(cid="c1", camera_id="cam-1", command_type="ptz_get_position", payload=None):
    return {
        "id": cid, "camera_id": camera_id, "command_type": command_type,
        "payload": payload or {}, "status": "processing",
    }


# ── Executor: read-only behaviour ────────────────────────────────────────────────

class TestExecutorGetPosition:
    async def test_returns_position_and_completes(self) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)

        await ex.execute(_cmd())

        ctrl.get_position.assert_awaited_once()
        _args, _kw = client.update.call_args
        patch = _args[2]
        assert patch["status"] == "completed"
        assert patch["payload"]["result_position"]["preset"] == "preset-1"

    async def test_does_not_issue_any_movement(self) -> None:
        ctrl = _controller()
        ex = CommandExecutor({"cam-1": ctrl}, _client(), update_max_retries=0)
        await ex.execute(_cmd())
        ctrl.continuous_move.assert_not_called()
        ctrl.stop.assert_not_called()

    async def test_unsupported_position_marks_failed(self) -> None:
        ctrl = _controller()
        ctrl.get_position = AsyncMock(side_effect=PTZCommandError("GetStatus unsupported"))
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)

        await ex.execute(_cmd())

        _args, _kw = client.update.call_args
        patch = _args[2]
        assert patch["status"] == "failed"
        assert "GetStatus unsupported" in patch["error_message"]


# ── Receiver: concurrency-safe (does not cancel an active move) ─────────────────

class TestReceiverConcurrency:
    async def test_get_position_does_not_cancel_active_move(self) -> None:
        move_started = asyncio.Event()
        move_cancelled = asyncio.Event()
        got_position = asyncio.Event()

        async def handler(cmd):
            if cmd["command_type"] == "ptz_continuous_move":
                move_started.set()
                try:
                    await _REAL_SLEEP(60)  # long-running move
                except asyncio.CancelledError:
                    move_cancelled.set()
                    raise
            elif cmd["command_type"] == "ptz_get_position":
                got_position.set()

        # Claim echoes the full row (return=representation) so command_type is
        # preserved through the claim, like real PostgREST.
        rows = {
            "m1": {"id": "m1", "camera_id": "cam-1",
                   "command_type": "ptz_continuous_move", "status": "pending"},
            "g1": {"id": "g1", "camera_id": "cam-1",
                   "command_type": "ptz_get_position", "status": "pending"},
        }

        async def _update(table, params, patch):
            row = rows[params["id"][len("eq."):]]
            row.update(patch)
            return [dict(row)]

        client = MagicMock(spec=SupabaseClient)
        client.update = AsyncMock(side_effect=_update)
        rcv = CommandReceiver(client=client, handler=handler, camera_ids=["cam-1"])

        # Start a move (it blocks), then a get_position for the same camera.
        await rcv._process_row(
            {"id": "m1", "camera_id": "cam-1", "command_type": "ptz_continuous_move",
             "status": "pending"}
        )
        await asyncio.wait_for(move_started.wait(), timeout=2.0)
        await rcv._process_row(
            {"id": "g1", "camera_id": "cam-1", "command_type": "ptz_get_position",
             "status": "pending"}
        )
        await asyncio.wait_for(got_position.wait(), timeout=2.0)

        # The move must still be running — get_position never cancels it.
        assert move_cancelled.is_set() is False
        assert rcv._inflight["cam-1"].done() is False

        # cleanup
        rcv._inflight["cam-1"].cancel()
        for t in rcv._readonly_tasks:
            t.cancel()
