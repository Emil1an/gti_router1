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

from camera.sources.base import VideoSource
from camera.sources.rtsp_source import RTSPSource
from config.loader import get_config
from health.degraded import log_degraded_mode_status
from health.monitor import SystemMonitor
from health.registration import DeviceRegistration
from health.reporter import HealthReporter
from health.state import AppState
from health.supabase_client import SupabaseClient
from health.watchdog import Watchdog
from upload.service import UploadService
from utils.errors import CameraSetupError, ConfigError, PipelineError, RouterError
from utils.logging import get_logger, setup_logging

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
        self._upload_service: UploadService | None = None
        self._reporter: HealthReporter | None = None
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

        # 10. systemd watchdog (3.5)
        self._watchdog = Watchdog()
        await self._watchdog.start()

        # 11. Degraded-mode / PTZ status (3.6)
        log_degraded_mode_status(self._state, self._logger)

        # 12. READY=1 (single emission point, coordinated with the watchdog)
        self._watchdog.notify_ready()
        self._logger.info("GTI Router initialised — READY")

    def _build_sources(self) -> list[VideoSource]:
        """Construct one VideoSource per configured camera (fail-fast)."""
        assert self._cfg is not None
        sources: list[VideoSource] = []
        for cam in self._cfg.cameras:
            if cam.input_type == "rtsp_ip":
                if not cam.rtsp_url:
                    raise CameraSetupError(f"camera {cam.camera_id}: missing rtsp_url")
                sources.append(RTSPSource(camera_id=cam.camera_id, rtsp_url=cam.rtsp_url))
            else:
                raise CameraSetupError(
                    f"camera {cam.camera_id}: input_type '{cam.input_type}' "
                    "not supported yet"
                )
        if not sources:
            raise CameraSetupError("no cameras configured")
        return sources

    # ── Shutdown (6 steps) ──────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        timeout = float(self._cfg.upload.shutdown_timeout_s) if self._cfg else 30.0
        self._logger.info("GTI Router shutting down", extra={"timeout_s": timeout})

        # 1. Notify systemd + best-effort final health report
        if self._watchdog is not None:
            self._watchdog.notify_stopping()
        await self._emit_final_report()

        # 2. Stop the health reporter
        if self._reporter is not None:
            await self._reporter.stop()

        # 3. Stop the upload subsystem (drain in-flight uploads, persist SQLite)
        if self._upload_service is not None:
            await self._upload_service.stop(drain_timeout_s=timeout)

        # 4. Stop the system monitor
        if self._monitor is not None:
            await self._monitor.stop()

        # 5. Stop device registration
        if self._registration is not None:
            await self._registration.stop()

        # 6. Stop the watchdog
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
        except CameraSetupError as exc:
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
