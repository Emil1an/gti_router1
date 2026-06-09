"""Fixtures for health-subsystem tests (Stories 3.1–3.3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.loader import reset_config

_HEALTH_YAML = """\
cameras:
  - camera_id: cam-test
    input_type: rtsp_ip
    rtsp_url: "rtsp://admin:pass@192.168.1.1:554/stream"
    orientation:
      azimuth: 0.0
      tilt: 0.0
      fov_h: 90.0
      mount_height_m: 5.0
hls:
  segment_duration: 4
aws:
  bucket: test-bucket
  region: us-east-1
  access_key_id: TESTKEY
  secret_access_key: TESTSECRET
supabase:
  url: "https://test.supabase.co"
  service_role_key: "srk-test"
device:
  serial_number: GTR-TEST-001
  name: Test Router
  gateway_id: gw-abc-123
  firmware_version: "1.2.3"
  sku: base
health:
  report_interval_s: 60
  monitor_interval_s: 5
  cpu_alert_threshold: 80.0
  memory_alert_threshold: 80.0
  disk_alert_threshold: 80.0
  temp_alert_threshold: 75.0
  temp_critical_threshold: 80.0
"""


@pytest.fixture(autouse=True)
def _health_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "router.yaml"
    cfg_file.write_text(_HEALTH_YAML, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(cfg_file)
    reset_config()
    yield
    del os.environ["ROUTER_CONFIG"]
    reset_config()
