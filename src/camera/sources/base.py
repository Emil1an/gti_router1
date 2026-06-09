"""Base interface for all video sources consumed by the GTI Router pipeline.

The pipeline (Story 1.4) and upload worker (Epic 2) interact exclusively with
``VideoSource``; they never import ``RTSPSource`` or ``CaptureCardSource``
directly.  This keeps the pipeline agnostic to the origin of the video
(network camera vs. V4L2 capture card).

Contract
--------
Every concrete implementation must:
  * Implement ``async probe() -> StreamMetadata`` — verify the source is
    reachable and return stream metadata *before* capture starts.
  * Expose ``camera_id`` as a read-only string property.
  * Raise typed exceptions from ``utils.errors`` — never ``Exception`` directly.

Extending in Epic 5
-------------------
``CaptureCardSource(VideoSource)`` will follow the same interface with a V4L2
``/dev/videoN`` path instead of an RTSP URL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StreamMetadata:
    """Immutable metadata returned by a successful ``probe()`` call.

    Attributes:
        codec:       video codec name as reported by ffprobe (e.g. ``"h264"``,
                     ``"hevc"``).  Lowercase, no spaces.
        width:       frame width in pixels.
        height:      frame height in pixels.
        framerate:   frames per second (float, may be fractional, e.g. 29.97).
        camera_id:   identifier of the source that produced this metadata.
    """

    codec: str
    width: int
    height: int
    framerate: float
    camera_id: str

    @property
    def resolution(self) -> str:
        """Human-readable resolution string, e.g. ``"1920x1080"``."""
        return f"{self.width}x{self.height}"

    @property
    def is_passthrough_compatible(self) -> bool:
        """Return ``True`` if the codec can be copied with ``-c copy`` (passthrough).

        Only H.264 and H.265/HEVC are accepted for passthrough (FR12).
        """
        return self.codec.lower() in {"h264", "hevc", "h265"}


class VideoSource(ABC):
    """Abstract base class for all GTI Router video sources.

    Lifecycle
    ---------
    1. Instantiate with source-specific configuration.
    2. Call ``await source.probe()`` to verify the source and get metadata.
    3. Pass the source (and its metadata) to ``HLSPipeline``.
    4. The pipeline calls ``source.ffmpeg_input_args`` to build the FFmpeg
       command without needing to know the source type.
    """

    @property
    @abstractmethod
    def camera_id(self) -> str:
        """Unique identifier for this camera / source."""

    @abstractmethod
    async def probe(self) -> StreamMetadata:
        """Verify the source is reachable and return its stream metadata.

        Returns:
            A :class:`StreamMetadata` instance with codec, resolution and
            framerate.

        Raises:
            RTSPConnectionError: host unreachable or connection timed out.
            RTSPAuthError:       credentials rejected by the camera.
            RTSPCodecError:      stream codec is unsupported or not detectable.
        """

    @property
    @abstractmethod
    def ffmpeg_input_args(self) -> list[str]:
        """FFmpeg ``-i`` argument list that describes this source.

        Example for RTSP::

            ["-rtsp_transport", "tcp", "-i", "rtsp://user:pass@host/stream"]

        The pipeline prepends these to its FFmpeg command without needing to
        know whether the source is a network camera or a capture card.
        """
