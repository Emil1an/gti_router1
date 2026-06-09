"""Tests for CommandExecutor — execution + feedback (Story 4.3).

PTZController and SupabaseClient are mocked; no hardware/network.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from camera.command_executor import CommandExecutor
from camera.ptz_control import PTZController
from health.supabase_client import SupabaseClient
from utils.errors import PTZCommandError, SupabaseTransientError


def _controller() -> MagicMock:
    c = MagicMock(spec=PTZController)
    c.continuous_move = AsyncMock(return_value="ok")
    c.absolute_move = AsyncMock(return_value="ok")
    c.stop = AsyncMock(return_value="ok")
    c.go_to_preset = AsyncMock(return_value="ok")
    c.get_position = AsyncMock(return_value={"pan": 0.1, "tilt": 0.2, "zoom": 0.0, "preset": None})
    return c


def _client(update_return=None, update_side=None) -> MagicMock:
    client = MagicMock(spec=SupabaseClient)
    if update_side is not None:
        client.update = AsyncMock(side_effect=update_side)
    else:
        client.update = AsyncMock(return_value=update_return or [{"id": "c1"}])
    return client


def _cmd(command_type="ptz_continuous_move", payload=None, camera_id="cam-1", cid="c1"):
    return {
        "id": cid,
        "camera_id": camera_id,
        "command_type": command_type,
        "payload": payload if payload is not None else {"pan": 0.5, "tilt": 0.0, "zoom": 0.0},
        "status": "processing",
    }


# ── Success path ─────────────────────────────────────────────────────────────────

class TestSuccess:
    async def test_completed_with_position(self) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)

        await ex.execute(_cmd())

        ctrl.continuous_move.assert_awaited_once_with(pan=0.5, tilt=0.0, zoom=0.0)
        ctrl.get_position.assert_awaited_once()
        _args, kwargs = client.update.call_args
        # update(table, params, patch)
        table, params, patch = _args
        assert table == "ptz_commands"
        assert params == {"id": "eq.c1"}
        assert patch["status"] == "completed"
        assert patch["error_message"] is None
        assert patch["executed_at"].endswith("Z")
        assert patch["payload"]["result_position"] == {
            "pan": 0.1, "tilt": 0.2, "zoom": 0.0, "preset": None
        }

    async def test_input_payload_not_overwritten(self) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)
        cmd = _cmd(payload={"pan": 0.5, "tilt": 0.0, "zoom": 0.0, "note": "keep me"})

        await ex.execute(cmd)

        _args, _kw = client.update.call_args
        patch = _args[2]
        assert patch["payload"]["note"] == "keep me"            # preserved
        assert "result_position" in patch["payload"]            # added
        assert "result_position" not in cmd["payload"]          # original untouched

    async def test_stop_command(self) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)
        await ex.execute(_cmd(command_type="ptz_stop", payload={}))
        ctrl.stop.assert_awaited_once()

    async def test_goto_preset_passes_token(self) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)
        await ex.execute(_cmd(command_type="ptz_goto_preset", payload={"preset_token": "p2"}))
        ctrl.go_to_preset.assert_awaited_once_with(preset_token="p2")


# ── Failure paths ────────────────────────────────────────────────────────────────

class TestFailure:
    async def test_controller_error_marks_failed(self) -> None:
        ctrl = _controller()
        ctrl.continuous_move = AsyncMock(side_effect=PTZCommandError("device says no"))
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)

        await ex.execute(_cmd())

        _args, _kw = client.update.call_args
        patch = _args[2]
        assert patch["status"] == "failed"
        assert "device says no" in patch["error_message"]

    async def test_unknown_command_type_does_not_touch_camera(self) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)

        await ex.execute(_cmd(command_type="ptz_dance"))

        ctrl.continuous_move.assert_not_called()
        ctrl.stop.assert_not_called()
        _args, _kw = client.update.call_args
        patch = _args[2]
        assert patch["status"] == "failed"
        assert "unknown command_type" in patch["error_message"]

    async def test_missing_controller_marks_failed(self) -> None:
        client = _client()
        ex = CommandExecutor({}, client, update_max_retries=0)  # no controllers
        await ex.execute(_cmd(camera_id="cam-x"))
        _args, _kw = client.update.call_args
        patch = _args[2]
        assert patch["status"] == "failed"
        assert "no PTZ controller" in patch["error_message"]


# ── Update retry + local buffering ───────────────────────────────────────────────

class TestUpdateRetryAndBuffer:
    async def test_update_retries_then_buffers(self) -> None:
        ctrl = _controller()
        client = _client(update_side=SupabaseTransientError("supabase down"))
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=2)

        with patch("utils.retry.asyncio.sleep", new=AsyncMock()):
            await ex.execute(_cmd(command_type="ptz_dance"))  # unknown → straight to update

        # max_retries=2 → 3 total attempts, all fail → buffered locally.
        assert client.update.await_count == 3
        assert ex.pending_feedback_count == 1

    async def test_flush_pending_feedback(self) -> None:
        ctrl = _controller()
        # First all fail (buffered), then a healthy client flushes.
        client = _client(update_side=SupabaseTransientError("down"))
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)
        with patch("utils.retry.asyncio.sleep", new=AsyncMock()):
            await ex.execute(_cmd(command_type="ptz_dance"))
        assert ex.pending_feedback_count == 1

        client.update = AsyncMock(return_value=[{"id": "c1"}])
        flushed = await ex.flush_pending_feedback()
        assert flushed == 1
        assert ex.pending_feedback_count == 0


# ── Latency metric ────────────────────────────────────────────────────────────────

class TestLatency:
    async def test_latency_metric_emitted(self, caplog) -> None:
        ctrl = _controller()
        client = _client()
        ex = CommandExecutor({"cam-1": ctrl}, client, update_max_retries=0)
        with caplog.at_level(logging.INFO, logger="camera.command_executor"):
            await ex.execute(_cmd())
        assert any("ptz_command_latency_ms" in r.__dict__ for r in caplog.records)
