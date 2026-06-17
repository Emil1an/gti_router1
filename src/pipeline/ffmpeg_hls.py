"""HLS segmentation pipeline + RTSP auto-recovery — 1 FFmpeg subprocess per camera.

``HLSPipeline`` wraps a single ``ffmpeg`` process that reads from a
:class:`~camera.sources.base.VideoSource` and writes HLS segments to a
per-camera buffer directory.  It is agnostic to the source type (RTSP IP camera
or V4L2 capture card).

Architectural invariants
------------------------
* **Passthrough (-c copy):** no transcoding for RTSP sources (FR12).
* **1 process + 1 supervisor task per camera:** hard fault-isolation boundary —
  a crash/reconnect of one camera never affects another (D2).
* **asyncio throughout:** the event loop is never blocked.
* **Callback contract:** ``(camera_id: str, segment_path: Path, created_at: str)``.

Auto-recovery (Story 3.4)
-------------------------
The supervisor detects connection loss when FFmpeg exits (non-zero or an
unexpected 0) **or** when no new ``.ts`` segment appears within
``recovery.rtsp_segment_timeout_s`` (stall).  It reconnects with the single
``@with_retry`` (backoff 1→60 s + jitter, NFR6 <60 s).  After
``recovery.rtsp_max_failures`` consecutive failures the camera is marked
**unavailable** in ``per_camera`` health, and the supervisor keeps retrying at a
bounded rate.  The buffer (FS) and upload queue (SQLite) are never touched during
reconnection.  Metrics ``rtsp_reconnect_count`` / ``rtsp_connected`` /
``rtsp_last_connected`` are published to :class:`~health.state.AppState`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from camera.sources.base import VideoSource
from config.loader import get_config
from health.state import AppState, CameraState
from utils.errors import FFmpegError, PipelineError, RTSPConnectionError
from utils.logging import get_logger
from utils.retry import with_retry

# Callback signature: async (camera_id, segment_path, created_at) -> None
SegmentCallback = Callable[[str, Path, str], Coroutine[Any, Any, None]]

# How long (s) to wait for FFmpeg to acknowledge a graceful SIGTERM before SIGKILL
_STOP_GRACEFUL_TIMEOUT: float = 5.0

# Poll interval (s) for detecting new .ts files in the output directory
_SEGMENT_POLL_INTERVAL: float = 0.5

# Errors that trigger an RTSP reconnect attempt.
_RECONNECT_ERRORS = (FFmpegError, PipelineError, RTSPConnectionError, OSError)


class HLSPipeline:
    """Manages one FFmpeg HLS segmentation subprocess + auto-recovery for a camera.

    Args:
        source:           :class:`~camera.sources.base.VideoSource` instance.
        on_segment:       async callback for every new ``.ts`` segment.
        output_base_dir:  parent dir for per-camera HLS output (defaults to
                          ``config.hls.output_dir``).
        app_state:        shared :class:`~health.state.AppState` to publish this
                          camera's ``per_camera`` status and RTSP metrics into.
        input_type:       ``"rtsp_ip"`` / ``"capture_card"`` (for ``per_camera``).
        segment_timeout_s / max_failures:  override ``recovery`` config (tests).
        reconnect_base_delay / reconnect_max_delay / reconnect_idle_s:
                          backoff knobs (production defaults 1 / 60 / 60 s).
    """

    def __init__(
        self,
        source: VideoSource,
        on_segment: SegmentCallback | None = None,
        output_base_dir: str | None = None,
        app_state: AppState | None = None,
        input_type: str = "rtsp_ip",
        segment_timeout_s: int | None = None,
        max_failures: int | None = None,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        reconnect_idle_s: float = 60.0,
    ) -> None:
        self._source = source
        self._on_segment = on_segment
        self._camera_id = source.camera_id
        self._logger = get_logger(__name__, camera_id=self._camera_id)

        cfg = get_config()
        self._segment_duration: int = cfg.hls.segment_duration
        base = output_base_dir or cfg.hls.output_dir
        self._output_dir: Path = Path(base) / self._camera_id
        self._playlist_path: Path = self._output_dir / "playlist.m3u8"

        # Recovery parameters
        self._segment_timeout_s: float = float(
            segment_timeout_s if segment_timeout_s is not None
            else cfg.recovery.rtsp_segment_timeout_s
        )
        self._max_failures: int = (
            max_failures if max_failures is not None else cfg.recovery.rtsp_max_failures
        )
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_idle_s = reconnect_idle_s
        self._stall_check_interval = min(self._segment_timeout_s, 1.0)

        # Shared state / per-camera health
        self._app_state = app_state
        self._input_type = input_type

        self._process: asyncio.subprocess.Process | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._known_segments: set[Path] = set()

        # Recovery state + metrics
        self._total_launches: int = 0
        self._consecutive_failures: int = 0
        self._rtsp_reconnect_count: int = 0
        self._rtsp_connected: bool = False
        self._rtsp_last_connected: str | None = None
        self._camera_unavailable: bool = False
        self._last_segment_monotonic: float = 0.0

        # Seed the per-camera health block.
        self._update_camera_state(connected=False, streaming=False)

    # ── Public lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the FFmpeg supervisor and segment-watcher tasks."""
        if self._running:
            return
        self._running = True
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._logger.info(
            "Starting HLS pipeline",
            extra={"output_dir": str(self._output_dir), "segment_s": self._segment_duration},
        )
        self._supervisor_task = asyncio.create_task(
            self._supervisor_loop(), name=f"hls-supervisor-{self._camera_id}"
        )
        self._watcher_task = asyncio.create_task(
            self._segment_watcher(), name=f"hls-watcher-{self._camera_id}"
        )

    async def stop(self) -> None:
        """Stop the pipeline gracefully (SIGTERM → wait → SIGKILL)."""
        self._running = False
        self._logger.info("Stopping HLS pipeline")

        for task in (self._supervisor_task, self._watcher_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        await self._terminate_process()
        self._rtsp_connected = False
        self._update_camera_state(connected=False, streaming=False)
        self._logger.info("HLS pipeline stopped")

    # ── Metrics (consumed by HealthReporter via per_camera) ─────────────────────

    @property
    def rtsp_connected(self) -> bool:
        return self._rtsp_connected

    @property
    def rtsp_reconnect_count(self) -> int:
        return self._rtsp_reconnect_count

    @property
    def rtsp_last_connected(self) -> str | None:
        return self._rtsp_last_connected

    @property
    def camera_unavailable(self) -> bool:
        return self._camera_unavailable

    # ── Internal: subprocess management ───────────────────────────────────────

    async def _launch_ffmpeg(self) -> asyncio.subprocess.Process:
        """Spawn a fresh FFmpeg process and return it."""
        cmd = self._build_ffmpeg_command()
        self._logger.debug("Launching FFmpeg", extra={"cmd": " ".join(cmd)})
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise PipelineError(
                f"[{self._camera_id}] 'ffmpeg' binary not found — "
                "install ffmpeg system package (apt install ffmpeg)"
            ) from exc
        except OSError as exc:
            raise PipelineError(
                f"[{self._camera_id}] OS error launching FFmpeg: {exc}"
            ) from exc
        self._logger.info("FFmpeg started", extra={"pid": proc.pid})
        return proc

    async def _terminate_process(self) -> None:
        """Send SIGTERM then SIGKILL to the running FFmpeg process."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACEFUL_TIMEOUT)
                self._logger.debug("FFmpeg exited after SIGTERM", extra={"pid": proc.pid})
            except asyncio.TimeoutError:
                self._logger.warning(
                    "FFmpeg did not exit after SIGTERM — sending SIGKILL",
                    extra={"pid": proc.pid},
                )
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass  # already dead
        finally:
            self._process = None

    async def _launch_and_wait(self) -> None:
        """One launch+watch cycle: connect, run until exit/stall, raise on failure.

        Raised exceptions (in :data:`_RECONNECT_ERRORS`) drive the ``@with_retry``
        reconnection in :meth:`_supervisor_loop`.
        """
        if self._total_launches > 0:
            self._rtsp_reconnect_count += 1
            self._logger.warning(
                "Reconnecting RTSP stream",
                extra={"rtsp_reconnect_count": self._rtsp_reconnect_count},
            )
        self._total_launches += 1

        self._process = await self._launch_ffmpeg()
        self._on_connected()

        outcome, stderr_bytes = await self._wait_exit_or_stall(self._process)
        returncode = self._process.returncode if self._process is not None else None
        self._process = None

        if not self._running:
            return  # clean shutdown — not a failure

        stderr_text = (stderr_bytes or b"").decode(errors="replace")

        if outcome == "stalled":
            self._on_failure()
            raise RTSPConnectionError(
                f"[{self._camera_id}] no new segment within "
                f"{self._segment_timeout_s:.0f}s — RTSP stream stalled"
            )
        if returncode != 0:
            self._on_failure()
            raise FFmpegError(
                camera_id=self._camera_id,
                returncode=returncode if returncode is not None else -1,
                stderr=stderr_text,
            )
        self._on_failure()
        raise PipelineError(
            f"[{self._camera_id}] FFmpeg exited with code 0 unexpectedly"
        )

    async def _wait_exit_or_stall(
        self, proc: asyncio.subprocess.Process
    ) -> tuple[str, bytes]:
        """Wait until FFmpeg exits, or the stream stalls (no new segment in time).

        Returns ``("exited", stderr)`` or ``("stalled", stderr)``.  On a stall the
        process is terminated so the supervisor can reconnect.
        """
        comm = asyncio.create_task(proc.communicate())
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {comm}, timeout=self._stall_check_interval
                )
                if comm in done:
                    _stdout, stderr = comm.result()
                    return "exited", stderr or b""
                if not self._running:
                    _stdout, stderr = await comm
                    return "exited", stderr or b""
                # Stall detection: no new segment within the timeout window.
                if (time.monotonic() - self._last_segment_monotonic) > self._segment_timeout_s:
                    self._logger.warning(
                        "RTSP stall detected — terminating FFmpeg to reconnect",
                        extra={"segment_timeout_s": self._segment_timeout_s},
                    )
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        _stdout, stderr = await comm
                    except Exception:
                        stderr = b""
                    return "stalled", stderr or b""
        finally:
            if not comm.done():
                comm.cancel()
                try:
                    await comm
                except (asyncio.CancelledError, Exception):
                    pass

    async def _supervisor_loop(self) -> None:
        """Keep FFmpeg running; reconnect with @with_retry; mark unavailable after N."""
        wrapped = with_retry(
            max_retries=self._max_failures,
            retryable=_RECONNECT_ERRORS,
            base_delay=self._reconnect_base_delay,
            max_delay=self._reconnect_max_delay,
        )(self._launch_and_wait)

        try:
            while self._running:
                try:
                    await wrapped()
                except asyncio.CancelledError:
                    raise
                except _RECONNECT_ERRORS as exc:
                    # N consecutive failures exhausted the retry cycle: mark the
                    # camera unavailable but KEEP retrying at a bounded rate
                    # (NFR — never give up; buffer/queue stay intact).
                    self._mark_unavailable(exc)
                    if not self._running:
                        break
                    try:
                        await asyncio.sleep(self._reconnect_idle_s)
                    except asyncio.CancelledError:
                        raise
                else:
                    if not self._running:
                        break
        except asyncio.CancelledError:
            pass

    # ── Recovery state transitions ──────────────────────────────────────────────

    def _on_connected(self) -> None:
        self._last_segment_monotonic = time.monotonic()  # reset stall timer
        self._rtsp_connected = True
        self._rtsp_last_connected = _utc_now_iso()
        self._consecutive_failures = 0
        self._camera_unavailable = False
        self._update_camera_state(connected=True, streaming=True, error=None)
        self._logger.info(
            "RTSP connected",
            extra={
                "rtsp_connected": True,
                "rtsp_last_connected": self._rtsp_last_connected,
                "rtsp_reconnect_count": self._rtsp_reconnect_count,
            },
        )

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        self._rtsp_connected = False
        self._update_camera_state(connected=False, streaming=False)

    def _mark_unavailable(self, exc: BaseException) -> None:
        self._camera_unavailable = True
        self._rtsp_connected = False
        self._logger.error(
            "Camera marked UNAVAILABLE after %d consecutive RTSP failures: %s",
            self._consecutive_failures,
            exc,
            extra={"rtsp_connected": False, "rtsp_reconnect_count": self._rtsp_reconnect_count},
        )
        self._update_camera_state(connected=False, streaming=False, error=str(exc))

    def _update_camera_state(self, **fields: Any) -> None:
        """Publish this camera's status into the shared per_camera health block."""
        if self._app_state is None:
            return
        cam = self._app_state.per_camera.get(self._camera_id)
        if cam is None:
            cam = CameraState(camera_id=self._camera_id, input_type=self._input_type)
            self._app_state.per_camera[self._camera_id] = cam
        for key, value in fields.items():
            setattr(cam, key, value)

    # ── Internal: segment detection ────────────────────────────────────────────

    async def _segment_watcher(self) -> None:
        """Poll the output directory and fire on_segment for new .ts files."""
        try:
            while self._running:
                await asyncio.sleep(_SEGMENT_POLL_INTERVAL)
                if not self._output_dir.exists():
                    continue
                try:
                    current = {
                        p for p in self._output_dir.iterdir() if p.suffix == ".ts"
                    }
                except OSError:
                    continue

                new = current - self._known_segments
                for segment_path in sorted(new):  # emit in chronological order
                    self._known_segments.add(segment_path)
                    created_at = _utc_now_iso()
                    # A fresh segment proves the stream is alive — reset stall timer.
                    self._last_segment_monotonic = time.monotonic()
                    self._update_camera_state(last_segment_at=created_at, streaming=True)
                    self._logger.debug(
                        "New segment",
                        extra={"segment": segment_path.name, "created_at": created_at},
                    )
                    if self._on_segment is not None:
                        try:
                            await self._on_segment(
                                self._camera_id, segment_path, created_at
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            self._logger.error("on_segment callback raised: %s", exc)
        except asyncio.CancelledError:
            pass

    # ── Internal: command builder ──────────────────────────────────────────────

    def _build_ffmpeg_command(self) -> list[str]:
        """Build the full FFmpeg CLI command list (passthrough HLS)."""
        return [
            "ffmpeg",
            "-y",  # overwrite outputs without asking
            *self._source.ffmpeg_input_args,
            # Codec args come from the source: passthrough (-c copy) for RTSP,
            # or the EncoderSelector's encode args for a capture card. The
            # pipeline stays agnostic to the source type (Story 5.1 AC#5).
            *self._source.ffmpeg_codec_args,
            "-f", "hls",
            "-hls_time", str(self._segment_duration),
            "-hls_segment_type", "mpegts",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", str(self._output_dir / "segment_%05d.ts"),
            str(self._playlist_path),
        ]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 with trailing Z."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
