"""V4L2 capture-card video source (Stories 5.1 + 5.2).

``CaptureCardSource`` implements :class:`~camera.sources.base.VideoSource` for a
local capture device (``/dev/videoN``) — e.g. an analogue grabber or an HDMI
capture card on a Router Pro.  Unlike RTSP (passthrough), V4L2 capture has no
compressed stream to copy, so the source **encodes** to H.264 using the args
chosen by :class:`~camera.encoder.EncoderSelector` (Story 5.2).  The resulting
H.264 stream feeds the same :class:`~pipeline.ffmpeg_hls.HLSPipeline` contract as
RTSP.

``probe()`` interrogates the device with ``ffprobe -f v4l2`` and reports the V4L2
capture format as the ``codec`` (e.g. ``"mjpeg"``, ``"rawvideo"``) — there is no
*stream* codec for an analogue capture, by convention we report the capture
pixel format.
"""

from __future__ import annotations

import asyncio
import json
from fractions import Fraction

from camera.encoder import EncoderConfig
from camera.sources.base import StreamMetadata, VideoSource
from utils.errors import CaptureCardError
from utils.logging import get_logger

# Software H.264 fallback when no EncoderConfig is injected (keeps the source
# usable standalone; the real codec decision belongs to EncoderSelector).
_FALLBACK_CODEC_ARGS = ["-c:v", "libx264", "-b:v", "4M", "-preset", "veryfast"]


def _parse_framerate(avg_frame_rate: str) -> float:
    try:
        return float(Fraction(avg_frame_rate))
    except (ValueError, ZeroDivisionError):
        return 0.0


class CaptureCardSource(VideoSource):
    """V4L2 capture device source that encodes to H.264.

    Args:
        camera_id:    unique identifier for this camera.
        device:       V4L2 device path, e.g. ``/dev/video0``.
        encoder:      :class:`~camera.encoder.EncoderConfig` from
                      :class:`~camera.encoder.EncoderSelector` (board-aware).
                      If ``None``, a software H.264 fallback is used.
        width/height/framerate: requested capture geometry for the V4L2 input.
        input_format: optional V4L2 pixel format (``-input_format``).
        probe_timeout: seconds to wait for ffprobe.
    """

    def __init__(
        self,
        camera_id: str,
        device: str,
        encoder: EncoderConfig | None = None,
        width: int = 1920,
        height: int = 1080,
        framerate: int = 30,
        input_format: str | None = None,
        probe_timeout: float = 10.0,
    ) -> None:
        self._camera_id = camera_id
        self._device = device
        self._encoder = encoder
        self._width = width
        self._height = height
        self._framerate = framerate
        self._input_format = input_format
        self._probe_timeout = probe_timeout
        self._logger = get_logger(__name__, camera_id=camera_id)

    # ── VideoSource interface ──────────────────────────────────────────────────

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def device(self) -> str:
        return self._device

    @property
    def ffmpeg_input_args(self) -> list[str]:
        """FFmpeg V4L2 capture input args."""
        args = ["-f", "v4l2", "-framerate", str(self._framerate)]
        if self._input_format:
            args += ["-input_format", self._input_format]
        args += ["-video_size", f"{self._width}x{self._height}", "-i", self._device]
        return args

    @property
    def ffmpeg_codec_args(self) -> list[str]:
        """Encode args from the EncoderSelector (or a software H.264 fallback).

        Capture cards never use passthrough (there is no compressed input to
        copy), so this overrides the base ``-c copy`` default.
        """
        if self._encoder is not None:
            return self._encoder.to_ffmpeg_args()
        return list(_FALLBACK_CODEC_ARGS)

    async def probe(self) -> StreamMetadata:
        """Interrogate the V4L2 device and return its capture metadata.

        Raises:
            CaptureCardError: the device is missing/inaccessible or ffprobe fails.
        """
        self._logger.debug(
            "Probing V4L2 capture device",
            extra={"device": self._device, "timeout": self._probe_timeout},
        )
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-f", "v4l2",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            self._device,
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
                proc.communicate(), timeout=self._probe_timeout
            )
        except asyncio.TimeoutError as exc:
            raise CaptureCardError(
                f"[{self._camera_id}] ffprobe timed out after {self._probe_timeout}s "
                f"for device {self._device}"
            ) from exc
        except FileNotFoundError as exc:
            raise CaptureCardError(
                f"[{self._camera_id}] 'ffprobe' binary not found — "
                "install ffmpeg system package (apt install ffmpeg)"
            ) from exc
        except OSError as exc:
            raise CaptureCardError(
                f"[{self._camera_id}] OS error launching ffprobe for "
                f"{self._device}: {exc}"
            ) from exc

        if proc.returncode != 0:
            short_err = stderr.decode(errors="replace")[:300].strip()
            raise CaptureCardError(
                f"[{self._camera_id}] capture device {self._device} not available "
                f"(ffprobe exit {proc.returncode}): {short_err}"
            )

        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise CaptureCardError(
                f"[{self._camera_id}] unexpected ffprobe output for "
                f"{self._device} (not JSON): {exc}"
            ) from exc

        streams = data.get("streams", [])
        if not streams:
            raise CaptureCardError(
                f"[{self._camera_id}] no video stream from device {self._device}"
            )

        video = streams[0]
        # For V4L2 the "codec" is the capture pixel format (rawvideo/mjpeg/...),
        # not a stream codec — documented convention (Story 5.1 AC#6).
        codec = video.get("codec_name", "").lower() or "rawvideo"
        width = int(video.get("width", self._width) or self._width)
        height = int(video.get("height", self._height) or self._height)
        framerate = _parse_framerate(video.get("avg_frame_rate", "0/1")) or float(
            self._framerate
        )

        metadata = StreamMetadata(
            codec=codec, width=width, height=height,
            framerate=framerate, camera_id=self._camera_id,
        )
        self._logger.info(
            "V4L2 probe successful",
            extra={
                "device": self._device,
                "capture_format": codec,
                "resolution": metadata.resolution,
                "fps": round(framerate, 3),
            },
        )
        return metadata
