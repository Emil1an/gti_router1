"""Tests for EncoderSelector (Story 5.2)."""

from __future__ import annotations

import pytest

from camera.encoder import EncoderSelector
from platform.board import Board
from utils.errors import UnsupportedEncoderError


class TestSelectionByBoard:
    def test_rpi4_uses_hardware_h264(self) -> None:
        cfg = EncoderSelector(Board.RPI4).select()
        assert cfg.encoder == "h264_v4l2m2m"
        assert cfg.hardware is True

    def test_rpi5_uses_software_h264(self) -> None:
        cfg = EncoderSelector(Board.RPI5).select()
        assert cfg.encoder == "libx264"
        assert cfg.hardware is False

    def test_unknown_falls_back_to_software(self) -> None:
        cfg = EncoderSelector(Board.UNKNOWN).select()
        assert cfg.encoder == "libx264"
        assert cfg.hardware is False


class TestHevcForbidden:
    def test_hevc_software_rejected(self) -> None:
        with pytest.raises(UnsupportedEncoderError):
            EncoderSelector(Board.RPI5).select(codec="hevc")

    def test_h265_rejected(self) -> None:
        with pytest.raises(UnsupportedEncoderError):
            EncoderSelector(Board.RPI5).select(codec="h265")

    def test_libx265_rejected(self) -> None:
        with pytest.raises(UnsupportedEncoderError):
            EncoderSelector(Board.RPI4).select(codec="libx265")

    def test_unsupported_codec_rejected(self) -> None:
        with pytest.raises(UnsupportedEncoderError):
            EncoderSelector(Board.RPI5).select(codec="av1")


class TestFfmpegArgs:
    def test_hw_args_contain_encoder(self) -> None:
        args = EncoderSelector(Board.RPI4).select().to_ffmpeg_args()
        assert "-c:v" in args
        assert args[args.index("-c:v") + 1] == "h264_v4l2m2m"

    def test_sw_args_contain_preset(self) -> None:
        args = EncoderSelector(Board.RPI5).select().to_ffmpeg_args()
        assert "-c:v" in args
        assert args[args.index("-c:v") + 1] == "libx264"
        assert "-preset" in args

    def test_bitrate_override(self) -> None:
        args = EncoderSelector(Board.RPI5).select(bitrate="8M").to_ffmpeg_args()
        assert args[args.index("-b:v") + 1] == "8M"
