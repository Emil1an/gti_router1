"""GTI Router entry point — orchestration only (Stories 1.5 / 3.7).

``main()`` contains **no business logic**: it constructs each subsystem (all of
which expose ``async start()`` / ``async stop()``) and drives the ordered
lifecycle.

Initialisation — 12 steps (fail-fast on config/camera, degraded on Supabase)
----------------------------------------------------------------------------
 1. Load & validate config            → fail-fast, exit 1 on error
 2. Set up logging
 3. Initialise shared AppState
 4. Create the Supabase client (service_role)
 5. Start device registration         → degraded/non-blocking (Story 3.1/3.6)
 6. Start the system monitor           (Story 3.3)
 7. Build video sources from config    → fail-fast, exit 2 on error
 8. Start the capture→upload subsystem (Epics 1–2; pipeline error → exit 3)
 9. Start the health reporter          (Story 3.2)
10. Start the systemd watchdog         (Story 3.5)
11. Log degraded-mode / PTZ status     (Story 3.6)
12. Emit ``READY=1``                    (Story 3.5/3.7)

Shutdown — 6 steps (on SIGTERM/SIGINT, timeout default 30 s)
------------------------------------------------------------
 1. Notify ``STOPPING=1`` + emit a best-effort final health report
 2. Stop the health reporter
 3. Stop the upload subsystem (drain in-flight uploads ≤ timeout; persist SQLite)
 4. Stop the system monitor
 5. Stop device registration
 6. Stop the watchdog

Exit codes (inherited from Story 1.5): 0 clean, 1 config, 2 camera, 3 pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from camera.ptz_service import PTZService
from camera.sources import create_source
from camera.sources.base import VideoSource
from config.loader import get_config
from health.degraded import log_degraded_mode_status
from health.monitor import SystemMonitor
from health.registration import DeviceRegistration
from health.reporter import HealthReporter
from health.state import AppState
from health.supabase_client import SupabaseClient
from health.watchdog import Watchdog
from licensing import enforce_camera_limit
from location.gps import GpsReader
from location.orientation import OrientationPublisher
from pipeline.snapshot import SnapshotService
from upload.service import UploadService
from web.server import LocalConsoleServer
from utils.errors import (
    CameraLimitError,
    CameraSetupError,
    ConfigError,
    PipelineError,
    RouterError,
    VideoSourceError,
)
from utils.logging import get_logger, setup_logging


def _ensure_platform_package() -> None:
    """Extend the stdlib ``platform`` module into a package so ``platform.board``
    (Story 5.5) is importable, without replacing stdlib platform (psutil-safe).

    Idempotent; mirrors the bootstrap in ``tests/conftest.py`` for production.
    """
    import os
    import platform as _stdlib_platform

    pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform")
    if pkg not in getattr(_stdlib_platform, "__path__", []):
        _stdlib_platform.__path__ = [
            *getattr(_stdlib_platform, "__path__", []),
            pkg,
        ]

# Exit codes (Story 1.5)
EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_CAMERA = 2
EXIT_PIPELINE = 3


class RouterApp:
    """Lifecycle orchestrator for all Router subsystems (no business logic)."""

    def __init__(self) -> None:
        self._state = AppState()
        self._shutdown_event = asyncio.Event()
        self._logger = logging.getLogger("main")

        self._cfg = None
        self._supabase_client: SupabaseClient | None = None
        self._registration: DeviceRegistration | None = None
        self._monitor: SystemMonitor | None = None
        self._board = None
        self._upload_service: UploadService | None = None
        self._reporter: HealthReporter | None = None
        self._gps: GpsReader | None = None
        self._orientation: OrientationPublisher | None = None
        self._snapshot: SnapshotService | None = None
        self._ptz_service: PTZService | None = None
        self._console: LocalConsoleServer | None = None
        self._watchdog: Watchdog | None = None

    # ── Initialisation (12 steps) ───────────────────────────────────────────────

    async def startup(self) -> None:
        # 1. Config (fail-fast → exit 1)
        self._cfg = get_config()

        # 2. Logging
        setup_logging()
        self._logger = get_logger("main")
        self._logger.info("GTI Router starting", extra={"device": self._cfg.device.serial_number})

        # 3. Shared application state (already constructed)

        # 4. Supabase client (service_role, env-only secrets)
        self._supabase_client = SupabaseClient()

        # 5. Device registration — non-blocking, degraded-tolerant (3.1/3.6)
        self._registration = DeviceRegistration(
            client=self._supabase_client, state=self._state
        )
        await self._registration.start()

        # 6. System monitor (3.3)
        self._monitor = SystemMonitor()
        await self._monitor.start()

        # 6b. Local console mini-API (Epic 11) — loopback, best-effort.
        #     Reads AppState + SystemMonitor live; a failure here never aborts
        #     the Router (capture/upload/health keep running).
        self._console = LocalConsoleServer(
            state=self._state, monitor=self._monitor, cfg=self._cfg
        )
        try:
            await self._console.start()
        except Exception as exc:  # noqa: BLE001 — console is non-essential
            self._logger.error("Local console failed to start (contained): %s", exc)

        # 7. Build video sources from config (fail-fast → exit 2)
        sources = self._build_sources()

        # 8. Capture→upload subsystem (E1+E2) — pipeline failure → exit 3
        try:
            self._upload_service = UploadService(sources=sources, app_state=self._state)
            await self._upload_service.start()
        except RouterError:
            raise
        except Exception as exc:  # any start failure here is a pipeline failure
            raise PipelineError(f"upload subsystem failed to start: {exc}") from exc

        # 9. Health reporter (3.2)
        self._reporter = HealthReporter(
            self._supabase_client, self._monitor, self._state
        )
        await self._reporter.start()

        # 9b. Location + last-frame services (Epic 6) — all best-effort/contained.
        #     GPS is Pro-only (inert otherwise); orientation/snapshot are degraded-
        #     tolerant; none of them aborts the Router on failure.
        try:
            self._gps = GpsReader(self._board, self._state, self._supabase_client)
            await self._gps.start()
            self._orientation = OrientationPublisher(self._supabase_client)
            await self._orientation.start()
            self._snapshot = SnapshotService(self._supabase_client)
            await self._snapshot.start()
        except Exception as exc:  # noqa: BLE001 — Epic 6 services never abort startup
            self._logger.error("Location/snapshot services failed to start (contained): %s", exc)

        # 10. PTZ subsystem (Epic 4) — best-effort, never aborts the Router.
        #     Activation is conditional on PTZ support + Supabase registration;
        #     a PTZ failure here is contained (capture/upload/health keep running).
        self._ptz_service = PTZService(self._supabase_client, self._state)
        try:
            await self._ptz_service.start()
        except Exception as exc:  # noqa: BLE001 — fault isolation (AC#5)
            self._logger.error("PTZ subsystem failed to start (contained): %s", exc)

        # 11. systemd watchdog (3.5)
        self._watchdog = Watchdog()
        await self._watchdog.start()

        # 12. Degraded-mode / PTZ status (3.6) + READY=1 (single emission point)
        log_degraded_mode_status(self._state, self._logger)
        self._watchdog.notify_ready()
        self._logger.info("GTI Router initialised — READY")

    def _build_sources(self) -> list[VideoSource]:
        """Detect the board, enforce the camera limit, and build one VideoSource
        per configured camera via the factory (fail-fast → exit 2).

        Each camera becomes its own VideoSource; the UploadService then gives
        each a dedicated HLSPipeline + supervisor task (hard isolation, Story 5.4).
        """
        assert self._cfg is not None

        _ensure_platform_package()
        from platform.board import detect_board  # noqa: PLC0415 — lazy (bootstrap)

        self._board = detect_board()
        self._logger.info("Hardware board detected", extra={"board": self._board.value})

        # Physical hardware ceiling (NFR12) — fail-fast if exceeded (Story 5.6).
        enforce_camera_limit(self._cfg.cameras, self._board)

        sources: list[VideoSource] = []
        for cam in self._cfg.cameras:
            sources.append(create_source(cam, board=self._board))
        return sources

    # ── Shutdown (6 steps) ──────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        timeout = float(self._cfg.upload.shutdown_timeout_s) if self._cfg else 30.0
        self._logger.info("GTI Router shutting down", extra={"timeout_s": timeout})

        # 1. Notify systemd + best-effort final health report
        if self._watchdog is not None:
            self._watchdog.notify_stopping()
        await self._emit_final_report()

        # 1b. Stop the local console (loopback HTTP server) early.
        if self._console is not None:
            try:
                await self._console.stop()
            except Exception as exc:  # noqa: BLE001 — contained
                self._logger.error("Error stopping local console: %s", exc)

        # 2. Stop the PTZ + location/snapshot services (cloud-writing producers)
        if self._ptz_service is not None:
            await self._ptz_service.stop()
        for svc in (self._snapshot, self._orientation, self._gps):
            if svc is not None:
                try:
                    await svc.stop()
                except Exception as exc:  # noqa: BLE001 — contained
                    self._logger.error("Error stopping Epic 6 service: %s", exc)

        # 3. Stop the health reporter
        if self._reporter is not None:
            await self._reporter.stop()

        # 4. Stop the upload subsystem (drain in-flight uploads, persist SQLite)
        if self._upload_service is not None:
            await self._upload_service.stop(drain_timeout_s=timeout)

        # 5. Stop the system monitor
        if self._monitor is not None:
            await self._monitor.stop()

        # 6. Stop device registration + watchdog
        if self._registration is not None:
            await self._registration.stop()
        if self._watchdog is not None:
            await self._watchdog.stop()

        self._logger.info("GTI Router shut down cleanly")

    async def _emit_final_report(self) -> None:
        """Emit one final health report (best-effort; degraded mode tolerant)."""
        if self._reporter is None:
            return
        try:
            await self._reporter.report_once()
        except Exception as exc:
            self._logger.warning("Final health report failed (best-effort): %s", exc)

    # ── Run loop + signals + exit codes ─────────────────────────────────────────

    def request_shutdown(self) -> None:
        """Signal-safe trigger for an orderly shutdown."""
        self._shutdown_event.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_shutdown)
            except (NotImplementedError, RuntimeError):
                # Windows / no-loop-signal support: fall back to signal.signal.
                try:
                    signal.signal(sig, lambda *_a: self.request_shutdown())
                except (ValueError, OSError):
                    pass  # e.g. not in main thread — tests drive shutdown directly

    async def run(self) -> int:
        """Full lifecycle: startup → wait for signal → shutdown. Returns exit code."""
        try:
            await self.startup()
        except ConfigError as exc:
            self._logger.error("Configuration error — aborting: %s", exc)
            return EXIT_CONFIG
        except (CameraSetupError, CameraLimitError, VideoSourceError) as exc:
            self._logger.error("Camera setup error — aborting: %s", exc)
            return EXIT_CAMERA
        except PipelineError as exc:
            self._logger.error("Pipeline error — aborting: %s", exc)
            return EXIT_PIPELINE
        except RouterError as exc:
            self._logger.error("Fatal error during startup — aborting: %s", exc)
            return EXIT_PIPELINE

        self._install_signal_handlers()
        await self._shutdown_event.wait()
        await self.shutdown()
        return EXIT_OK


async def main() -> int:
    """Async application entry point — constructs and runs the orchestrator."""
    app = RouterApp()
    return await app.run()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
