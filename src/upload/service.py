"""Capture→upload subsystem wiring (Story 2.6).

``UploadService`` is the integration seam that turns the Epic 2 building blocks
into a single continuous, resilient flow::

    VideoSource → HLSPipeline → buffer(FS) + SQLite → UploadQueue → S3

It owns the lifecycle of:

* a shared :class:`~storage.db.SegmentDB` (the single durable state index),
* the :class:`~upload.s3_client.S3Uploader` (cloud boundary),
* the :class:`~upload.queue.UploadQueue` (worker, 3:1 scheduling, retry),
* the :class:`~pipeline.buffer.BufferManager` (FIFO of uploaded segments),
* one :class:`~pipeline.ffmpeg_hls.HLSPipeline` per camera, whose ``on_segment``
  callback is wired straight to ``UploadQueue.enqueue``.

The service exposes only ``async start()`` / ``async stop()`` so the top-level
orchestrator (``main.py`` in Stories 1.5 / 3.7) coordinates it without holding
any business logic.  Graceful shutdown drains in-flight uploads (Story 2.6 AC#4).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from camera.sources.base import VideoSource
from config.loader import get_config
from health.state import AppState, CameraState
from pipeline.buffer import BufferManager
from pipeline.ffmpeg_hls import HLSPipeline
from storage.db import SegmentDB
from upload.queue import UploadQueue
from upload.s3_client import S3Uploader
from utils.logging import get_logger

# How often (s) the buffer FIFO cleanup runs.  Patchable in tests.
_BUFFER_ENFORCE_INTERVAL: float = 30.0


class UploadService:
    """Owns and coordinates the capture→upload subsystem for all cameras.

    Args:
        sources:    one :class:`~camera.sources.base.VideoSource` per camera.
                    A :class:`~pipeline.ffmpeg_hls.HLSPipeline` is built for each
                    and its segment callback is wired to the upload queue.
        db_path:    SQLite index path.  Defaults to
                    ``/var/lib/gti-router/upload_queue.db``.
        uploader:   optional pre-built ``S3Uploader`` (mainly for tests).  When
                    ``None`` a new one is constructed from config.
    """

    def __init__(
        self,
        sources: list[VideoSource] | None = None,
        db_path: Path | None = None,
        uploader: S3Uploader | None = None,
        app_state: AppState | None = None,
    ) -> None:
        cfg = get_config()
        self._logger = get_logger(__name__)
        self._app_state = app_state

        self._output_base = Path(cfg.hls.output_dir)
        self._db = SegmentDB(db_path or Path("/var/lib/gti-router/upload_queue.db"))
        self._uploader = uploader if uploader is not None else S3Uploader()

        # Queue shares the service-owned DB so the buffer sees the same index.
        self._queue = UploadQueue(
            uploader=self._uploader,
            buffer_dir=self._output_base,
            db=self._db,
        )
        self._buffer = BufferManager(db=self._db, buffer_dir=self._output_base)

        # Map camera_id → input_type from config to label per_camera health.
        input_types = {c.camera_id: c.input_type for c in cfg.cameras}

        # One pipeline per camera, callback wired to the queue.  Each publishes
        # its RTSP/per-camera health into the shared AppState (Story 3.4).
        self._pipelines: list[HLSPipeline] = []
        for source in sources or []:
            input_type = input_types.get(source.camera_id, "rtsp_ip")
            if app_state is not None and source.camera_id not in app_state.per_camera:
                app_state.set_camera(
                    CameraState(camera_id=source.camera_id, input_type=input_type)
                )
            self._pipelines.append(
                HLSPipeline(
                    source=source,
                    on_segment=self._queue.enqueue,
                    output_base_dir=str(self._output_base),
                    app_state=app_state,
                    input_type=input_type,
                )
            )

        self._aws_enabled: bool = cfg.aws.enabled
        self._cloud_started: bool = False
        self._buffer_task: asyncio.Task[None] | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the subsystem, keeping capture alive even if the cloud fails.

        Order matters: the DB is opened first (pipelines enqueue into it), then
        the S3 upload path is brought up **best-effort**, then the capture
        pipelines always start. If S3 is disabled (``aws.enabled=false``, test
        mode) or its init fails (e.g. dummy/invalid credentials), we log a warning
        and run capture-only — segments accumulate as ``pending`` in SQLite and
        upload once valid credentials exist. This mirrors the degraded-mode
        philosophy of the rest of the Router (registration/PTZ/snapshot).
        """
        self._logger.info("UploadService starting", extra={"cameras": len(self._pipelines)})

        # DB must be open before any pipeline enqueues a segment.
        await self._db.open()          # service owns the shared DB

        # Cloud upload path — best-effort, never blocks capture.
        if not self._aws_enabled:
            self._logger.warning(
                "AWS S3 disabled by config (aws.enabled=false) — capture-only, "
                "no uploads. Segments will queue locally in SQLite."
            )
            if self._app_state is not None:
                self._app_state.s3_connected = False
        else:
            try:
                await self._uploader.start()
                await self._queue.start()  # uses the shared DB (does not close it)
                self._cloud_started = True
                if self._app_state is not None:
                    self._app_state.s3_connected = True
            except Exception as exc:  # noqa: BLE001 — cloud must never abort capture
                self._logger.warning(
                    "S3 upload subsystem failed to start (contained) — capture-only, "
                    "segments will queue locally: %s",
                    exc,
                )
                if self._app_state is not None:
                    self._app_state.s3_connected = False

        self._running = True

        # The buffer FIFO only recycles *uploaded* segments, so it is only useful
        # when the cloud path is live.
        if self._cloud_started:
            self._buffer_task = asyncio.create_task(
                self._buffer_loop(), name="buffer-enforcer"
            )

        # Capture pipelines ALWAYS start — this is the whole point of local test.
        for pipe in self._pipelines:
            await pipe.start()

        self._logger.info(
            "UploadService started",
            extra={"cloud": self._cloud_started, "cameras": len(self._pipelines)},
        )

    async def stop(self, drain_timeout_s: float | None = None) -> None:
        """Stop in the safe order: producers → drain uploads → uploader → DB.

        Pipelines are stopped first so no new segments are produced, then the
        upload worker drains its in-flight upload (Story 2.6 graceful shutdown),
        then the S3 client and the DB are closed.  Un-uploaded segments remain
        ``pending``/``failed`` in SQLite and resume on the next start.
        """
        self._logger.info("UploadService stopping")
        self._running = False

        # 1. Stop producers — no more new segments enter the buffer/queue.
        for pipe in self._pipelines:
            try:
                await pipe.stop()
            except Exception as exc:  # never let one camera block shutdown
                self._logger.error("Error stopping pipeline: %s", exc)

        # 2. Stop the periodic buffer cleanup.
        if self._buffer_task is not None and not self._buffer_task.done():
            self._buffer_task.cancel()
            try:
                await self._buffer_task
            except (asyncio.CancelledError, Exception):
                pass
        self._buffer_task = None

        # 3. Drain in-flight uploads within the configured timeout, then close.
        #    Only if the cloud path actually started (test/degraded mode skips it).
        if self._cloud_started:
            await self._queue.stop(drain_timeout_s=drain_timeout_s)  # shares DB → won't close it
            await self._uploader.stop()
        await self._db.close()

        self._logger.info("UploadService stopped")

    # ── Accessors (used by tests / health reporting in Epic 3) ─────────────────

    @property
    def queue(self) -> UploadQueue:
        return self._queue

    @property
    def buffer(self) -> BufferManager:
        return self._buffer

    # ── Periodic buffer cleanup ────────────────────────────────────────────────

    async def _buffer_loop(self) -> None:
        """Periodically enforce the FIFO buffer policy (only uploaded segments)."""
        try:
            while self._running:
                try:
                    await self._buffer.enforce()
                    await self._publish_upload_metrics()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._logger.error("Buffer enforcement error: %s", exc)
                await asyncio.sleep(_BUFFER_ENFORCE_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _publish_upload_metrics(self) -> None:
        """Mirror upload queue counters into the shared AppState for health."""
        if self._app_state is None:
            return
        counts = await self._db.counts()
        self._app_state.upload_queue_size = counts["pending"] + counts["uploading"]
        self._app_state.upload_pending = counts["pending"]
        self._app_state.upload_success_count = self._queue.upload_success_count
        self._app_state.upload_error_count = self._queue.upload_error_count
