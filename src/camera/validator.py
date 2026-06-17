"""PTZ command security validation (Story 4.4).

``PTZCommandValidator`` is the single security gate for PTZ commands, invoked by
:class:`~camera.command_receiver.CommandReceiver` **before** the atomic claim.
It enforces:

* **Anti-replay (freshness):** reject commands whose ``issued_at`` is older than
  ``ptz.command_max_age_s`` (default 30 s), or already past ``expires_at``.
* **Camera membership:** reject commands for cameras not on this Router.
* **Rate-limit:** reject beyond ``ptz.rate_limit_per_min`` (default 60/min,
  sliding window) — **except** ``ptz_stop`` (must always be able to halt the
  camera) and ``ptz_get_position`` (read-only, Story 4.6), which are never
  rate-limited.
* **Known command type:** reject unrecognised ``command_type``.

Validation is pure/synchronous (no I/O) and returns an explicit
:class:`ValidationResult`; the receiver turns a rejection into a ``failed`` row
with the reason and emits ``ptz_commands_rejected``.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from config.loader import get_config
from utils.logging import get_logger

# Canonical PTZ command types accepted by the Router.
VALID_COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "ptz_continuous_move",
        "ptz_relative_move",
        "ptz_absolute_move",
        "ptz_stop",
        "ptz_goto_preset",
        "ptz_get_position",
    }
)

# Never rate-limited: a stop must always pass, and read-only position queries
# are free (Story 4.4 / 4.6).
RATE_LIMIT_EXEMPT: frozenset[str] = frozenset({"ptz_stop", "ptz_get_position"})

# Rejection reasons (written to ptz_commands.error_message).
REASON_STALE = "stale"
REASON_EXPIRED = "expired"
REASON_FOREIGN_CAMERA = "foreign_camera"
REASON_RATE_LIMITED = "rate_limited"
REASON_UNKNOWN_TYPE = "unknown_command_type"

_ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    # Accept both with and without fractional seconds, and trailing Z.
    for fmt in (_ISO_FMT, "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one PTZ command."""

    ok: bool
    reason: str | None = None


class PTZCommandValidator:
    """Stateful (rate-limit window) validator for one Router's PTZ commands."""

    def __init__(
        self,
        camera_ids: list[str] | set[str],
        max_age_s: int | None = None,
        rate_limit_per_min: int | None = None,
    ) -> None:
        cfg = get_config()
        self._camera_ids = set(camera_ids)
        self._max_age_s = max_age_s if max_age_s is not None else cfg.ptz.command_max_age_s
        self._rate_limit = (
            rate_limit_per_min if rate_limit_per_min is not None
            else cfg.ptz.rate_limit_per_min
        )
        # Monotonic timestamps of rate-limited commands accepted in the last 60 s.
        self._window: deque[float] = deque()
        self._rejected_count = 0
        self._logger = get_logger(__name__)

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def validate(self, command: dict) -> ValidationResult:
        """Validate a command. Returns ok=True or ok=False with a reason."""
        command_type = command.get("command_type")
        camera_id = command.get("camera_id")

        # 1. Known command type
        if command_type not in VALID_COMMAND_TYPES:
            return self._reject(camera_id, REASON_UNKNOWN_TYPE)

        # 2. Camera membership (barrier against foreign cameras)
        if camera_id not in self._camera_ids:
            return self._reject(camera_id, REASON_FOREIGN_CAMERA)

        now = datetime.now(tz=UTC)

        # 3. Expiry (explicit expires_at in the past)
        expires_at = _parse_iso(command.get("expires_at"))
        if expires_at is not None and expires_at < now:
            return self._reject(camera_id, REASON_EXPIRED)

        # 4. Freshness / anti-replay (issued_at older than max_age)
        issued_at = _parse_iso(command.get("issued_at"))
        if issued_at is not None and (now - issued_at).total_seconds() > self._max_age_s:
            return self._reject(camera_id, REASON_STALE)

        # 5. Rate-limit (stop + get_position are exempt and ALWAYS pass)
        if command_type not in RATE_LIMIT_EXEMPT:
            if not self._allow_rate():
                return self._reject(camera_id, REASON_RATE_LIMITED)

        return ValidationResult(ok=True)

    # ── Internals ────────────────────────────────────────────────────────────────

    def _allow_rate(self) -> bool:
        """Sliding 60 s window. Records the event and returns False if over limit."""
        now = time.monotonic()
        cutoff = now - 60.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()
        if len(self._window) >= self._rate_limit:
            return False
        self._window.append(now)
        return True

    def _reject(self, camera_id: str | None, reason: str) -> ValidationResult:
        self._rejected_count += 1
        self._logger.warning(
            "PTZ command rejected",
            extra={"camera_id": camera_id, "reason": reason},
        )
        return ValidationResult(ok=False, reason=reason)
