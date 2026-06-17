"""PTZ command execution + feedback (Story 4.3).

``CommandExecutor`` takes a ``ptz_commands`` row already claimed as ``processing``
by the :class:`~camera.command_receiver.CommandReceiver` (Story 4.2), dispatches
it to the right :class:`~camera.ptz_control.PTZController` method (Story 4.1)
based on ``command_type``, then writes feedback back to ``ptz_commands``:

* ``status`` → ``completed`` / ``failed``
* ``executed_at`` (UTC ISO-8601 Z)
* ``error_message`` (typed message on failure, ``None`` on success)
* the post-execution position under ``payload.result_position`` (without
  overwriting the input payload).

The feedback update is retried (``ptz.update_max_retries``) via the single
``@with_retry``; if it still fails the result is buffered locally so feedback is
never lost.  Nothing blocks the event loop and a command is never left dangling
in ``processing``.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from camera.ptz_control import PTZController
from config.loader import get_config
from health.supabase_client import SupabaseClient
from utils.errors import PTZError, SupabaseError, SupabaseTransientError
from utils.logging import get_logger
from utils.retry import with_retry

_TABLE = "ptz_commands"

# Explicit, validated command_type → PTZController method map.
_COMMAND_METHODS: dict[str, str] = {
    "ptz_continuous_move": "continuous_move",
    "ptz_relative_move": "relative_move",
    "ptz_absolute_move": "absolute_move",
    "ptz_stop": "stop",
    "ptz_goto_preset": "go_to_preset",
}


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class CommandExecutor:
    """Executes claimed PTZ commands and writes their feedback to Supabase."""

    def __init__(
        self,
        controllers: dict[str, PTZController],
        client: SupabaseClient,
        update_max_retries: int | None = None,
    ) -> None:
        cfg = get_config()
        self._controllers = controllers
        self._client = client
        self._update_retries = (
            update_max_retries if update_max_retries is not None
            else cfg.ptz.update_max_retries
        )
        self._pending_feedback: deque[tuple[str, dict[str, Any]]] = deque()
        self._logger = get_logger(__name__)

    @property
    def pending_feedback_count(self) -> int:
        """Number of feedback updates buffered locally after update failures."""
        return len(self._pending_feedback)

    # ── Execution ───────────────────────────────────────────────────────────────

    async def execute(self, command: dict[str, Any]) -> None:
        """Execute one claimed command and write its feedback (never raises)."""
        camera_id: str = command.get("camera_id", "")
        command_type: str = command.get("command_type", "")
        payload: dict[str, Any] = command.get("payload") or {}
        logger = get_logger(__name__, camera_id=camera_id)
        start = time.monotonic()

        is_get_position = command_type == "ptz_get_position"
        method_name = _COMMAND_METHODS.get(command_type)
        if method_name is None and not is_get_position:
            await self._finalize(
                command, "failed",
                f"unknown command_type '{command_type}'", start,
            )
            return

        controller = self._controllers.get(camera_id)
        if controller is None:
            await self._finalize(
                command, "failed",
                f"no PTZ controller available for camera '{camera_id}'", start,
            )
            return

        try:
            # ptz_get_position (Story 4.6): read-only — no movement is ever issued.
            if is_get_position:
                position = await controller.get_position()
                await self._finalize(command, "completed", None, start, position=position)
                return

            method = getattr(controller, method_name)
            await method(**self._args_for(command_type, payload))

            # Post-execution position (best-effort; failure must not flip success).
            position: dict[str, Any] | None = None
            try:
                position = await controller.get_position()
            except PTZError as exc:
                logger.warning("get_position after command failed: %s", exc)

            await self._finalize(command, "completed", None, start, position=position)
        except PTZError as exc:
            logger.error("PTZ command failed: %s", exc)
            await self._finalize(command, "failed", str(exc), start)
        except Exception as exc:  # noqa: BLE001 — never leave it in 'processing'
            logger.error("Unexpected PTZ execution error: %s", exc)
            await self._finalize(command, "failed", f"unexpected error: {exc}", start)

    @staticmethod
    def _args_for(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Translate the command payload into the controller method's kwargs."""
        if command_type in ("ptz_continuous_move", "ptz_relative_move", "ptz_absolute_move"):
            return {
                "pan": float(payload.get("pan", 0.0)),
                "tilt": float(payload.get("tilt", 0.0)),
                "zoom": float(payload.get("zoom", 0.0)),
            }
        if command_type == "ptz_goto_preset":
            return {"preset_token": str(payload.get("preset_token") or payload.get("preset") or "")}
        return {}  # ptz_stop → controller defaults (stop pan/tilt + zoom)

    # ── Feedback ──────────────────────────────────────────────────────────────────

    async def _finalize(
        self,
        command: dict[str, Any],
        status: str,
        error: str | None,
        start: float,
        position: dict[str, Any] | None = None,
    ) -> None:
        """Write the terminal status to ``ptz_commands`` (retry + local buffer)."""
        command_id = command.get("id")
        camera_id = command.get("camera_id", "")

        # Copy the input payload so we never overwrite it; attach result_position.
        payload = dict(command.get("payload") or {})
        if position is not None:
            payload["result_position"] = position

        patch = {
            "status": status,
            "executed_at": _utc_now_iso(),
            "error_message": error,
            "payload": payload,
        }

        ok = await self._update_with_retry(str(command_id), patch)
        if not ok:
            self._pending_feedback.append((str(command_id), patch))

        latency_ms = (time.monotonic() - start) * 1000.0
        self._logger.info(
            "PTZ command finalized",
            extra={
                "camera_id": camera_id,
                "command_id": command_id,
                "status": status,
                "feedback_buffered": not ok,
                "ptz_command_latency_ms": round(latency_ms, 1),
            },
        )

    async def _update_with_retry(self, command_id: str, patch: dict[str, Any]) -> bool:
        """Update one command row, retrying transient failures; ``False`` if it
        ultimately fails (the caller then buffers it)."""
        async def _do() -> list[dict[str, Any]]:
            return await self._client.update(
                _TABLE, {"id": f"eq.{command_id}"}, patch
            )

        wrapped = with_retry(
            max_retries=self._update_retries, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            await wrapped()
            return True
        except SupabaseError as exc:
            self._logger.error(
                "ptz_commands feedback update exhausted: %s",
                exc,
                extra={"command_id": command_id},
            )
            return False

    async def flush_pending_feedback(self) -> int:
        """Retry buffered feedback updates; returns how many were flushed."""
        flushed = 0
        for _ in range(len(self._pending_feedback)):
            command_id, patch = self._pending_feedback.popleft()
            if await self._update_with_retry(command_id, patch):
                flushed += 1
            else:
                self._pending_feedback.append((command_id, patch))
                break  # still down — keep the rest for later
        return flushed
