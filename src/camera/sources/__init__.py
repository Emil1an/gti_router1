"""Video source abstractions + factory (Story 5.1).

``create_source(camera_config, board=None)`` dispatches on ``input_type`` to the
right :class:`~camera.sources.base.VideoSource`, so the rest of the code (and the
pipeline in particular) never branches on the source origin — it only ever sees
the ``VideoSource`` interface (AC#5).

For ``capture_card`` sources the factory also resolves the H.264 encoder via
:class:`~camera.encoder.EncoderSelector` using the injected ``board`` (Story 5.2);
when no board is given it falls back to software H.264 so the source is still
constructible off-hardware.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from camera.encoder import EncoderSelector
from camera.sources.base import VideoSource
from camera.sources.capture_card_source import CaptureCardSource
from camera.sources.rtsp_source import RTSPSource
from config.schema import CameraConfig
from utils.errors import VideoSourceError

if TYPE_CHECKING:  # only for typing; avoids importing the clashing platform pkg
    from platform.board import Board

__all__ = ["VideoSource", "RTSPSource", "CaptureCardSource", "create_source"]


def create_source(
    camera_config: CameraConfig,
    board: "Board | None" = None,
) -> VideoSource:
    """Instantiate the correct :class:`VideoSource` for ``camera_config``.

    Args:
        camera_config: validated per-camera config (provides ``input_type``).
        board:         detected board (Story 5.5) used to pick the capture-card
                       encoder. ``None`` → software H.264 fallback.

    Raises:
        VideoSourceError: ``input_type`` is missing fields or is unknown.
    """
    input_type = camera_config.input_type

    if input_type == "rtsp_ip":
        if not camera_config.rtsp_url:
            raise VideoSourceError(
                f"camera {camera_config.camera_id}: rtsp_url required for rtsp_ip"
            )
        return RTSPSource(
            camera_id=camera_config.camera_id, rtsp_url=camera_config.rtsp_url
        )

    if input_type == "capture_card":
        if not camera_config.device:
            raise VideoSourceError(
                f"camera {camera_config.camera_id}: device required for capture_card"
            )
        encoder_config = None
        if board is not None:
            encoder_config = EncoderSelector(board).select()
        return CaptureCardSource(
            camera_id=camera_config.camera_id,
            device=camera_config.device,
            encoder=encoder_config,
        )

    raise VideoSourceError(
        f"camera {camera_config.camera_id}: unknown input_type '{input_type}'"
    )
