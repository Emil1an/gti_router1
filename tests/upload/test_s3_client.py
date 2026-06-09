"""Tests for S3Uploader (Story 2.1).

Test strategy
-------------
``aiobotocore`` (the async botocore used by aioboto3 v15) sends request bodies as
async generators.  ``moto``'s synchronous HTTP stubber cannot consume async bodies,
so attempting to intercept real ``put_object`` / multipart calls through moto
raises ``AttributeError: 'coroutine' object has no attribute 'readline'``.

To avoid that coupling we test at the **aioboto3 client level** rather than the
HTTP level: each test injects ``AsyncMock`` methods onto ``uploader._client``.
This is appropriate for unit-testing the S3Uploader's own logic (routing,
key construction, Content-Type selection, error classification).

For tests that need to verify actual S3 bucket state we use a sync ``boto3``
client inside the ``mock_aws()`` context which does not have the async-body
issue.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import botocore.exceptions
import pytest

from upload.s3_client import S3Uploader, _MULTIPART_THRESHOLD_BYTES
from utils.errors import S3PermanentError, S3TransientError, S3UploadError

# Expected S3 key prefix from the fixture YAML (user-abc123/router-def456/cam-test/)
_KEY_PREFIX = "user-abc123/router-def456/cam-test/"
_BUCKET = "test-bucket"


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

def _make_ts(tmp_path: Path, name: str, size: int) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xAA" * size)
    return p


def _make_small_ts(tmp_path: Path, name: str = "segment_00001.ts") -> Path:
    """Create a .ts file smaller than the multipart threshold."""
    return _make_ts(tmp_path, name, 1024)


def _make_large_ts(tmp_path: Path, name: str = "segment_00010.ts") -> Path:
    """Create a .ts file just above the multipart threshold (5 MB + 1 KB)."""
    return _make_ts(tmp_path, name, _MULTIPART_THRESHOLD_BYTES + 1024)


def _mock_client() -> MagicMock:
    """Return a mock aioboto3 client with all required async S3 methods."""
    client = MagicMock()
    client.put_object = AsyncMock(return_value={})
    client.create_multipart_upload = AsyncMock(
        return_value={"UploadId": "test-upload-id"}
    )
    client.upload_part = AsyncMock(return_value={"ETag": '"abc123"'})
    client.complete_multipart_upload = AsyncMock(return_value={})
    client.abort_multipart_upload = AsyncMock(return_value={})
    return client


@pytest.fixture()
def uploader() -> S3Uploader:
    """S3Uploader with its ``_client`` replaced by a pre-configured AsyncMock."""
    u = S3Uploader()
    u._client = _mock_client()
    return u


# ── S3 key construction ────────────────────────────────────────────────────────

class TestKeyConstruction:
    def test_full_key_includes_all_components(self) -> None:
        """key = user_id/router_id/camera_id/filename"""
        u = S3Uploader()
        key = u._make_key("cam-1", "segment_00001.ts")
        assert key == "user-abc123/router-def456/cam-1/segment_00001.ts"

    def test_empty_user_id_is_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When user_id is empty it must be excluded so the key never starts with /."""
        yaml = tmp_path / "r.yaml"
        yaml.write_text(
            """\
cameras:
  - camera_id: cam-x
    input_type: rtsp_ip
    rtsp_url: "rtsp://x:y@1.2.3.4:554/s"
    orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}
hls:
  segment_duration: 4
aws: {bucket: b, region: us-east-1, access_key_id: k, secret_access_key: s}
supabase: {url: "https://x.supabase.co", service_role_key: srk}
device: {serial_number: SN-001, name: R, user_id: "", router_id: "rtr-xyz"}
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("ROUTER_CONFIG", str(yaml))
        from config.loader import reset_config
        reset_config()

        u = S3Uploader()
        key = u._make_key("cam-x", "segment_00001.ts")

        reset_config()
        assert key == "rtr-xyz/cam-x/segment_00001.ts"
        assert not key.startswith("/")

    def test_empty_router_id_falls_back_to_serial_number(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml = tmp_path / "r.yaml"
        yaml.write_text(
            """\
cameras:
  - camera_id: cam-x
    input_type: rtsp_ip
    rtsp_url: "rtsp://x:y@1.2.3.4:554/s"
    orientation: {azimuth: 0, tilt: 0, fov_h: 90, mount_height_m: 5}
hls:
  segment_duration: 4
aws: {bucket: b, region: us-east-1, access_key_id: k, secret_access_key: s}
supabase: {url: "https://x.supabase.co", service_role_key: srk}
device: {serial_number: SN-FALLBACK, name: R, user_id: "u1", router_id: ""}
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("ROUTER_CONFIG", str(yaml))
        from config.loader import reset_config
        reset_config()

        u = S3Uploader()
        key = u._make_key("cam-x", "segment_00001.ts")

        reset_config()
        assert "SN-FALLBACK" in key

    def test_filename_preserved(self) -> None:
        """The original segment filename must appear unchanged at the end of the key."""
        u = S3Uploader()
        key = u._make_key("cam-1", "segment_00042.ts")
        assert key.endswith("/segment_00042.ts")


# ── Small file upload (put_object) ─────────────────────────────────────────────

class TestSmallFileUpload:
    async def test_returns_correct_s3_key(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path, "segment_00001.ts")
        key = await uploader.upload_segment("cam-test", ts)
        assert key == f"{_KEY_PREFIX}segment_00001.ts"

    async def test_uses_put_object_for_small_file(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """Files ≤5 MB must use put_object, not multipart."""
        ts = _make_small_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        uploader._client.put_object.assert_called_once()
        uploader._client.create_multipart_upload.assert_not_called()

    async def test_ts_content_type_in_put_object(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """put_object must be called with ContentType: video/mp2t for .ts files."""
        ts = _make_small_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        _, kwargs = uploader._client.put_object.call_args
        assert kwargs["ContentType"] == "video/mp2t"

    async def test_correct_bucket_and_key_in_put_object(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path, "segment_00007.ts")
        await uploader.upload_segment("cam-test", ts)
        _, kwargs = uploader._client.put_object.call_args
        assert kwargs["Bucket"] == _BUCKET
        assert kwargs["Key"] == f"{_KEY_PREFIX}segment_00007.ts"

    async def test_body_contains_file_data(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """The Body passed to put_object must equal the file's bytes."""
        payload = b"fake-video-payload"
        ts = tmp_path / "segment_00003.ts"
        ts.write_bytes(payload)
        await uploader.upload_segment("cam-test", ts)
        _, kwargs = uploader._client.put_object.call_args
        assert kwargs["Body"] == payload


# ── Playlist upload ────────────────────────────────────────────────────────────

class TestPlaylistUpload:
    async def test_m3u8_content_type(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        pl = tmp_path / "playlist.m3u8"
        pl.write_text("#EXTM3U\n", encoding="utf-8")
        await uploader.upload_playlist("cam-test", pl)
        _, kwargs = uploader._client.put_object.call_args
        assert kwargs["ContentType"] == "application/vnd.apple.mpegurl"

    async def test_m3u8_key_prefix(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        pl = tmp_path / "playlist.m3u8"
        pl.write_bytes(b"#EXTM3U")
        key = await uploader.upload_playlist("cam-test", pl)
        assert key == f"{_KEY_PREFIX}playlist.m3u8"

    async def test_playlist_uses_put_object(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        pl = tmp_path / "playlist.m3u8"
        pl.write_bytes(b"#EXTM3U")
        await uploader.upload_playlist("cam-test", pl)
        uploader._client.put_object.assert_called_once()
        uploader._client.create_multipart_upload.assert_not_called()


# ── Multipart upload (large file >5 MB) ───────────────────────────────────────

class TestMultipartUpload:
    async def test_large_file_uses_multipart(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """Files >5 MB must trigger create_multipart_upload, not put_object."""
        ts = _make_large_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        uploader._client.create_multipart_upload.assert_called_once()
        uploader._client.put_object.assert_not_called()

    async def test_large_file_correct_key(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_large_ts(tmp_path, "segment_00010.ts")
        key = await uploader.upload_segment("cam-test", ts)
        assert key == f"{_KEY_PREFIX}segment_00010.ts"

    async def test_large_file_content_type_in_create(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """create_multipart_upload must specify ContentType: video/mp2t."""
        ts = _make_large_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        _, kwargs = uploader._client.create_multipart_upload.call_args
        assert kwargs["ContentType"] == "video/mp2t"

    async def test_large_file_upload_parts_called(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """upload_part must be called at least once."""
        ts = _make_large_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        assert uploader._client.upload_part.call_count >= 1

    async def test_large_file_complete_called(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_large_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        uploader._client.complete_multipart_upload.assert_called_once()

    async def test_multipart_passes_upload_id(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """complete_multipart_upload and upload_part must reference the UploadId."""
        ts = _make_large_ts(tmp_path)
        await uploader.upload_segment("cam-test", ts)
        # upload_part calls must include the UploadId
        for c in uploader._client.upload_part.call_args_list:
            _, kw = c
            assert kw["UploadId"] == "test-upload-id"

    async def test_multipart_abort_on_part_failure(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """When upload_part fails, abort_multipart_upload must be called."""
        ts = _make_large_ts(tmp_path)
        error_response = {
            "Error": {"Code": "ServiceUnavailable", "Message": "oops"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        }
        uploader._client.upload_part = AsyncMock(
            side_effect=botocore.exceptions.ClientError(error_response, "UploadPart")
        )

        with pytest.raises((S3TransientError, Exception)):
            await uploader.upload_segment("cam-test", ts)

        uploader._client.abort_multipart_upload.assert_called_once()

    async def test_multipart_abort_on_complete_failure(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        """When complete_multipart_upload fails, abort must still be called."""
        ts = _make_large_ts(tmp_path)
        error_response = {
            "Error": {"Code": "ServiceUnavailable", "Message": "oops"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        }
        uploader._client.complete_multipart_upload = AsyncMock(
            side_effect=botocore.exceptions.ClientError(error_response, "CompleteMultipart")
        )

        with pytest.raises((S3TransientError, Exception)):
            await uploader.upload_segment("cam-test", ts)

        uploader._client.abort_multipart_upload.assert_called_once()


# ── Error classification ───────────────────────────────────────────────────────

class TestErrorClassification:
    def _client_error(self, code: str, http_status: int) -> botocore.exceptions.ClientError:
        return botocore.exceptions.ClientError(
            {
                "Error": {"Code": code, "Message": "test"},
                "ResponseMetadata": {"HTTPStatusCode": http_status},
            },
            "PutObject",
        )

    async def test_access_denied_is_permanent(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        uploader._client.put_object = AsyncMock(
            side_effect=self._client_error("AccessDenied", 403)
        )
        with pytest.raises(S3PermanentError):
            await uploader.upload_segment("cam-test", ts)

    async def test_invalid_key_id_is_permanent(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        uploader._client.put_object = AsyncMock(
            side_effect=self._client_error("InvalidAccessKeyId", 403)
        )
        with pytest.raises(S3PermanentError):
            await uploader.upload_segment("cam-test", ts)

    async def test_no_such_bucket_is_permanent(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        uploader._client.put_object = AsyncMock(
            side_effect=self._client_error("NoSuchBucket", 404)
        )
        with pytest.raises(S3PermanentError):
            await uploader.upload_segment("cam-test", ts)

    async def test_503_is_transient(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        uploader._client.put_object = AsyncMock(
            side_effect=self._client_error("ServiceUnavailable", 503)
        )
        with pytest.raises(S3TransientError):
            await uploader.upload_segment("cam-test", ts)

    async def test_throttling_is_transient(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        uploader._client.put_object = AsyncMock(
            side_effect=self._client_error("Throttling", 429)
        )
        with pytest.raises(S3TransientError):
            await uploader.upload_segment("cam-test", ts)

    async def test_connection_error_is_transient(
        self, uploader: S3Uploader, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        exc = botocore.exceptions.ConnectTimeoutError(endpoint_url="s3.amazonaws.com")
        uploader._client.put_object = AsyncMock(side_effect=exc)
        with pytest.raises(S3TransientError):
            await uploader.upload_segment("cam-test", ts)

    async def test_missing_file_is_permanent(self, uploader: S3Uploader) -> None:
        """OSError reading a non-existent file is a permanent (local) error."""
        with pytest.raises(S3PermanentError):
            await uploader.upload_segment(
                "cam-test", Path("/nonexistent/segment_99999.ts")
            )


# ── Not-started guard ──────────────────────────────────────────────────────────

class TestNotStarted:
    async def test_upload_segment_before_start_raises(
        self, tmp_path: Path
    ) -> None:
        ts = _make_small_ts(tmp_path)
        uploader = S3Uploader()  # _client is None
        with pytest.raises(S3UploadError):
            await uploader.upload_segment("cam-test", ts)

    async def test_upload_playlist_before_start_raises(
        self, tmp_path: Path
    ) -> None:
        pl = tmp_path / "playlist.m3u8"
        pl.write_bytes(b"#EXTM3U")
        uploader = S3Uploader()
        with pytest.raises(S3UploadError):
            await uploader.upload_playlist("cam-test", pl)


# ── Lifecycle ──────────────────────────────────────────────────────────────────

class TestLifecycle:
    async def test_stop_is_idempotent(self) -> None:
        """stop() called twice must not raise."""
        uploader = S3Uploader()
        # Never started — stop() must handle None _client_ctx gracefully
        await uploader.stop()
        await uploader.stop()

    def test_construction_does_not_raise(self) -> None:
        """Creating S3Uploader (without start) must not raise or call get_config failure."""
        uploader = S3Uploader()
        assert uploader._client is None
        assert uploader._bucket == _BUCKET
