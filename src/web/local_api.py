"""Local console mini-API — in-process FastAPI app (Epic 11, Stories 11.1–11.4).

This is the read-only HTTP surface the touch-screen console consumes. It runs
**inside the Router process** so it can read live :class:`~health.state.AppState`
and :class:`~health.monitor.SystemMonitor` without re-querying the cloud, and is
bound to loopback only (the server in :mod:`web.server` enforces ``127.0.0.1``).

Endpoints (all JSON unless noted)
---------------------------------
* ``GET /api/identity``                 → device identity (Story 11.1)
* ``GET /api/health``                   → live resource + connectivity snapshot
* ``GET /api/cameras``                  → config ⨯ runtime per-camera fusion
* ``GET /api/qr``                        → claim payload for the QR (Story 11.4)
* ``GET /api/cameras/{id}/last_frame.jpg`` → last retained JPEG (Story 11.2)
* ``/hls/...``  (static mount)           → HLS playlists/segments (Story 11.3)
* ``/`` (static mount, optional)         → exported Next.js bundle (Story 11.10)

Design notes
------------
* No business logic: the app only *reads* shared state and the filesystem the
  pipeline already writes. It never blocks the event loop with cloud calls.
* Robust to disconnected cameras / missing frames: missing data degrades to a
  clear status field or a 404, never a stack trace (AC#5).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.schema import RouterConfig
from health.monitor import SystemMonitor
from health.state import AppState

_LAST_FRAME_NAME = "last_frame.jpg"
_PLAYLIST_NAME = "playlist.m3u8"


# ── Response models ──────────────────────────────────────────────────────────────

class IdentityResponse(BaseModel):
    serial_number: str
    name: str
    sku: str
    firmware_version: str
    router_id: str | None = None
    gateway_id: str | None = None


class HealthResponse(BaseModel):
    # Resource snapshot (None until the first SystemMonitor sample lands).
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    temperature_celsius: float | None = None
    uptime_seconds: float | None = None
    sampled_at: str | None = None
    # Alert flags
    cpu_alert: bool = False
    memory_alert: bool = False
    disk_alert: bool = False
    temperature_alert: bool = False
    throttling: bool = False
    # Connectivity
    connectivity: dict[str, bool]
    # Upload queue counters
    upload_queue: dict[str, int]
    # GPS (last known) — jsonb passthrough or null
    gps: dict | None = None


class CameraResponse(BaseModel):
    camera_id: str
    name: str
    input_type: str
    connected: bool = False
    streaming: bool = False
    last_segment_at: str | None = None
    error: str | None = None
    ptz: dict | None = None
    # Convenience URLs the UI consumes directly.
    hls_url: str
    last_frame_url: str
    has_last_frame: bool = False


class QrResponse(BaseModel):
    claim_token: str
    serial_number: str
    router_id: str | None = None
    # one of: "unregistered" | "registered" | "claimed"
    status: str


# ── App factory ──────────────────────────────────────────────────────────────────

def create_app(
    *,
    state: AppState,
    monitor: SystemMonitor,
    cfg: RouterConfig,
) -> FastAPI:
    """Build the FastAPI app wired to live router state (no copies).

    ``state`` and ``monitor`` are captured by closure so every request reads the
    current in-memory values — there is no caching layer.
    """
    app = FastAPI(
        title="GTI Router — Local Console API",
        version=cfg.device.firmware_version,
        docs_url=None,       # no public docs surface on the device
        redoc_url=None,
        openapi_url=None,
    )

    # The UI is served from the same origin, but allow loopback CORS so the dev
    # server (next dev on another port) can also talk to the device during work.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    hls_root = Path(cfg.hls.output_dir)
    cameras_by_id = {c.camera_id: c for c in cfg.cameras}

    # ── Data endpoints ──────────────────────────────────────────────────────────

    @app.get("/api/identity", response_model=IdentityResponse)
    async def get_identity() -> IdentityResponse:
        d = cfg.device
        return IdentityResponse(
            serial_number=d.serial_number,
            name=d.name,
            sku=d.sku,
            firmware_version=d.firmware_version,
            # Prefer the live, registration-confirmed id; fall back to config.
            router_id=state.router_id or (d.router_id or None),
            gateway_id=state.gateway_id or d.gateway_id,
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def get_health() -> HealthResponse:
        snap = monitor.snapshot()
        return HealthResponse(
            cpu_percent=snap.cpu_percent if snap else None,
            memory_percent=snap.memory_percent if snap else None,
            disk_percent=snap.disk_percent if snap else None,
            temperature_celsius=snap.temperature_celsius if snap else None,
            uptime_seconds=snap.uptime_seconds if snap else None,
            sampled_at=snap.sampled_at if snap else None,
            cpu_alert=snap.cpu_alert if snap else False,
            memory_alert=snap.memory_alert if snap else False,
            disk_alert=snap.disk_alert if snap else False,
            temperature_alert=snap.temperature_alert if snap else False,
            throttling=snap.throttling if snap else False,
            connectivity={
                "supabase": state.supabase_connected,
                "s3": state.s3_connected,
                "rtsp": state.rtsp_connected,
            },
            upload_queue={
                "size": state.upload_queue_size,
                "pending": state.upload_pending,
                "success_count": state.upload_success_count,
                "error_count": state.upload_error_count,
            },
            gps=state.gps,
        )

    @app.get("/api/cameras", response_model=list[CameraResponse])
    async def get_cameras() -> list[CameraResponse]:
        out: list[CameraResponse] = []
        for cam_id, cam_cfg in cameras_by_id.items():
            rt = state.per_camera.get(cam_id)
            last_frame = hls_root / cam_id / _LAST_FRAME_NAME
            out.append(
                CameraResponse(
                    camera_id=cam_id,
                    name=getattr(cam_cfg, "name", None) or cam_id,
                    input_type=cam_cfg.input_type,
                    connected=rt.connected if rt else False,
                    streaming=rt.streaming if rt else False,
                    last_segment_at=rt.last_segment_at if rt else None,
                    error=rt.error if rt else None,
                    ptz=rt.ptz if rt else None,
                    hls_url=f"/hls/{cam_id}/{_PLAYLIST_NAME}",
                    last_frame_url=f"/api/cameras/{cam_id}/{_LAST_FRAME_NAME}",
                    has_last_frame=last_frame.exists(),
                )
            )
        return out

    @app.get("/api/qr", response_model=QrResponse)
    async def get_qr() -> QrResponse:
        d = cfg.device
        # Never expose service secrets — only the claim datum (token/serial).
        claim_token = d.claim_token or d.serial_number
        router_id = state.router_id or (d.router_id or None)
        if d.user_id:
            status = "claimed"
        elif router_id:
            status = "registered"
        else:
            status = "unregistered"
        return QrResponse(
            claim_token=claim_token,
            serial_number=d.serial_number,
            router_id=router_id,
            status=status,
        )

    @app.get("/api/cameras/{camera_id}/last_frame.jpg")
    async def get_last_frame(camera_id: str) -> FileResponse:
        # Validate against configured cameras → no arbitrary filesystem access.
        if camera_id not in cameras_by_id:
            raise HTTPException(status_code=404, detail="unknown camera")
        frame = hls_root / camera_id / _LAST_FRAME_NAME
        if not frame.is_file():
            raise HTTPException(
                status_code=404, detail="no frame captured yet for this camera"
            )
        return FileResponse(
            frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    # ── Static mounts ───────────────────────────────────────────────────────────

    # HLS preview (Story 11.3). StaticFiles is rooted at output_dir so it cannot
    # escape it; .m3u8/.ts get correct content-types from the response.
    if hls_root.is_dir():
        app.mount("/hls", StaticFiles(directory=str(hls_root)), name="hls")

    # Exported Next.js bundle (Story 11.10). Optional: the API stays usable when
    # the UI has not been built yet. html=True serves index.html for "/".
    static_dir = Path(cfg.console.static_dir)
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")

    # ── Error handling: clean JSON, never raw tracebacks (AC#5) ──────────────────

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc: Exception) -> JSONResponse:  # noqa: ANN001
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app
