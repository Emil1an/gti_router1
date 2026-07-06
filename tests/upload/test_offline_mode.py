"""Local/offline test mode: cloud failure or aws.enabled=false must NOT abort
capture (segments queue locally, console stays alive)."""

from __future__ import annotations

from pathlib import Path

from config.loader import get_config
from health.state import AppState
from upload.service import UploadService


class _RaisingUploader:
    """Uploader whose start() fails like dummy/invalid AWS credentials would."""

    async def start(self) -> None:
        raise RuntimeError("invalid AWS credentials (dummy)")

    async def stop(self) -> None:  # pragma: no cover - not reached
        pass


class _SpyUploader:
    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        pass


async def test_s3_start_failure_is_contained(tmp_path: Path) -> None:
    state = AppState()
    svc = UploadService(
        sources=[], db_path=tmp_path / "q.db",
        uploader=_RaisingUploader(), app_state=state,
    )

    await svc.start()  # must NOT raise despite the uploader blowing up

    assert state.s3_connected is False
    # DB was still opened (pipelines can enqueue) → counts() works.
    counts = await svc._db.counts()
    assert counts["pending"] == 0

    await svc.stop()  # must not touch the never-started queue/uploader


async def test_aws_disabled_skips_cloud_entirely(tmp_path: Path) -> None:
    get_config().aws.enabled = False  # test-mode switch
    spy = _SpyUploader()
    state = AppState()
    svc = UploadService(
        sources=[], db_path=tmp_path / "q.db",
        uploader=spy, app_state=state,
    )

    await svc.start()

    assert spy.started is False        # uploader never even touched
    assert state.s3_connected is False
    counts = await svc._db.counts()    # DB still open for capture
    assert counts["pending"] == 0

    await svc.stop()
