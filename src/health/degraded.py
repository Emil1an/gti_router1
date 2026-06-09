"""Shared degraded-mode mechanism for the health subsystem (Story 3.6).

When Supabase is unavailable the Router must keep capturing and uploading video
without ever blocking the event loop.  The pieces of "degraded mode" are
consolidated here so :mod:`health.registration` (3.1) and
:mod:`health.reporter` (3.2) reuse one mechanism instead of each rolling their
own:

* :class:`LocalHealthQueue` — a FIFO buffer of un-sent health reports with a
  time cap (default 1 h); the oldest entries are evicted past the cap.  The
  reporter flushes it in a single batch on the next successful insert.
* the ``supabase_connected`` flag lives on :class:`~health.state.AppState` and
  is toggled by whichever service last talked to Supabase.
* :func:`ptz_available` — PTZ (Epic 4) requires a linked ``gateway_id`` from a
  completed registration; without it PTZ stays inactive (documented).

The periodic 60 s reconnect is inherent to the consumers: registration
reschedules its upsert, and the reporter retries every ``report_interval_s`` and
drains this queue on success.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from health.state import AppState


class LocalHealthQueue:
    """FIFO buffer of health reports kept during a Supabase outage (1 h cap).

    Entries are stored as ``(enqueued_monotonic, payload)``.  Anything older than
    ``max_age_s`` is dropped (oldest first) whenever the queue is touched.
    """

    def __init__(self, max_age_s: float) -> None:
        self._max_age = max_age_s
        self._items: deque[tuple[float, dict[str, Any]]] = deque()

    def append(self, payload: dict[str, Any]) -> None:
        """Buffer a report and evict anything past the time cap."""
        self._items.append((time.monotonic(), payload))
        self.evict_old()

    def evict_old(self) -> int:
        """Drop entries older than the cap. Returns the number evicted."""
        cutoff = time.monotonic() - self._max_age
        removed = 0
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()
            removed += 1
        return removed

    def snapshot(self) -> list[dict[str, Any]]:
        """Return buffered payloads (oldest first) without clearing the queue."""
        self.evict_old()
        return [payload for _ts, payload in self._items]

    def clear(self) -> None:
        """Drop all buffered reports (after a successful batch flush)."""
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


def ptz_available(state: AppState) -> bool:
    """PTZ (Epic 4) is only usable once registration linked a ``gateway_id``."""
    return bool(state.gateway_id)


def log_degraded_mode_status(state: AppState, logger: logging.Logger) -> None:
    """Emit a clear INFO line documenting degraded behaviour at startup."""
    if not state.supabase_connected:
        logger.info(
            "Starting in degraded mode — Supabase not yet connected; capture and "
            "upload continue, health reports are buffered locally (1 h FIFO)."
        )
    if not ptz_available(state):
        logger.info(
            "PTZ control inactive — no gateway_id linked from registration "
            "(Router will not attempt PTZ until a Gateway is bound)."
        )
