"""Tests for GpsReader (Story 6.1). gpsd/NMEA mocked; no hardware."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from health.state import AppState
from health.supabase_client import SupabaseClient
from location.gps import GpsReader, parse_nmea
from platform.board import Board
from tests.fixtures.mock_gps import (
    NMEA_GARBAGE,
    NMEA_GGA_FIX,
    NMEA_GGA_NOFIX,
    NMEA_RMC_FIX,
    stream_of,
)
from utils.errors import SupabaseTransientError

_REAL_SLEEP = asyncio.sleep


def _client() -> MagicMock:
    c = MagicMock(spec=SupabaseClient)
    c.update = AsyncMock(return_value=[{"id": "r1"}])
    return c


async def _wait(predicate, timeout_s: float = 3.0) -> None:
    for _ in range(int(timeout_s / 0.02) + 1):
        if predicate():
            return
        await _REAL_SLEEP(0.02)


# ── NMEA parsing ────────────────────────────────────────────────────────────────

class TestParse:
    def test_valid_gga(self) -> None:
        coord = parse_nmea(NMEA_GGA_FIX)
        assert coord is not None
        assert round(coord["lat"], 3) == 48.117
        assert round(coord["lon"], 3) == 11.517
        assert coord["updated_at"].endswith("Z")
        assert coord["fix_quality"] == 1

    def test_valid_rmc(self) -> None:
        assert parse_nmea(NMEA_RMC_FIX) is not None

    def test_nofix_discarded(self) -> None:
        assert parse_nmea(NMEA_GGA_NOFIX) is None

    def test_garbage_discarded(self) -> None:
        assert parse_nmea(NMEA_GARBAGE) is None


# ── Pro-only activation ─────────────────────────────────────────────────────────

class TestActivation:
    async def test_inert_on_base_rpi4(self) -> None:
        client = _client()
        reader = GpsReader(Board.RPI4, AppState(), client)
        assert reader.active is False
        await reader.start()
        await reader.stop()
        client.update.assert_not_awaited()

    async def test_inert_on_unknown(self) -> None:
        reader = GpsReader(Board.UNKNOWN, AppState(), _client())
        assert reader.active is False
        await reader.start()  # no-op, no task
        assert reader._task is None
        await reader.stop()

    async def test_active_on_pro_rpi5(self) -> None:
        reader = GpsReader(Board.RPI5, AppState(), _client(),
                           stream_factory=stream_of([NMEA_GGA_FIX]))
        assert reader.active is True


# ── Fix handling + persistence ──────────────────────────────────────────────────

class TestFixHandling:
    async def test_valid_fix_persists_and_updates_state(self) -> None:
        state = AppState()
        client = _client()
        reader = GpsReader(Board.RPI5, state, client,
                           stream_factory=stream_of([NMEA_GGA_FIX]))
        await reader.start()
        await _wait(lambda: client.update.await_count >= 1)
        await reader.stop()

        # State carries the coordinate for the health report.
        assert state.gps is not None
        assert round(state.gps["lat"], 2) == 48.12
        # Persisted to routers.location (jsonb) by serial_number.
        args, _ = client.update.call_args
        table, params, patch = args
        assert table == "routers"
        assert params["serial_number"] == "eq.GTR-PRO-1"
        assert "location" in patch and "lat" in patch["location"]

    async def test_no_fix_keeps_last_known(self) -> None:
        state = AppState()
        client = _client()
        # A valid fix, then a no-fix and garbage — last known must survive.
        reader = GpsReader(
            Board.RPI5, state, client,
            stream_factory=stream_of([NMEA_GGA_FIX, NMEA_GGA_NOFIX, NMEA_GARBAGE]),
        )
        await reader.start()
        await _wait(lambda: reader.last_coordinate is not None)
        await _REAL_SLEEP(0.05)  # let the no-fix/garbage lines get processed
        last = reader.last_coordinate
        await reader.stop()

        assert last is not None
        assert round(last["lat"], 2) == 48.12  # not overwritten by null
        assert state.gps is not None

    async def test_no_valid_fix_never_persists(self) -> None:
        client = _client()
        reader = GpsReader(
            Board.RPI5, AppState(), client,
            stream_factory=stream_of([NMEA_GGA_NOFIX, NMEA_GARBAGE]),
        )
        await reader.start()
        await _REAL_SLEEP(0.1)
        await reader.stop()
        client.update.assert_not_awaited()
        assert reader.last_coordinate is None


# ── Degraded Supabase ───────────────────────────────────────────────────────────

class TestDegraded:
    async def test_persist_failure_does_not_crash(self) -> None:
        state = AppState()
        client = MagicMock(spec=SupabaseClient)
        client.update = AsyncMock(side_effect=SupabaseTransientError("supabase down"))
        reader = GpsReader(Board.RPI5, state, client,
                           stream_factory=stream_of([NMEA_GGA_FIX]))
        with patch("utils.retry.asyncio.sleep", new=AsyncMock()):
            await reader.start()
            await _wait(lambda: client.update.await_count >= 1)
            await _REAL_SLEEP(0.05)
            # Still alive, last coord kept despite persist failure.
            still_has_coord = reader.last_coordinate is not None
            await reader.stop()
        assert still_has_coord is True
        assert state.gps is not None
