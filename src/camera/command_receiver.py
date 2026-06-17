"""PTZ command receiver — Realtime + polling fallback (Story 4.2).

``CommandReceiver`` watches the **``ptz_commands``** table (never
``router_commands``) for ``INSERT``s targeting this Router's ``camera_id``s and
delivers each claimed command to an execution handler (Story 4.3).  It does not
execute ONVIF itself.

Delivery paths
--------------
* **Realtime (WebSocket):** if a realtime subscriber is supplied, it streams
  inserts with minimal latency; reconnects use the single ``@with_retry``.
* **Polling fallback (every ``ptz.poll_interval_s`` = 2 s):** always runs as a
  safety net (and the only path when realtime is unavailable), picking up
  ``pending`` rows — including stragglers after a restart.

De-duplication & atomic claim
-----------------------------
Both paths funnel into :meth:`_process_row`, which (a) ignores rows for other
cameras / non-``pending`` rows, (b) performs an **atomic claim** —
``UPDATE ... SET status='processing' WHERE id=? AND status='pending'`` — so a
row is processed exactly once even if Realtime and polling both see it, and
(c) dispatches the claimed row to the handler.

Priority & cancellation
------------------------
``ptz_stop`` is processed first within a polling sweep, and any newly dispatched
command for a camera **cancels that camera's still-running prior command** so
stale moves never pile up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from camera.validator import PTZCommandValidator
from config.loader import get_config
from health.state import AppState
from health.supabase_client import SupabaseClient
from utils.errors import SupabaseError, SupabaseTransientError
from utils.logging import get_logger
from utils.retry import with_retry

# async handler invoked with the claimed command row.
CommandHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

_TABLE = "ptz_commands"


class CommandReceiver:
    """Receives PTZ commands for this Router's cameras and delivers them claimed.

    Args:
        client:     shared :class:`SupabaseClient` for REST polling/claim.
        handler:    async callable that executes a claimed command (Story 4.3).
        camera_ids: this Router's camera_ids; if omitted, taken from
                    ``app_state.per_camera`` (reuses the shared AppState).
        app_state:  shared application state (source of active cameras).
        realtime:   optional realtime subscriber exposing ``async connect()``,
                    ``messages()`` (async iterator of rows) and ``async close()``.
    """

    def __init__(
        self,
        client: SupabaseClient,
        handler: CommandHandler,
        camera_ids: list[str] | None = None,
        app_state: AppState | None = None,
        realtime: Any | None = None,
        validator: PTZCommandValidator | None = None,
    ) -> None:
        cfg = get_config()
        self._client = client
        self._handler = handler
        self._app_state = app_state
        self._validator = validator
        if camera_ids is not None:
            self._camera_ids = list(camera_ids)
        elif app_state is not None:
            self._camera_ids = list(app_state.per_camera.keys())
        else:
            self._camera_ids = []
        self._realtime = realtime

        self._poll_interval = cfg.ptz.poll_interval_s
        self._realtime_retries = cfg.ptz.realtime_reconnect_max_retries

        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._realtime_task: asyncio.Task[None] | None = None
        self._seen_ids: set[Any] = set()
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._inflight_type: dict[str, str] = {}
        self._readonly_tasks: set[asyncio.Task[None]] = set()
        self._logger = get_logger(__name__)

        # Metrics
        self._ptz_commands_received = 0
        self._ptz_commands_rejected = 0
        self._ptz_realtime_connected = False
        self._ptz_polling_active = False

    # ── Metrics ──────────────────────────────────────────────────────────────────

    @property
    def ptz_commands_received(self) -> int:
        return self._ptz_commands_received

    @property
    def ptz_commands_rejected(self) -> int:
        return self._ptz_commands_rejected

    @property
    def ptz_realtime_connected(self) -> bool:
        return self._ptz_realtime_connected

    @property
    def ptz_polling_active(self) -> bool:
        return self._ptz_polling_active

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the initial sweep, the polling loop, and (if any) Realtime."""
        if not self._camera_ids:
            self._logger.info(
                "CommandReceiver inactive — no cameras for this router "
                "(PTZ stays inactive until cameras are known)"
            )
            return
        self._running = True
        await self._initial_sweep()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="ptz-poll")
        if self._realtime is not None:
            self._realtime_task = asyncio.create_task(
                self._realtime_loop(), name="ptz-realtime"
            )
        self._logger.info(
            "CommandReceiver started", extra={"cameras": self._camera_ids}
        )

    async def stop(self) -> None:
        """Stop loops, cancel in-flight command tasks, close Realtime."""
        self._running = False
        for task in (self._poll_task, self._realtime_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._poll_task = self._realtime_task = None

        for task in list(self._inflight.values()) + list(self._readonly_tasks):
            if not task.done():
                task.cancel()
        self._inflight.clear()
        self._readonly_tasks.clear()

        if self._realtime is not None:
            try:
                await self._realtime.close()
            except Exception:
                pass
        self._logger.info("CommandReceiver stopped")

    # ── Realtime ───────────────────────────────────────────────────────────────

    async def _realtime_loop(self) -> None:
        connect = with_retry(
            max_retries=self._realtime_retries, retryable=(SupabaseTransientError,)
        )(self._realtime.connect)

        try:
            while self._running:
                try:
                    await connect()
                    self._ptz_realtime_connected = True
                    self._logger.info("PTZ Realtime connected")
                    async for row in self._realtime.messages():
                        if not self._running:
                            break
                        await self._process_row(row)
                    # Generator returned → connection closed cleanly.
                    self._ptz_realtime_connected = False
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # WebSocket dropped → rely on polling
                    self._ptz_realtime_connected = False
                    self._logger.warning(
                        "PTZ Realtime error — falling back to polling: %s", exc
                    )
                if self._running:
                    await asyncio.sleep(1.0)  # brief pause before reconnect
        except asyncio.CancelledError:
            pass
        finally:
            self._ptz_realtime_connected = False

    # ── Polling ────────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        try:
            while self._running:
                # Polling is the "active" path whenever Realtime is not connected.
                self._ptz_polling_active = not self._ptz_realtime_connected
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._logger.error("PTZ polling error: %s", exc)
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._ptz_polling_active = False

    async def _initial_sweep(self) -> None:
        """One sweep at startup to pick up pending rows left from a restart."""
        try:
            await self._poll_once()
        except Exception as exc:
            self._logger.warning("Initial PTZ sweep failed: %s", exc)

    async def _poll_once(self) -> None:
        rows = await self._fetch_pending()
        # ptz_stop has priority over queued moves within a sweep.
        rows.sort(key=lambda r: 0 if r.get("command_type") == "ptz_stop" else 1)
        for row in rows:
            if not self._running:
                break
            await self._process_row(row)

    async def _fetch_pending(self) -> list[dict[str, Any]]:
        cam_filter = "in.(" + ",".join(self._camera_ids) + ")"

        async def _do() -> list[dict[str, Any]]:
            return await self._client.select(
                _TABLE,
                {
                    "status": "eq.pending",
                    "camera_id": cam_filter,
                    "order": "issued_at.asc",
                },
            )

        wrapped = with_retry(
            max_retries=self._realtime_retries, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            return await wrapped() or []
        except SupabaseError as exc:
            self._logger.warning("Could not fetch pending PTZ commands: %s", exc)
            return []

    # ── Core: filter → claim → dispatch ──────────────────────────────────────────

    async def _process_row(self, row: dict[str, Any]) -> None:
        camera_id = row.get("camera_id")
        command_id = row.get("id")

        if camera_id not in self._camera_ids:
            return  # not ours
        if command_id in self._seen_ids:
            return  # de-dup (Realtime + polling)
        if row.get("status") != "pending":
            return  # only pending

        # Security validation BEFORE the claim (Story 4.4). A rejected command is
        # closed as 'failed' with the reason and never reaches 'processing'.
        if self._validator is not None:
            result = self._validator.validate(row)
            if not result.ok:
                self._seen_ids.add(command_id)
                self._ptz_commands_rejected += 1
                self._logger.warning(
                    "PTZ command rejected — marking failed",
                    extra={
                        "camera_id": camera_id,
                        "command_id": command_id,
                        "reason": result.reason,
                    },
                )
                await self._reject(command_id, result.reason or "rejected")
                return

        claimed = await self._claim(command_id)
        # Mark seen regardless: a failed/empty claim means someone else took it.
        self._seen_ids.add(command_id)
        if claimed is None:
            self._logger.debug(
                "PTZ command already claimed elsewhere — skipping",
                extra={"camera_id": camera_id, "command_id": command_id},
            )
            return

        self._ptz_commands_received += 1
        self._logger.info(
            "PTZ command claimed",
            extra={
                "camera_id": camera_id,
                "command_id": command_id,
                "command_type": claimed.get("command_type"),
            },
        )
        self._dispatch(claimed)

    async def _reject(self, command_id: Any, reason: str) -> None:
        """Close a rejected command as ``failed`` with its reason (conditional on
        still being ``pending`` so a concurrently-claimed row is never clobbered).
        """
        now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        async def _do() -> list[dict[str, Any]]:
            return await self._client.update(
                _TABLE,
                {"id": f"eq.{command_id}", "status": "eq.pending"},
                {"status": "failed", "error_message": reason, "executed_at": now},
            )

        wrapped = with_retry(
            max_retries=self._realtime_retries, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            await wrapped()
        except SupabaseError as exc:
            self._logger.warning("Could not mark rejected command failed: %s", exc)

    async def _claim(self, command_id: Any) -> dict[str, Any] | None:
        """Atomic claim: pending → processing. Returns the row, or ``None``."""
        async def _do() -> list[dict[str, Any]]:
            return await self._client.update(
                _TABLE,
                {"id": f"eq.{command_id}", "status": "eq.pending"},
                {"status": "processing"},
            )

        wrapped = with_retry(
            max_retries=self._realtime_retries, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            rows = await wrapped()
        except SupabaseError as exc:
            self._logger.warning("PTZ claim failed: %s", exc)
            return None
        return rows[0] if rows else None

    def _dispatch(self, command: dict[str, Any]) -> None:
        """Hand the claimed command to the executor, cancelling a stale prior move.

        A new command for a camera cancels that camera's still-running previous
        **movement** (so moves don't pile up) — but never cancels a ``ptz_stop``
        in progress, since the stop has priority and must always complete.
        """
        camera_id = str(command.get("camera_id"))
        command_type = str(command.get("command_type"))

        # ptz_get_position is read-only (Story 4.6): it must NOT cancel an active
        # move nor be tracked as the camera's in-flight command. Run it
        # independently so it can execute even during an ongoing movement.
        if command_type == "ptz_get_position":
            task = asyncio.create_task(self._handler(command))
            self._readonly_tasks.add(task)
            task.add_done_callback(self._readonly_tasks.discard)
            return

        prev = self._inflight.get(camera_id)
        prev_type = self._inflight_type.get(camera_id)
        if prev is not None and not prev.done() and prev_type != "ptz_stop":
            prev.cancel()

        task = asyncio.create_task(self._handler(command))
        self._inflight[camera_id] = task
        self._inflight_type[camera_id] = command_type
