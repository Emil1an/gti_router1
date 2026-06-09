"""Tests for DeviceRegistration (Story 3.1).

The Supabase client is mocked at the method boundary — no real network.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import health.registration as reg_mod
from health.registration import DeviceRegistration
from health.state import AppState
from health.supabase_client import SupabaseClient
from utils.errors import SupabasePermanentError, SupabaseTransientError

# Real asyncio.sleep captured before any test patches it globally (via
# patch("utils.retry.asyncio.sleep", ...)), so the polling helper always yields.
_REAL_SLEEP = asyncio.sleep


# ── Helpers ────────────────────────────────────────────────────────────────────

def _client(upsert_side=None, upsert_return=None) -> SupabaseClient:
    client = MagicMock(spec=SupabaseClient)
    if upsert_side is not None:
        client.upsert = AsyncMock(side_effect=upsert_side)
    else:
        client.upsert = AsyncMock(
            return_value=upsert_return
            if upsert_return is not None
            else [{"id": "router-uuid-1", "gateway_id": "gw-abc-123"}]
        )
    return client


async def _wait_until(predicate, timeout_s: float = 2.0) -> None:
    deadline = int(timeout_s / 0.02) + 1
    for _ in range(deadline):
        if predicate():
            return
        await _REAL_SLEEP(0.02)


# ── Successful upsert ────────────────────────────────────────────────────────────

class TestSuccessfulRegistration:
    async def test_upsert_called_with_correct_payload(self) -> None:
        client = _client()
        state = AppState()
        reg = DeviceRegistration(client=client, state=state)

        await reg.start()
        await _wait_until(lambda: reg.supabase_connected)
        await reg.stop()

        client.upsert.assert_awaited()
        args, kwargs = client.upsert.call_args
        table, payload = args[0], args[1]
        assert table == "routers"
        assert kwargs.get("on_conflict") == "serial_number"
        # Required fields present
        assert payload["serial_number"] == "GTR-TEST-001"
        assert payload["name"] == "Test Router"
        assert payload["gateway_id"] == "gw-abc-123"
        assert payload["firmware_version"] == "1.2.3"
        assert payload["last_seen_at"].endswith("Z")
        # Router must NOT write user_id
        assert "user_id" not in payload

    async def test_gateway_id_and_router_id_cached(self) -> None:
        client = _client(upsert_return=[{"id": "router-xyz", "gateway_id": "gw-999"}])
        state = AppState()
        reg = DeviceRegistration(client=client, state=state)

        await reg.start()
        await _wait_until(lambda: reg.supabase_connected)
        await reg.stop()

        assert reg.router_id == "router-xyz"
        assert reg.gateway_id == "gw-999"
        assert state.router_id == "router-xyz"
        assert state.gateway_id == "gw-999"
        assert state.supabase_connected is True

    async def test_idempotent_on_conflict_serial_number(self) -> None:
        """Upsert always targets the serial_number conflict key (no duplicates)."""
        client = _client()
        reg = DeviceRegistration(client=client, state=AppState())
        await reg.start()
        await _wait_until(lambda: reg.supabase_connected)
        await reg.stop()
        _args, kwargs = client.upsert.call_args
        assert kwargs["on_conflict"] == "serial_number"


# ── Non-blocking degraded mode ───────────────────────────────────────────────────

class TestDegradedMode:
    async def test_start_is_non_blocking(self) -> None:
        """start() returns immediately even while the upsert is in-flight."""
        gate = asyncio.Event()

        async def _blocking_upsert(*_a, **_kw):
            await gate.wait()
            return [{"id": "r1", "gateway_id": "gw"}]

        client = _client(upsert_side=_blocking_upsert)
        reg = DeviceRegistration(client=client, state=AppState())

        # If start() blocked on the upsert this would hang; wait_for guards it.
        await asyncio.wait_for(reg.start(), timeout=1.0)
        assert reg.supabase_connected is False  # not done yet

        gate.set()
        await _wait_until(lambda: reg.supabase_connected)
        await reg.stop()
        assert reg.supabase_connected is True

    async def test_transient_failure_reschedules_then_succeeds(self) -> None:
        """After a transient failure the registration reschedules and recovers."""
        calls = {"n": 0}

        async def _side(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SupabaseTransientError("supabase down")
            return [{"id": "r2", "gateway_id": "gw-2"}]

        client = _client(upsert_side=_side)
        # max_retries=0 → @with_retry never sleeps; only the (short, real)
        # reschedule sleep runs, which yields control so the loop can recover.
        reg = DeviceRegistration(client=client, state=AppState(), max_retries=0)

        with patch.object(reg_mod, "_REGISTRATION_RETRY_INTERVAL_S", 0.02):
            await reg.start()
            await _wait_until(lambda: reg.supabase_connected, timeout_s=3.0)
            await reg.stop()

        assert reg.supabase_connected is True
        assert calls["n"] == 2  # first transient, second success

    async def test_does_not_abort_when_supabase_down(self) -> None:
        """A persistently-down Supabase keeps retrying without raising."""

        async def _always_down(*_a, **_kw):
            raise SupabaseTransientError("still down")

        client = _client(upsert_side=_always_down)
        reg = DeviceRegistration(client=client, state=AppState(), max_retries=0)

        with patch.object(reg_mod, "_REGISTRATION_RETRY_INTERVAL_S", 0.02):
            await reg.start()
            await _wait_until(lambda: client.upsert.await_count >= 2, timeout_s=3.0)
            connected = reg.supabase_connected
            await reg.stop()

        assert connected is False
        assert client.upsert.await_count >= 2  # kept retrying, never aborted


# ── Permanent error not retried ──────────────────────────────────────────────────

class TestPermanentError:
    async def test_permanent_error_not_retried(self) -> None:
        async def _permanent(*_a, **_kw):
            raise SupabasePermanentError("400 invalid payload")

        client = _client(upsert_side=_permanent)
        # A permanent error is not in the retryable set, so @with_retry never
        # sleeps regardless of max_retries.
        reg = DeviceRegistration(client=client, state=AppState(), max_retries=10)

        await reg.start()
        # Loop must terminate on permanent error → task done quickly.
        await _wait_until(
            lambda: reg._task is not None and reg._task.done(), timeout_s=2.0
        )
        await reg.stop()

        # Exactly one call — permanent errors are never retried.
        assert client.upsert.await_count == 1
        assert reg.supabase_connected is False
