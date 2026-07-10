"""Raspberry Pi board detection (Story 5.5).

This is the **single source of truth** for which board the Router runs on.  It
reads ``/proc/device-tree/model`` once at startup and classifies the host as
``RPI4``, ``RPI5`` or ``UNKNOWN``.  Nobody else reads the device-tree model — the
:class:`~camera.encoder.EncoderSelector` (Story 5.2) and the licensing limits
(Story 5.6) consume :func:`detect_board`.

Robustness
----------
Detection never crashes startup: a missing file (e.g. x86 dev/CI), a permission
error, or an unrecognised model all degrade to ``Board.UNKNOWN`` with a WARNING.
The trailing NUL byte of the device-tree string is stripped and matching is
case-insensitive substring.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from utils.logging import get_logger

_logger = get_logger(__name__)

# Device-tree model path on a real Raspberry Pi.  Patchable in tests.
_MODEL_PATH = Path("/proc/device-tree/model")

_cached_board: "Board | None" = None


class Board(str, Enum):
    """Detected hardware board (SKU mapping: RPI4=Base, RPI5=Pro)."""

    RPI4 = "rpi4"
    RPI5 = "rpi5"
    UNKNOWN = "unknown"


def _read_model_text() -> str | None:
    """Read and normalise the device-tree model string, or ``None`` if absent."""
    try:
        if not _MODEL_PATH.exists():
            return None
        raw = _MODEL_PATH.read_bytes()
    except OSError as exc:
        _logger.warning("Could not read board model (%s) — assuming UNKNOWN", exc)
        return None
    # Device-tree strings are NUL-terminated; strip it and normalise.
    return raw.replace(b"\x00", b"").decode("utf-8", errors="replace").strip()


def _classify(model_text: str | None) -> Board:
    if not model_text:
        _logger.warning(
            "Board model unavailable (not a Raspberry Pi?) — assuming UNKNOWN"
        )
        return Board.UNKNOWN
    lowered = model_text.lower()
    if "raspberry pi 5" in lowered:
        return Board.RPI5
    if "raspberry pi 4" in lowered:
        return Board.RPI4
    _logger.warning("Unrecognised board model %r — assuming UNKNOWN", model_text)
    return Board.UNKNOWN


def detect_board(*, reload: bool = False) -> Board:
    """Return the detected :class:`Board`, caching the first read.

    Args:
        reload: re-read the device-tree model instead of using the cache.
    """
    global _cached_board  # noqa: PLW0603
    if _cached_board is not None and not reload:
        return _cached_board
    _cached_board = _classify(_read_model_text())
    _logger.info("Board detected", extra={"board": _cached_board.value})
    return _cached_board


def reset_board_cache() -> None:
    """Clear the cached board (for tests only)."""
    global _cached_board  # noqa: PLW0603
    _cached_board = None
