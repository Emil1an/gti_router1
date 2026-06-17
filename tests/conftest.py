"""Shared pytest fixtures for the GTI Router test suite."""

from __future__ import annotations

import os
import platform as _stdlib_platform
import sys
from pathlib import Path

import pytest

# Make src/ importable without installing the package.
_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC))

# The project ships a ``src/platform`` package (Story 5.5), but Python's stdlib
# ``platform`` module is already imported by the interpreter/pytest/psutil and
# shadows it, so ``import platform.board`` would fail. Extend the *existing*
# stdlib platform module into a package by adding our directory to its
# ``__path__`` — this lets ``platform.board`` resolve to our file while leaving
# every stdlib ``platform`` function (used by psutil) intact.
_platform_pkg = str(_SRC / "platform")
if _platform_pkg not in getattr(_stdlib_platform, "__path__", []):
    _stdlib_platform.__path__ = [*getattr(_stdlib_platform, "__path__", []), _platform_pkg]


@pytest.fixture()
def minimal_yaml() -> str:
    """Return a minimal valid router.yaml content (no env-var secrets)."""
    return """
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
  service_role_key: "test-service-role-key"
device:
  serial_number: GTR-TEST-001
  name: Test Router
  sku: base
"""
