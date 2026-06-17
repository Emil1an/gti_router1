"""Mock helpers for V4L2 capture-card tests (no hardware required).

``CaptureCardSource.probe()`` uses ``ffprobe -f v4l2`` via
``asyncio.create_subprocess_exec`` — the same pattern as RTSP — so we reuse
:func:`tests.fixtures.mock_rtsp.make_ffprobe_mock` and just supply V4L2-shaped
JSON payloads here.
"""

from __future__ import annotations

import json

from tests.fixtures.mock_rtsp import make_ffprobe_mock  # re-exported for tests

__all__ = ["make_ffprobe_mock", "FFPROBE_V4L2_RAW", "FFPROBE_V4L2_MJPEG", "FFPROBE_V4L2_EMPTY"]


def _v4l2_output(
    codec_name: str,
    width: int = 1920,
    height: int = 1080,
    avg_frame_rate: str = "30/1",
) -> bytes:
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec_name,   # V4L2 capture format (not a stream codec)
                "width": width,
                "height": height,
                "avg_frame_rate": avg_frame_rate,
            }
        ]
    }
    return json.dumps(payload).encode()


FFPROBE_V4L2_RAW: bytes = _v4l2_output("rawvideo", 1920, 1080, "30/1")
FFPROBE_V4L2_MJPEG: bytes = _v4l2_output("mjpeg", 1280, 720, "60/1")
FFPROBE_V4L2_EMPTY: bytes = json.dumps({"streams": []}).encode()
