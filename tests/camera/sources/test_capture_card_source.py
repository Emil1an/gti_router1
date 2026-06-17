"""Tests for CaptureCardSource (Stories 5.1 + 5.2)."""

from __future__ import annotations

from typing import Any

import pytest

from camera.encoder import EncoderSelector
from camera.sources.capture_card_source import CaptureCardSource
from platform.board import Board
from tests.fixtures.mock_v4l2 import (
    FFPROBE_V4L2_EMPTY,
    FFPROBE_V4L2_MJPEG,
    FFPROBE_V4L2_RAW,
    make_ffprobe_mock,
)
from utils.errors import CaptureCardError

_EXEC = "camera.sources.capture_card_source.asyncio.create_subprocess_exec"


def _source(encoder=None) -> CaptureCardSource:
    return CaptureCardSource(camera_id="cam-cap", device="/dev/video0", encoder=encoder)


# ── ffmpeg args ─────────────────────────────────────────────────────────────────

class TestFfmpegArgs:
    def test_input_args_are_v4l2(self) -> None:
        args = _source().ffmpeg_input_args
        assert "-f" in args and args[args.index("-f") + 1] == "v4l2"
        assert "-i" in args and args[args.index("-i") + 1] == "/dev/video0"

    def test_codec_args_fallback_is_software_h264(self) -> None:
        # No encoder injected → software H.264 fallback (never passthrough).
        args = _source().ffmpeg_codec_args
        assert args[args.index("-c:v") + 1] == "libx264"
        assert "copy" not in args

    def test_codec_args_use_injected_encoder_rpi4(self) -> None:
        enc = EncoderSelector(Board.RPI4).select()
        args = _source(encoder=enc).ffmpeg_codec_args
        assert args[args.index("-c:v") + 1] == "h264_v4l2m2m"

    def test_codec_args_use_injected_encoder_rpi5(self) -> None:
        enc = EncoderSelector(Board.RPI5).select()
        args = _source(encoder=enc).ffmpeg_codec_args
        assert args[args.index("-c:v") + 1] == "libx264"


# ── probe ─────────────────────────────────────────────────────────────────────

class TestProbe:
    async def test_probe_rawvideo(self, monkeypatch) -> None:
        monkeypatch.setattr(_EXEC, make_ffprobe_mock(stdout=FFPROBE_V4L2_RAW))
        meta = await _source().probe()
        assert meta.codec == "rawvideo"
        assert meta.resolution == "1920x1080"
        assert meta.framerate == pytest.approx(30.0)
        assert meta.camera_id == "cam-cap"

    async def test_probe_mjpeg(self, monkeypatch) -> None:
        monkeypatch.setattr(_EXEC, make_ffprobe_mock(stdout=FFPROBE_V4L2_MJPEG))
        meta = await _source().probe()
        assert meta.codec == "mjpeg"
        assert meta.resolution == "1280x720"

    async def test_device_unavailable_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _EXEC,
            make_ffprobe_mock(stdout=b"", stderr=b"No such device", returncode=1),
        )
        with pytest.raises(CaptureCardError):
            await _source().probe()

    async def test_no_stream_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(_EXEC, make_ffprobe_mock(stdout=FFPROBE_V4L2_EMPTY))
        with pytest.raises(CaptureCardError):
            await _source().probe()

    async def test_ffprobe_missing_raises(self, monkeypatch) -> None:
        async def _boom(*_a: Any, **_k: Any):
            raise FileNotFoundError("ffprobe not found")

        monkeypatch.setattr(_EXEC, _boom)
        with pytest.raises(CaptureCardError):
            await _source().probe()
