"""ONVIF PTZ controller (Story 4.1).

``PTZController`` is the single project gateway for speaking ONVIF (Profile S) to
a camera, built on ``onvif-zeep``.  ``onvif-zeep`` is **synchronous** (zeep /
requests under the hood), so every SOAP call is offloaded with
:func:`asyncio.to_thread` and wrapped with the single ``@with_retry`` — the event
loop that drives video + health is never blocked.

All ONVIF/zeep/requests failures are translated into typed
:class:`~utils.errors.PTZError` subclasses; raw third-party exceptions never
escape this module.  Every operation logs with ``camera_id`` context and emits
``ptz_command_latency_ms``.

``onvif-zeep`` is imported defensively so the module (and its tests) load even
when the library is absent (dev/CI); tests patch :data:`ONVIFCamera`.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from config.schema import CameraConfig
from utils.errors import (
    PTZAuthError,
    PTZCommandError,
    PTZConnectionError,
    PTZError,
    PTZUnsupportedError,
)
from utils.logging import get_logger
from utils.retry import with_retry

try:  # onvif-zeep is optional at import time (heavy native deps; absent in CI)
    from onvif import ONVIFCamera  # type: ignore[import-untyped]
except Exception:  # noqa: BLE001 — any import problem → treat as unavailable
    ONVIFCamera = None  # type: ignore[assignment]


def _looks_like_auth(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(k in text for k in ("unauthor", "not authorized", "401", "auth", "password"))


def _looks_like_connection(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout, OSError)):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(k in text for k in ("timed out", "timeout", "connection", "refused", "unreachable", "network"))


class PTZController:
    """Typed async wrapper over an ONVIF Profile S PTZ service for one camera."""

    def __init__(
        self,
        camera_id: str,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
        ptz_enabled: bool = True,
        timeout_s: int = 10,
        command_max_retries: int = 3,
    ) -> None:
        self._camera_id = camera_id
        self._host = host
        self._port = port
        self._username = username or ""
        self._password = password or ""
        self._ptz_enabled = ptz_enabled
        self._timeout_s = timeout_s
        self._cmd_retries = command_max_retries
        self._logger = get_logger(__name__, camera_id=camera_id)

        self._cam: Any | None = None
        self._ptz: Any | None = None
        self._media: Any | None = None
        self._profile_token: str | None = None
        self._connected = False

        # Capabilities (populated by connect()).
        self.supports_pan = False
        self.supports_tilt = False
        self.supports_zoom = False
        self.supports_presets = False
        self._pan_range: tuple[float, float] | None = None
        self._tilt_range: tuple[float, float] | None = None
        self._zoom_range: tuple[float, float] | None = None

    @classmethod
    def from_camera_config(cls, cam: CameraConfig, timeout_s: int = 10,
                           command_max_retries: int = 3) -> "PTZController":
        """Build a controller from a :class:`CameraConfig`, deriving the ONVIF
        host from ``onvif_host`` or the RTSP URL host."""
        host = cam.onvif_host or _host_from_rtsp(cam.rtsp_url) or ""
        return cls(
            camera_id=cam.camera_id,
            host=host,
            port=cam.onvif_port,
            username=cam.onvif_username,
            password=cam.onvif_password,
            ptz_enabled=cam.ptz_enabled,
            timeout_s=timeout_s,
            command_max_retries=command_max_retries,
        )

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def camera_id(self) -> str:
        return self._camera_id

    async def connect(self) -> None:
        """Connect, resolve the media profile + PTZ service, detect capabilities.

        Raises:
            PTZUnsupportedError: PTZ disabled in config / no PTZ service / no lib.
            PTZAuthError:        credentials rejected.
            PTZConnectionError:  cannot reach the camera (network/timeout).
        """
        if not self._ptz_enabled:
            raise PTZUnsupportedError(
                f"[{self._camera_id}] PTZ is not enabled for this camera"
            )
        if ONVIFCamera is None:
            raise PTZUnsupportedError(
                f"[{self._camera_id}] onvif-zeep is not installed"
            )
        if not self._host:
            raise PTZUnsupportedError(
                f"[{self._camera_id}] no ONVIF host configured/derivable"
            )

        def _do_connect() -> tuple[Any, Any, Any, str, Any]:
            cam = ONVIFCamera(self._host, self._port, self._username, self._password)
            media = cam.create_media_service()
            profiles = media.GetProfiles()
            if not profiles:
                raise PTZUnsupportedError(
                    f"[{self._camera_id}] camera reports no media profiles"
                )
            token = profiles[0].token
            try:
                ptz = cam.create_ptz_service()
            except PTZError:
                raise
            except Exception as exc:  # no PTZ service on this camera
                raise PTZUnsupportedError(
                    f"[{self._camera_id}] camera has no PTZ service: {exc}"
                ) from exc
            node = _resolve_ptz_node(ptz)
            return cam, media, ptz, token, node

        cam, media, ptz, token, node = await self._invoke("connect", _do_connect)
        self._cam, self._media, self._ptz, self._profile_token = cam, media, ptz, token
        self._apply_capabilities(node)
        self._connected = True
        self._logger.info(
            "PTZ connected",
            extra={
                "supports_pan": self.supports_pan,
                "supports_tilt": self.supports_tilt,
                "supports_zoom": self.supports_zoom,
                "supports_presets": self.supports_presets,
            },
        )

    async def disconnect(self) -> None:
        """Release the controller (onvif-zeep keeps no persistent socket)."""
        self._connected = False
        self._logger.info("PTZ disconnected")

    # ── Movement API ─────────────────────────────────────────────────────────────

    async def continuous_move(
        self, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0
    ) -> Any:
        """Start a continuous PTZ move (velocity space). Use :meth:`stop` to halt."""
        self._ensure_connected()

        def _fn() -> Any:
            req = self._ptz.create_type("ContinuousMove")
            req.ProfileToken = self._profile_token
            req.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
            return self._ptz.ContinuousMove(req)

        return await self._invoke("continuous_move", _fn)

    async def relative_move(
        self, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0
    ) -> Any:
        """Move by a relative translation."""
        self._ensure_connected()

        def _fn() -> Any:
            req = self._ptz.create_type("RelativeMove")
            req.ProfileToken = self._profile_token
            req.Translation = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
            return self._ptz.RelativeMove(req)

        return await self._invoke("relative_move", _fn)

    async def absolute_move(
        self, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0
    ) -> Any:
        """Move to an absolute position (clamped to the camera's reported ranges)."""
        self._ensure_connected()
        pan = _clamp(pan, self._pan_range)
        tilt = _clamp(tilt, self._tilt_range)
        zoom = _clamp(zoom, self._zoom_range)

        def _fn() -> Any:
            req = self._ptz.create_type("AbsoluteMove")
            req.ProfileToken = self._profile_token
            req.Position = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
            return self._ptz.AbsoluteMove(req)

        return await self._invoke("absolute_move", _fn)

    async def stop(self, pan_tilt: bool = True, zoom: bool = True) -> Any:
        """Stop ongoing pan/tilt and/or zoom motion."""
        self._ensure_connected()

        def _fn() -> Any:
            req = self._ptz.create_type("Stop")
            req.ProfileToken = self._profile_token
            req.PanTilt = pan_tilt
            req.Zoom = zoom
            return self._ptz.Stop(req)

        return await self._invoke("stop", _fn)

    async def get_presets(self) -> list[Any]:
        """Return the camera's stored presets."""
        self._ensure_connected()

        def _fn() -> list[Any]:
            return self._ptz.GetPresets({"ProfileToken": self._profile_token}) or []

        return await self._invoke("get_presets", _fn)

    async def go_to_preset(self, preset_token: str) -> Any:
        """Recall a stored preset."""
        self._ensure_connected()

        def _fn() -> Any:
            req = self._ptz.create_type("GotoPreset")
            req.ProfileToken = self._profile_token
            req.PresetToken = preset_token
            return self._ptz.GotoPreset(req)

        return await self._invoke("go_to_preset", _fn)

    async def get_position(self) -> dict[str, Any]:
        """Read the current PTZ position via ``GetStatus`` — **does not move**.

        Returns ``{"pan", "tilt", "zoom", "preset"}`` (values ``None`` when the
        camera does not report them).
        """
        self._ensure_connected()

        def _fn() -> Any:
            return self._ptz.GetStatus({"ProfileToken": self._profile_token})

        status = await self._invoke("get_position", _fn)
        return _parse_status(status)

    # ── Internals ─────────────────────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if not self._connected or self._ptz is None:
            raise PTZConnectionError(
                f"[{self._camera_id}] PTZ controller is not connected"
            )

    async def _invoke(self, op_name: str, fn: Any) -> Any:
        """Run a blocking ONVIF call off-thread with retry + latency + translation."""
        async def _attempt() -> Any:
            try:
                return await asyncio.to_thread(fn)
            except PTZError:
                raise  # already typed (e.g. PTZUnsupportedError from connect)
            except Exception as exc:  # noqa: BLE001 — translate any raw onvif/zeep error
                raise self._translate(op_name, exc) from exc

        wrapped = with_retry(
            max_retries=self._cmd_retries, retryable=(PTZConnectionError,)
        )(_attempt)

        start = time.monotonic()
        try:
            return await wrapped()
        finally:
            latency_ms = (time.monotonic() - start) * 1000.0
            self._logger.info(
                "PTZ operation",
                extra={"op": op_name, "ptz_command_latency_ms": round(latency_ms, 1)},
            )

    def _translate(self, op_name: str, exc: Exception) -> PTZError:
        """Map a raw onvif/zeep/requests exception to a typed PTZError."""
        if _looks_like_auth(exc):
            return PTZAuthError(f"[{self._camera_id}] {op_name}: authentication rejected: {exc}")
        if _looks_like_connection(exc):
            return PTZConnectionError(f"[{self._camera_id}] {op_name}: connection error: {exc}")
        return PTZCommandError(f"[{self._camera_id}] {op_name}: command failed: {exc}")

    def _apply_capabilities(self, node: Any) -> None:
        flags = _capabilities_from_node(node)
        self.supports_pan = flags["pan"]
        self.supports_tilt = flags["tilt"]
        self.supports_zoom = flags["zoom"]
        self.supports_presets = flags["presets"]
        self._pan_range = flags["pan_range"]
        self._tilt_range = flags["tilt_range"]
        self._zoom_range = flags["zoom_range"]


# ── Module helpers ───────────────────────────────────────────────────────────────

def _host_from_rtsp(rtsp_url: str | None) -> str | None:
    if not rtsp_url:
        return None
    try:
        from urllib.parse import urlparse
        return urlparse(rtsp_url).hostname
    except Exception:
        return None


def _resolve_ptz_node(ptz: Any) -> Any:
    """Best-effort fetch of the PTZ node (for capability detection)."""
    try:
        configs = ptz.GetConfigurations() or []
    except Exception:
        configs = []
    node_token = None
    if configs:
        node_token = getattr(configs[0], "NodeToken", None)
    try:
        if node_token is not None:
            return ptz.GetNode({"NodeToken": node_token})
    except Exception:
        return None
    return None


def _capabilities_from_node(node: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pan": False, "tilt": False, "zoom": False, "presets": False,
        "pan_range": None, "tilt_range": None, "zoom_range": None,
    }
    if node is None:
        return result

    spaces = getattr(node, "SupportedPTZSpaces", None)
    if spaces is not None:
        pt_abs = getattr(spaces, "AbsolutePanTiltPositionSpace", None) or []
        pt_cont = getattr(spaces, "ContinuousPanTiltVelocitySpace", None) or []
        pt_rel = getattr(spaces, "RelativePanTiltTranslationSpace", None) or []
        z_abs = getattr(spaces, "AbsoluteZoomPositionSpace", None) or []
        z_cont = getattr(spaces, "ContinuousZoomVelocitySpace", None) or []

        if pt_abs or pt_cont or pt_rel:
            result["pan"] = True
            result["tilt"] = True
        if z_abs or z_cont:
            result["zoom"] = True

        if pt_abs:
            result["pan_range"] = _range_of(pt_abs[0], "XRange")
            result["tilt_range"] = _range_of(pt_abs[0], "YRange")
        if z_abs:
            result["zoom_range"] = _range_of(z_abs[0], "XRange")

    if (getattr(node, "MaximumNumberOfPresets", 0) or 0) > 0:
        result["presets"] = True
    return result


def _range_of(space: Any, attr: str) -> tuple[float, float] | None:
    rng = getattr(space, attr, None)
    if rng is None:
        return None
    lo = getattr(rng, "Min", None)
    hi = getattr(rng, "Max", None)
    if lo is None or hi is None:
        return None
    return float(lo), float(hi)


def _clamp(value: float, rng: tuple[float, float] | None) -> float:
    if rng is None:
        return value
    lo, hi = rng
    return max(lo, min(hi, value))


def _parse_status(status: Any) -> dict[str, Any]:
    pan = tilt = zoom = None
    preset = None
    pos = getattr(status, "Position", None)
    if pos is not None:
        pt = getattr(pos, "PanTilt", None)
        if pt is not None:
            pan = getattr(pt, "x", None)
            tilt = getattr(pt, "y", None)
        z = getattr(pos, "Zoom", None)
        if z is not None:
            zoom = getattr(z, "x", None)
    # Some cameras report an active preset token in the status.
    preset = getattr(status, "PresetToken", None) or preset
    return {"pan": pan, "tilt": tilt, "zoom": zoom, "preset": preset}
