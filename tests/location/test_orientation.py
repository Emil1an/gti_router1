"""Tests for OrientationPublisher (Story 6.2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from config.schema import CameraConfig, Orientation
from health.supabase_client import SupabaseClient
from location.orientation import (
    OrientationPublisher,
    orientation_payload,
    validate_orientation,
)
from utils.errors import OrientationError, SupabaseTransientError

_O = Orientation(azimuth=45.0, tilt=-15.0, fov_h=90.0, mount_height_m=6.0)


def _cam(camera_id="cam-1", orientation=_O) -> CameraConfig:
    return CameraConfig(
        camera_id=camera_id, input_type="rtsp_ip",
        rtsp_url="rtsp://a:b@10.0.0.1:554/s", orientation=orientation,
    )


def _client(side=None) -> MagicMock:
    c = MagicMock(spec=SupabaseClient)
    c.update = AsyncMock(side_effect=side) if side else AsyncMock(return_value=[{"id": "c1"}])
    return c


# ── Validation ──────────────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_passes(self) -> None:
        validate_orientation(0.0, 0.0, 90.0, 5.0)  # no raise

    def test_azimuth_out_of_range(self) -> None:
        with pytest.raises(OrientationError):
            validate_orientation(360.0, 0.0, 90.0, 5.0)

    def test_tilt_out_of_range(self) -> None:
        with pytest.raises(OrientationError):
            validate_orientation(0.0, 120.0, 90.0, 5.0)

    def test_fov_out_of_range(self) -> None:
        with pytest.raises(OrientationError):
            validate_orientation(0.0, 0.0, 200.0, 5.0)

    def test_height_must_be_positive(self) -> None:
        with pytest.raises(OrientationError):
            validate_orientation(0.0, 0.0, 90.0, 0.0)


# ── Mapping ─────────────────────────────────────────────────────────────────────

class TestMapping:
    def test_azimuth_maps_to_heading(self) -> None:
        payload = orientation_payload(_O)
        assert payload == {
            "heading": 45.0, "tilt": -15.0, "fov_h": 90.0, "mount_height_m": 6.0,
        }


# ── Persistence ─────────────────────────────────────────────────────────────────

class TestPersistence:
    async def test_publishes_to_cameras(self) -> None:
        client = _client()
        pub = OrientationPublisher(client, cameras=[_cam("cam-1")])
        ok = await pub.publish_one(_cam("cam-1"))
        assert ok is True
        args, _ = client.update.call_args
        table, params, patch = args
        assert table == "cameras"
        assert params == {"id": "eq.cam-1"}
        assert patch["heading"] == 45.0

    async def test_start_publishes_all(self) -> None:
        client = _client()
        pub = OrientationPublisher(client, cameras=[_cam("cam-1"), _cam("cam-2")])
        await pub.start()
        await pub._task  # wait for the background publish to finish
        await pub.stop()
        assert client.update.await_count == 2

    async def test_degraded_does_not_crash(self) -> None:
        client = _client(side=SupabaseTransientError("down"))
        pub = OrientationPublisher(client, cameras=[_cam("cam-1")])
        # publish_one swallows the failure and returns False (deferred).
        ok = await pub.publish_one(_cam("cam-1"))
        assert ok is False
