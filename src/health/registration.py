"""Device registration in Supabase (Story 3.1).

``DeviceRegistration`` upserts this Router into the ``routers`` table on
startup, keyed on ``serial_number`` (a UNIQUE column, so a Router is never
duplicated).  The linked ``gateway_id`` and the resulting ``router_id`` are
cached in :class:`~health.state.AppState` for later subsystems (PTZ in Epic 4,
the Health Reporter in 3.2) to reuse without re-querying.

Non-blocking degraded mode
--------------------------
Registration must **never** block Router startup (capture/upload keep running).
``start()`` spawns a background task that:

1. attempts the upsert through the single ``@with_retry`` (backoff 1→60 s +
   jitter, transient errors only), and
2. on retry exhaustion, reschedules itself in the background (the ``main()``
   coroutine is never aborted).

A permanent error (4xx validation/constraint/auth) is **not** retried — it is
logged as a typed ERROR and the loop stops.  ``supabase_connected`` reflects the
current state throughout.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from config.loader import get_config
from health.state import AppState
from health.supabase_client import SupabaseClient
from utils.errors import SupabasePermanentError, SupabaseTransientError
from utils.logging import get_logger
from utils.retry import with_retry

# Background reschedule interval after @with_retry is exhausted (seconds).
# Patchable in tests.
_REGISTRATION_RETRY_INTERVAL_S: float = 60.0

# Additional upsert attempts for transient errors (besides the first call).
_DEFAULT_MAX_RETRIES: int = 10


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class DeviceRegistration:
    """Service that upserts this Router into ``routers`` and caches identity."""

    def __init__(
        self,
        client: SupabaseClient | None = None,
        state: AppState | None = None,
        max_retries: int | None = None,
    ) -> None:
        cfg = get_config()
        self._client = client if client is not None else SupabaseClient()
        self._state = state if state is not None else AppState()
        self._device = cfg.device
        self._max_retries = max_retries if max_retries is not None else _DEFAULT_MAX_RETRIES

        self._gateway_id: str | None = self._device.gateway_id
        self._router_id: str | None = None
        self._supabase_connected: bool = False

        self._task: asyncio.Task[None] | None = None
        self._stopped: bool = False
        self._logger = get_logger(__name__)

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the (non-blocking) background registration task and return."""
        self._stopped = False
        self._task = asyncio.create_task(
            self._registration_loop(), name="device-registration"
        )
        self._logger.info(
            "DeviceRegistration started (non-blocking)",
            extra={"serial_number": self._device.serial_number},
        )

    async def stop(self) -> None:
        """Stop the background registration task."""
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._logger.info("DeviceRegistration stopped")

    # ── Cached identity (consumed by PTZ / HealthReporter) ──────────────────────

    @property
    def gateway_id(self) -> str | None:
        return self._gateway_id

    @property
    def router_id(self) -> str | None:
        return self._router_id

    @property
    def supabase_connected(self) -> bool:
        return self._supabase_connected

    # ── Background registration ──────────────────────────────────────────────────

    async def _registration_loop(self) -> None:
        """Keep trying to register until success, a permanent error, or stop."""
        try:
            while not self._stopped:
                try:
                    await self._register_with_retry()
                except SupabasePermanentError as exc:
                    # 4xx — do NOT retry; surface as typed ERROR and give up.
                    self._supabase_connected = False
                    self._state.supabase_connected = False
                    self._logger.error(
                        "Device registration permanently failed (not retried): %s", exc
                    )
                    return
                except SupabaseTransientError as exc:
                    # Retries exhausted — reschedule in the background.
                    self._supabase_connected = False
                    self._state.supabase_connected = False
                    self._logger.warning(
                        "Device registration failed after retries — rescheduling: %s",
                        exc,
                    )
                    try:
                        await asyncio.sleep(_REGISTRATION_RETRY_INTERVAL_S)
                    except asyncio.CancelledError:
                        raise
                    continue
                else:
                    return  # success
        except asyncio.CancelledError:
            pass

    async def _register_with_retry(self) -> None:
        """Run the upsert through the single ``@with_retry`` (transient only)."""
        wrapped = with_retry(
            max_retries=self._max_retries,
            retryable=(SupabaseTransientError,),
        )(self._upsert_once)
        await wrapped()

    async def _upsert_once(self) -> None:
        """Perform one upsert into ``routers`` on conflict ``serial_number``.

        The Router never writes ``user_id`` (that is set by the user's claim).
        """
        payload = {
            "serial_number": self._device.serial_number,
            "name": self._device.name,
            "gateway_id": self._device.gateway_id,
            "firmware_version": self._device.firmware_version,
            "last_seen_at": _utc_now_iso(),
        }
        rows = await self._client.upsert(
            "routers", payload, on_conflict="serial_number"
        )

        if rows:
            row = rows[0]
            self._router_id = row.get("id", self._router_id)
            # Prefer the gateway_id the DB returns (may be set by the claim).
            self._gateway_id = row.get("gateway_id") or self._device.gateway_id

        self._supabase_connected = True
        self._state.router_id = self._router_id
        self._state.gateway_id = self._gateway_id
        self._state.supabase_connected = True

        self._logger.info(
            "Router registered in Supabase",
            extra={
                "serial_number": self._device.serial_number,
                "router_id": self._router_id,
                "gateway_id": self._gateway_id,
            },
        )
