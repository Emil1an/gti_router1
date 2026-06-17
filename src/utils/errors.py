"""Typed exception hierarchy for GTI Router.

All domain errors inherit from RouterError. Using bare `raise Exception(...)` is
prohibited across the codebase — always raise a typed subclass.

Hierarchy
---------
RouterError
├── ConfigError
│   └── ConfigValidationError
├── RTSPError
│   ├── RTSPConnectionError
│   ├── RTSPAuthError
│   └── RTSPCodecError
├── PipelineError
├── S3UploadError
├── SupabaseError
├── PTZError
└── GPSError
"""


class RouterError(Exception):
    """Base class for all GTI Router domain exceptions."""


# ── Configuration ──────────────────────────────────────────────────────────────

class ConfigError(RouterError):
    """Raised for any configuration-related problem."""


class ConfigValidationError(ConfigError):
    """Raised when the router.yaml fails pydantic validation.

    Args:
        field: dotted field path that failed (e.g. ``cameras[0].rtsp_url``).
        reason: human-readable explanation.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Config validation error — {field}: {reason}")


# ── RTSP / Camera ───────────────────────────────────────────────────────────────

class RTSPError(RouterError):
    """Base for RTSP-related errors."""


class RTSPConnectionError(RTSPError):
    """Cannot reach the camera endpoint (network, timeout)."""


class RTSPAuthError(RTSPError):
    """Authentication rejected by the camera."""


class RTSPCodecError(RTSPError):
    """Stream codec is unsupported or undetectable."""


class CameraSetupError(RTSPError):
    """A camera could not be set up from configuration (fail-fast, exit 2)."""


# ── Video source abstraction (Epic 5) ────────────────────────────────────────────

class VideoSourceError(RouterError):
    """Base for video-source abstraction errors (RTSP / capture card)."""


class CaptureCardError(VideoSourceError):
    """A V4L2 capture device is missing, inaccessible, or returned bad data."""


# ── Encoder selection (Epic 5) ───────────────────────────────────────────────────

class EncoderError(RouterError):
    """Encoder configuration/selection failed."""


class UnsupportedEncoderError(EncoderError):
    """A forbidden/unsupported encoder was requested (e.g. HEVC in software)."""


# ── Licensing / hardware limits (Epic 5) ─────────────────────────────────────────

class CameraLimitError(RouterError):
    """The configured cameras exceed the board's physical limit (fail-fast)."""


# ── Pipeline ────────────────────────────────────────────────────────────────────

class PipelineError(RouterError):
    """FFmpeg pipeline failed or produced an unexpected exit code."""


class FFmpegError(PipelineError):
    """FFmpeg subprocess exited with a non-zero code or crashed.

    Args:
        camera_id:   camera that owns the failed pipeline.
        returncode:  FFmpeg exit code.
        stderr:      last lines of FFmpeg stderr for diagnosis.
    """

    def __init__(self, camera_id: str, returncode: int, stderr: str = "") -> None:
        self.camera_id = camera_id
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"[{camera_id}] FFmpeg exited with code {returncode}. "
            f"stderr tail: {stderr[-300:].strip()!r}"
        )


# ── Upload / S3 ─────────────────────────────────────────────────────────────────

class S3UploadError(RouterError):
    """Segment or playlist upload to AWS S3 failed."""


class S3TransientError(S3UploadError):
    """Transient S3 failure — network/timeout/5xx/throttling.

    The upload worker SHOULD retry this via ``@with_retry``.
    """


class S3PermanentError(S3UploadError):
    """Permanent S3 failure — 403/404/invalid credentials/bad bucket.

    The upload worker MUST NOT retry this; the segment moves to ``failed``
    state immediately.
    """


# ── Supabase ────────────────────────────────────────────────────────────────────

class SupabaseError(RouterError):
    """Communication with Supabase failed."""


class SupabaseTransientError(SupabaseError):
    """Transient Supabase failure — timeout/network/5xx/throttling.

    The caller SHOULD retry this via ``@with_retry``.
    """


class SupabasePermanentError(SupabaseError):
    """Permanent Supabase failure — 4xx validation/constraint/auth.

    The caller MUST NOT retry this.
    """


# ── Health / system monitor ─────────────────────────────────────────────────────

class MonitorError(RouterError):
    """A system-metric sample (CPU/RAM/disk/temperature) could not be read."""


class WatchdogError(RouterError):
    """Sending an ``sd_notify`` message to systemd failed."""


# ── PTZ / ONVIF ─────────────────────────────────────────────────────────────────

class PTZError(RouterError):
    """ONVIF PTZ operation failed."""


class PTZConnectionError(PTZError):
    """Cannot reach the camera's ONVIF endpoint (network/timeout). Retryable."""


class PTZAuthError(PTZError):
    """ONVIF authentication was rejected by the camera. Not retryable."""


class PTZUnsupportedError(PTZError):
    """The camera has no PTZ service / PTZ is not enabled. Not retryable."""


class PTZCommandError(PTZError):
    """An ONVIF PTZ command was rejected or returned a fault. Not retryable."""


class PTZValidationError(PTZError):
    """A PTZ command failed security validation (stale/expired/foreign/rate). """


# ── GPS / Location (Epic 6) ──────────────────────────────────────────────────────

class GPSError(RouterError):
    """GPS module read failed."""


# Story 6.1 uses the ``GpsError`` spelling — keep an alias for both.
GpsError = GPSError


class OrientationError(RouterError):
    """Camera orientation is invalid or could not be persisted (Story 6.2)."""


# ── Snapshot / last-frame (Epic 6) ───────────────────────────────────────────────

class SnapshotError(RouterError):
    """A last-frame JPEG snapshot could not be generated or uploaded (Story 6.3)."""
