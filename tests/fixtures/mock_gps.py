"""Mock GPS/NMEA data for GpsReader tests (no hardware, no gpsd)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

# Valid GGA fix (quality=1): 48.1173 N, 11.5167 E
NMEA_GGA_FIX = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
# Valid RMC fix (status=A)
NMEA_RMC_FIX = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
# GGA with no fix (quality=0) → must be discarded
NMEA_GGA_NOFIX = "$GPGGA,123519,,,,,0,00,,,M,,M,,*47"
# Junk line → parse error → discarded
NMEA_GARBAGE = "not-a-nmea-sentence"

_REAL_SLEEP = asyncio.sleep


def stream_of(lines: list[str], hold_open: bool = True):
    """Return a stream_factory yielding ``lines`` then (optionally) staying open.

    Staying open prevents GpsReader's loop from busy-reconnecting during a test.
    """

    def _factory() -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            for line in lines:
                yield line
            if hold_open:
                await _REAL_SLEEP(60)

        return _gen()

    return _factory
