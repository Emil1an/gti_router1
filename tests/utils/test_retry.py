"""Tests for src/utils/retry.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from utils.retry import with_retry


class _Transient(Exception):
    """Simulated transient error."""


class _Permanent(Exception):
    """Simulated permanent error — must NOT be retried."""


@pytest.mark.asyncio
async def test_success_on_first_attempt() -> None:
    calls: list[int] = []

    @with_retry(max_retries=3, retryable=(_Transient,))
    async def op() -> str:
        calls.append(1)
        return "ok"

    result = await op()
    assert result == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_success_after_n_failures() -> None:
    """Should succeed on the 3rd attempt (2 failures then 1 success)."""
    attempt = [0]

    @with_retry(max_retries=5, retryable=(_Transient,))
    async def op() -> str:
        attempt[0] += 1
        if attempt[0] < 3:
            raise _Transient("boom")
        return "recovered"

    with patch("utils.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await op()

    assert result == "recovered"
    assert attempt[0] == 3


@pytest.mark.asyncio
async def test_retries_exhausted_raises_last_exception() -> None:
    @with_retry(max_retries=2, retryable=(_Transient,))
    async def op() -> None:
        raise _Transient("always fails")

    with patch("utils.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(_Transient, match="always fails"):
            await op()


@pytest.mark.asyncio
async def test_permanent_error_not_retried() -> None:
    calls = [0]

    @with_retry(max_retries=5, retryable=(_Transient,))
    async def op() -> None:
        calls[0] += 1
        raise _Permanent("do not retry")

    with pytest.raises(_Permanent):
        await op()

    assert calls[0] == 1  # called exactly once


@pytest.mark.asyncio
async def test_backoff_sleep_is_called() -> None:
    """Sleep must be called between retries (verifies backoff is applied)."""

    @with_retry(max_retries=2, retryable=(_Transient,))
    async def op() -> None:
        raise _Transient("fail")

    with patch("utils.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(_Transient):
            await op()

    assert mock_sleep.call_count == 2  # one sleep per retry (not after last attempt)


@pytest.mark.asyncio
async def test_max_retries_zero_means_no_retry() -> None:
    calls = [0]

    @with_retry(max_retries=0, retryable=(_Transient,))
    async def op() -> None:
        calls[0] += 1
        raise _Transient("single shot")

    with pytest.raises(_Transient):
        await op()

    assert calls[0] == 1
