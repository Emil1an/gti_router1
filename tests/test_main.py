"""Tests for the lifecycle orchestrator (Story 3.7).

Every subsystem is mocked at the class boundary (each exposes ``start``/``stop``),
so we verify the 12-step init order, the 6-step shutdown order, the READY=1 /
STOPPING=1 signals, the final health report, and the per-scenario exit codes —
all without hardware or network.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main as main_mod
from config.loader import reset_config
from main import EXIT_CAMERA, EXIT_CONFIG, EXIT_OK, EXIT_PIPELINE, RouterApp

_RTSP_YAML = """\
cameras:
  - camera_id: cam-test
    input_type: rtsp_ip
    rtsp_url: "rtsp://admin:pass@192.168.1.1:554/stream"
    orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}
hls:
  segment_duration: 4
aws: {bucket: b, region: us-east-1, access_key_id: k, secret_access_key: s}
supabase: {url: "https://x.supabase.co", service_role_key: srk}
device: {serial_number: GTR-1, name: R, gateway_id: gw-1, firmware_version: "1.0"}
upload: {shutdown_timeout_s: 5}
"""

# 4 IP cameras exceed an RPi4's physical limit (2 IP + 1 capture) → exit 2.
_OVER_LIMIT_YAML = """\
cameras:
  - {camera_id: c1, input_type: rtsp_ip, rtsp_url: "rtsp://a:b@10.0.0.1:554/s", orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}}
  - {camera_id: c2, input_type: rtsp_ip, rtsp_url: "rtsp://a:b@10.0.0.2:554/s", orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}}
  - {camera_id: c3, input_type: rtsp_ip, rtsp_url: "rtsp://a:b@10.0.0.3:554/s", orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}}
  - {camera_id: c4, input_type: rtsp_ip, rtsp_url: "rtsp://a:b@10.0.0.4:554/s", orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}}
hls:
  segment_duration: 4
