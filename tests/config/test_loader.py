"""Tests for src/config/loader.py."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from config.loader import get_config, reset_config
from utils.errors import ConfigValidationError


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Ensure each test starts with a clean config cache."""
    reset_config()


@pytest.fixture()
def config_file(tmp_path: Path, minimal_yaml: str) -> Path:
    """Write minimal valid YAML to a tmp file and point $ROUTER_CONFIG at it."""
    p = tmp_path / "router.yaml"
    p.write_text(minimal_yaml, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(p)
    yield p
    del os.environ["ROUTER_CONFIG"]


# ── Happy path ─────────────────────────────────────────────────────────────────

class TestGetConfigValid:
    def test_loads_valid_config(self, config_file: Path) -> None:
        cfg = get_config()
        assert cfg.device.serial_number == "GTR-TEST-001"
        assert len(cfg.cameras) == 1
        assert cfg.cameras[0].camera_id == "cam-test"

    def test_singleton_returns_same_object(self, config_file: Path) -> None:
        a = get_config()
        b = get_config()
        assert a is b

    def test_reload_rereads_file(self, config_file: Path) -> None:
        a = get_config()
        b = get_config(reload=True)
        # Different object but equal content
        assert a is not b
        assert a.device.serial_number == b.device.serial_number

    def test_hls_default_segment_duration(self, config_file: Path) -> None:
        cfg = get_config()
        assert cfg.hls.segment_duration == 4


# ── ${ENV} expansion ──────────────────────────────────────────────────────────

class TestEnvExpansion:
    def test_env_vars_are_expanded(self, tmp_path: Path) -> None:
        os.environ["TEST_S3_KEY"] = "expanded-key"
        yaml_content = (
            """
cameras:
  - camera_id: cam-env
    input_type: rtsp_ip
    rtsp_url: "rtsp://x@192.168.1.1/s"
    orientation:
      azimuth: 0.0
      tilt: 0.0
      fov_h: 90.0
      mount_height_m: 3.0
aws:
  bucket: b
  region: us-east-1
  access_key_id: "${TEST_S3_KEY}"
  secret_access_key: "static-secret"
supabase:
  url: "https://x.supabase.co"
  service_role_key: "srk"
device:
  serial_number: GTR-ENV
  name: EnvRouter
"""
        )
        p = tmp_path / "router.yaml"
        p.write_text(yaml_content, encoding="utf-8")
        os.environ["ROUTER_CONFIG"] = str(p)
        try:
            cfg = get_config()
            assert cfg.aws.access_key_id == "expanded-key"
        finally:
            del os.environ["ROUTER_CONFIG"]
            del os.environ["TEST_S3_KEY"]

    def test_missing_env_var_raises(self, tmp_path: Path) -> None:
        yaml_content = """
cameras:
  - camera_id: c
    input_type: rtsp_ip
    rtsp_url: "rtsp://x/s"
    orientation:
      azimuth: 0.0
      tilt: 0.0
      fov_h: 90.0
      mount_height_m: 3.0
aws:
  bucket: b
  region: us-east-1
  access_key_id: "${NONEXISTENT_VAR_XYZ}"
  secret_access_key: s
supabase:
  url: "https://x.supabase.co"
  service_role_key: "srk"
device:
  serial_number: GTR-X
  name: X
"""
        p = tmp_path / "router.yaml"
        p.write_text(yaml_content, encoding="utf-8")
        os.environ["ROUTER_CONFIG"] = str(p)
        try:
            with pytest.raises(ConfigValidationError, match="NONEXISTENT_VAR_XYZ"):
                get_config()
        finally:
            del os.environ["ROUTER_CONFIG"]


# ── Failure modes ──────────────────────────────────────────────────────────────

class TestGetConfigFailures:
    def test_no_config_file_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ROUTER_CONFIG", raising=False)
        # Patch the path constants so the test doesn't depend on /etc or /boot
        monkeypatch.setattr("config.loader._ETC_PATH", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr("config.loader._BOOT_PATH", tmp_path / "nonexistent2.yaml")
        with pytest.raises(ConfigValidationError, match="No configuration file found"):
            get_config()

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "router.yaml"
        p.write_text(": invalid: yaml: {{", encoding="utf-8")
        os.environ["ROUTER_CONFIG"] = str(p)
        try:
            with pytest.raises(ConfigValidationError):
                get_config()
        finally:
            del os.environ["ROUTER_CONFIG"]

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "router.yaml"
        p.write_text("cameras: []\n", encoding="utf-8")
        os.environ["ROUTER_CONFIG"] = str(p)
        try:
            with pytest.raises(ConfigValidationError):
                get_config()
        finally:
            del os.environ["ROUTER_CONFIG"]

    def test_invalid_input_type_raises(self, tmp_path: Path, minimal_yaml: str) -> None:
        broken = minimal_yaml.replace("rtsp_ip", "unknown_type")
        p = tmp_path / "router.yaml"
        p.write_text(broken, encoding="utf-8")
        os.environ["ROUTER_CONFIG"] = str(p)
        try:
            with pytest.raises(ConfigValidationError):
                get_config()
        finally:
            del os.environ["ROUTER_CONFIG"]

    def test_orientation_out_of_range_raises(self, tmp_path: Path, minimal_yaml: str) -> None:
        broken = minimal_yaml.replace("azimuth: 0.0", "azimuth: 400.0")
        p = tmp_path / "router.yaml"
        p.write_text(broken, encoding="utf-8")
        os.environ["ROUTER_CONFIG"] = str(p)
        try:
            with pytest.raises(ConfigValidationError):
                get_config()
        finally:
            del os.environ["ROUTER_CONFIG"]


# ── First-boot copy /boot → /etc ───────────────────────────────────────────────

class TestBootCopy:
    def test_boot_config_copied_to_etc(
        self, tmp_path: Path, minimal_yaml: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boot = tmp_path / "boot" / "router.yaml"
        etc = tmp_path / "etc" / "gti-router" / "router.yaml"
        boot.parent.mkdir(parents=True)
        boot.write_text(minimal_yaml, encoding="utf-8")

        monkeypatch.setattr("config.loader._BOOT_PATH", boot)
        monkeypatch.setattr("config.loader._ETC_PATH", etc)
        monkeypatch.delenv("ROUTER_CONFIG", raising=False)

        cfg = get_config()
        assert etc.exists(), "/etc copy must be created on first boot"
        assert cfg.device.serial_number == "GTR-TEST-001"
        # Permissions are only enforced on POSIX systems (chmod is a no-op on Windows/NTFS).
        if hasattr(os, "getuid"):
            mode = etc.stat().st_mode & 0o777
            assert mode == stat.S_IRUSR | stat.S_IWUSR, f"Expected 0600, got {oct(mode)}"

    def test_boot_copy_skipped_when_etc_exists(
        self, tmp_path: Path, minimal_yaml: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boot = tmp_path / "boot" / "router.yaml"
        etc = tmp_path / "etc" / "gti-router" / "router.yaml"
        boot.parent.mkdir(parents=True)
        etc.parent.mkdir(parents=True)
        boot.write_text(minimal_yaml.replace("GTR-TEST-001", "BOOT-SERIAL"), encoding="utf-8")
        etc.write_text(minimal_yaml, encoding="utf-8")

        monkeypatch.setattr("config.loader._BOOT_PATH", boot)
        monkeypatch.setattr("config.loader._ETC_PATH", etc)
        monkeypatch.delenv("ROUTER_CONFIG", raising=False)

        cfg = get_config()
        # Should have loaded /etc version, not the boot version
        assert cfg.device.serial_number == "GTR-TEST-001"
