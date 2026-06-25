"""Loopback uvicorn server for the local console (Story 11.1).

``LocalConsoleServer`` runs the FastAPI app from :mod:`web.local_api` as a
non-blocking asyncio task inside the Router process. It binds to ``127.0.0.1``
by default (loopback-only, AC#2) and exposes the same ``async start()`` /
``async stop()`` lifecycle as every other Router subsystem so ``main.py`` drives
it uniformly.

Two integration details matter:

* **Signal handlers are disabled.** uvicorn installs its own SIGINT/SIGTERM
  handlers by default, which would fight ``main.py``'s orchestrated shutdown.
  We no-op ``install_signal_handlers`` so the Router stays in control.
* **Failure is contained.** A bind/startup error here must never abort the
  Router — capture/upload/health keep running; the console is best-effort.
"""

from __future__ import annotations

import asyncio

import uvicorn

from config.schema import RouterConfig
from health.monitor import SystemMonitor
from health.state import AppState
from utils.logging import get_logger
from web.local_api import create_app


class LocalConsoleServer:
    """Owns the uvicorn server task for the in-process console API."""

    def __init__(
        self,
        *,
        state: AppState,
        monitor: SystemMonitor,
        cfg: RouterConfig,
    ) -> None:
        self._cfg = cfg
        self._enabled = cfg.console.enabled
        self._logger = get_logger(__name__)

        app = create_app(state=state, monitor=monitor, cfg=cfg)
        uconfig = uvicorn.Config(
            app,
            host=cfg.console.host,
            port=cfg.console.port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
        )
        self._server = uvicorn.Server(uconfig)
        # Do not let uvicorn grab the process signal handlers (main.py owns them).
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._task: asyncio.Task[None] | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the server as a background task (non-blocking)."""
        if not self._enabled:
            self._logger.info("Local console disabled by config")
            return
        self._task = asyncio.create_task(self._server.serve(), name="local-console")
        self._logger.info(
            "Local console started",
            extra={"host": self._cfg.console.host, "port": self._cfg.console.port},
        )

    async def stop(self) -> None:
        """Ask uvicorn to exit and await the task."""
        if self._task is None:
            return
        self._server.should_exit = True
        if not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                if not self._task.done():
                    self._task.cancel()
                    try:
                        await self._task
                    except (asyncio.CancelledError, Exception):
                        pass
        self._task = None
        self._logger.info("Local console stopped")
