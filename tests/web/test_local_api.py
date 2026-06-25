"""Tests for the local console mini-API (Stories 11.1–11.4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.loader import get_config, reset_config
from health.monitor import SystemMonitor
from health.state import AppState, CameraState
from web.local_api import create_app

_YAML_TMPL = """\
cameras:
  - camera_id: cam-1
    input_type: rtsp_ip
    rtsp_url: "rtsp://admin:pass@192.168.1.1:554/stream"
    orientation: {{azimuth: 0.0, tilt: 0.0, fov_h: 90.0, mount_height_m: 5.0}}
hls:
  segment_duration: 4
  output_dir: "{hls_dir}"
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
console:
  static_dir: "{static_dir}"
"""


@pytest.fixture()
def client(tmp_path: Path):
    hls_dir = tmp_path / "hls"
    (hls_dir / "cam-1").mkdir(parents=True)
    cfg_file = tmp_path / "router.yaml"
    cfg_file.write_text(
        _YAML_TMPL.format(hls_dir=hls_dir.as_posix(), static_dir=(tmp_path / "ui").as_posix()),
        encoding="utf-8",
    )
    os.environ["ROUTER_CONFIG"] = str(cfg_file)
    reset_config()
    cfg = get_config()

    state = AppState()
    state.supabase_connected = True
    state.set_camera(
        CameraState(camera_id="cam-1", input_type="rtsp_ip", connected=True, streaming=True)
    )
    monitor = SystemMonitor()

    app = create_app(state=state, monitor=monitor, cfg=cfg)
    with TestClient(app) as c:
        c._hls_dir = hls_dir  # type: ignore[attr-defined]
        yield c

    del os.environ["ROUTER_CONFIG"]
    reset_config()


def test_identity(client: TestClient) -> None:
    r = client.get("/api/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["serial_number"] == "GTR-TEST-001"
    assert body["firmware_version"] == "1.2.3"
    assert body["gateway_id"] == "gw-abc-123"


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["connectivity"]["supabase"] is True
    assert set(body["upload_queue"]) == {"size", "pending", "success_count", "error_count"}


def test_cameras_fusion(client: TestClient) -> None:
    r = client.get("/api/cameras")
    assert r.status_code == 200
    cams = r.json()
    assert len(cams) == 1
    assert cams[0]["camera_id"] == "cam-1"
    assert cams[0]["connected"] is True
    assert cams[0]["hls_url"] == "/hls/cam-1/playlist.m3u8"
    assert cams[0]["has_last_frame"] is False


def test_qr_unregistered(client: TestClient) -> None:
    r = client.get("/api/qr")
    assert r.status_code == 200
    body = r.json()
    # No claim_token seeded → falls back to serial; not yet registered.
    assert body["claim_token"] == "GTR-TEST-001"
    assert body["status"] == "unregistered"


def test_last_frame_404_then_200(client: TestClient) -> None:
    assert client.get("/api/cameras/cam-1/last_frame.jpg").status_code == 404
    assert client.get("/api/cameras/unknown/last_frame.jpg").status_code == 404

    frame = client._hls_dir / "cam-1" / "last_frame.jpg"  # type: ignore[attr-defined]
    frame.write_bytes(b"\xff\xd8\xff\xe0jpegdata")
    r = client.get("/api/cameras/cam-1/last_frame.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
