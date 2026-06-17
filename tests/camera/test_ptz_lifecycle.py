"""Tests for PTZ lifecycle integration (Story 4.5).

PTZController + CommandReceiver are patched; no hardware/network.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import camera.ptz_service as ptz_service_mod
from camera.ptz_control import PTZController
from camera.ptz_service import PTZService
from config.loader import get_config
from health.state import AppState
from utils.errors import PTZConnectionError, PTZUnsupportedError


def _controller(supports=True, position=None) -> MagicMock:
    c = MagicMock(spec=PTZController)
    c.connect = AsyncMock()
    c.disconnect = AsyncMock()
    c.get_position = AsyncMock(
        return_value=position or {"pan": 0.1, "tilt": 0.0, "zoom": 0.0, "preset": None}
    )
    c.supports_pan = supports
    c.supports_tilt = supports
    c.supports_zoom = supports
    c.supports_presets = supports
    return c


def _patch(stack: ExitStack, controller: MagicMock, receiver: MagicMock):
    ctrl_cls = MagicMock()
    ctrl_cls.from_camera_config.return_value = controller
    stack.enter_context(patch.object(ptz_service_mod, "PTZController", ctrl_cls))
    stack.enter_context(
        patch.object(ptz_service_mod, "CommandReceiver", MagicMock(return_value=receiver))
    )
    return ctrl_cls


def _receiver() -> MagicMock:
    r = MagicMock()
    r.start = AsyncMock()
    r.stop = AsyncMock()
    r.ptz_realtime_connected = False
    return r


def _registered_state() -> AppState:
    s = AppState()
    s.supabase_connected = True
    s.gateway_id = "gw-1"
    return s


def _cameras():
    return list(get_config().cameras)  # cam-1 ptz_enabled, cam-2 not (from conftest)


# ── Activation gating ────────────────────────────────────────────────────────────

class TestActivation:
    async def test_activates_when_registered_and_ptz_capable(self) -> None:
        controller, receiver = _controller(), _receiver()
        with ExitStack() as stack:
            ctrl_cls = _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), _registered_state(), cameras=_cameras())
            await svc.start()
            await svc.stop()

        assert svc.activated is True
        controller.connect.assert_awaited()        # only cam-1 (ptz_enabled)
        receiver.start.assert_awaited_once()
        # cam-2 has ptz_enabled=False → only one controller built.
        assert ctrl_cls.from_camera_config.call_count == 1

    async def test_inactive_without_registration(self) -> None:
        controller, receiver = _controller(), _receiver()
        state = AppState()  # supabase_connected=False, no gateway_id
        with ExitStack() as stack:
            _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), state, cameras=_cameras())
            await svc.start()
            try:
                assert svc.activated is False
                receiver.start.assert_not_awaited()
                controller.connect.assert_not_awaited()
            finally:
                await svc.stop()


# ── Camera without PTZ → INFO, continue ──────────────────────────────────────────

class TestNoPtzCamera:
    async def test_camera_without_ptz_skipped(self) -> None:
        controller, receiver = _controller(), _receiver()
        controller.connect = AsyncMock(side_effect=PTZUnsupportedError("no ptz service"))
        with ExitStack() as stack:
            _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), _registered_state(), cameras=_cameras())
            await svc.start()
            await svc.stop()

        # No controllers → receiver not started, but it's not a failure.
        receiver.start.assert_not_awaited()
        assert svc.activated is True


# ── Fault isolation ──────────────────────────────────────────────────────────────

class TestFaultIsolation:
    async def test_connect_failure_is_contained(self) -> None:
        controller, receiver = _controller(), _receiver()
        controller.connect = AsyncMock(side_effect=PTZConnectionError("onvif down"))
        with ExitStack() as stack:
            _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), _registered_state(), cameras=_cameras())
            # Must not raise — PTZ failure is contained.
            await svc.start()
            await svc.stop()
        receiver.start.assert_not_awaited()


# ── Health report inclusion ──────────────────────────────────────────────────────

class TestHealthInclusion:
    async def test_capabilities_and_position_published(self) -> None:
        controller, receiver = _controller(), _receiver()
        state = _registered_state()
        with ExitStack() as stack:
            _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), state, cameras=_cameras())
            await svc.start()
            await svc.stop()

        ptz = state.per_camera["cam-1"].ptz
        assert ptz is not None
        assert ptz["supported"] is True
        assert ptz["capabilities"] == {"pan": True, "tilt": True, "zoom": True, "presets": True}
        assert ptz["position"] == {"pan": 0.1, "tilt": 0.0, "zoom": 0.0, "preset": None}


# ── Ordered start/stop ───────────────────────────────────────────────────────────

class TestStartStop:
    async def test_stop_disconnects_controllers_and_stops_receiver(self) -> None:
        controller, receiver = _controller(), _receiver()
        with ExitStack() as stack:
            _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), _registered_state(), cameras=_cameras())
            await svc.start()
            await svc.stop()
        receiver.stop.assert_awaited_once()
        controller.disconnect.assert_awaited_once()

    async def test_stop_is_safe_when_inactive(self) -> None:
        controller, receiver = _controller(), _receiver()
        with ExitStack() as stack:
            _patch(stack, controller, receiver)
            svc = PTZService(MagicMock(), AppState(), cameras=_cameras())
            await svc.start()   # not registered → inactive
            await svc.stop()    # must not raise
