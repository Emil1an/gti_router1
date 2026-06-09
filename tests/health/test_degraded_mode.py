"""Tests for the shared degraded-mode mechanism (Story 3.6).

Covers the consolidated local 1 h FIFO queue, the supabase_connected flag
transition, batch drain on reconnect, and PTZ-inactive-without-gateway_id.
No real network.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock

from health.degraded import LocalHealthQueue, log_degraded_mode_status, ptz_available
from health.monitor import SystemSnapshot
from health.reporter import HealthReporter
from health.state import AppState
from health.supabase_client import SupabaseClient
from utils.errors import SupabaseTransientError


# ── LocalHealthQueue ─────────────────────────────────────────────────────────────

class TestLocalHealthQueue:
    def test_append_and_len(self) -> None:
        q = LocalHealthQueue(max_age_s=3600)
        q.append({"a": 1})
        q.append({"a": 2})
        assert len(q) == 2

    def test_snapshot_preserves_order_without_clearing(self) -> None:
        q = LocalHealthQueue(max_age_s=3600)
        q.append({"n": 1})
        q.append({"n": 2})
        snap = q.snapshot()
        assert [p["n"] for p in snap] == [1, 2]
        assert len(q) == 2  # snapshot does not clear

    def test_clear(self) -> None:
        q = LocalHealthQueue(max_age_s=3600)
        q.append({"n": 1})
        q.clear()
        assert len(q) == 0

    def test_evicts_entries_older_than_cap(self) -> None:
        q = LocalHealthQueue(max_age_s=0.05)
        q.append({"old": True})
        assert len(q) == 1
        time.sleep(0.08)
        evicted = q.evict_old()
        assert evicted == 1
        assert len(q) == 0

    def test_fifo_eviction_keeps_newest(self) -> None:
        q = LocalHealthQueue(max_age_s=0.05)
        q.append({"n": "old"})
        time.sleep(0.08)
        q.append({"n": "new"})  # append() also evicts the stale one
        assert len(q) == 1
        assert q.snapshot()[0]["n"] == "new"


# ── ptz_available + degraded-mode logging ───────────────────────────────────────

class TestPtzAvailability:
    def test_ptz_inactive_without_gateway_id(self) -> None:
        state = AppState()
        assert state.gateway_id is None
        assert ptz_available(state) is False

    def test_ptz_active_with_gateway_id(self) -> None:
        state = AppState(gateway_id="gw-123")
        assert ptz_available(state) is True

    def test_degraded_status_logs_ptz_inactive(self, caplog) -> None:
        state = AppState()  # no gateway_id, not connected
        logger = logging.getLogger("health.degraded")
        with caplog.at_level(logging.INFO, logger="health.degraded"):
            log_degraded_mode_status(state, logger)
        msgs = " ".join(r.message for r in caplog.records)
        assert "PTZ control inactive" in msgs

    def test_no_ptz_log_when_gateway_present(self, caplog) -> None:
        state = AppState(gateway_id="gw-1", supabase_connected=True)
        logger = logging.getLogger("health.degraded")
        with caplog.at_level(logging.INFO, logger="health.degraded"):
            log_degraded_mode_status(state, logger)
        msgs = " ".join(r.message for r in caplog.records)
        assert "PTZ control inactive" not in msgs


# ── Reporter degraded behaviour (uses the shared queue) ─────────────────────────

def _snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        cpu_percent=10.0, memory_percent=20.0, disk_percent=30.0,
        temperature_celsius=45.0, uptime_seconds=100.0,
        sampled_at="2026-06-08T10:00:00.000Z",
    )


def _monitor() -> MagicMock:
    m = MagicMock()
    m.snapshot.return_value = _snapshot()
    return m


def _state() -> AppState:
    s = AppState()
    s.router_id = "router-1"
    return s


class TestReporterDegradedMode:
    async def test_supabase_down_buffers_and_flag_false(self) -> None:
        client = MagicMock(spec=SupabaseClient)
        client.insert_batch = AsyncMock(side_effect=SupabaseTransientError("down"))
        state = _state()
        rep = HealthReporter(client, _monitor(), state, max_retries=0)

        await rep.report_once()

        assert rep.local_queue_size == 1
        assert state.supabase_connected is False

    async def test_flag_true_and_drain_on_reconnect(self) -> None:
        client = MagicMock(spec=SupabaseClient)
        client.insert_batch = AsyncMock(
            side_effect=[SupabaseTransientError("down"), [{"id": "ok"}]]
        )
        state = _state()
        rep = HealthReporter(client, _monitor(), state, max_retries=0)

        await rep.report_once()  # down → buffered, flag False
        assert state.supabase_connected is False
        assert rep.local_queue_size == 1

        await rep.report_once()  # reconnect → batch of 2, drained, flag True
        last_args, _ = client.insert_batch.call_args
        assert len(last_args[1]) == 2
        assert rep.local_queue_size == 0
        assert state.supabase_connected is True

    async def test_capture_upload_unaffected_by_supabase(self) -> None:
        """A failing reporter must never raise — capture/upload keep running."""
        client = MagicMock(spec=SupabaseClient)
        client.insert_batch = AsyncMock(side_effect=SupabaseTransientError("down"))
        rep = HealthReporter(client, _monitor(), _state(), max_retries=0)
        # report_once swallows the failure (buffers) and returns normally.
        await rep.report_once()  # must not raise
