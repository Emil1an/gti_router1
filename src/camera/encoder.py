"""Encoder selection by board (Story 5.2).

``EncoderSelector`` is the **single** place in the code that decides which video
encoder a capture card uses, based on the detected board:

* **RPi4 (Base)** → ``h264_v4l2m2m`` — the SoC's hardware H.264 encoder.
* **RPi5 (Pro)**  → ``libx264`` — software H.264 (RPi5 has no HW H.264 encoder).
* **UNKNOWN** (x86 dev/CI) → ``libx264`` software, so the pipeline is at least
  constructible off-hardware.

Hard rule (AC#2): **HEVC in software is forbidden** — it is infeasible at any
useful resolution (1080p50 ≈ 80% of 4 cores on RPi5). Requesting it raises
:class:`~utils.errors.UnsupportedEncoderError`.

Quality over quantity (AC#4/#5)
-------------------------------
The encode counts against the per-stream CPU budget (NFR1, <70%/stream). The
bounded presets/resolution caps below are chosen to stay within budget. If the
budget is exceeded the rule is to **reduce the number of cameras, never degrade
quality/resolution/bitrate per stream** (enforced as a hardware limit in
Story 5.6).

RT1 gate: the real CPU/temperature/latency benchmark of capture-card encoding on
RPi4/RPi5 is a manual hardware checklist (``scripts/`` / handoff), not CI. CI only
verifies the *selection* logic here with a mocked board.

``EncoderSelector`` does not import ``platform.board`` (which clashes with the
stdlib ``platform`` module); it accepts the board as an injected value and reads
its ``.value`` — keeping this module portable and import-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from utils.errors import UnsupportedEncoderError
from utils.logging import get_logger

if TYPE_CHECKING:  # only for type-checkers; never imported at runtime
    from platform.board import Board

_logger = get_logger(__name__)

# Encoder names (FFmpeg ``-c:v`` values).
_H264_HW = "h264_v4l2m2m"   # RPi4 hardware H.264
_H264_SW = "libx264"        # RPi5 / x86 software H.264

# Codecs that are forbidden in software (CPU-infeasible). HEVC-SW is the headline.
_FORBIDDEN_SW_CODECS = frozenset({"hevc", "h265", "libx265"})

# Bounded software-encode defaults that keep within the per-stream CPU budget.
_SW_PRESET = "veryfast"
_SW_MAX_WIDTH = 1920
_SW_MAX_HEIGHT = 1080
_SW_MAX_FPS = 30
_DEFAULT_BITRATE = "4M"


@dataclass(frozen=True)
class EncoderConfig:
    """Resolved encoder configuration for a capture-card pipeline."""

    encoder: str                 # FFmpeg -c:v value
    hardware: bool               # True if HW-accelerated
    max_width: int
    max_height: int
    max_fps: int
    bitrate: str
    extra_args: list[str] = field(default_factory=list)

    def to_ffmpeg_args(self) -> list[str]:
        """Render the FFmpeg codec/output args for this encoder config."""
        args = ["-c:v", self.encoder, "-b:v", self.bitrate]
        args += self.extra_args
        return args


class EncoderSelector:
    """Chooses the H.264 encoder for capture-card video, given the board."""

    def __init__(self, board: "Board") -> None:
        self._board = board
        # Compare by .value (string) so we never import the clashing
        # ``platform.board`` module at runtime.
        self._board_value = getattr(board, "value", str(board))

    @property
    def board_value(self) -> str:
        return self._board_value

    def select(
        self,
        *,
        bitrate: str = _DEFAULT_BITRATE,
        codec: str = "h264",
    ) -> EncoderConfig:
        """Return the :class:`EncoderConfig` for this board.

        Args:
            bitrate: target video bitrate (default 4 Mbps).
            codec:   requested codec family. Anything that resolves to HEVC in
                     software is rejected (AC#2).

        Raises:
            UnsupportedEncoderError: HEVC-SW (or any forbidden SW codec) requested.
        """
        normalized = codec.strip().lower()
        if normalized in _FORBIDDEN_SW_CODECS:
            raise UnsupportedEncoderError(
                f"HEVC/H.265 software encoding is forbidden (requested {codec!r}); "
                "it is CPU-infeasible on RPi — use H.264 and reduce camera count "
                "if needed (quality over quantity)."
            )
        if normalized not in ("h264", "avc"):
            raise UnsupportedEncoderError(
                f"Unsupported encoder codec {codec!r}; only H.264 is supported "
                "for capture cards."
            )

        if self._board_value == "rpi4":
            cfg = EncoderConfig(
                encoder=_H264_HW, hardware=True,
                max_width=_SW_MAX_WIDTH, max_height=_SW_MAX_HEIGHT, max_fps=_SW_MAX_FPS,
                bitrate=bitrate,
            )
        else:
            # RPi5 (no HW H.264) and UNKNOWN/x86 fall back to software H.264.
            if self._board_value not in ("rpi5", "unknown"):
                _logger.warning(
                    "Unrecognised board %r — defaulting to software H.264",
                    self._board_value,
                )
            cfg = EncoderConfig(
                encoder=_H264_SW, hardware=False,
                max_width=_SW_MAX_WIDTH, max_height=_SW_MAX_HEIGHT, max_fps=_SW_MAX_FPS,
                bitrate=bitrate,
                extra_args=["-preset", _SW_PRESET],
            )

        _logger.info(
            "Encoder selected",
            extra={
                "board": self._board_value,
                "encoder": cfg.encoder,
                "hardware": cfg.hardware,
            },
        )
        return cfg