aws: {bucket: b, region: us-east-1, access_key_id: k, secret_access_key: s}
supabase: {url: "https://x.supabase.co", service_role_key: srk}
device: {serial_number: GTR-1, name: R}
"""


@pytest.fixture(autouse=True)
def _valid_config(tmp_path: Path):
    cfg = tmp_path / "router.yaml"
    cfg.write_text(_RTSP_YAML, encoding="utf-8")
    os.environ["ROUTER_CONFIG"] = str(cfg)
    reset_config()
    yield
    os.environ.pop("ROUTER_CONFIG", None)
    reset_config()


# ── Component-patching helper ────────────────────────────────────────────────────

def _component(order: list[str], name: str) -> MagicMock:
    inst = MagicMock()

    async def _start(*_a, **_k):
        order.append(f"start:{name}")

    async def _stop(*_a, **_k):
        order.append(f"stop:{name}")

    inst.start = AsyncMock(side_effect=_start)
    inst.stop = AsyncMock(side_effect=_stop)
    return inst


def _patch_components(stack: ExitStack, order: list[str]) -> dict[str, MagicMock]:
    """Patch every subsystem class in main with order-recording mocks."""
    reg = _component(order, "registration")
    mon = _component(order, "monitor")
    svc = _component(order, "upload")
    rep = _component(order, "reporter")
    gps = _component(order, "gps")
    orient = _component(order, "orientation")
    snap = _component(order, "snapshot")
    ptz = _component(order, "ptz")
    wd = _component(order, "watchdog")

    async def _final_report(*_a, **_k):
        order.append("final_report")

    rep.report_once = AsyncMock(side_effect=_final_report)
    wd.notify_ready = MagicMock(side_effect=lambda: order.append("READY=1"))
    wd.notify_stopping = MagicMock(side_effect=lambda: order.append("STOPPING=1"))

    stack.enter_context(patch.object(main_mod, "SupabaseClient", MagicMock()))
    stack.enter_context(patch.object(main_mod, "DeviceRegistration", MagicMock(return_value=reg)))
    stack.enter_context(patch.object(main_mod, "SystemMonitor", MagicMock(return_value=mon)))
    stack.enter_context(patch.object(main_mod, "UploadService", MagicMock(return_value=svc)))
    stack.enter_context(patch.object(main_mod, "HealthReporter", MagicMock(return_value=rep)))
    stack.enter_context(patch.object(main_mod, "GpsReader", MagicMock(return_value=gps)))
    stack.enter_context(patch.object(main_mod, "OrientationPublisher", MagicMock(return_value=orient)))
    stack.enter_context(patch.object(main_mod, "SnapshotService", MagicMock(return_value=snap)))
    stack.enter_context(patch.object(main_mod, "PTZService", MagicMock(return_value=ptz)))
    stack.enter_context(patch.object(main_mod, "Watchdog", MagicMock(return_value=wd)))
    return {
        "registration": reg, "monitor": mon, "upload": svc, "reporter": rep,
        "gps": gps, "orientation": orient, "snapshot": snap,
        "ptz": ptz, "watchdog": wd,
    }


# ── Init order (12 steps) ─────────────────────────────────────────────────────────

class TestStartup:
    async def test_start_order(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            _patch_components(stack, order)
            app = RouterApp()
            await app.startup()

        assert order == [
            "start:registration",
            "start:monitor",
            "start:upload",
            "start:reporter",
            "start:gps",
            "start:orientation",
            "start:snapshot",
            "start:ptz",
            "start:watchdog",
            "READY=1",
        ]

    async def test_ready_emitted_after_all_starts(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            comps = _patch_components(stack, order)
            app = RouterApp()
            await app.startup()
        comps["watchdog"].notify_ready.assert_called_once()
        # READY must be the very last init action.
        assert order[-1] == "READY=1"


# ── Shutdown order (6 steps) ──────────────────────────────────────────────────────

class TestShutdown:
    async def test_shutdown_order(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            _patch_components(stack, order)
            app = RouterApp()
            await app.startup()
            order.clear()
            await app.shutdown()

        assert order == [
            "STOPPING=1",
            "final_report",
            "stop:ptz",
            "stop:snapshot",
            "stop:orientation",
            "stop:gps",
            "stop:reporter",
            "stop:upload",
            "stop:monitor",
            "stop:registration",
            "stop:watchdog",
        ]

    async def test_final_report_emitted_during_shutdown(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            comps = _patch_components(stack, order)
            app = RouterApp()
            await app.startup()
            await app.shutdown()
        comps["reporter"].report_once.assert_awaited()

    async def test_upload_stop_gets_drain_timeout(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            comps = _patch_components(stack, order)
            app = RouterApp()
            await app.startup()
            await app.shutdown()
        # shutdown_timeout_s=5 from the test config.
        _args, kwargs = comps["upload"].stop.call_args
        assert kwargs.get("drain_timeout_s") == 5.0


# ── Exit codes ────────────────────────────────────────────────────────────────────

class TestExitCodes:
    async def test_clean_shutdown_returns_zero(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            _patch_components(stack, order)
            app = RouterApp()
            app.request_shutdown()  # pre-arm so run() doesn't block
            code = await app.run()
        assert code == EXIT_OK

    async def test_config_error_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            _patch_components(stack, order)
            monkeypatch.delenv("ROUTER_CONFIG", raising=False)
            reset_config()
            app = RouterApp()
            code = await app.run()
        assert code == EXIT_CONFIG

    async def test_camera_error_returns_two(self, tmp_path: Path) -> None:
        from platform.board import Board

        order: list[str] = []
        over_cfg = tmp_path / "over.yaml"
        over_cfg.write_text(_OVER_LIMIT_YAML, encoding="utf-8")
        with ExitStack() as stack:
            _patch_components(stack, order)
            # Force an RPi4 board: 4 IP cameras exceed its 2-IP limit → exit 2.
            stack.enter_context(
                patch("platform.board.detect_board", return_value=Board.RPI4)
            )
            os.environ["ROUTER_CONFIG"] = str(over_cfg)
            reset_config()
            app = RouterApp()
            code = await app.run()
        assert code == EXIT_CAMERA

    async def test_pipeline_error_returns_three(self) -> None:
        order: list[str] = []
        with ExitStack() as stack:
            comps = _patch_components(stack, order)
            comps["upload"].start = AsyncMock(side_effect=RuntimeError("ffmpeg boom"))
            app = RouterApp()
            code = await app.run()
        assert code == EXIT_PIPELINE
