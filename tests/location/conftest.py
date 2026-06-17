"""Config fixture for location/GPS/orientation tests (Epic 6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.loader import reset_config

_YAML = """\
cameras:
  - camera_id: cam-1
    input_type: rtsp_ip
    rtsp_url: "rtsp://a:b@10.0.0.1:554/s"
    orientation: {azimuth: 45.0, tilt: -15.0, fov_h: 90.0, mount_height_m: 6.0}
hls:
  segment_duration: 4
aws: {bucket: b, region: us-east-1, access_key_id: k, secret_access_key: s}
supabase: {url: "https://x.supabase.co", service_role_key: srk}
device: {serial_number: GTR-PRO-1, name: R}
gps: {enabled: true, persist_interval_s: 5}
snapshot: {enabled: true, interval_s: 10}
"""


@pytest.fixture(autouse=True)
def _location_config(tmp_path: Path) -> None:
    cfg = tmp_path / "router.yaml"
    cfg.write_text(_YAML, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(cfg)
    reset_config()
    yield
    os.environ.pop("ROUTER_CONFIG", None)
    reset_config()
