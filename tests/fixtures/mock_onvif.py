"""Mock ONVIF camera for PTZ tests (no hardware, no onvif-zeep needed).

Builds a fake ``ONVIFCamera`` whose PTZ service records the requests it receives
so tests can assert the right ONVIF operations were issued.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _Range:
    def __init__(self, lo: float, hi: float) -> None:
        self.Min = lo
        self.Max = hi


class _RecordingPTZService:
    """Fake PTZ service: records calls and returns canned responses."""

    def __init__(self, with_presets: bool = True) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._with_presets = with_presets

    # create_type returns a blank request object to fill in.
    def create_type(self, name: str) -> SimpleNamespace:
        self.calls.append(("create_type", name))
        return SimpleNamespace()

    def GetConfigurations(self) -> list[Any]:
        return [SimpleNamespace(NodeToken="node-0")]

    def GetNode(self, params: Any) -> Any:
        spaces = SimpleNamespace(
            AbsolutePanTiltPositionSpace=[
                SimpleNamespace(XRange=_Range(-1.0, 1.0), YRange=_Range(-1.0, 1.0))
            ],
            ContinuousPanTiltVelocitySpace=[SimpleNamespace()],
            RelativePanTiltTranslationSpace=[SimpleNamespace()],
            AbsoluteZoomPositionSpace=[SimpleNamespace(XRange=_Range(0.0, 1.0))],
            ContinuousZoomVelocitySpace=[SimpleNamespace()],
        )
        return SimpleNamespace(
            SupportedPTZSpaces=spaces,
            MaximumNumberOfPresets=8 if self._with_presets else 0,
        )

    # Movement operations — record the request.
    def ContinuousMove(self, req: Any) -> str:
        self.calls.append(("ContinuousMove", req))
        return "ok"

    def RelativeMove(self, req: Any) -> str:
        self.calls.append(("RelativeMove", req))
        return "ok"

    def AbsoluteMove(self, req: Any) -> str:
        self.calls.append(("AbsoluteMove", req))
        return "ok"

    def Stop(self, req: Any) -> str:
        self.calls.append(("Stop", req))
        return "ok"

    def GetPresets(self, params: Any) -> list[Any]:
        self.calls.append(("GetPresets", params))
        return [SimpleNamespace(token="preset-1", Name="Home")]

    def GotoPreset(self, req: Any) -> str:
        self.calls.append(("GotoPreset", req))
        return "ok"

    def GetStatus(self, params: Any) -> Any:
        self.calls.append(("GetStatus", params))
        return SimpleNamespace(
            Position=SimpleNamespace(
                PanTilt=SimpleNamespace(x=0.25, y=-0.5),
                Zoom=SimpleNamespace(x=0.1),
            ),
            PresetToken="preset-1",
        )

    def names_called(self) -> list[str]:
        return [name for name, _ in self.calls]


class _MediaService:
    def GetProfiles(self) -> list[Any]:
        return [SimpleNamespace(token="profile-0")]


class FakeONVIFCamera:
    """Stand-in for ``onvif.ONVIFCamera`` used via patching."""

    def __init__(self, *args: Any, ptz_service: _RecordingPTZService | None = None,
                 has_ptz: bool = True, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self._ptz_service = ptz_service or _RecordingPTZService()
        self._has_ptz = has_ptz

    def create_media_service(self) -> _MediaService:
        return _MediaService()

    def create_ptz_service(self) -> _RecordingPTZService:
        if not self._has_ptz:
            raise RuntimeError("no PTZ service on this device")
        return self._ptz_service


def make_onvif_factory(ptz_service: _RecordingPTZService | None = None,
                       has_ptz: bool = True):
    """Return a callable usable as a patched ``ONVIFCamera`` constructor."""
    service = ptz_service or _RecordingPTZService()

    def _factory(*args: Any, **kwargs: Any) -> FakeONVIFCamera:
        return FakeONVIFCamera(*args, ptz_service=service, has_ptz=has_ptz, **kwargs)

    _factory.service = service  # type: ignore[attr-defined]
    return _factory
