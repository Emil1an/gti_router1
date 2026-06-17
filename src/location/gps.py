"""GPS reader — gpsd + pynmea2, persists to ``routers.location`` (Story 6.1).

**Pro-only:** GPS hardware exists on the RPi5 (Pro) variant. On Base (RPi4) or
x86/dev the reader stays **inert** (``start()`` is a no-op) — never an error.

When active it connects to **gpsd** (NMEA mode), parses sentences with
**pynmea2**, and on a valid fix:
* keeps the last known coordinate in memory (never overwritten by an invalid
  reading / ``null``),
* publishes it to :class:`~health.state.AppState` so the Health Reporter's
  ``gps`` block carries it (Story 3.2),
* persists ``{lat, lon, …, updated_at}`` to the ``routers.location`` jsonb column
  via Supabase (``service_role``), throttled, non-blocking, under ``@with_retry``.

Privacy (NFR14): the exact coordinate is **never** logged at INFO; writes use
``service_role`` and read-protection is the Epic 0 RLS. ``pynmea2`` is imported
defensively so the module loads even when it's absent (tests mock the stream).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from config.loader import get_config
from health.state import AppState
from health.supabase_client import SupabaseClient
from utils.errors import SupabaseError, SupabaseTransientError
from utils.logging import get_logger
from utils.retry import with_retry

try:  # pynmea2 is light/pure-python but guarded for portability
    import pynmea2  # type: ignore[import-untyped]
except Exception:  # noqa: BLE001
    pynmea2 = None  # type: ignore[assignment]

_PERSIST_MAX_RETRIES = 5


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_nmea(line: str) -> dict[str, Any] | None:
    """Parse one NMEA sentence into a coordinate dict, or ``None`` if invalid.

    Only sentences carrying a **valid fix** (GGA quality > 0, or RMC status 'A')
    with non-zero lat/lon are accepted.
    """
    if pynmea2 is None:
        return None
    try:
        msg = pynmea2.parse(line.strip())
    except Exception:  # noqa: BLE001 — pynmea2.ParseError and friends
        return None

    lat = getattr(msg, "latitude", None)
    lon = getattr(msg, "longitude", None)
    if lat is None or lon is None:
        return None
    if lat == 0.0 and lon == 0.0:
        return None  # no fix

    sentence_type = getattr(msg, "sentence_type", "")
    if sentence_type == "GGA":
        try:
            if int(getattr(msg, "gps_qual", 0) or 0) == 0:
                return None
        except (TypeError, ValueError):
            return None
    elif sentence_type == "RMC":
        if getattr(msg, "status", "V") != "A":
            return None

    coord: dict[str, Any] = {
        "lat": float(lat),
        "lon": float(lon),
        "updated_at": _utc_now_iso(),
    }
    altitude = getattr(msg, "altitude", None)
    if altitude not in (None, ""):
        try:
            coord["altitude"] = float(altitude)
        except (TypeError, ValueError):
            pass
    qual = getattr(msg, "gps_qual", None)
    if qual not in (None, ""):
        try:
            coord["fix_quality"] = int(qual)
        except (TypeError, ValueError):
            pass
    return coord


class GpsReader:
    """Reads GPS from gpsd (Pro only) and persists to ``routers.location``."""

    def __init__(
        self,
        board: Any,
        state: AppState,
        client: SupabaseClient | None = None,
        stream_factory: Callable[[], AsyncIterator[str]] | None = None,
    ) -> None:
        cfg = get_config()
        self._board = board
        self._state = state
        self._client = client if client is not None else SupabaseClient()
        self._gps_cfg = cfg.gps
        self._serial = cfg.device.serial_number
        self._persist_interval = cfg.gps.persist_interval_s
        self._stream_factory = stream_factory

        board_value = getattr(board, "value", str(board))
        # GPS is Pro-only (RPi5). Anything else stays inert.
        self._active = bool(cfg.gps.enabled) and board_value == "rpi5"

        self._last_coord: dict[str, Any] | None = None
        self._last_persist_monotonic = 0.0
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._persist_tasks: set[asyncio.Task[None]] = set()
        self._logger = get_logger(__name__)

    # ── State ────────────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def last_coordinate(self) -> dict[str, Any] | None:
        """Last known valid coordinate (kept across signal loss)."""
        return self._last_coord

    # ── Lifecycle ─────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start GPS reading — inert no-op on non-Pro hardware."""
        board_value = getattr(self._board, "value", str(self._board))
        if not self._active:
            self._logger.info(
                "GPS inactive — Pro-only (no GPS on board=%s)", board_value
            )
            return
        if pynmea2 is None:
            self._logger.warning("pynmea2 not installed — GPS disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="gps-reader")
        self._logger.info("GpsReader started", extra={"gpsd": f"{self._gps_cfg.host}:{self._gps_cfg.port}"})

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        for task in list(self._persist_tasks):
            if not task.done():
                task.cancel()
        self._persist_tasks.clear()
        self._logger.info("GpsReader stopped")

    # ── Read loop ─────────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    stream = (
                        self._stream_factory()
                        if self._stream_factory is not None
                        else self._gpsd_stream()
                    )
                    async for line in stream:
                        if not self._running:
                            break
                        self._handle_sentence(line)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # gpsd dropped / read error → reconnect
                    self._logger.warning("GPS stream error (will retry): %s", exc)
                if self._running:
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    def _handle_sentence(self, line: str) -> None:
        coord = parse_nmea(line)
        if coord is None:
            # Invalid / no fix → keep the last known coordinate (NFR: never null).
            self._logger.warning("Discarded invalid/no-fix GPS sentence")
            return
        self._last_coord = coord
        self._state.gps = coord
        # Never log the exact coordinate at INFO (NFR14).
        self._logger.info("GPS fix acquired")

        now = time.monotonic()
        if now - self._last_persist_monotonic >= self._persist_interval:
            self._last_persist_monotonic = now
            task = asyncio.create_task(self._persist(coord))
            self._persist_tasks.add(task)
            task.add_done_callback(self._persist_tasks.discard)

    async def _persist(self, coord: dict[str, Any]) -> None:
        """Persist the coordinate to ``routers.location`` (non-blocking, retry)."""
        payload = {k: coord[k] for k in ("lat", "lon", "updated_at") if k in coord}
        for opt in ("altitude", "fix_quality"):
            if opt in coord:
                payload[opt] = coord[opt]

        async def _do() -> list[dict[str, Any]]:
            return await self._client.update(
                "routers", {"serial_number": f"eq.{self._serial}"}, {"location": payload}
            )

        wrapped = with_retry(
            max_retries=_PERSIST_MAX_RETRIES, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            await wrapped()
            self._logger.debug("routers.location updated")
        except SupabaseError as exc:
            # Degraded: keep the coordinate in memory; the health report still
            # carries it. Do not crash.
            self._logger.warning("Could not persist GPS location (deferred): %s", exc)

    # ── Default gpsd stream ──────────────────────────────────────────────────────

    async def _gpsd_stream(self) -> AsyncIterator[str]:
        """Connect to gpsd in NMEA mode and yield ``$...`` sentences."""
        reader, writer = await asyncio.open_connection(
            self._gps_cfg.host, self._gps_cfg.port
        )
        writer.write(b'?WATCH={"enable":true,"nmea":true}\n')
        await writer.drain()
        try:
            while self._running:
                raw = await asyncio.wait_for(
                    reader.readline(), timeout=self._gps_cfg.read_timeout_s
                )
                if not raw:
                    break
                text = raw.decode("ascii", errors="replace").strip()
                if text.startswith("$"):
                    yield text
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
