"""Shared application state for the health subsystem (Epic 3).

A single :class:`AppState` instance is passed between the resilience/monitoring
services so they can publish and consume cross-cutting flags without importing
one another:

* :class:`~health.registration.DeviceRegistration` writes ``router_id`` /
  ``gateway_id`` / ``supabase_connected`` after a successful upsert (3.1).
* :class:`~health.monitor.SystemMonitor` publishes its latest sample/alert flags
  (3.3) — exposed via the monitor itself, mirrored here when convenient.
* The upload subsystem publishes ``upload_*`` counters and the ``s3_connected``
  flag; the pipeline publishes ``rtsp_connected`` and ``per_camera`` status.
* :class:`~health.reporter.HealthReporter` reads everything to compose the
  ``router_health`` row (3.2).

This is plain shared state — no business logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CameraState:
    """Per-camera status block for the health report ``per_camera`` array.

    Matches the fixed health contract
    ``{camera_id, input_type, connected, streaming, last_segment_at, error}``.
    """

    camera_id: str
    input_type: str
    connected: bool = False
    streaming: bool = False
    last_segment_at: str | None = None  # ISO-8601 UTC with Z
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "camera_id": self.camera_id,
            "input_type": self.input_type,
            "connected": self.connected,
            "streaming": self.streaming,
            "last_segment_at": self.last_segment_at,
            "error": self.error,
        }


@dataclass
class AppState:
    """Mutable, process-wide state shared across health services."""

    # ── Identity (published by DeviceRegistration, 3.1) ──────────────────────────
    router_id: str | None = None
    gateway_id: str | None = None

    # ── Connectivity flags ───────────────────────────────────────────────────────
    supabase_connected: bool = False
    s3_connected: bool = False
    rtsp_connected: bool = False

    # ── Upload subsystem metrics (published by UploadQueue/Service) ─────────────
    upload_queue_size: int = 0
    upload_pending: int = 0
    upload_success_count: int = 0
    upload_error_count: int = 0

    # ── GPS (Epic 6 — last known coordinate, jsonb) ──────────────────────────────
    gps: dict[str, object] | None = None

    # ── Per-camera status ────────────────────────────────────────────────────────
    per_camera: dict[str, CameraState] = field(default_factory=dict)

    def set_camera(self, camera: CameraState) -> None:
        """Insert or replace a camera's status block."""
        self.per_camera[camera.camera_id] = camera
