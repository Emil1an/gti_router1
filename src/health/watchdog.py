"""systemd watchdog heartbeat (Story 3.5).

``Watchdog`` sends ``sd_notify("WATCHDOG=1")`` every 15 s **as a coroutine on the
main event loop**.  Because the heartbeat shares the loop it is meant to guard, a
blocked/hung loop stops the heartbeat and systemd (``WatchdogSec=30``,
``Restart=on-failure``) restarts the service — a heartbeat sent from a separate
thread would mask a hung loop, so we never do that.

It also exposes :meth:`notify_ready` (``READY=1``) and :meth:`notify_stopping`
(``STOPPING=1``) for the orchestrator (Story 3.7) to call at the right moments —
emitted from a single place to avoid duplication.

Outside systemd (no ``NOTIFY_SOCKET`` / no ``systemd-python``) every notify is a
safe no-op so development and CI are unaffected.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from utils.errors import WatchdogError
from utils.logging import get_logger

# Heartbeat period (s): half of the systemd ``WatchdogSec=30`` margin.
_HEARTBEAT_INTERVAL_S: float = 15.0


def _resolve_systemd_notify() -> Callable[[str], object] | None:
    """Return ``systemd.daemon.notify`` if available, else ``None``."""
    try:
        from systemd.daemon import notify  # type: ignore[import-untyped]
    except ImportError:
        return None
    return notify


class Watchdog:
    """Periodic ``sd_notify`` heartbeat tied to the health of the event loop."""

    def __init__(
        self,
        notifier: Callable[[str], object] | None = None,
        interval_s: float | None = None,
    ) -> None:
        self._notify_fn = notifier if notifier is not None else _resolve_systemd_notify()
        self._interval = interval_s if interval_s is not None else _HEARTBEAT_INTERVAL_S
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._logger = get_logger(__name__)

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the 15 s heartbeat loop on the current event loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="watchdog")
        self._logger.info(
            "Watchdog started",
            extra={"interval_s": self._interval, "enabled": self.enabled},
        )

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._logger.info("Watchdog stopped")

    # ── State ───────────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """``True`` only under systemd ``Type=notify`` (``NOTIFY_SOCKET`` set)."""
        return self._notify_fn is not None and bool(os.environ.get("NOTIFY_SOCKET"))

    # ── Notifications ─────────────────────────────────────────────────────────────

    def notify_ready(self) -> None:
        """Emit ``READY=1`` once initialisation is complete (Type=notify)."""
        self._send("READY=1")

    def notify_stopping(self) -> None:
        """Emit ``STOPPING=1`` at the start of an orderly shutdown."""
        self._send("STOPPING=1")

    # ── Internal ──────────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                self._send("WATCHDOG=1")
        except asyncio.CancelledError:
            pass

    def _send(self, message: str) -> None:
        """Send one sd_notify message; no-op off systemd, never fatal on error."""
        if not self.enabled:
            return  # development / CI — safe no-op
        try:
            self._notify_fn(message)  # type: ignore[misc]
        except Exception as exc:
            # A notify failure must never tear the Router down (AC#7).
            err = WatchdogError(f"sd_notify({message!r}) failed: {exc}")
            self._logger.error("%s", err)
