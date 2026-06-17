"""Tests for SnapshotService (Stories 6.3 + 6.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pipeline.snapshot as snap_mod
from health.supabase_client import SupabaseClient
from pipeline.snapshot import SnapshotService
from upload.s3_client import S3Uploader
from utils.contract import ROUTER_SOURCE
from utils.errors import SnapshotError

_REAL_SLEEP = asyncio.sleep


def _uploader() -> MagicMock:
    u = MagicMock(spec=S3Uploader)
    u.start = AsyncMock()
    u.stop = AsyncMock()
    u.upload_snapshot = AsyncMock(return_value="user/router/cam-1/last_frame.jpg")
    u.object_url = MagicMock(
        return_value="https://b.s3.us-east-1.amazonaws.com/user/router/cam-1/last_frame.jpg"
    )
    return u


def _client() -> MagicMock:
    c = MagicMock(spec=SupabaseClient)
    c.update = AsyncMock(return_value=[{"id": "cam-1"}])
    return c


def _service(tmp_path, uploader=None, client=None, **kw) -> SnapshotService:
    return SnapshotService(
        client=client or _client(),
        camera_ids=["cam-1"],
        output_base=str(tmp_path),
        uploader=uploader or _uploader(),
        interval_s=kw.get("interval_s", 10),
    )


def _make_segment(tmp_path: Path, camera: str = "cam-1") -> Path:
    cam = tmp_path / camera
    cam.mkdir(parents=True, exist_ok=True)
    seg = cam / "segment_00003.ts"
    seg.write_bytes(b"ts-data")
    return seg


# ── snapshot_once: upload + contract + cameras update ────────────────────────────

class TestSnapshotOnce:
    async def test_uploads_with_no_detection_contract(self, tmp_path) -> None:
        _make_segment(tmp_path)
        uploader = _uploader()
        client = _client()
        svc = _service(tmp_path, uploader=uploader, client=client)

        # Make frame extraction produce a jpg without invoking ffmpeg.
        async def _fake_extract(_self, camera_id):
            jpg = tmp_path / camera_id / "last_frame.jpg"
            jpg.write_bytes(b"\xff\xd8\xff jpeg")
            return jpg

        with patch.object(SnapshotService, "_extract_frame", _fake_extract):
            url = await svc.snapshot_once("cam-1")

        assert url is not None
        # Uploaded with the source=router (no-detection) metadata (Story 6.4).
        _args, kwargs = uploader.upload_snapshot.call_args
        assert kwargs["metadata"]["source"] == ROUTER_SOURCE
        assert "contract_version" in kwargs["metadata"]
        # cameras.last_frame_url + last_frame_at updated.
        u_args, _ = client.update.call_args
        table, params, patch_payload = u_args
        assert table == "cameras"
        assert params == {"id": "eq.cam-1"}
        assert patch_payload["last_frame_url"].startswith("https://")
        assert patch_payload["last_frame_at"].endswith("Z")

    async def test_no_segment_skips(self, tmp_path) -> None:
        # No segment on disk → extraction returns None → no upload.
        uploader = _uploader()
        svc = _service(tmp_path, uploader=uploader)
        result = await svc.snapshot_once("cam-1")
        assert result is None
        uploader.upload_snapshot.assert_not_called()

    async def test_works_without_gateway_id(self, tmp_path) -> None:
        # Autonomous: no gateway_id involved anywhere in the flow (AC#4).
        _make_segment(tmp_path)
        uploader = _uploader()
        svc = _service(tmp_path, uploader=uploader)

        async def _fake_extract(_self, camera_id):
            jpg = tmp_path / camera_id / "last_frame.jpg"
            jpg.write_bytes(b"jpeg")
            return jpg

        with patch.object(SnapshotService, "_extract_frame", _fake_extract):
            url = await svc.snapshot_once("cam-1")
        assert url is not None


# ── Frame extraction (ffmpeg mocked) ─────────────────────────────────────────────

class TestExtraction:
    def test_extract_command_targets_last_frame(self, tmp_path) -> None:
        svc = _service(tmp_path)
        cmd = svc._build_extract_command(Path("/x/seg.ts"), Path("/x/out.jpg"))
        assert cmd[0] == "ffmpeg"
        assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
        assert cmd[-1].endswith("out.jpg")

    async def test_extract_fails_when_ffmpeg_missing(self, tmp_path) -> None:
        _make_segment(tmp_path)
        svc = _service(tmp_path)

        async def _no_ffmpeg(*_a, **_k):
            raise FileNotFoundError("no ffmpeg")

        with patch.object(snap_mod.asyncio, "create_subprocess_exec", new=_no_ffmpeg):
            with pytest.raises(SnapshotError):
                await svc._extract_frame("cam-1")


# ── Periodic loop + isolation ────────────────────────────────────────────────────

class TestLoop:
    async def test_periodic_snapshots(self, tmp_path) -> None:
        uploader = _uploader()
        svc = _service(tmp_path, uploader=uploader, interval_s=1)
        svc._interval = 0.02

        calls = [0]

        async def _fake_once(_self, camera_id):
            calls[0] += 1
            return "url"

        with patch.object(SnapshotService, "snapshot_once", _fake_once):
            await svc.start()
            await _wait(lambda: calls[0] >= 2)
            await svc.stop()
        assert calls[0] >= 2

    async def test_one_camera_failure_isolated(self, tmp_path) -> None:
        # A snapshot raising must not kill the loop (other cameras unaffected).
        uploader = _uploader()
        svc = SnapshotService(
            client=_client(), camera_ids=["cam-1"], output_base=str(tmp_path),
            uploader=uploader, interval_s=1,
        )
        svc._interval = 0.02
        attempts = [0]

        async def _boom(_self, camera_id):
            attempts[0] += 1
            raise SnapshotError("frame failed")

        with patch.object(SnapshotService, "snapshot_once", _boom):
            await svc.start()
            await _wait(lambda: attempts[0] >= 2)  # keeps retrying, not dead
            await svc.stop()
        assert attempts[0] >= 2


async def _wait(predicate, timeout_s: float = 3.0) -> None:
    for _ in range(int(timeout_s / 0.02) + 1):
        if predicate():
            return
        await _REAL_SLEEP(0.02)
