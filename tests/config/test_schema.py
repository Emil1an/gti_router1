"""Tests for src/config/schema.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.schema import (
    CameraConfig,
    LicensingConfig,
    Orientation,
    RouterConfig,
)


def _minimal_camera(**overrides: object) -> dict:
    base: dict = {
        "camera_id": "cam-1",
        "input_type": "rtsp_ip",
        "rtsp_url": "rtsp://admin:pass@192.168.1.1:554/stream",
        "orientation": {
            "azimuth": 45.0,
            "tilt": -10.0,
            "fov_h": 90.0,
            "mount_height_m": 5.0,
        },
    }
    base.update(overrides)
    return base


def _minimal_config(**overrides: object) -> dict:
    base: dict = {
        "cameras": [_minimal_camera()],
        "aws": {
            "bucket": "test-bucket",
            "region": "us-east-1",
            "access_key_id": "KEY",
            "secret_access_key": "SECRET",
        },
        "supabase": {
            "url": "https://test.supabase.co",
            "service_role_key": "srk",
        },
        "device": {
            "serial_number": "GTR-001",
            "name": "Test",
            "sku": "base",
        },
    }
    base.update(overrides)
    return base


# ── Orientation ────────────────────────────────────────────────────────────────

class TestOrientation:
    def test_valid(self) -> None:
        o = Orientation(azimuth=0.0, tilt=0.0, fov_h=90.0, mount_height_m=5.0)
        assert o.azimuth == 0.0

    def test_azimuth_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            Orientation(azimuth=360.0, tilt=0.0, fov_h=90.0, mount_height_m=5.0)

    def test_azimuth_negative(self) -> None:
        with pytest.raises(ValidationError):
            Orientation(azimuth=-1.0, tilt=0.0, fov_h=90.0, mount_height_m=5.0)

    def test_tilt_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            Orientation(azimuth=0.0, tilt=91.0, fov_h=90.0, mount_height_m=5.0)

    def test_fov_h_zero(self) -> None:
        with pytest.raises(ValidationError):
            Orientation(azimuth=0.0, tilt=0.0, fov_h=0.0, mount_height_m=5.0)

    def test_mount_height_zero(self) -> None:
        with pytest.raises(ValidationError):
            Orientation(azimuth=0.0, tilt=0.0, fov_h=90.0, mount_height_m=0.0)


# ── CameraConfig ───────────────────────────────────────────────────────────────

class TestCameraConfig:
    def test_rtsp_requires_rtsp_url(self) -> None:
        with pytest.raises(ValidationError, match="rtsp_url"):
            CameraConfig.model_validate({
                "camera_id": "c1",
                "input_type": "rtsp_ip",
                "orientation": {"azimuth": 0, "tilt": 0, "fov_h": 60, "mount_height_m": 4},
            })

    def test_capture_card_requires_device(self) -> None:
        with pytest.raises(ValidationError, match="device"):
            CameraConfig.model_validate({
                "camera_id": "c1",
                "input_type": "capture_card",
                "orientation": {"azimuth": 0, "tilt": 0, "fov_h": 60, "mount_height_m": 4},
            })

    def test_invalid_input_type(self) -> None:
        with pytest.raises(ValidationError):
            CameraConfig.model_validate({
                **_minimal_camera(),
                "input_type": "usb",
            })

    def test_capture_card_valid(self) -> None:
        cam = CameraConfig.model_validate({
            "camera_id": "c2",
            "input_type": "capture_card",
            "device": "/dev/video0",
            "orientation": {"azimuth": 90, "tilt": 0, "fov_h": 60, "mount_height_m": 3},
        })
        assert cam.device == "/dev/video0"


# ── RouterConfig ───────────────────────────────────────────────────────────────

class TestRouterConfig:
    def test_valid_minimal(self) -> None:
        cfg = RouterConfig.model_validate(_minimal_config())
        assert len(cfg.cameras) == 1
        assert cfg.hls.segment_duration == 4  # default

    def test_duplicate_camera_ids(self) -> None:
        cam2 = _minimal_camera(camera_id="cam-1")  # same id
        with pytest.raises(ValidationError, match="Duplicate"):
            RouterConfig.model_validate(_minimal_config(cameras=[_minimal_camera(), cam2]))

    def test_missing_cameras(self) -> None:
        data = _minimal_config(cameras=[])
        with pytest.raises(ValidationError):
            RouterConfig.model_validate(data)

    def test_segment_duration_out_of_range(self) -> None:
        data = _minimal_config()
        data["hls"] = {"segment_duration": 9}
        with pytest.raises(ValidationError):
            RouterConfig.model_validate(data)

    def test_missing_required_aws_field(self) -> None:
        data = _minimal_config()
        del data["aws"]["bucket"]
        with pytest.raises(ValidationError):
            RouterConfig.model_validate(data)

    def test_unknown_sku(self) -> None:
        data = _minimal_config()
        data["device"]["sku"] = "ultra"
        with pytest.raises(ValidationError):
            RouterConfig.model_validate(data)
