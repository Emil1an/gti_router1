"""Fixtures for pipeline tests.

Sets up a minimal valid config (via ROUTER_CONFIG env var) so that
HLSPipeline can call get_config() during construction without a real
/etc/gti-router/router.yaml on the test machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.loader import reset_config

_MINIMAL_YAML = """\
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
  service_role_key: "srk"
device:
  serial_number: GTR-TEST-001
  name: Test Router
  sku: base
"""


@pytest.fixture(autouse=True)
def _pipeline_config(tmp_path: Path) -> None:
    """Write a minimal router.yaml and point ROUTER_CONFIG at it for every test."""
    cfg_file = tmp_path / "router.yaml"
    cfg_file.write_text(_MINIMAL_YAML, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(cfg_file)
    reset_config()
    yield
    del os.environ["ROUTER_CONFIG"]
    reset_config()
