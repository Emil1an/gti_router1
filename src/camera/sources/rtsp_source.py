"""RTSP video source with stream probe via ffprobe.

``RTSPSource`` implements :class:`~camera.sources.base.VideoSource` for IP
cameras connected via the RTSP protocol.  It uses **ffprobe** (part of the
system FFmpeg package, apt 5.1 on Raspberry Pi OS Bookworm) to interrogate the
stream and return codec/resolution/framerate metadata.

Design decisions
----------------
* **TCP transport:** ``-rtsp_transport tcp`` avoids packet loss over unstable
  WiFi links (required by architecture, FR1).
* **ffprobe for probe:** uses the system ``ffprobe`` binary via
  ``asyncio.create_subprocess_exec``; no Python RTSP library is needed, and
  the output format is JSON for reliable parsing.
* **No retry on probe:** probe is a single point-in-time check (timeout
  enforced); the caller (e.g. ``main.py`` or Story 3.4 reconnect loop) is
  responsible for retrying with ``@with_retry``.
* **Passthrough only:** if the detected codec is not H.264 or H.265, a
  ``RTSPCodecError`` is raised immediately (AC #4).
"""

from __future__ import annotations

import asyncio
import json
import re
from fractions import Fraction

from camera.sources.base import StreamMetadata, VideoSource
from utils.errors import RTSPAuthError, RTSPCodecError, RTSPConnectionError
from utils.logging import get_logger

# Codecs accepted for passthrough (-c copy) — FR12
_PASSTHROUGH_CODECS: frozenset[str] = frozenset({"h264", "hevc", "h265"})

# ffprobe exit-code patterns that map to auth failure
_AUTH_STDERR_PATTERNS: tuple[str, ...] = (
    "401 unauthorized",
    "403 forbidden",
    "authentication failed",
    "incorrect password",
)


def _parse_framerate(avg_frame_rate: str) -> float:
    """Convert ``"25/1"`` or ``"30000/1001"`` to a float."""
    try:
        return float(Fraction(avg_frame_rate))
    except (ValueError, ZeroDivisionError):
        return 0.0


class RTSPSource(VideoSource):
    """IP camera source that connects over RTSP/TCP.

    Args:
        camera_id:   unique identifier for this camera (from ``CameraConfig``).
        rtsp_url:    full RTSP URL including credentials if required,
                     e.g. ``rtsp://admin:pass@192.168.1.100:554/stream1``.
        probe_timeout: seconds to wait for ffprobe to return. Default 10 s.
    """

    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        probe_timeout: float = 10.0,
    ) -> None:
        self._camera_id = camera_id
        self._rtsp_url = rtsp_url
        self._probe_timeout = probe_timeout
        self._logger = get_logger(__name__, camera_id=camera_id)

    # ── VideoSource interface ──────────────────────────────────────────────────

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def ffmpeg_input_args(self) -> list[str]:
        """FFmpeg input flags for RTSP/TCP passthrough."""
        return ["-rtsp_transport", "tcp", "-i", self._rtsp_url]

    async def probe(self) -> StreamMetadata:
        """Probe the RTSP stream and return its :class:`~camera.sources.base.StreamMetadata`.

        Raises:
            RTSPConnectionError: if the host is unreachable or the probe times out.
            RTSPAuthError:       if the camera rejects the credentials.
            RTSPCodecError:      if the video codec is absent or not H.264/H.265.
        """
        self._logger.debug(
            "Probing RTSP stream",
            extra={"url": self._sanitized_url, "timeout": self._probe_timeout},
        )
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-rtsp_transport", "tcp",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            "-timeout", str(int(self._probe_timeout * 1_000_000)),  # µs
            self._rtsp_url,
        ]
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=self._probe_timeout,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._probe_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RTSPConnectionError(
                f"[{self._camera_id}] ffprobe timed out after {self._probe_timeout}s "
                f"({self._sanitized_url})"
            ) from exc
        except FileNotFoundError as exc:
            raise RTSPConnectionError(
                f"[{self._camera_id}] 'ffprobe' binary not found — "
                "install ffmpeg system package (apt install ffmpeg)"
            ) from exc
        except OSError as exc:
            raise RTSPConnectionError(
                f"[{self._camera_id}] OS error launching ffprobe: {exc}"
            ) from exc

        stderr_text = stderr.decode(errors="replace").lower()
        return_code = proc.returncode

        # ── Auth failure ──────────────────────────────────────────────────────
        if any(pat in stderr_text for pat in _AUTH_STDERR_PATTERNS):
            raise RTSPAuthError(
                f"[{self._camera_id}] Authentication rejected by camera "
                f"({self._sanitized_url})"
            )

        # ── Connection / network failure ──────────────────────────────────────
        if return_code != 0:
            short_err = stderr_text[:300].strip()
            raise RTSPConnectionError(
                f"[{self._camera_id}] ffprobe exited with code {return_code} "
                f"for {self._sanitized_url}: {short_err}"
            )

        # ── Parse JSON output ─────────────────────────────────────────────────
        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise RTSPConnectionError(
                f"[{self._camera_id}] Unexpected ffprobe output (not JSON): {exc}"
            ) from exc

        streams = data.get("streams", [])
        if not streams:
            raise RTSPCodecError(
                f"[{self._camera_id}] ffprobe found no video stream in "
                f"{self._sanitized_url}"
            )

        video = streams[0]
        codec = video.get("codec_name", "").lower()

        # ── Codec validation (passthrough gate) ───────────────────────────────
        if codec not in _PASSTHROUGH_CODECS:
            raise RTSPCodecError(
                f"[{self._camera_id}] Unsupported codec '{codec}' detected in "
                f"{self._sanitized_url}. Only H.264 and H.265 are accepted for "
                "passthrough (-c copy)."
            )

        width = int(video.get("width", 0))
        height = int(video.get("height", 0))
        framerate = _parse_framerate(video.get("avg_frame_rate", "0/1"))

        metadata = StreamMetadata(
            codec=codec,
            width=width,
            height=height,
            framerate=framerate,
            camera_id=self._camera_id,
        )
        self._logger.info(
            "RTSP probe successful",
            extra={
                "codec": codec,
                "resolution": metadata.resolution,
                "fps": round(framerate, 3),
            },
        )
        return metadata

    # ── Internal helpers ───────────────────────────────────────────────────────

    @property
    def _sanitized_url(self) -> str:
        """Return the RTSP URL with any embedded password replaced by ``***``."""
        return re.sub(r"(rtsp://[^:]+:)[^@]+(@)", r"\1***\2", self._rtsp_url)
