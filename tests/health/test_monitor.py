"""Tests for SystemMonitor (Story 3.3).

``psutil`` and the temperature source are mocked — no hardware needed.
"""

from __future__ import annotations

import logging
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

import health.monitor as monitor_mod
from health.monitor import SystemMonitor
from utils.errors import MonitorError

_VM = namedtuple("VM", "percent")
_DU = namedtuple("DU", "percent")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _patch_psutil(
    cpu: float = 10.0,
    memory: float = 20.0,
    disk: float = 30.0,
    boot: float = 1_000_000.0,
):
    """Patch health.monitor.psutil with controllable readings."""
    fake = MagicMock()
    fake.cpu_percent.return_value = cpu
    fake.virtual_memory.return_value = _VM(memory)
    fake.disk_usage.return_value = _DU(disk)
    fake.boot_time.return_value = boot
    # No sensors_temperatures by default → temperature comes from thermal zone.
    fake.sensors_temperatures.return_value = {}
    return patch.object(monitor_mod, "psutil", fake)


def _patch_temp(celsius: float | None):
    """Patch the SystemMonitor temperature reader to return a fixed value."""
    async def _fake_read(self):  # noqa: ANN001
        return celsius

    return patch.object(SystemMonitor, "_read_temperature", _fake_read)


# ── Naming / basic sample ────────────────────────────────────────────────────────

class TestSampling:
    async def test_metric_naming_and_values(self) -> None:
        with _patch_psutil(cpu=12.5, memory=22.0, disk=33.0), _patch_temp(50.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()

        assert snap.cpu_percent == 12.5
        assert snap.memory_percent == 22.0
        assert snap.disk_percent == 33.0
        assert snap.temperature_celsius == 50.0
        assert snap.uptime_seconds >= 0.0
        assert snap.sampled_at.endswith("Z")

    async def test_snapshot_stored_after_sample(self) -> None:
        with _patch_psutil(), _patch_temp(40.0):
            mon = SystemMonitor(interval_s=1)
            assert mon.snapshot() is None
            await mon.sample()
            assert mon.snapshot() is not None
            assert mon.snapshot().cpu_percent == 10.0


# ── Threshold flags ─────────────────────────────────────────────────────────────

class TestThresholdFlags:
    async def test_no_flags_below_thresholds(self) -> None:
        with _patch_psutil(cpu=10, memory=10, disk=10), _patch_temp(40.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert not snap.cpu_alert
        assert not snap.memory_alert
        assert not snap.disk_alert
        assert not snap.temperature_alert
        assert not snap.throttling

    async def test_cpu_alert_set(self) -> None:
        with _patch_psutil(cpu=95), _patch_temp(40.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.cpu_alert is True
        assert mon.cpu_alert is True

    async def test_memory_alert_set(self) -> None:
        with _patch_psutil(memory=90), _patch_temp(40.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.memory_alert is True

    async def test_disk_alert_set(self) -> None:
        with _patch_psutil(disk=88), _patch_temp(40.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.disk_alert is True

    async def test_temperature_alert_at_75(self) -> None:
        with _patch_psutil(), _patch_temp(76.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.temperature_alert is True
        assert snap.throttling is False  # 76 ≤ 80 critical


# ── Critical temperature → throttling + WARNING ─────────────────────────────────

class TestCriticalTemperature:
    async def test_above_80_sets_throttling(self) -> None:
        with _patch_psutil(), _patch_temp(85.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.throttling is True
        assert mon.throttling is True

    async def test_above_80_logs_warning(self, caplog) -> None:
        with _patch_psutil(), _patch_temp(85.0):
            mon = SystemMonitor(interval_s=1)
            with caplog.at_level(logging.WARNING, logger="health.monitor"):
                await mon.sample()
        assert any(
            "temperature critical" in r.message.lower() for r in caplog.records
        )

    async def test_exactly_80_not_throttling(self) -> None:
        """Critical condition is strictly >80 °C."""
        with _patch_psutil(), _patch_temp(80.0):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.throttling is False


# ── Temperature unavailable ─────────────────────────────────────────────────────

class TestTemperatureUnavailable:
    async def test_none_temperature_no_temp_flags(self) -> None:
        with _patch_psutil(), _patch_temp(None):
            mon = SystemMonitor(interval_s=1)
            snap = await mon.sample()
        assert snap.temperature_celsius is None
        assert snap.temperature_alert is False
        assert snap.throttling is False


# ── Robustness: sampling failure ────────────────────────────────────────────────

class TestRobustness:
    async def test_sample_failure_raises_monitor_error(self) -> None:
        fake = MagicMock()
        fake.cpu_percent.side_effect = OSError("psutil boom")
        with patch.object(monitor_mod, "psutil", fake):
            mon = SystemMonitor(interval_s=1)
            with pytest.raises(MonitorError):
                await mon.sample()

    async def test_loop_survives_bad_sample(self, caplog) -> None:
        """A MonitorError inside the loop is logged, not fatal."""
        fake = MagicMock()
        fake.cpu_percent.side_effect = OSError("boom")
        with patch.object(monitor_mod, "psutil", fake):
            mon = SystemMonitor(interval_s=1)
            # start() primes a sample (fails, logged) then runs the loop.
            with caplog.at_level(logging.ERROR, logger="health.monitor"):
                await mon.start()
                await mon.stop()
        assert any("sample failed" in r.message.lower() for r in caplog.records)


# ── Temperature reader (real implementation, mocked sources) ────────────────────

class TestTemperatureReader:
    async def test_reads_from_thermal_zone(self, tmp_path, monkeypatch) -> None:
        zone = tmp_path / "temp"
        zone.write_text("54321\n")  # millidegrees → 54.321 °C
        monkeypatch.setattr(monitor_mod, "_THERMAL_ZONE", zone)

        fake = MagicMock()
        fake.sensors_temperatures.return_value = {}
        with patch.object(monitor_mod, "psutil", fake):
            mon = SystemMonitor(interval_s=1)
            temp = await mon._read_temperature()
        assert temp == pytest.approx(54.321, abs=0.001)

    async def test_reads_from_psutil_sensors(self) -> None:
        Reading = namedtuple("Reading", "current")
        fake = MagicMock()
        fake.sensors_temperatures.return_value = {"cpu_thermal": [Reading(61.0)]}
        with patch.object(monitor_mod, "psutil", fake):
            mon = SystemMonitor(interval_s=1)
            temp = await mon._read_temperature()
        assert temp == 61.0
