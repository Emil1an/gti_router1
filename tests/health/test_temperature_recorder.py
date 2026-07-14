"""Tests for TemperatureRecorder (rolling in-memory CPU temperature history).

A fake monitor stub is used so tests are deterministic and hardware-independent
(no ``platform.board`` import, no Linux-only deps) — they must run on Windows.
"""

from __future__ import annotations

import asyncio

import pytest

from health.state import AppState
from health.temperature_recorder import TemperatureRecorder


class _FakeSnap:
    def __init__(self, c, at="2026-07-13T00:00:00.000Z"):
        self.temperature_celsius = c
        self.sampled_at = at


class _FakeMonitor:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self):
        return self._snap


class _RaisingMonitor:
    def snapshot(self):
        raise RuntimeError("sensor read boom")


async def test_appends_and_respects_maxlen() -> None:
    state = AppState()
    monitor = _FakeMonitor(_FakeSnap(42.0))
    rec = TemperatureRecorder(monitor=monitor, state=state, interval_s=0.01, history_max=3)

    await rec.start()
    await asyncio.sleep(0.2)  # long enough for well over 3 samples
    await rec.stop()

    assert len(state.temperature_history) == 3
    for entry in state.temperature_history:
        assert set(entry) == {"celsius", "at"}


async def test_start_reconciles_maxlen() -> None:
    state = AppState()
    assert state.temperature_history.maxlen == 1440

    monitor = _FakeMonitor(_FakeSnap(30.0))
    rec = TemperatureRecorder(monitor=monitor, state=state, interval_s=60, history_max=5)
    await rec.start()
    try:
        assert state.temperature_history.maxlen == 5
    finally:
        await rec.stop()


async def test_non_blocking_loop() -> None:
    state = AppState()
    monitor = _FakeMonitor(_FakeSnap(35.0))
    rec = TemperatureRecorder(monitor=monitor, state=state, interval_s=0.01, history_max=1440)

    counter = 0
    stop_counting = False

    async def _counter_task() -> None:
        nonlocal counter
        while not stop_counting:
            counter += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(_counter_task())
    await rec.start()
    await asyncio.sleep(0.2)
    stop_counting = True
    await rec.stop()
    await task

    assert counter > 100  # event loop kept making progress concurrently


async def test_snapshot_none_is_recorded() -> None:
    state = AppState()
    monitor = _FakeMonitor(None)
    rec = TemperatureRecorder(monitor=monitor, state=state, interval_s=60, history_max=1440)

    await rec.start()
    try:
        assert len(state.temperature_history) == 1
        assert state.temperature_history[-1] == {"celsius": None, "at": None}
    finally:
        await rec.stop()


async def test_snapshot_raises_keeps_running() -> None:
    state = AppState()
    monitor = _RaisingMonitor()
    rec = TemperatureRecorder(monitor=monitor, state=state, interval_s=0.01, history_max=1440)

    await rec.start()
    await asyncio.sleep(0.1)
    await rec.stop()  # must complete cleanly, no exception propagated
