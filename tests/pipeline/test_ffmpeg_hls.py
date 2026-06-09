"""Integration tests for HLSPipeline (Story 1.4).

Test strategy
-------------
* **Real FFmpeg + sample.mp4:** tests that verify actual segment generation run
  FFmpeg against ``tests/fixtures/sample.mp4`` using a ``file://`` pseudo-source.
  These tests require the ``ffmpeg`` binary on PATH; they are skipped otherwise.
* **Mocked subprocess:** tests for supervisor restart behaviour and callback
  contract replace ``asyncio.create_subprocess_exec`` with a fake process whose
  lifecycle (exit code, timing) we control precisely — no real FFmpeg needed.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, patch as mpatch

import pytest

from camera.sources.base import StreamMetadata, VideoSource
from health.state import AppState
from pipeline.ffmpeg_hls import HLSPipeline, _utc_now_iso
from utils.errors import FFmpegError, PipelineError

# ── Helpers ────────────────────────────────────────────────────────────────────

SAMPLE_MP4 = Path(__file__).parent.parent / "fixtures" / "sample.mp4"
FFMPEG_AVAILABLE = False
try:
    import subprocess
    subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=5)
    FFMPEG_AVAILABLE = True
except Exception:
    pass

requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE,
    reason="ffmpeg binary not available on PATH",
)


class _FakeSource(VideoSource):
    """Minimal VideoSource stub that reads from a local file."""

    def __init__(self, camera_id: str, path: Path) -> None:
        self._camera_id = camera_id
        self._path = path

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def ffmpeg_input_args(self) -> list[str]:
        return ["-i", str(self._path)]

    async def probe(self) -> StreamMetadata:
        return StreamMetadata(
            codec="h264", width=640, height=480, framerate=25.0,
            camera_id=self._camera_id,
        )


def _make_source(cam_id: str = "cam-test", path: Path = SAMPLE_MP4) -> _FakeSource:
    return _FakeSource(cam_id, path)


# ── Fake process for mocked subprocess tests ───────────────────────────────────

class _FakeProcess:
    """Minimal asyncio.subprocess.Process stand-in.

    ``returncode`` is ``None`` while the process is "running" (i.e. before
    ``communicate()`` or ``terminate()`` is called), exactly like a real
    ``asyncio.subprocess.Process``.
    """

    def __init__(
        self,
        returncode: int = 0,
        stderr: bytes = b"",
        delay: float = 0.0,
    ) -> None:
        self._exit_returncode = returncode
        self._stderr = stderr
        self._delay = delay
        self.pid = 99999
        self._returncode: int | None = None  # None = process still running

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        if self._returncode is None:
            self._returncode = -15

    def kill(self) -> None:
        if self._returncode is None:
            self._returncode = -9

    async def wait(self) -> int:
        return self._returncode if self._returncode is not None else self._exit_returncode

    async def communicate(self) -> tuple[None, bytes]:
        if self._delay:
            await asyncio.sleep(self._delay)
        self._returncode = self._exit_returncode
        return None, self._stderr


def _patch_exec(returncode: int = 0, stderr: bytes = b"", delay: float = 0.0):
    """Patch asyncio.create_subprocess_exec to return a _FakeProcess."""
    async def _fake(*_a, **_kw) -> _FakeProcess:
        return _FakeProcess(returncode=returncode, stderr=stderr, delay=delay)

    return patch(
        "pipeline.ffmpeg_hls.asyncio.create_subprocess_exec",
        new=_fake,
    )


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def pipeline(tmp_path: Path) -> HLSPipeline:
    src = _make_source()
    return HLSPipeline(
        source=src,
        output_base_dir=str(tmp_path),
    )


# ── Real FFmpeg integration tests ──────────────────────────────────────────────

@requires_ffmpeg
class TestRealFFmpeg:
    @pytest.mark.asyncio
    async def test_generates_ts_segments_and_playlist(self, tmp_path: Path) -> None:
        """Running FFmpeg against sample.mp4 must produce .ts files and playlist.m3u8."""
        src = _make_source(path=SAMPLE_MP4)
        pipe = HLSPipeline(source=src, output_base_dir=str(tmp_path))

        await pipe.start()
        # Give FFmpeg up to 15 s to produce at least 1 segment from the 10 s clip
        for _ in range(30):
            await asyncio.sleep(0.5)
            segments = list((tmp_path / "cam-test").glob("*.ts"))
            if segments:
                break

        await pipe.stop()

        out_dir = tmp_path / "cam-test"
        assert out_dir.exists()
        ts_files = sorted(out_dir.glob("*.ts"))
        assert len(ts_files) >= 1, "Expected at least one .ts segment"
        assert (out_dir / "playlist.m3u8").exists()

    @pytest.mark.asyncio
    async def test_callback_receives_correct_contract(self, tmp_path: Path) -> None:
        """on_segment callback must receive (camera_id, Path, iso_str) for each segment."""
        received: list[tuple[str, Path, str]] = []

        async def on_seg(cam_id: str, path: Path, ts: str) -> None:
            received.append((cam_id, path, ts))

        src = _make_source(path=SAMPLE_MP4)
        pipe = HLSPipeline(source=src, on_segment=on_seg, output_base_dir=str(tmp_path))

        await pipe.start()
        # Wait until at least one callback fires
        for _ in range(30):
            await asyncio.sleep(0.5)
            if received:
                break

        await pipe.stop()

        assert len(received) >= 1
        cam_id, seg_path, iso_ts = received[0]
        assert cam_id == "cam-test"
        assert seg_path.suffix == ".ts"
        assert seg_path.exists()
        # ISO-8601 UTC with Z
        assert iso_ts.endswith("Z")
        assert "T" in iso_ts

    @pytest.mark.asyncio
    async def test_segment_names_follow_pattern(self, tmp_path: Path) -> None:
        """Segments must be named segment_NNNNN.ts (5 zero-padded digits)."""
        src = _make_source(path=SAMPLE_MP4)
        pipe = HLSPipeline(source=src, output_base_dir=str(tmp_path))

        await pipe.start()
        for _ in range(30):
            await asyncio.sleep(0.5)
            segs = list((tmp_path / "cam-test").glob("segment_?????.ts"))
            if segs:
                break

        await pipe.stop()
        segs = sorted((tmp_path / "cam-test").glob("segment_?????.ts"))
        assert len(segs) >= 1


# ── Mocked subprocess tests ────────────────────────────────────────────────────

class TestSupervisorRestart:
    @pytest.mark.asyncio
    async def test_ffmpeg_not_found_marks_camera_unavailable(
        self, tmp_path: Path
    ) -> None:
        """A persistently missing ffmpeg binary exhausts retries and marks the
        camera unavailable in per_camera health (Story 3.4), without hanging."""
        async def _not_found(*_a, **_kw):
            raise FileNotFoundError("ffmpeg not found")

        state = AppState()
        pipe = HLSPipeline(
            source=_make_source(),
            output_base_dir=str(tmp_path),
            app_state=state,
            max_failures=2,
            reconnect_base_delay=0.001,
            reconnect_max_delay=0.002,
            reconnect_idle_s=0.02,
        )
        with patch(
            "pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_not_found
        ):
            await pipe.start()
            for _ in range(300):
                if pipe.camera_unavailable:
                    break
                await asyncio.sleep(0.01)
            await pipe.stop()

        assert pipe.camera_unavailable is True
        assert pipe.rtsp_connected is False
        cam = state.per_camera["cam-test"]
        assert cam.connected is False
        assert cam.error is not None

    @pytest.mark.asyncio
    async def test_ffmpeg_crash_triggers_reconnect(self, tmp_path: Path) -> None:
        """A non-zero FFmpeg exit must trigger an automatic reconnect (Story 3.4)."""
        call_count = [0]
        ready = asyncio.Event()

        async def _crash(*_a, **_kw) -> _FakeProcess:
            call_count[0] += 1
            if call_count[0] == 1:
                return _FakeProcess(returncode=1, stderr=b"signal 11")
            # On 2nd call, signal that the reconnect happened, then block.
            ready.set()
            return _FakeProcess(returncode=0, delay=9999)

        pipe = HLSPipeline(
            source=_make_source(),
            output_base_dir=str(tmp_path),
            max_failures=5,
            reconnect_base_delay=0.001,
            reconnect_max_delay=0.002,
            reconnect_idle_s=0.02,
        )
        with patch(
            "pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_crash
        ):
            await pipe.start()
            await asyncio.wait_for(ready.wait(), timeout=5.0)
            await pipe.stop()

        assert call_count[0] >= 2
        assert pipe.rtsp_reconnect_count >= 1


class TestSegmentCallback:
    @pytest.mark.asyncio
    async def test_callback_fires_for_each_new_ts(self, tmp_path: Path) -> None:
        """Watcher must call on_segment for each .ts file that appears."""
        received: list[tuple[str, Path, str]] = []
        got_two = asyncio.Event()

        async def on_seg(cam_id: str, path: Path, ts: str) -> None:
            received.append((cam_id, path, ts))
            if len(received) >= 2:
                got_two.set()

        out_dir = tmp_path / "cam-test"
        out_dir.mkdir()

        pipe = HLSPipeline(
            source=_make_source(),
            on_segment=on_seg,
            output_base_dir=str(tmp_path),
        )
        # Use a fast poll interval so the test doesn't take 0.5 s per cycle
        import pipeline.ffmpeg_hls as _hls_mod
        with patch.object(_hls_mod, "_SEGMENT_POLL_INTERVAL", 0.02):
            pipe._running = True
            pipe._watcher_task = asyncio.create_task(pipe._segment_watcher())

            await asyncio.sleep(0.02)  # let watcher start
            (out_dir / "segment_00000.ts").write_bytes(b"fake")
            (out_dir / "segment_00001.ts").write_bytes(b"fake")

            # Wait until both callbacks have fired
            await asyncio.wait_for(got_two.wait(), timeout=3.0)

            pipe._running = False
            pipe._watcher_task.cancel()
            try:
                await pipe._watcher_task
            except asyncio.CancelledError:
                pass

        assert len(received) == 2
        assert all(cb[0] == "cam-test" for cb in received)
        assert all(cb[1].suffix == ".ts" for cb in received)
        assert all(cb[2].endswith("Z") for cb in received)

    @pytest.mark.asyncio
    async def test_duplicate_segments_not_emitted_twice(self, tmp_path: Path) -> None:
        """Watcher must not emit the same segment more than once."""
        received: list[Path] = []
        got_one = asyncio.Event()

        async def on_seg(cam_id: str, path: Path, ts: str) -> None:
            received.append(path)
            got_one.set()

        out_dir = tmp_path / "cam-test"
        out_dir.mkdir()
        (out_dir / "segment_00000.ts").write_bytes(b"x")

        pipe = HLSPipeline(
            source=_make_source(),
            on_segment=on_seg,
            output_base_dir=str(tmp_path),
        )
        import pipeline.ffmpeg_hls as _hls_mod
        with patch.object(_hls_mod, "_SEGMENT_POLL_INTERVAL", 0.02):
            pipe._running = True
            pipe._watcher_task = asyncio.create_task(pipe._segment_watcher())

            # Wait for first callback, then let 3 more poll cycles run
            await asyncio.wait_for(got_one.wait(), timeout=3.0)
            await asyncio.sleep(0.08)  # 4 more poll cycles at 0.02s

            pipe._running = False
            pipe._watcher_task.cancel()
            try:
                await pipe._watcher_task
            except asyncio.CancelledError:
                pass

        assert received.count(out_dir / "segment_00000.ts") == 1


class TestStopBehaviour:
    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        """Calling stop() twice must not raise."""
        pipe = HLSPipeline(source=_make_source(), output_base_dir=str(tmp_path))
        with _patch_exec(delay=9999):
            await pipe.start()
            await asyncio.sleep(0.05)
            await pipe.stop()
            await pipe.stop()  # second call must be safe

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self, tmp_path: Path) -> None:
        """stop() must call terminate() on the running FFmpeg process."""
        terminated = [False]

        class _LongProcess(_FakeProcess):
            def terminate(self) -> None:
                terminated[0] = True
                super().terminate()

        async def _long(*_a, **_kw) -> _LongProcess:
            return _LongProcess(delay=9999)

        pipe = HLSPipeline(source=_make_source(), output_base_dir=str(tmp_path))
        with patch("pipeline.ffmpeg_hls.asyncio.create_subprocess_exec", new=_long):
            await pipe.start()
            await asyncio.sleep(0.05)
            await pipe.stop()

        assert terminated[0]


class TestCommandBuilder:
    def test_passthrough_flag_present(self, pipeline: HLSPipeline) -> None:
        cmd = pipeline._build_ffmpeg_command()
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == "copy"

    def test_hls_time_matches_config(self, pipeline: HLSPipeline) -> None:
        cmd = pipeline._build_ffmpeg_command()
        assert "-hls_time" in cmd
        idx = cmd.index("-hls_time")
        assert cmd[idx + 1] == str(pipeline._segment_duration)

    def test_segment_filename_pattern(self, pipeline: HLSPipeline) -> None:
        cmd = pipeline._build_ffmpeg_command()
        seg_flag_idx = cmd.index("-hls_segment_filename")
        assert "segment_%05d.ts" in cmd[seg_flag_idx + 1]

    def test_playlist_is_last_arg(self, pipeline: HLSPipeline) -> None:
        cmd = pipeline._build_ffmpeg_command()
        assert cmd[-1].endswith("playlist.m3u8")

    def test_input_args_from_source_included(self, pipeline: HLSPipeline) -> None:
        cmd = pipeline._build_ffmpeg_command()
        # _FakeSource.ffmpeg_input_args = ["-i", str(path)]
        assert "-i" in cmd


class TestUtcNowIso:
    def test_format(self) -> None:
        ts = _utc_now_iso()
        assert ts.endswith("Z")
        assert "T" in ts
        # Should be parseable
        from datetime import datetime, UTC
        parsed = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=UTC)
        assert parsed.year >= 2024
