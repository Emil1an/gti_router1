"""Mock helpers for RTSP-related tests (no hardware required).

Strategy
--------
``RTSPSource.probe()`` delegates all I/O to ``asyncio.create_subprocess_exec``
(spawning ``ffprobe``).  Rather than running a real RTSP server, we patch that
coroutine with a factory that returns a fake process whose ``stdout`` /
``stderr`` and ``returncode`` we control per-test.

Usage in tests
--------------
```python
from tests.fixtures.mock_rtsp import make_ffprobe_mock, FFPROBE_H264_OUTPUT

async def test_probe_ok(monkeypatch):
    monkeypatch.setattr(
        "camera.sources.rtsp_source.asyncio.create_subprocess_exec",
        make_ffprobe_mock(stdout=FFPROBE_H264_OUTPUT, returncode=0),
    )
    src = RTSPSource("cam-1", "rtsp://x/s")
    meta = await src.probe()
    assert meta.codec == "h264"
```
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock


# ── Canonical ffprobe JSON outputs ─────────────────────────────────────────────

def _ffprobe_output(
    codec_name: str,
    width: int = 1920,
    height: int = 1080,
    avg_frame_rate: str = "25/1",
) -> bytes:
    """Return a minimal ffprobe JSON payload for a single video stream."""
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec_name,
                "width": width,
                "height": height,
                "avg_frame_rate": avg_frame_rate,
            }
        ]
    }
    return json.dumps(payload).encode()


FFPROBE_H264_OUTPUT: bytes = _ffprobe_output("h264", 1920, 1080, "25/1")
FFPROBE_H265_OUTPUT: bytes = _ffprobe_output("hevc", 2560, 1440, "30000/1001")
FFPROBE_MJPEG_OUTPUT: bytes = _ffprobe_output("mjpeg", 1280, 720, "25/1")
FFPROBE_EMPTY_OUTPUT: bytes = json.dumps({"streams": []}).encode()
FFPROBE_GARBAGE_OUTPUT: bytes = b"not-json-garbage"


# ── Mock process factory ───────────────────────────────────────────────────────

class _FakeProcess:
    """Minimal ``asyncio.subprocess.Process`` stand-in."""

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def make_ffprobe_mock(
    stdout: bytes = FFPROBE_H264_OUTPUT,
    stderr: bytes = b"",
    returncode: int = 0,
) -> Callable[..., Any]:
    """Return an async callable that mimics ``asyncio.create_subprocess_exec``.

    The returned callable ignores all arguments and immediately yields a
    ``_FakeProcess`` with the provided ``stdout`` / ``stderr`` / ``returncode``.
    """

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return _FakeProcess(stdout=stdout, stderr=stderr, returncode=returncode)

    return _fake_exec


# ── Common error payloads ──────────────────────────────────────────────────────

STDERR_AUTH_FAILURE: bytes = b"Server returned 401 Unauthorized"
STDERR_CONNECTION_REFUSED: bytes = b"Connection refused"
STDERR_GENERIC_FAILURE: bytes = b"No route to host"
