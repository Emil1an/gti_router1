"""Hardware camera limits (Story 5.6).

Applies the **physical** camera ceiling derived from the detected board
(``platform/board.py``, Story 5.5), materialising NFR12:

* **RPi4 (Base)** → 2 IP cameras + 1 capture card  (max 3)
* **RPi5 (Pro)**  → 3 IP cameras + 1 capture card  (max 4)
* **UNKNOWN** (x86 dev/CI) → permissive, so development is never blocked.

Policy: **fail-fast at startup** — if ``router.yaml`` declares more cameras than
the board allows, the Router refuses to start (raising
:class:`~utils.errors.CameraLimitError`) rather than silently dropping cameras.

Quality over quantity
---------------------
The limit exists to guarantee **full quality per stream** (NFR1). When hardware
or bandwidth cannot sustain N cameras at full quality, the rule is to reduce the
**number of cameras** — never the resolution/bitrate per stream.

Scope
-----
This module enforces **only** the physical hardware cap. The paid-subscription
quota (``device_subscriptions.camera_quota``) and the effective limit
``LEAST(camera_quota, max_cameras)`` belong to Epic 10 (Story 10.5) and are **not**
consulted here (no billing tables).

The ``Board`` type is only referenced for typing (``TYPE_CHECKING``) — limits are
keyed by ``board.value`` so this module never imports the clashing
``platform.board`` at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from utils.errors import CameraLimitError
from utils.logging import get_logger

if TYPE_CHECKING:  # type-only; avoids importing the stdlib-clashing platform pkg
    from config.schema import CameraConfig
    from platform.board import Board

_logger = get_logger(__name__)

# NFR12 physical limits by board, split by input_type.
_BOARD_LIMITS: dict[str, dict[str, int]] = {
    "rpi4": {"rtsp_ip": 2, "capture_card": 1},
    "rpi5": {"rtsp_ip": 3, "capture_card": 1},
}

# x86 / unknown boards: permissive caps so dev/CI can run multi-camera configs.
_UNKNOWN_LIMITS: dict[str, int] = {"rtsp_ip": 8, "capture_card": 8}


def limits_for_board(board: "Board") -> dict[str, int]:
    """Return the per-input-type camera limits for the given board."""
    board_value = getattr(board, "value", str(board))
    return _BOARD_LIMITS.get(board_value, _UNKNOWN_LIMITS)


def max_cameras_for_board(board: "Board") -> int:
    """Return the total physical camera ceiling for the given board."""
    return sum(limits_for_board(board).values())


def enforce_camera_limit(
    cameras: Iterable["CameraConfig"], board: "Board"
) -> None:
    """Fail-fast if the configured cameras exceed the board's physical limit.

    Raises:
        CameraLimitError: too many cameras overall or of a given input_type.
    """
    cams = list(cameras)
    limits = limits_for_board(board)
    board_value = getattr(board, "value", str(board))

    counts: dict[str, int] = {}
    for cam in cams:
        counts[cam.input_type] = counts.get(cam.input_type, 0) + 1

    # Per-type ceilings (e.g. RPi4: ≤2 rtsp_ip, ≤1 capture_card).
    for input_type, count in counts.items():
        allowed = limits.get(input_type, 0)
        if count > allowed:
            for cam in cams:
                if cam.input_type == input_type:
                    _logger.error(
                        "Camera exceeds hardware limit",
                        extra={
                            "camera_id": cam.camera_id,
                            "input_type": input_type,
                            "board": board_value,
                            "allowed": allowed,
                            "configured": count,
                        },
                    )
            raise CameraLimitError(
                f"board {board_value} allows at most {allowed} '{input_type}' "
                f"camera(s), but {count} are configured — reduce the number of "
                "cameras (quality over quantity); never degrade per-stream quality."
            )

    total_allowed = sum(limits.values())
    if len(cams) > total_allowed:
        raise CameraLimitError(
            f"board {board_value} allows at most {total_allowed} cameras, "
            f"but {len(cams)} are configured."
        )

    _logger.info(
        "Camera limit check passed",
        extra={
            "board": board_value,
            "configured": len(cams),
            "max_cameras": total_allowed,
        },
    )
