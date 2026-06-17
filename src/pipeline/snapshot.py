"""Autonomous last-frame snapshot service (Stories 6.3 + 6.4).

For each camera, a dedicated asyncio task periodically (default 10 s, NFR13)
extracts a JPEG from the most recent buffered segment, uploads it to S3 (reusing
:class:`~upload.s3_client.S3Uploader`) and updates ``cameras.last_frame_url`` /
``cameras.last_frame_at`` in Supabase.

Key properties
--------------
* **Autonomous:** works with **no Gateway linked** (no ``gateway_id``) and runs
  independently of the segment-upload pipeline.
* **No detection (Story 6.4):** the JPEG is raw, carries **no** detection
  metadata, and is tagged with the centralised no-detection contract
  (``source=router`` + ``contract_version``) as S3 object metadata so Satélites
  knows it is an unanalysed view. The Router never runs inference.
* **Per-camera isolation:** one camera's snapshot failure never affects the
  others or the HLS pipeline.
* All cloud calls go through ``@with_retry`` and tolerate a degraded backend;
  errors are typed (:class:`~utils.errors.SnapshotError` / ``S3UploadError``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from config.loader import get_config
from health.supabase_client import SupabaseClient
from upload.s3_client import S3Uploader
from utils.contract import no_detection_contract
from utils.errors import (
    S3TransientError,
    S3UploadError,
    SnapshotError,
    SupabaseError,
    SupabaseTransientError,
)
from utils.logging import get_logger
from utils.retry import with_retry

_TABLE = "cameras"
_UPLOAD_MAX_RETRIES = 3
_UPDATE_MAX_RETRIES = 3


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SnapshotService:
    """Periodic per-camera last-frame JPEG → S3 + ``cameras`` update."""

    def __init__(
        self,
        client: SupabaseClient,
        camera_ids: list[str] | None = None,
        output_base: str | None = None,
        uploader: S3Uploader | None = None,
        interval_s: int | None = None,
    ) -> None:
        cfg = get_config()
        self._client = client
        self._uploader = uploader if uploader is not None else S3Uploader()
        self._camera_ids = (
            camera_ids if camera_ids is not None else [c.camera_id for c in cfg.cameras]
        )
        self._output_base = Path(output_base or cfg.hls.output_dir)
        self._interval = interval_s if interval_s is not None else cfg.snapshot.interval_s
        self._enabled = cfg.snapshot.enabled

        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False
        self._snapshots_taken = 0
        self._logger = get_logger(__name__)

    @property
    def snapshots_taken(self) -> int:
        return self._snapshots_taken

    # ── Lifecycle ─────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start one snapshot task per camera (autonomous, no Gateway needed)."""
        if not self._enabled:
            self._logger.info("Snapshot service disabled by config")
            return
        await self._uploader.start()
        self._running = True
        for camera_id in self._camera_ids:
            self._tasks[camera_id] = asyncio.create_task(
                self._camera_loop(camera_id), name=f"snapshot-{camera_id}"
            )
        self._logger.info(
            "SnapshotService started",
            extra={"cameras": self._camera_ids, "interval_s": self._interval},
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        try:
            await self._uploader.stop()
        except Exception:
            pass
        self._logger.info("SnapshotService stopped")

    # ── Per-camera loop (isolated) ────────────────────────────────────────────────

    async def _camera_loop(self, camera_id: str) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                try:
                    await self.snapshot_once(camera_id)
                except (SnapshotError, S3UploadError, SupabaseError) as exc:
                    # Contained per camera — never affects other cameras (AC#7).
                    self._logger.warning(
                        "Snapshot failed (camera isolated): %s",
                        exc,
                        extra={"camera_id": camera_id},
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(
                        "Unexpected snapshot error: %s", exc,
                        extra={"camera_id": camera_id},
                    )
        except asyncio.CancelledError:
            pass

    async def snapshot_once(self, camera_id: str) -> str | None:
        """Take, upload, and record one snapshot. Returns the S3 URL or ``None``."""
        jpeg_path = await self._extract_frame(camera_id)
        if jpeg_path is None:
            return None  # no segment available yet

        try:
            key = await self._upload_with_retry(camera_id, jpeg_path)
            url = self._uploader.object_url(key)
            await self._update_camera(camera_id, url)
            self._snapshots_taken += 1
            self._logger.info(
                "Last-frame snapshot published",
                extra={"camera_id": camera_id, "url": url},
            )
            return url
        finally:
            try:
                jpeg_path.unlink(missing_ok=True)
            except OSError:
                pass

    # ── Frame extraction ──────────────────────────────────────────────────────────

    def _build_extract_command(self, segment: Path, out: Path) -> list[str]:
        """FFmpeg command to grab the last frame of a segment as a JPEG."""
        return [
            "ffmpeg", "-y",
            "-sseof", "-1",           # seek ~1s before end → the last frame
            "-i", str(segment),
            "-frames:v", "1",
            "-q:v", "2",              # high-quality JPEG
            str(out),
        ]

    async def _extract_frame(self, camera_id: str) -> Path | None:
        """Extract a JPEG from the camera's most recent buffered segment."""
        cam_dir = self._output_base / camera_id
        if not cam_dir.exists():
            return None
        segments = sorted(cam_dir.glob("segment_*.ts"))
        if not segments:
            return None
        latest = segments[-1]
        out = cam_dir / "last_frame.jpg"

        cmd = self._build_extract_command(latest, out)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
        except FileNotFoundError as exc:
            raise SnapshotError(
                f"[{camera_id}] 'ffmpeg' not found for snapshot extraction"
            ) from exc
        except OSError as exc:
            raise SnapshotError(
                f"[{camera_id}] OS error extracting snapshot: {exc}"
            ) from exc

        if proc.returncode != 0 or not out.exists():
            tail = (stderr or b"").decode(errors="replace")[-200:]
            raise SnapshotError(
                f"[{camera_id}] frame extraction failed (rc={proc.returncode}): {tail}"
            )
        return out

    # ── Upload + Supabase update ──────────────────────────────────────────────────

    async def _upload_with_retry(self, camera_id: str, jpeg_path: Path) -> str:
        """Upload the JPEG with the no-detection contract metadata (Story 6.4)."""
        async def _do() -> str:
            return await self._uploader.upload_snapshot(
                camera_id, jpeg_path, metadata=no_detection_contract()
            )

        wrapped = with_retry(
            max_retries=_UPLOAD_MAX_RETRIES, retryable=(S3TransientError,)
        )(_do)
        return await wrapped()

    async def _update_camera(self, camera_id: str, url: str) -> None:
        payload = {"last_frame_url": url, "last_frame_at": _utc_now_iso()}

        async def _do() -> list[dict[str, object]]:
            return await self._client.update(
                _TABLE, {"id": f"eq.{camera_id}"}, payload
            )

        wrapped = with_retry(
            max_retries=_UPDATE_MAX_RETRIES, retryable=(SupabaseTransientError,)
        )(_do)
        try:
            await wrapped()
        except SupabaseError as exc:
            self._logger.warning(
                "Could not update cameras.last_frame_url (deferred): %s",
                exc, extra={"camera_id": camera_id},
            )
