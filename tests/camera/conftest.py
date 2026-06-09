"""Config fixture for camera/PTZ tests (Epic 4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.loader import reset_config

_CAMERA_YAML = """\
cameras:
  - camera_id: cam-1
    input_type: rtsp_ip
    rtsp_url: "rtsp://admin:pass@192.168.1.10:554/stream"
    ptz_enabled: true
    onvif_host: "192.168.1.10"
    onvif_port: 80
    onvif_username: admin
    onvif_password: pass
    orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}
  - camera_id: cam-2
    input_type: rtsp_ip
    rtsp_url: "rtsp://admin:pass@192.168.1.11:554/stream"
    orientation: {azimuth: 90, tilt: 0, fov_h: 90, mount_height_m: 5}
hls:
  segment_duration: 4
aws: {bucket: b, region: us-east-1, access_key_id: k, secret_access_key: s}
supabase: {url: "https://x.supabase.co", service_role_key: srk}
device: {serial_number: GTR-1, name: R, gateway_id: gw-1}
ptz:
  poll_interval_s: 2
  command_max_retries: 0
  update_max_retries: 0
  realtime_reconnect_max_retries: 0
"""


@pytest.fixture(autouse=True)
def _camera_config(tmp_path: Path) -> None:
    cfg = tmp_path / "router.yaml"
    cfg.write_text(_CAMERA_YAML, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(cfg)
    reset_config()
    yield
    os.environ.pop("ROUTER_CONFIG", None)
    reset_config()
