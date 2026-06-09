"""Tests for HealthReporter (Story 3.2).

The Supabase client and the SystemMonitor are mocked; the loop clock is driven
with a short interval — no real network, hardware, or 60 s waits.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from health.monitor import SystemSnapshot
from health.reporter import HealthReporter
from health.state import AppState, CameraState
from health.supabase_client import SupabaseClient
from utils.errors import SupabasePermanentError, SupabaseTransientError

# Real sleep captured before any global patching of asyncio.sleep.
_REAL_SLEEP = asyncio.sleep


# ── Helpers ────────────────────────────────────────────────────────────────────

def _snapshot(**over) -> SystemSnapshot:
    base = dict(
        cpu_percent=15.0,
        memory_percent=40.0,
        disk_percent=55.0,
        temperature_celsius=62.0,
        uptime_seconds=3600.0,
        sampled_at="2026-06-08T10:00:00.000Z",
        cpu_alert=False,
        memory_alert=False,
        disk_alert=False,
        temperature_alert=False,
        throttling=False,
    )
    base.update(over)
    return SystemSnapshot(**base)


def _monitor(snap: SystemSnapshot | None = None) -> SystemMonitorStub:
    mon = MagicMock()
    mon.snapshot.return_value = snap if snap is not None else _snapshot()
    return mon


# A typing alias for readability only.
SystemMonitorStub = MagicMock


def _state_with_camera() -> AppState:
    s = AppState()
    s.router_id = "router-xyz"
    s.rtsp_connected = True
    s.s3_connected = True
    s.supabase_connected = True
    s.upload_queue_size = 3
    s.upload_pending = 2
    s.upload_success_count = 100
    s.upload_error_count = 1
    s.gps = {"lat": 40.0, "lon": -3.0}
    s.set_camera(
        CameraState(
            camera_id="cam-test",
            input_type="rtsp_ip",
            connected=True,
            streaming=True,
            last_segment_at="2026-06-08T10:00:00.000Z",
            error=None,
        )
    )
    return s


def _client() -> SupabaseClient:
    client = MagicMock(spec=SupabaseClient)
    client.insert_batch = AsyncMock(return_value=[{"id": "h1"}])
    return client


async def _wait_until(predicate, timeout_s: float = 2.0) -> None:
    deadline = int(timeout_s / 0.02) + 1
    for _ in range(deadline):
        if predicate():
            return
        await _REAL_SLEEP(0.02)


# ── Report composition ───────────────────────────────────────────────────────────

class TestComposeReport:
    def test_report_has_required_fields(self) -> None:
        rep = HealthReporter(_client(), _monitor(), _state_with_camera())
        report = rep._compose_report()

        assert report["router_id"] == "router-xyz"
        assert report["cpu_percent"] == 15.0
        assert report["memory_percent"] == 40.0
        assert report["disk_percent"] == 55.0
        assert report["temperature_celsius"] == 62.0
        assert report["uptime_seconds"] == 3600.0
        assert report["reported_at"].endswith("Z")

    def test_report_includes_connectivity(self) -> None:
        rep = HealthReporter(_client(), _monitor(), _state_with_camera())
        conn = rep._compose_report()["connectivity"]
        assert conn == {"rtsp": True, "s3": True, "supabase": True}

    def test_report_includes_upload_queue(self) -> None:
        rep = HealthReporter(_client(), _monitor(), _state_with_camera())
        uq = rep._compose_report()["upload_queue"]
        assert uq["size"] == 3
        assert uq["pending"] == 2
        assert uq["success_count"] == 100
        assert uq["error_count"] == 1

    def test_report_includes_gps(self) -> None:
        rep = HealthReporter(_client(), _monitor(), _state_with_camera())
        assert rep._compose_report()["gps"] == {"lat": 40.0, "lon": -3.0}

    def test_report_includes_per_camera_contract(self) -> None:
        rep = HealthReporter(_client(), _monitor(), _state_with_camera())
        per_camera = rep._compose_report()["per_camera"]
        assert len(per_camera) == 1
        cam = per_camera[0]
        assert set(cam.keys()) == {
            "camera_id", "input_type", "connected", "streaming",
            "last_segment_at", "error",
        }
        assert cam["camera_id"] == "cam-test"
        assert cam["input_type"] == "rtsp_ip"

    def test_report_services_status_from_snapshot(self) -> None:
        snap = _snapshot(throttling=True, cpu_alert=True)
        rep = HealthReporter(_client(), _monitor(snap), _state_with_camera())
        status = rep._compose_report()["services_status"]
        assert status["throttling"] is True
        assert status["cpu_alert"] is True

    def test_report_handles_missing_snapshot(self) -> None:
        mon = MagicMock()
        mon.snapshot.return_value = None
        rep = HealthReporter(_client(), mon, _state_with_camera())
        report = rep._compose_report()
        assert report["cpu_percent"] is None
        assert report["services_status"]["throttling"] is False


# ── Insert into router_health ────────────────────────────────────────────────────

class TestInsert:
    async def test_report_once_inserts_into_router_health(self) -> None:
        client = _client()
        rep = HealthReporter(client, _monitor(), _state_with_camera())
        await rep.report_once()

        client.insert_batch.assert_awaited_once()
        args, _ = client.insert_batch.call_args
        assert args[0] == "router_health"
        assert isinstance(args[1], list) and len(args[1]) == 1

    async def test_periodic_loop_reports(self) -> None:
        """The loop must insert repeatedly at the configured interval."""
        client = _client()
        rep = HealthReporter(
            client, _monitor(), _state_with_camera(), interval_s=1
        )
        # Drive the interval to ~20ms so several ticks happen quickly.
        rep._interval = 0.02
        await rep.start()
        await _wait_until(lambda: client.insert_batch.await_count >= 3, timeout_s=3.0)
        await rep.stop()
        assert client.insert_batch.await_count >= 3


# ── Degraded mode: local 1h queue + batch flush ─────────────────────────────────

class TestDegradedMode:
    async def test_transient_failure_buffers_locally(self) -> None:
        client = _client()
        client.insert_batch = AsyncMock(side_effect=SupabaseTransientError("down"))
        rep = HealthReporter(
            client, _monitor(), _state_with_camera(), max_retries=0
        )
        await rep.report_once()

        assert rep.local_queue_size == 1
        assert rep._state.supabase_connected is False

    async def test_batch_flush_on_reconnect(self) -> None:
        """Buffered reports are flushed in one batch on the next success."""
        client = _client()
        # First two ticks fail (transient), third succeeds.
        client.insert_batch = AsyncMock(
            side_effect=[
                SupabaseTransientError("down"),
                SupabaseTransientError("down"),
                [{"id": "ok"}],
            ]
        )
        rep = HealthReporter(
            client, _monitor(), _state_with_camera(), max_retries=0
        )

        await rep.report_once()  # fail → queue=1
        await rep.report_once()  # fail → queue=2
        assert rep.local_queue_size == 2

        await rep.report_once()  # success → flush queued(2) + current(1) = 3 rows

        # The successful call carried all 3 reports in one batch.
        last_args, _ = client.insert_batch.call_args
        assert len(last_args[1]) == 3
        assert rep.local_queue_size == 0
        assert rep._state.supabase_connected is True

    async def test_old_reports_evicted_past_cap(self) -> None:
        """Reports older than the 1h cap are dropped (FIFO)."""
        client = _client()
        client.insert_batch = AsyncMock(side_effect=SupabaseTransientError("down"))
        rep = HealthReporter(
            client, _monitor(), _state_with_camera(), max_age_s=0.05, max_retries=0
        )
        await rep.report_once()
        assert rep.local_queue_size == 1
        await _REAL_SLEEP(0.08)  # let the buffered report age past the cap
        rep._evict_old()
        assert rep.local_queue_size == 0

    async def test_permanent_error_dropped_not_queued(self) -> None:
        client = _client()
        client.insert_batch = AsyncMock(
            side_effect=SupabasePermanentError("400 bad column")
        )
        rep = HealthReporter(
            client, _monitor(), _state_with_camera(), max_retries=0
        )
        await rep.report_once()
        # Permanent → dropped, not buffered.
        assert rep.local_queue_size == 0


# ── Stop ─────────────────────────────────────────────────────────────────────────

class TestStop:
    async def test_stop_is_idempotent(self) -> None:
        rep = HealthReporter(_client(), _monitor(), _state_with_camera())
        rep._interval = 0.02
        await rep.start()
        await rep.stop()
        await rep.stop()
