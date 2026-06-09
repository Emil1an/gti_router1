"""Fixtures for upload tests (Stories 2.1, 2.2, 2.3).

Provides:
* A minimal router.yaml with ``user_id`` / ``router_id`` and the S3 bucket
  configured for moto mocks.
* Fake AWS credentials injected into the environment so aioboto3 / moto do not
  raise a NoCredentialsError.
* A ``mock_s3_bucket`` fixture that activates moto and creates the test bucket.
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from config.loader import reset_config

# ── Minimal YAML for upload tests ──────────────────────────────────────────────

_UPLOAD_YAML = """\
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
  upload_max_retries: 3
supabase:
  url: "https://test.supabase.co"
  service_role_key: "srk"
device:
  serial_number: GTR-TEST-001
  name: Test Router
  user_id: user-abc123
  router_id: router-def456
  sku: base
"""

_BUCKET = "test-bucket"
_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _upload_config(tmp_path: Path) -> None:
    """Write a minimal router.yaml and point ROUTER_CONFIG at it for every test."""
    cfg_file = tmp_path / "router.yaml"
    cfg_file.write_text(_UPLOAD_YAML, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(cfg_file)
    reset_config()
    yield
    del os.environ["ROUTER_CONFIG"]
    reset_config()


@pytest.fixture()
def aws_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set fake AWS credentials so moto / aioboto3 initialise without errors."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)


@pytest.fixture()
def mock_s3(aws_creds: None):  # noqa: F811
    """Activate moto mock_aws and create the test bucket.

    Yields the bucket name as a string.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name=_REGION)
        s3.create_bucket(Bucket=_BUCKET)
        yield _BUCKET
