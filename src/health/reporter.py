"""Periodic health reporter (Story 3.2).

``HealthReporter`` composes a health report every ``health.report_interval_s``
seconds (default 60 s) and inserts it into the ``router_health`` table via the
shared :class:`~health.supabase_client.SupabaseClient` (service_role).

It **consumes** the latest :class:`~health.monitor.SystemMonitor` snapshot — it
never re-samples ``psutil`` — and reads app-level state (connectivity, upload
queue, GPS, per-camera status) from :class:`~health.state.AppState`.

Degraded mode (local 1 h queue)
-------------------------------
If Supabase is unreachable, reports are buffered locally (FIFO, capped at
``health.local_queue_max_age_s`` = 1 h; the oldest are evicted past the cap) and
flushed in a **single batch** on the next successful insert, preserving temporal
order.  Sending never blocks the event loop and uses the single ``@with_retry``
for transient errors; permanent errors are logged (typed) and dropped.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from config.loader import get_config
from health.degraded import LocalHealthQueue
from health.monitor import SystemMonitor
from health.state import AppState
from health.supabase_client import SupabaseClient
from utils.errors import SupabasePermanentError, SupabaseTransientError
from utils.logging import get_logger
from utils.retry import with_retry

_TABLE = "router_health"
_DEFAULT_MAX_RETRIES: int = 10


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class HealthReporter:
    """Service that periodically inserts a ``router_health`` row."""

    def __init__(
        self,
        client: SupabaseClient,
        monitor: SystemMonitor,
        state: AppState,
        interval_s: int | None = None,
        max_age_s: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        cfg = get_config()
        self._client = client
        self._monitor = monitor
        self._state = state
        self._interval = interval_s if interval_s is not None else cfg.health.report_interval_s
        self._max_age = max_age_s if max_age_s is not None else cfg.health.local_queue_max_age_s
        self._max_retries = max_retries if max_retries is not None else _DEFAULT_MAX_RETRIES

        # Shared degraded-mode buffer (Story 3.6): FIFO with a 1 h time cap.
        self._local = LocalHealthQueue(self._max_age)
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._logger = get_logger(__name__)

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the periodic reporting loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="health-reporter")
        self._logger.info("HealthReporter started", extra={"interval_s": self._interval})

    async def stop(self) -> None:
        """Stop the reporting loop."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._logger.info(
            "HealthReporter stopped", extra={"local_queue_size": len(self._local)}
        )

    # ── Reporting loop ───────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    await asyncio.sleep(self._interval)
                except asyncio.CancelledError:
                    raise
                if not self._running:
                    break
                await self.report_once()
        except asyncio.CancelledError:
            pass

    async def report_once(self) -> None:
        """Compose and send one report; buffer locally on transient failure.

        On a successful send any locally-queued reports are flushed in the same
        batch (oldest first), so a reconnection drains the 1 h backlog at once.
        """
        report = self._compose_report()
        batch = self._local.snapshot() + [report]

        try:
            await self._send_batch(batch)
        except SupabasePermanentError as exc:
            # 4xx — drop the current report (do not retry); keep the queue intact.
            self._logger.error("Health insert permanently failed (dropped): %s", exc)
            return
        except SupabaseTransientError as exc:
            # Network/5xx — buffer the current report for the next reconnect.
            self._state.supabase_connected = False
            self._local.append(report)
            self._logger.warning(
                "Health insert failed — buffered locally (queue=%d): %s",
                len(self._local),
                exc,
            )
            return

        # Success: the whole batch (queued + current) landed.
        if len(self._local):
            self._logger.info(
                "Flushed buffered health reports", extra={"count": len(self._local)}
            )
        self._local.clear()
        self._state.supabase_connected = True

    # ── Sending ──────────────────────────────────────────────────────────────────

    async def _send_batch(self, reports: list[dict[str, Any]]) -> None:
        """Insert a batch of reports through the single ``@with_retry``."""

        async def _insert() -> None:
            await self._client.insert_batch(_TABLE, reports)

        wrapped = with_retry(
            max_retries=self._max_retries,
            retryable=(SupabaseTransientError,),
        )(_insert)
        await wrapped()

    # ── Report composition ───────────────────────────────────────────────────────

    def _compose_report(self) -> dict[str, Any]:
        """Build the ``router_health`` payload (snake_case, ISO-8601 Z)."""
        snap = self._monitor.snapshot() if self._monitor is not None else None
        s = self._state

        per_camera = [cam.as_dict() for cam in s.per_camera.values()]

        return {
            "router_id": s.router_id,
            # System metrics (from SystemMonitor — not re-sampled here)
            "cpu_percent": snap.cpu_percent if snap else None,
            "memory_percent": snap.memory_percent if snap else None,
            "disk_percent": snap.disk_percent if snap else None,
            "temperature_celsius": snap.temperature_celsius if snap else None,
            "uptime_seconds": snap.uptime_seconds if snap else None,
            # Connectivity flags
            "connectivity": {
                "rtsp": s.rtsp_connected,
                "s3": s.s3_connected,
                "supabase": s.supabase_connected,
            },
            # Upload subsystem state
            "upload_queue": {
                "size": s.upload_queue_size,
                "pending": s.upload_pending,
                "success_count": s.upload_success_count,
                "error_count": s.upload_error_count,
            },
            # GPS (jsonb) — last known coordinate or null
            "gps": s.gps,
            # Fixed per-camera health contract
            "per_camera": per_camera,
            # Service alert flags
            "services_status": {
                "cpu_alert": snap.cpu_alert if snap else False,
                "memory_alert": snap.memory_alert if snap else False,
                "disk_alert": snap.disk_alert if snap else False,
                "temperature_alert": snap.temperature_alert if snap else False,
                "throttling": snap.throttling if snap else False,
            },
            "reported_at": _utc_now_iso(),
        }

    # ── Local queue housekeeping ─────────────────────────────────────────────────

    def _evict_old(self) -> None:
        """Drop locally-buffered reports older than the 1 h cap (FIFO)."""
        self._local.evict_old()

    @property
    def local_queue_size(self) -> int:
        """Number of reports currently buffered for the next reconnect."""
        return len(self._local)
