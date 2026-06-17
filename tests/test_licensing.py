"""Tests for hardware camera limits (Story 5.6)."""

from __future__ import annotations

import pytest

from config.schema import CameraConfig, Orientation
from licensing import enforce_camera_limit, limits_for_board, max_cameras_for_board
from platform.board import Board
from utils.errors import CameraLimitError

_ORIENT = Orientation(azimuth=0, tilt=0, fov_h=90, mount_height_m=5)


def _ip(n: int) -> CameraConfig:
    return CameraConfig(
        camera_id=f"ip{n}", input_type="rtsp_ip",
        rtsp_url=f"rtsp://a:b@10.0.0.{n}:554/s", orientation=_ORIENT,
    )


def _cap(n: int) -> CameraConfig:
    return CameraConfig(
        camera_id=f"cap{n}", input_type="capture_card",
        device=f"/dev/video{n}", orientation=_ORIENT,
    )


class TestLimitsForBoard:
    def test_rpi4_limits(self) -> None:
        assert limits_for_board(Board.RPI4) == {"rtsp_ip": 2, "capture_card": 1}
        assert max_cameras_for_board(Board.RPI4) == 3

    def test_rpi5_limits(self) -> None:
        assert limits_for_board(Board.RPI5) == {"rtsp_ip": 3, "capture_card": 1}
        assert max_cameras_for_board(Board.RPI5) == 4

    def test_unknown_permissive(self) -> None:
        assert max_cameras_for_board(Board.UNKNOWN) >= 8


class TestWithinLimit:
    def test_rpi4_2ip_1cap_ok(self) -> None:
        enforce_camera_limit([_ip(1), _ip(2), _cap(0)], Board.RPI4)  # no raise

    def test_rpi5_3ip_1cap_ok(self) -> None:
        enforce_camera_limit([_ip(1), _ip(2), _ip(3), _cap(0)], Board.RPI5)

    def test_unknown_allows_many(self) -> None:
        enforce_camera_limit([_ip(i) for i in range(6)], Board.UNKNOWN)


class TestFailFast:
    def test_rpi4_three_ip_rejected(self) -> None:
        with pytest.raises(CameraLimitError):
            enforce_camera_limit([_ip(1), _ip(2), _ip(3)], Board.RPI4)

    def test_rpi4_two_capture_rejected(self) -> None:
        with pytest.raises(CameraLimitError):
            enforce_camera_limit([_cap(0), _cap(1)], Board.RPI4)

    def test_rpi5_four_ip_rejected(self) -> None:
        with pytest.raises(CameraLimitError):
            enforce_camera_limit([_ip(1), _ip(2), _ip(3), _ip(4)], Board.RPI5)

    def test_message_mentions_reducing_cameras(self) -> None:
        with pytest.raises(CameraLimitError, match="reduce the number of cameras"):
            enforce_camera_limit([_ip(1), _ip(2), _ip(3)], Board.RPI4)

    def test_subscription_quota_not_consulted(self) -> None:
        # Physical cap only — no billing/quota tables touched (Epic 10 scope).
        # An RPi5 with exactly its physical max must pass regardless of any quota.
        enforce_camera_limit([_ip(1), _ip(2), _ip(3), _cap(0)], Board.RPI5)
