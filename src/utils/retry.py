"""Unique retry decorator for all network operations in GTI Router.

This is the **single** source of retry logic for the project.
No other module may implement its own retry / sleep loop.

Usage
-----
```python
from utils.retry import with_retry
from utils.errors import S3UploadError

@with_retry(max_retries=10, retryable=(S3UploadError, TimeoutError))
async def upload(path: str) -> str:
    ...
```

Backoff strategy
----------------
``delay = min(base * 2**attempt, max_delay) * jitter``

where ``jitter`` is a uniform random factor in ``[0.8, 1.2]`` (±20 %).
First retry fires after ~1 s; subsequent retries double until capped at 60 s.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Coroutine[Any, Any, Any]])

_logger = logging.getLogger(__name__)

_BASE_DELAY: float = 1.0
_MAX_DELAY: float = 60.0
_JITTER_RANGE: tuple[float, float] = (0.8, 1.2)


def with_retry(
    max_retries: int = 10,
    retryable: tuple[type[BaseException], ...] = (Exception,),
    base_delay: float = _BASE_DELAY,
    max_delay: float = _MAX_DELAY,
) -> Callable[[_F], _F]:
    """Async decorator with exponential backoff + jitter.

    Args:
        max_retries: maximum number of *additional* attempts after the first call.
                     ``0`` means no retries (one attempt only).
        retryable:   tuple of exception types that trigger a retry.
                     Exceptions **not** in this tuple propagate immediately.
        base_delay:  initial sleep in seconds (default 1 s).
        max_delay:   upper cap on sleep (default 60 s).

    Raises:
        The last exception raised by the wrapped coroutine once retries are
        exhausted, or immediately for non-retryable exceptions.
    """

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        _logger.error(
                            "Retries exhausted for %s after %d attempts: %s",
                            func.__qualname__,
                            max_retries + 1,
                            exc,
                        )
                        raise
                    delay = min(base_delay * (2**attempt), max_delay)
                    delay *= random.uniform(*_JITTER_RANGE)
                    _logger.warning(
                        "Attempt %d/%d failed for %s (%s). Retrying in %.1fs.",
                        attempt + 1,
                        max_retries + 1,
                        func.__qualname__,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
            # unreachable, but satisfies type checkers
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
