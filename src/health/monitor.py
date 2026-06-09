"""System resource monitor (Story 3.3).

``SystemMonitor`` periodically samples CPU, RAM, disk and temperature with
``psutil`` — never blocking the event loop (every potentially-blocking call is
offloaded with :func:`asyncio.to_thread`).  It compares each metric against the
configurable thresholds in the ``health`` config block and raises alert flags
that :class:`~health.reporter.HealthReporter` (3.2) consumes without re-sampling.

Temperature
-----------
On a Raspberry Pi the CPU temperature comes from ``psutil.sensors_temperatures``
or, failing that, ``/sys/class/thermal/thermal_zone0/temp``.  A reading above
``temp_critical_threshold`` (>80 °C) logs a WARNING and sets the ``throttling``
flag (NFR3 targets <75 °C sustained; >80 °C is the critical condition).

Robustness
----------
A sampling failure raises a typed :class:`~utils.errors.MonitorError`, which the
loop logs and swallows — one bad sample never tears the monitor down.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psutil

from config.loader import get_config
from utils.errors import MonitorError
from utils.logging import get_logger

# Filesystem whose usage represents the device's main storage.
_DISK_PATH: str = "/"

# RPi thermal sysfs fallback for CPU temperature.
_THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(frozen=True)
class SystemSnapshot:
    """Immutable point-in-time view of host resource usage + alert flags."""

    cpu_percent: float
    memory_percent: float
    disk_percent: float
    temperature_celsius: float | None
    uptime_seconds: float
    sampled_at: str  # ISO-8601 UTC with Z

    cpu_alert: bool = False
    memory_alert: bool = False
    disk_alert: bool = False
    temperature_alert: bool = False
    throttling: bool = False


class SystemMonitor:
    """Service that keeps a fresh :class:`SystemSnapshot` of host resources."""

    def __init__(self, interval_s: int | None = None, disk_path: str | None = None) -> None:
        cfg = get_config()
        self._interval: int = interval_s if interval_s is not None else cfg.health.monitor_interval_s
        self._disk_path: str = disk_path or _DISK_PATH

        self._cpu_threshold: float = cfg.health.cpu_alert_threshold
        self._memory_threshold: float = cfg.health.memory_alert_threshold
        self._disk_threshold: float = cfg.health.disk_alert_threshold
        self._temp_alert_threshold: float = cfg.health.temp_alert_threshold
        self._temp_critical_threshold: float = cfg.health.temp_critical_threshold

        self._snapshot: SystemSnapshot | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._logger = get_logger(__name__)

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Take an initial sample and start the periodic sampling loop."""
        self._running = True
        try:
            await self.sample()  # prime the snapshot so the reporter has data
        except MonitorError as exc:
            self._logger.error("Initial system sample failed: %s", exc)
        self._task = asyncio.create_task(self._loop(), name="system-monitor")
        self._logger.info(
            "SystemMonitor started", extra={"interval_s": self._interval}
        )

    async def stop(self) -> None:
        """Stop the sampling loop."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._logger.info("SystemMonitor stopped")

    # ── State access (consumed by HealthReporter, 3.2) ──────────────────────────

    def snapshot(self) -> SystemSnapshot | None:
        """Return the most recent sample, or ``None`` before the first sample."""
        return self._snapshot

    @property
    def throttling(self) -> bool:
        return self._snapshot.throttling if self._snapshot else False

    @property
    def cpu_alert(self) -> bool:
        return self._snapshot.cpu_alert if self._snapshot else False

    @property
    def memory_alert(self) -> bool:
        return self._snapshot.memory_alert if self._snapshot else False

    @property
    def disk_alert(self) -> bool:
        return self._snapshot.disk_alert if self._snapshot else False

    @property
    def temperature_alert(self) -> bool:
        return self._snapshot.temperature_alert if self._snapshot else False

    # ── Sampling ────────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                try:
                    await self.sample()
                except MonitorError as exc:
                    # A bad sample must not kill the monitor.
                    self._logger.error("System sample failed: %s", exc)
        except asyncio.CancelledError:
            pass

    async def sample(self) -> SystemSnapshot:
        """Take one resource sample, update flags, and store the snapshot.

        Raises:
            MonitorError: if a ``psutil`` read fails.
        """
        try:
            cpu = await asyncio.to_thread(psutil.cpu_percent, None)
            vm = await asyncio.to_thread(psutil.virtual_memory)
            du = await asyncio.to_thread(psutil.disk_usage, self._disk_path)
            boot = await asyncio.to_thread(psutil.boot_time)
        except Exception as exc:  # psutil raises a variety of OS errors
            raise MonitorError(f"Failed to read system metrics: {exc}") from exc

        temperature = await self._read_temperature()

        cpu_percent = float(cpu)
        memory_percent = float(vm.percent)
        disk_percent = float(du.percent)
        uptime_seconds = max(0.0, time.time() - float(boot))

        cpu_alert = cpu_percent >= self._cpu_threshold
        memory_alert = memory_percent >= self._memory_threshold
        disk_alert = disk_percent >= self._disk_threshold
        temperature_alert = (
            temperature is not None and temperature >= self._temp_alert_threshold
        )
        throttling = (
            temperature is not None and temperature > self._temp_critical_threshold
        )

        snapshot = SystemSnapshot(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            temperature_celsius=temperature,
            uptime_seconds=uptime_seconds,
            sampled_at=_utc_now_iso(),
            cpu_alert=cpu_alert,
            memory_alert=memory_alert,
            disk_alert=disk_alert,
            temperature_alert=temperature_alert,
            throttling=throttling,
        )
        self._snapshot = snapshot

        if throttling:
            self._logger.warning(
                "CPU temperature critical — throttling condition",
                extra={
                    "temperature_celsius": temperature,
                    "temp_critical_threshold": self._temp_critical_threshold,
                },
            )
        if cpu_alert or memory_alert or disk_alert:
            self._logger.warning(
                "Resource threshold exceeded",
                extra={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_percent,
                    "disk_percent": disk_percent,
                },
            )
        return snapshot

    async def _read_temperature(self) -> float | None:
        """Read CPU temperature (°C), or ``None`` when unavailable."""

        def _read() -> float | None:
            # 1. psutil sensors (Linux servers / some RPi setups)
            getter = getattr(psutil, "sensors_temperatures", None)
            if getter is not None:
                try:
                    temps = getter()
                except Exception:
                    temps = {}
                for key in ("cpu_thermal", "coretemp", "cpu-thermal", "soc_thermal"):
                    entries = temps.get(key)
                    if entries:
                        return float(entries[0].current)
                for entries in temps.values():
                    if entries:
                        return float(entries[0].current)
            # 2. RPi thermal sysfs (millidegrees Celsius)
            if _THERMAL_ZONE.exists():
                try:
                    return int(_THERMAL_ZONE.read_text().strip()) / 1000.0
                except (OSError, ValueError):
                    return None
            return None

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:
            # Temperature is best-effort — never fail the whole sample for it.
            self._logger.debug("Temperature read failed: %s", exc)
            return None
