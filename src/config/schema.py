"""Pydantic-settings models for router.yaml.

All config access in the project MUST go through ``get_config()`` in
``src/config/loader.py``.  No module may read YAML or ``os.environ`` directly.

Model hierarchy
---------------
RouterConfig (root)
├── cameras: list[CameraConfig]
│   ├── Orientation
│   └── GpsCoord (optional)
├── hls: HlsConfig
├── aws: AwsConfig
├── supabase: SupabaseConfig
├── device: DeviceConfig
├── health: HealthConfig
└── licensing: LicensingConfig
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


# ── Orientation ────────────────────────────────────────────────────────────────

class Orientation(BaseModel):
    """Physical mounting orientation of a camera.

    azimuth: compass heading the camera faces, 0–360° (0 = North).
    tilt:    vertical angle relative to horizontal, -90 to +90°.
    fov_h:   horizontal field-of-view in degrees, 1–180°.
    mount_height_m: installation height above ground in metres, >0.
    """

    azimuth: Annotated[float, Field(ge=0, lt=360)]
    tilt: Annotated[float, Field(ge=-90, le=90)]
    fov_h: Annotated[float, Field(gt=0, le=180)]
    mount_height_m: Annotated[float, Field(gt=0)]


# ── GPS coordinate (per-camera, optional) ─────────────────────────────────────

class GpsCoord(BaseModel):
    """Static GPS coordinates of a camera installation point."""

    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    altitude_m: float = 0.0


# ── Camera ─────────────────────────────────────────────────────────────────────

class CameraConfig(BaseModel):
    """Per-camera configuration block inside the ``cameras`` list."""

    camera_id: str = Field(min_length=1)
    input_type: Literal["rtsp_ip", "capture_card"]

    # rtsp_ip fields
    rtsp_url: str | None = None

    # capture_card fields
    device: str | None = None  # e.g. /dev/video0

    orientation: Orientation
    gps: GpsCoord | None = None

    # ── PTZ / ONVIF (Epic 4) ────────────────────────────────────────────────────
    # PTZ is opt-in per camera.  Credentials must come from env via ${VAR}
    # expansion (NFR9) — never plaintext in YAML.  When onvif_host is omitted it
    # is derived from the RTSP URL host at connect time.
    ptz_enabled: bool = False
    onvif_host: str | None = None
    onvif_port: Annotated[int, Field(ge=1, le=65535)] = 80
    onvif_username: str | None = None
    onvif_password: str | None = None  # expanded from ${CAMERA_x_PASSWORD}

    @model_validator(mode="after")
    def _validate_source_fields(self) -> "CameraConfig":
        if self.input_type == "rtsp_ip" and not self.rtsp_url:
            raise ValueError("rtsp_url is required when input_type is 'rtsp_ip'")
        if self.input_type == "capture_card" and not self.device:
            raise ValueError("device is required when input_type is 'capture_card'")
        return self


# ── HLS ────────────────────────────────────────────────────────────────────────

class HlsConfig(BaseModel):
    """FFmpeg HLS segmentation parameters."""

    segment_duration: Annotated[int, Field(ge=2, le=8)] = 4
    output_dir: str = "/var/lib/gti-router/hls"


# ── AWS S3 ─────────────────────────────────────────────────────────────────────

class AwsConfig(BaseModel):
    """AWS S3 upload settings.

    Secrets (``access_key_id``, ``secret_access_key``) must be provided via
    environment variables using the ``${ENV_VAR}`` expansion syntax in YAML —
    they must NEVER appear as plaintext in router.yaml (NFR9).
    """

    bucket: str
    region: str = "us-east-1"
    access_key_id: str      # expanded from ${AWS_ACCESS_KEY_ID}
    secret_access_key: str  # expanded from ${AWS_SECRET_ACCESS_KEY}
    prefix: str = ""        # optional extra path prefix
    upload_max_retries: Annotated[int, Field(ge=0, le=100)] = 10


# ── Upload scheduling ──────────────────────────────────────────────────────────

class UploadConfig(BaseModel):
    """Upload worker scheduling parameters (Stories 2.5 / 2.6).

    priority_ratio:        number of ``realtime`` segments uploaded per
                           ``backlog`` segment when both queues have items
                           (FR6 — default 3:1).
    backlog_age_threshold_s: a pending segment is classified ``backlog`` once
                           ``now - created_at`` exceeds this many seconds;
                           otherwise it is ``realtime``.
    shutdown_timeout_s:    max seconds the worker waits for in-flight uploads to
                           drain on graceful shutdown before cancelling (NFR /
                           Story 2.6 — default 30 s).
    """

    priority_ratio: Annotated[int, Field(ge=1, le=20)] = 3
    backlog_age_threshold_s: Annotated[int, Field(ge=1, le=3600)] = 60
    shutdown_timeout_s: Annotated[int, Field(ge=1, le=300)] = 30


# ── RTSP auto-recovery ─────────────────────────────────────────────────────────

class RecoveryConfig(BaseModel):
    """Per-camera RTSP auto-recovery parameters (Story 3.4).

    rtsp_segment_timeout_s: if no new ``.ts`` segment appears within this many
                            seconds the stream is treated as stalled and FFmpeg
                            is restarted.
    rtsp_max_failures:      consecutive reconnect failures before the camera is
                            marked "unavailable" in ``per_camera`` health (the
                            supervisor keeps retrying at a bounded rate after).
    """

    rtsp_segment_timeout_s: Annotated[int, Field(ge=5, le=300)] = 30
    rtsp_max_failures: Annotated[int, Field(ge=1, le=1000)] = 30


# ── PTZ / ONVIF control (Epic 4) ────────────────────────────────────────────────

class PtzConfig(BaseModel):
    """PTZ control timing and retry parameters (Stories 4.1–4.3).

    onvif_timeout_s:          per-ONVIF-operation timeout.
    poll_interval_s:          fallback polling interval for ``ptz_commands``.
    command_max_retries:      retries for a transient ONVIF network failure.
    update_max_retries:       retries for the ``ptz_commands`` feedback update
                              before the result is buffered locally (Story 4.3).
    realtime_reconnect_max_retries: retries when re-subscribing to Realtime.
    """

    onvif_timeout_s: Annotated[int, Field(ge=1, le=60)] = 10
    poll_interval_s: Annotated[int, Field(ge=1, le=60)] = 2
    command_max_retries: Annotated[int, Field(ge=0, le=20)] = 3
    update_max_retries: Annotated[int, Field(ge=0, le=20)] = 3
    realtime_reconnect_max_retries: Annotated[int, Field(ge=0, le=100)] = 10

    # Security (Story 4.4) — anti-replay + rate-limit
    command_max_age_s: Annotated[int, Field(ge=1, le=300)] = 30
    rate_limit_per_min: Annotated[int, Field(ge=1, le=600)] = 60

    # Lifecycle (Story 4.5) — re-check activation when registration recovers
    activation_retry_s: Annotated[int, Field(ge=1, le=600)] = 30


# ── GPS / location (Epic 6) ──────────────────────────────────────────────────────

class GpsConfig(BaseModel):
    """GPS reader settings (Story 6.1). GPS is Pro-only (gated by board)."""

    enabled: bool = True            # further gated to RPi5/Pro by board detection
    host: str = "127.0.0.1"         # gpsd host
    port: Annotated[int, Field(ge=1, le=65535)] = 2947  # gpsd port
    read_timeout_s: Annotated[int, Field(ge=1, le=120)] = 10
    persist_interval_s: Annotated[int, Field(ge=5, le=3600)] = 60


# ── Snapshot / last-frame (Epic 6) ───────────────────────────────────────────────

class SnapshotConfig(BaseModel):
    """Last-frame JPEG snapshot settings (Story 6.3, NFR13)."""

    enabled: bool = True
    interval_s: Annotated[int, Field(ge=1, le=3600)] = 10  # default 10 s (NFR13)


# ── Local buffer / disk management ─────────────────────────────────────────────

class BufferConfig(BaseModel):
    """Local buffer and disk-space management (Story 2.4).

    retention_hours:            target minimum hours of video the buffer must be
                                able to hold during a disconnection (FR5 — ≥4 h).
    alert_threshold_percent:    disk-usage percentage that raises a buffer alert
                                surfaced to the health report (default 80 %).
    cleanup_threshold_percent:  disk-usage percentage that triggers FIFO cleanup
                                of already-uploaded segments (default 85 %).
    """

    retention_hours: Annotated[int, Field(ge=4, le=8)] = 4
    alert_threshold_percent: Annotated[float, Field(gt=0, le=100)] = 80.0
    cleanup_threshold_percent: Annotated[float, Field(gt=0, le=100)] = 85.0


# ── Supabase ───────────────────────────────────────────────────────────────────

class SupabaseConfig(BaseModel):
    """Supabase project settings.

    ``service_role_key`` must come from env (NFR9).
    """

    url: str
    service_role_key: str   # expanded from ${SUPABASE_SERVICE_ROLE_KEY}


# ── Device ─────────────────────────────────────────────────────────────────────

class DeviceConfig(BaseModel):
    """Identity of this Router unit."""

    serial_number: str = Field(min_length=1)
    name: str = Field(min_length=1)
    gateway_id: str | None = None   # UUID of the Gateway this Router is linked to
    sku: Literal["base", "pro"] = "base"
    firmware_version: str = "0.1.0"  # reported to Supabase on registration (3.1)
    # Set after Supabase device registration (Story 3.1); used as S3 key prefix.
    # Falls back to serial_number if empty.
    user_id: str = ""    # Supabase user UUID
    router_id: str = ""  # Supabase router UUID
    # Claim token seeded by workshop provisioning (Story 11.4). Used by the local
    # console to draw the QR the user scans at gtisatelites.com to claim the unit.
    # Never a service secret — falls back to serial_number when absent.
    claim_token: str | None = None


# ── Local console (Epic 11) ──────────────────────────────────────────────────────

class ConsoleConfig(BaseModel):
    """In-process local console / mini-API settings (Story 11.1 / 11.10).

    The console is a FastAPI app bound to loopback only (NOT exposed on the LAN)
    that serves read-only device state and the static Next.js UI bundle.
    """

    enabled: bool = True
    host: str = "127.0.0.1"  # loopback-only by contract (Story 11.1 AC#2)
    port: Annotated[int, Field(ge=1, le=65535)] = 8770
    # Directory holding the exported Next.js bundle (Story 11.10). Served at "/"
    # when present; the API still works if it is missing (UI not built yet).
    static_dir: str = "/var/lib/gti-router/console"


# ── Health ─────────────────────────────────────────────────────────────────────

class HealthConfig(BaseModel):
    """Health-monitoring and -reporting parameters (Stories 3.2 / 3.3)."""

    # Reporter (3.2)
    report_interval_s: Annotated[int, Field(ge=10, le=300)] = 60
    local_queue_max_age_s: Annotated[int, Field(ge=60, le=86400)] = 3600  # 1 h FIFO

    # Monitor (3.3)
    monitor_interval_s: Annotated[int, Field(ge=1, le=60)] = 5

    # Alert thresholds (3.3) — percentages and °C
    cpu_alert_threshold: Annotated[float, Field(ge=0, le=100)] = 80.0
    memory_alert_threshold: Annotated[float, Field(ge=0, le=100)] = 80.0
    disk_alert_threshold: Annotated[float, Field(ge=0, le=100)] = 80.0
    temp_alert_threshold: Annotated[float, Field(ge=0, le=120)] = 75.0
    temp_critical_threshold: Annotated[float, Field(ge=0, le=120)] = 80.0


# ── Licensing ──────────────────────────────────────────────────────────────────

class LicensingConfig(BaseModel):
    """Hardware and licence-enforced limits."""

    max_cameras: Annotated[int, Field(ge=1, le=8)] = 1


# ── Root ───────────────────────────────────────────────────────────────────────

class RouterConfig(BaseModel):
    """Root configuration model for router.yaml.

    All fields are validated on load; the service will not start with an
    invalid configuration (fail-fast, AC#4).
    """

    cameras: list[CameraConfig] = Field(min_length=1)
    hls: HlsConfig = Field(default_factory=HlsConfig)
    aws: AwsConfig
    upload: UploadConfig = Field(default_factory=UploadConfig)
    buffer: BufferConfig = Field(default_factory=BufferConfig)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    ptz: PtzConfig = Field(default_factory=PtzConfig)
    gps: GpsConfig = Field(default_factory=GpsConfig)
    snapshot: SnapshotConfig = Field(default_factory=SnapshotConfig)
    supabase: SupabaseConfig
    device: DeviceConfig
    health: HealthConfig = Field(default_factory=HealthConfig)
    licensing: LicensingConfig = Field(default_factory=LicensingConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)

    @model_validator(mode="after")
    def _validate_unique_camera_ids(self) -> "RouterConfig":
        ids = [c.camera_id for c in self.cameras]
        if len(ids) != len(set(ids)):
            duplicates = [cid for cid in ids if ids.count(cid) > 1]
            raise ValueError(f"Duplicate camera_id(s) found: {sorted(set(duplicates))}")
        return self
