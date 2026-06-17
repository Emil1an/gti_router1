"""Per-camera orientation persistence (Story 6.2).

Reads each camera's ``orientation`` block from the validated config and persists
it to the dedicated ``cameras`` columns so GTI Satélites can build each camera's
3D view frustum (Epic 8):

    azimuth → cameras.heading,  tilt → cameras.tilt,
    fov_h   → cameras.fov_h,    mount_height_m → cameras.mount_height_m

Ranges are validated fail-fast by the ``Orientation`` pydantic model (Story 1.2);
this module re-checks defensively and raises a typed
:class:`~utils.errors.OrientationError`. Writes use Supabase (``service_role``),
are non-blocking, idempotent (update by ``camera_id``), under ``@with_retry``, and
tolerate a degraded Supabase.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from config.loader import get_config
from health.supabase_client import SupabaseClient
from utils.errors import OrientationError, SupabaseError, SupabaseTransientError
from utils.logging import get_logger
from utils.retry import with_retry

if TYPE_CHECKING:
    from config.schema import CameraConfig, Orientation

_TABLE = "cameras"
_UPDATE_MAX_RETRIES = 3

_logger = get_logger(__name__)


def validate_orientation(
    azimuth: float, tilt: float, fov_h: float, mount_height_m: float
) -> None:
    """Raise :class:`OrientationError` if any value is out of plausible range."""
    if not (0.0 <= azimuth < 360.0):
        raise OrientationError(f"azimuth {azimuth} out of range [0, 360)")
    if not (-90.0 <= tilt <= 90.0):
        raise OrientationError(f"tilt {tilt} out of range [-90, 90]")
    if not (0.0 < fov_h <= 180.0):
        raise OrientationError(f"fov_h {fov_h} out of range (0, 180]")
    if not (mount_height_m > 0.0):
        raise OrientationError(f"mount_height_m {mount_height_m} must be > 0")


def orientation_payload(orientation: "Orientation") -> dict[str, float]:
    """Map an ``Orientation`` to the ``cameras`` column payload (azimuth→heading)."""
    validate_orientation(
        orientation.azimuth, orientation.tilt, orientation.fov_h,
        orientation.mount_height_m,
    )
    return {
        "heading": orientation.azimuth,
        "tilt": orientation.tilt,
        "fov_h": orientation.fov_h,
        "mount_height_m": orientation.mount_height_m,
    }


class OrientationPublisher:
    """Persists each camera's configured orientation to the ``cameras`` table."""

    def __init__(
        self,
        client: SupabaseClient,
        cameras: list["CameraConfig"] | None = None,
    ) -> None:
        cfg = get_config()
        self._client = client
        self._cameras = cameras if cameras is not None else list(cfg.cameras)
        self._task: asyncio.Task[None] | None = None
        self._logger = get_logger(__name__)

    async def start(self) -> None:
        """Publish all camera orientations in the background (non-blocking)."""
        self._task = asyncio.create_task(self._publish_all(), name="orientation-publish")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _publish_all(self) -> None:
        try:
            for cam in self._cameras:
                await self.publish_one(cam)
        except asyncio.CancelledError:
            pass

    async def publish_one(self, cam: "CameraConfig") -> bool:
        """Persist one camera's orientation; returns ``True`` on success."""
        logger = get_logger(__name__, camera_id=cam.camera_id)
        try:
            payload = orientation_payload(cam.orientation)
        except OrientationError as exc:
            logger.error("Invalid orientation for %s: %s", cam.camera_id, exc)
            return False

        async def _do() -> list[dict[str, Any]]:
            return await self._client.update(
                _TABLE, {"id": f"eq.{cam.camera_id}"}, payload
            )

        wrapped = with_retry(
            max_retries=_UPDATE_MAX_RETRIES, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            await wrapped()
            logger.info(
                "Camera orientation persisted",
                extra={"camera_id": cam.camera_id, "heading": payload["heading"]},
            )
            return True
        except SupabaseError as exc:
            logger.warning(
                "Could not persist orientation for %s (deferred): %s",
                cam.camera_id, exc,
            )
            return False
