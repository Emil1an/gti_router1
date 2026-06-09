"""Tests for RTSPSource.probe() — no hardware required.

All RTSP / ffprobe I/O is replaced by in-process fake processes built with
``tests.fixtures.mock_rtsp.make_ffprobe_mock``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from camera.sources.base import StreamMetadata
from camera.sources.rtsp_source import RTSPSource
from tests.fixtures.mock_rtsp import (
    FFPROBE_EMPTY_OUTPUT,
    FFPROBE_GARBAGE_OUTPUT,
    FFPROBE_H264_OUTPUT,
    FFPROBE_H265_OUTPUT,
    FFPROBE_MJPEG_OUTPUT,
    STDERR_AUTH_FAILURE,
    STDERR_CONNECTION_REFUSED,
    make_ffprobe_mock,
)
from utils.errors import RTSPAuthError, RTSPCodecError, RTSPConnectionError

_RTSP_URL = "rtsp://admin:secret@192.168.1.1:554/stream"
_CAM_ID = "cam-front"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _src(timeout: float = 5.0) -> RTSPSource:
    return RTSPSource(_CAM_ID, _RTSP_URL, probe_timeout=timeout)


def _patch_exec(mock_fn):
    """Context manager that patches asyncio.create_subprocess_exec in rtsp_source."""
    return patch(
        "camera.sources.rtsp_source.asyncio.create_subprocess_exec",
        new=mock_fn,
    )


# ── Successful probe — H.264 ───────────────────────────────────────────────────

class TestProbeSuccess:
    @pytest.mark.asyncio
    async def test_h264_returns_correct_metadata(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_H264_OUTPUT)):
            meta = await _src().probe()

        assert isinstance(meta, StreamMetadata)
        assert meta.codec == "h264"
        assert meta.width == 1920
        assert meta.height == 1080
        assert meta.framerate == pytest.approx(25.0)
        assert meta.camera_id == _CAM_ID
        assert meta.resolution == "1920x1080"

    @pytest.mark.asyncio
    async def test_h265_returns_correct_metadata(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_H265_OUTPUT)):
            meta = await _src().probe()

        assert meta.codec == "hevc"
        assert meta.width == 2560
        assert meta.height == 1440
        assert meta.framerate == pytest.approx(30000 / 1001, rel=1e-4)

    @pytest.mark.asyncio
    async def test_passthrough_compatible_flag(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_H264_OUTPUT)):
            meta = await _src().probe()
        assert meta.is_passthrough_compatible is True

    @pytest.mark.asyncio
    async def test_camera_id_propagated_into_metadata(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_H264_OUTPUT)):
            meta = await _src().probe()
        assert meta.camera_id == _CAM_ID


# ── Auth failure ───────────────────────────────────────────────────────────────

class TestProbeAuthFailure:
    @pytest.mark.asyncio
    async def test_401_stderr_raises_auth_error(self) -> None:
        with _patch_exec(
            make_ffprobe_mock(stdout=b"", stderr=STDERR_AUTH_FAILURE, returncode=1)
        ):
            with pytest.raises(RTSPAuthError, match=_CAM_ID):
                await _src().probe()

    @pytest.mark.asyncio
    async def test_auth_error_is_rtsp_error_subclass(self) -> None:
        from utils.errors import RTSPError
        with _patch_exec(
            make_ffprobe_mock(stdout=b"", stderr=STDERR_AUTH_FAILURE, returncode=1)
        ):
            with pytest.raises(RTSPError):
                await _src().probe()


# ── Connection / timeout failures ─────────────────────────────────────────────

class TestProbeConnectionFailure:
    @pytest.mark.asyncio
    async def test_nonzero_returncode_raises_connection_error(self) -> None:
        with _patch_exec(
            make_ffprobe_mock(
                stdout=b"",
                stderr=STDERR_CONNECTION_REFUSED,
                returncode=1,
            )
        ):
            with pytest.raises(RTSPConnectionError, match=_CAM_ID):
                await _src().probe()

    @pytest.mark.asyncio
    async def test_timeout_raises_connection_error(self) -> None:
        """asyncio.wait_for TimeoutError must map to RTSPConnectionError."""
        # Patch wait_for directly so no dangling coroutine is created.
        with patch(
            "camera.sources.rtsp_source.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            with pytest.raises(RTSPConnectionError, match="timed out"):
                await _src(timeout=0.001).probe()

    @pytest.mark.asyncio
    async def test_ffprobe_not_found_raises_connection_error(self) -> None:
        async def _not_found(*_a, **_kw):
            raise FileNotFoundError("ffprobe not found")

        with patch(
            "camera.sources.rtsp_source.asyncio.create_subprocess_exec",
            new=_not_found,
        ):
            with pytest.raises(RTSPConnectionError, match="ffprobe"):
                await _src().probe()

    @pytest.mark.asyncio
    async def test_garbage_stdout_raises_connection_error(self) -> None:
        with _patch_exec(
            make_ffprobe_mock(stdout=FFPROBE_GARBAGE_OUTPUT, returncode=0)
        ):
            with pytest.raises(RTSPConnectionError, match="not JSON"):
                await _src().probe()


# ── Codec failures ─────────────────────────────────────────────────────────────

class TestProbeCodecFailure:
    @pytest.mark.asyncio
    async def test_unsupported_codec_raises_codec_error(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_MJPEG_OUTPUT)):
            with pytest.raises(RTSPCodecError, match="mjpeg"):
                await _src().probe()

    @pytest.mark.asyncio
    async def test_codec_error_includes_camera_id(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_MJPEG_OUTPUT)):
            with pytest.raises(RTSPCodecError, match=_CAM_ID):
                await _src().probe()

    @pytest.mark.asyncio
    async def test_empty_streams_raises_codec_error(self) -> None:
        with _patch_exec(make_ffprobe_mock(stdout=FFPROBE_EMPTY_OUTPUT)):
            with pytest.raises(RTSPCodecError, match="no video stream"):
                await _src().probe()


# ── ffmpeg_input_args ─────────────────────────────────────────────────────────

class TestFFmpegInputArgs:
    def test_args_include_tcp_transport(self) -> None:
        src = _src()
        args = src.ffmpeg_input_args
        assert "-rtsp_transport" in args
        assert "tcp" in args

    def test_args_include_url(self) -> None:
        src = _src()
        args = src.ffmpeg_input_args
        assert _RTSP_URL in args

    def test_camera_id_property(self) -> None:
        assert _src().camera_id == _CAM_ID


# ── Password sanitisation ─────────────────────────────────────────────────────

class TestSanitizedUrl:
    def test_password_is_masked_in_logs(self) -> None:
        src = RTSPSource("c", "rtsp://admin:topsecret@192.168.1.1/s")
        assert "topsecret" not in src._sanitized_url
        assert "***" in src._sanitized_url

    def test_url_without_credentials_unchanged(self) -> None:
        src = RTSPSource("c", "rtsp://192.168.1.1/s")
        assert src._sanitized_url == "rtsp://192.168.1.1/s"
