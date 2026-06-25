"""Dev launcher for the GTI local console — workstation testing (no Pi, no cloud).

Runs ONLY the FastAPI console app (``web.local_api.create_app``) with a seeded
demo ``AppState`` and a live ``SystemMonitor``. It does NOT start the capture
pipeline / S3 / Supabase / watchdog, so it runs fine on Windows/macOS/Linux.

Usage (from the repo root):

    python scripts/run_console_dev.py "C:/path/to/web/out"

The positional argument (or the GTI_OUT_DIR env var) points at the Next.js
static export. If omitted, console.static_dir from router.dev.yaml is used.
Then open http://127.0.0.1:8770
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

# Use the dev config unless the caller already set ROUTER_CONFIG.
os.environ.setdefault("ROUTER_CONFIG", str(_ROOT / "config" / "router.dev.yaml"))

import uvicorn  # noqa: E402

from config.loader import get_config  # noqa: E402
from health.monitor import SystemMonitor  # noqa: E402
from health.state import AppState, CameraState  # noqa: E402
from web.local_api import create_app  # noqa: E402


def _seed_state() -> AppState:
    """Build an AppState with demo data so every screen shows something."""
    state = AppState()
    state.router_id = "11111111-2222-3333-4444-555555555555"
    state.gateway_id = "gw-dev-0001"
    state.supabase_connected = True
    state.s3_connected = True
    state.rtsp_connected = True
    state.upload_queue_size = 3
    state.upload_pending = 3
    state.upload_success_count = 128
    state.upload_error_count = 0
    state.gps = {"lat": 19.4326, "lon": -99.1332, "fix": "3d"}
    state.set_camera(
        CameraState(
            camera_id="cam-front", input_type="rtsp_ip",
            connected=True, streaming=True,
            last_segment_at="2026-06-25T12:00:00.000Z",
        )
    )
    state.set_camera(
        CameraState(
            camera_id="cam-rear", input_type="rtsp_ip",
            connected=False, streaming=False, error="RTSP timeout (demo)",
        )
    )
    return state


async def main() -> None:
    cfg = get_config()

    # static_dir override: CLI arg > GTI_OUT_DIR env > router.dev.yaml value.
    override = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GTI_OUT_DIR")
    if override:
        cfg.console.static_dir = str(Path(override).expanduser().resolve())

    # Make sure dev dirs exist so the /hls mount and static checks behave.
    Path(cfg.hls.output_dir).mkdir(parents=True, exist_ok=True)
    static_dir = Path(cfg.console.static_dir)

    state = _seed_state()
    # Real CPU/RAM/disk on this machine ("C:\\" on Windows, "/" elsewhere).
    monitor = SystemMonitor(disk_path="C:\\" if os.name == "nt" else "/")
    await monitor.start()

    app = create_app(state=state, monitor=monitor, cfg=cfg)

    print("\n" + "=" * 64)
    print("  GTI Local Console — DEV mode")
    print(f"  static_dir : {static_dir}  {'(OK)' if static_dir.is_dir() else '(MISSING — UI will 404, API still works)'}")
    print(f"  API + UI   : http://{cfg.console.host}:{cfg.console.port}")
    print(f"  Health     : http://{cfg.console.host}:{cfg.console.port}/api/health")
    print("=" * 64 + "\n")

    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.console.host, port=cfg.console.port, log_level="info")
    )
    await server.serve()
    await monitor.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
