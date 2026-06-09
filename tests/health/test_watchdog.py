"""Tests for the systemd Watchdog (Story 3.5).

``sd_notify`` is mocked; no real systemd needed.
"""

from __future__ import annotations

import asyncio

import pytest

from health.watchdog import Watchdog

_REAL_SLEEP = asyncio.sleep


async def _wait_until(predicate, timeout_s: float = 2.0) -> None:
    deadline = int(timeout_s / 0.01) + 1
    for _ in range(deadline):
        if predicate():
            return
        await _REAL_SLEEP(0.01)


# ── Heartbeat ────────────────────────────────────────────────────────────────────

class TestHeartbeat:
    async def test_sends_watchdog_pings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
        calls: list[str] = []
        wd = Watchdog(notifier=lambda m: calls.append(m), interval_s=0.02)

        await wd.start()
        await _wait_until(lambda: calls.count("WATCHDOG=1") >= 3, timeout_s=2.0)
        await wd.stop()

        assert calls.count("WATCHDOG=1") >= 3

    async def test_interval_is_15s_by_default(self) -> None:
        import health.watchdog as wd_mod
        assert wd_mod._HEARTBEAT_INTERVAL_S == 15.0
        wd = Watchdog(notifier=lambda m: None)
        assert wd._interval == 15.0


# ── No-op without NOTIFY_SOCKET ─────────────────────────────────────────────────

class TestNoSocket:
    async def test_no_heartbeat_without_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        calls: list[str] = []
        wd = Watchdog(notifier=lambda m: calls.append(m), interval_s=0.02)

        assert wd.enabled is False
        await wd.start()
        await _REAL_SLEEP(0.1)  # several would-be intervals
        await wd.stop()

        assert calls == []  # no-op: nothing sent

    async def test_notify_ready_noop_without_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        calls: list[str] = []
        wd = Watchdog(notifier=lambda m: calls.append(m))
        wd.notify_ready()
        assert calls == []

    async def test_no_systemd_module_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no notifier resolved at all, everything is a safe no-op."""
        monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
        wd = Watchdog(notifier=None)  # systemd-python not installed on dev/CI
        # On a machine without systemd-python, _notify_fn is None → disabled.
        if wd._notify_fn is None:
            assert wd.enabled is False
            wd.notify_ready()  # must not raise
            await wd.start()
            await wd.stop()


# ── READY=1 ───────────────────────────────────────────────────────────────────────

class TestReady:
    async def test_notify_ready_sends_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
        calls: list[str] = []
        wd = Watchdog(notifier=lambda m: calls.append(m))
        wd.notify_ready()
        assert "READY=1" in calls

    async def test_notify_stopping_sends_stopping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
        calls: list[str] = []
        wd = Watchdog(notifier=lambda m: calls.append(m))
        wd.notify_stopping()
        assert "STOPPING=1" in calls


# ── Robustness ──────────────────────────────────────────────────────────────────

class TestRobustness:
    async def test_notify_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")

        def _boom(_m: str):
            raise OSError("notify socket gone")

        wd = Watchdog(notifier=_boom)
        # Must swallow the error (logged), never propagate.
        wd.notify_ready()

    async def test_stop_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
        wd = Watchdog(notifier=lambda m: None, interval_s=0.02)
        await wd.start()
        await wd.stop()
        await wd.stop()
