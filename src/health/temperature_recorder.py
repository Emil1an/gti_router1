"""CPU temperature history recorder.

Samples the CPU temperature once per ``interval_s`` and appends it to a rolling,
fixed-size in-memory deque on :class:`~health.state.AppState`. It does NOT read
hardware directly — it consumes the snapshot already produced non-blockingly by
:class:`~health.monitor.SystemMonitor`, so the loop only reads an in-memory value
and sleeps. History is memory-only (Zero-Disk-Write); it is lost on reboot, which
is acceptable because critical temperature data is already reported to Supabase by
:class:`~health.reporter.HealthReporter`.
"""

from __future__ import annotations

import asyncio
from collections import deque

from health.monitor import SystemMonitor
from health.state import AppState
from utils.logging import get_logger


class TemperatureRecorder:
    """Periodically records the current CPU temperature into a rolling history."""

    def __init__(
        self,
        monitor: SystemMonitor,
        state: AppState,
        interval_s: int = 60,
        history_max: int = 1440,
    ) -> None:
        self._monitor = monitor
        self._state = state
        self._interval = interval_s
        self._history_max = history_max
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._logger = get_logger(__name__)

    async def start(self) -> None:
        """Reconcile the history cap, take an initial sample, start the loop."""
        # Honor a non-default configured cap (deque maxlen is fixed at creation).
        if self._state.temperature_history.maxlen != self._history_max:
            self._state.temperature_history = deque(
                self._state.temperature_history, maxlen=self._history_max
            )
        self._running = True
        self._record_once()  # prime one sample so the history is non-empty
        self._task = asyncio.create_task(self._loop(), name="temperature-recorder")
        self._logger.info(
            "TemperatureRecorder started",
            extra={"interval_s": self._interval, "history_max": self._history_max},
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._logger.info("TemperatureRecorder stopped")

    def _record_once(self) -> None:
        """Append the current snapshot temperature. Never raises."""
        try:
            snap = self._monitor.snapshot()
            celsius = snap.temperature_celsius if snap is not None else None
            at = snap.sampled_at if snap is not None else None
            self._state.temperature_history.append({"celsius": celsius, "at": at})
        except Exception as exc:  # noqa: BLE001 — a bad sample must not kill the loop
            self._logger.error("Temperature record failed: %s", exc)

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                self._record_once()
        except asyncio.CancelledError:
            pass
