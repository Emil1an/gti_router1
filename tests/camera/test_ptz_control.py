"""Tests for PTZController (Story 4.1).

``onvif-zeep`` is mocked via patching ``camera.ptz_control.ONVIFCamera`` — no
hardware and the real library need not be installed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import camera.ptz_control as ptz_mod
from camera.ptz_control import PTZController
from tests.fixtures.mock_onvif import _RecordingPTZService, make_onvif_factory
from utils.errors import (
    PTZAuthError,
    PTZCommandError,
    PTZConnectionError,
    PTZUnsupportedError,
)


def _controller(**kw) -> PTZController:
    defaults = dict(
        camera_id="cam-1", host="192.168.1.10", port=80,
        username="admin", password="pass", ptz_enabled=True,
        command_max_retries=0,
    )
    defaults.update(kw)
    return PTZController(**defaults)


async def _connected(service: _RecordingPTZService | None = None) -> PTZController:
    factory = make_onvif_factory(ptz_service=service)
    ctrl = _controller()
    with patch.object(ptz_mod, "ONVIFCamera", factory):
        await ctrl.connect()
    return ctrl


# ── Connection + capability detection ───────────────────────────────────────────

class TestConnect:
    async def test_connect_sets_connected_and_capabilities(self) -> None:
        ctrl = await _connected()
        assert ctrl.connected is True
        assert ctrl.supports_pan is True
        assert ctrl.supports_tilt is True
        assert ctrl.supports_zoom is True
        assert ctrl.supports_presets is True

    async def test_connect_no_presets(self) -> None:
        service = _RecordingPTZService(with_presets=False)
        ctrl = await _connected(service)
        assert ctrl.supports_presets is False

    async def test_ptz_disabled_raises_unsupported(self) -> None:
        ctrl = _controller(ptz_enabled=False)
        with pytest.raises(PTZUnsupportedError):
            await ctrl.connect()

    async def test_no_onvif_library_raises_unsupported(self) -> None:
        ctrl = _controller()
        with patch.object(ptz_mod, "ONVIFCamera", None):
            with pytest.raises(PTZUnsupportedError):
                await ctrl.connect()

    async def test_no_ptz_service_raises_unsupported(self) -> None:
        factory = make_onvif_factory(has_ptz=False)
        ctrl = _controller()
        with patch.object(ptz_mod, "ONVIFCamera", factory):
            with pytest.raises(PTZUnsupportedError):
                await ctrl.connect()


# ── Movement requests ────────────────────────────────────────────────────────────

class TestMovement:
    async def test_continuous_move_issues_continuous_move(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        await ctrl.continuous_move(pan=0.5, tilt=-0.5, zoom=0.1)
        assert "ContinuousMove" in service.names_called()
        # The request carried the profile token + velocity.
        _name, req = next(c for c in service.calls if c[0] == "ContinuousMove")
        assert req.ProfileToken == "profile-0"
        assert req.Velocity["PanTilt"] == {"x": 0.5, "y": -0.5}

    async def test_relative_move_issues_relative_move(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        await ctrl.relative_move(pan=0.1, tilt=0.2, zoom=0.0)
        assert "RelativeMove" in service.names_called()

    async def test_absolute_move_clamps_to_range(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        # Pan range is [-1, 1]; request 5.0 must be clamped to 1.0.
        await ctrl.absolute_move(pan=5.0, tilt=-9.0, zoom=2.0)
        _name, req = next(c for c in service.calls if c[0] == "AbsoluteMove")
        assert req.Position["PanTilt"] == {"x": 1.0, "y": -1.0}
        assert req.Position["Zoom"] == {"x": 1.0}  # zoom range [0,1]

    async def test_stop_issues_stop(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        await ctrl.stop()
        _name, req = next(c for c in service.calls if c[0] == "Stop")
        assert req.PanTilt is True
        assert req.Zoom is True

    async def test_get_presets(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        presets = await ctrl.get_presets()
        assert "GetPresets" in service.names_called()
        assert presets[0].token == "preset-1"

    async def test_go_to_preset(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        await ctrl.go_to_preset("preset-1")
        _name, req = next(c for c in service.calls if c[0] == "GotoPreset")
        assert req.PresetToken == "preset-1"


# ── get_position() does not move ────────────────────────────────────────────────

class TestGetPosition:
    async def test_returns_position(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        pos = await ctrl.get_position()
        assert pos == {"pan": 0.25, "tilt": -0.5, "zoom": 0.1, "preset": "preset-1"}

    async def test_does_not_issue_any_move(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)
        service.calls.clear()
        await ctrl.get_position()
        called = service.names_called()
        assert "GetStatus" in called
        for move in ("ContinuousMove", "RelativeMove", "AbsoluteMove", "GotoPreset", "Stop"):
            assert move not in called


# ── Error translation ────────────────────────────────────────────────────────────

class TestErrorTranslation:
    async def test_auth_error_translated(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)

        def _boom(_req):
            raise RuntimeError("401 Unauthorized")

        service.ContinuousMove = _boom  # type: ignore[assignment]
        with pytest.raises(PTZAuthError):
            await ctrl.continuous_move(pan=0.1)

    async def test_connection_error_translated(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)

        def _boom(_req):
            raise ConnectionError("connection refused")

        service.Stop = _boom  # type: ignore[assignment]
        with pytest.raises(PTZConnectionError):
            await ctrl.stop()

    async def test_generic_fault_translated_to_command_error(self) -> None:
        service = _RecordingPTZService()
        ctrl = await _connected(service)

        def _boom(_req):
            raise ValueError("SOAP fault: invalid token")

        service.AbsoluteMove = _boom  # type: ignore[assignment]
        with pytest.raises(PTZCommandError):
            await ctrl.absolute_move(pan=0.0)

    async def test_raw_onvif_exception_never_escapes(self) -> None:
        """Any raw exception must surface as a typed PTZError, never raw."""
        service = _RecordingPTZService()
        ctrl = await _connected(service)

        def _boom(_params):
            raise KeyError("zeep internal")

        service.GetStatus = _boom  # type: ignore[assignment]
        with pytest.raises(PTZCommandError):
            await ctrl.get_position()


# ── Not connected guard ──────────────────────────────────────────────────────────

class TestNotConnected:
    async def test_move_before_connect_raises(self) -> None:
        ctrl = _controller()
        with pytest.raises(PTZConnectionError):
            await ctrl.continuous_move(pan=0.1)
