"""Tests for the video-source factory create_source (Story 5.1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from camera.sources import create_source
from camera.sources.capture_card_source import CaptureCardSource
from camera.sources.rtsp_source import RTSPSource
from config.schema import CameraConfig, Orientation
from platform.board import Board
from utils.errors import VideoSourceError

_ORIENT = Orientation(azimuth=0, tilt=0, fov_h=90, mount_height_m=5)


def _rtsp_cfg() -> CameraConfig:
    return CameraConfig(
        camera_id="cam-1", input_type="rtsp_ip",
        rtsp_url="rtsp://a:b@192.168.1.5:554/s", orientation=_ORIENT,
    )


def _capture_cfg() -> CameraConfig:
    return CameraConfig(
        camera_id="cam-cap", input_type="capture_card",
        device="/dev/video0", orientation=_ORIENT,
    )


class TestDispatch:
    def test_rtsp_ip_creates_rtsp_source(self) -> None:
        src = create_source(_rtsp_cfg())
        assert isinstance(src, RTSPSource)
        assert src.camera_id == "cam-1"
        # RTSP is passthrough.
        assert src.ffmpeg_codec_args == ["-c", "copy"]

    def test_capture_card_creates_capture_source(self) -> None:
        src = create_source(_capture_cfg())
        assert isinstance(src, CaptureCardSource)
        assert src.device == "/dev/video0"

    def test_capture_card_uses_board_encoder_rpi4(self) -> None:
        src = create_source(_capture_cfg(), board=Board.RPI4)
        assert isinstance(src, CaptureCardSource)
        assert src.ffmpeg_codec_args[src.ffmpeg_codec_args.index("-c:v") + 1] == "h264_v4l2m2m"

    def test_capture_card_uses_board_encoder_rpi5(self) -> None:
        src = create_source(_capture_cfg(), board=Board.RPI5)
        assert src.ffmpeg_codec_args[src.ffmpeg_codec_args.index("-c:v") + 1] == "libx264"

    def test_capture_card_without_board_falls_back_software(self) -> None:
        src = create_source(_capture_cfg())  # no board
        assert src.ffmpeg_codec_args[src.ffmpeg_codec_args.index("-c:v") + 1] == "libx264"


class TestErrors:
    def test_unknown_input_type_raises(self) -> None:
        bogus = SimpleNamespace(camera_id="x", input_type="thermal_cam")
        with pytest.raises(VideoSourceError):
            create_source(bogus)  # type: ignore[arg-type]

    def test_rtsp_without_url_raises(self) -> None:
        bogus = SimpleNamespace(camera_id="x", input_type="rtsp_ip", rtsp_url=None)
        with pytest.raises(VideoSourceError):
            create_source(bogus)  # type: ignore[arg-type]

    def test_capture_without_device_raises(self) -> None:
        bogus = SimpleNamespace(camera_id="x", input_type="capture_card", device=None)
        with pytest.raises(VideoSourceError):
            create_source(bogus)  # type: ignore[arg-type]
