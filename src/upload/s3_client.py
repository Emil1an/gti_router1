"""Async S3 client for HLS segment and playlist upload (Story 2.1).

Uses ``aioboto3`` to avoid blocking the asyncio event loop.  Files ≤ 5 MB use
``put_object``; files > 5 MB use a manual multipart upload with explicit abort
on failure to prevent dangling multipart uploads in S3.

S3 key layout::

    {user_id}/{router_id}/{camera_id}/segment_NNNNN.ts

``user_id`` and ``router_id`` come from ``device`` config block and are set after
Supabase registration (Story 3.1); until then they fall back to ``serial_number``.

IAM note
--------
The IAM user / role must be scoped to ``s3:PutObject`` and ``s3:AbortMultipartUpload``
on the bucket prefix ``{bucket}/{user_id}/{router_id}/*`` (principle of least
privilege, NFR9).  Credentials live exclusively in environment variables
(``${AWS_ACCESS_KEY_ID}`` / ``${AWS_SECRET_ACCESS_KEY}``) — never in YAML.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aioboto3
import botocore.exceptions

from config.loader import get_config
from utils.errors import S3PermanentError, S3TransientError, S3UploadError
from utils.logging import get_logger

# ── Constants ──────────────────────────────────────────────────────────────────

_MULTIPART_THRESHOLD_BYTES: int = 5 * 1024 * 1024   # 5 MB — below → put_object
_MULTIPART_CHUNK_BYTES: int = 5 * 1024 * 1024        # 5 MB per multipart part

_CONTENT_TYPE_TS = "video/mp2t"
_CONTENT_TYPE_M3U8 = "application/vnd.apple.mpegurl"
_CONTENT_TYPE_JPEG = "image/jpeg"

# botocore error codes that indicate a permanent failure (must not be retried)
_PERMANENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "AccessDenied",
        "AuthFailure",
        "InvalidAccessKeyId",
        "InvalidClientTokenId",
        "InvalidSignatureException",
        "SignatureDoesNotMatch",
        "TokenRefreshRequired",
        "NoSuchBucket",
        "InvalidBucketName",
        "NoCredentialsError",
    }
)

# HTTP status codes that map to a permanent error
_PERMANENT_HTTP_STATUSES: frozenset[int] = frozenset({400, 403, 404})


class S3Uploader:
    """Async S3 uploader for segments and playlists.

    Lifecycle::

        uploader = S3Uploader()
        await uploader.start()
        key = await uploader.upload_segment("cam-1", Path("segment_00001.ts"))
        await uploader.stop()

    This class does **not** implement retry logic — that responsibility belongs to
    the ``UploadQueue`` worker which wraps each call with ``@with_retry``
    (Story 2.3, ``src/utils/retry.py``).  It does, however, classify failures as
    :class:`~utils.errors.S3TransientError` (retryable) or
    :class:`~utils.errors.S3PermanentError` (not retryable) so the worker can
    decide correctly.
    """

    def __init__(self) -> None:
        cfg = get_config()
        aws = cfg.aws
        device = cfg.device

        self._bucket: str = aws.bucket
        self._region: str = aws.region
        self._access_key_id: str = aws.access_key_id
        self._secret_access_key: str = aws.secret_access_key

        # S3 key prefix — fall back to serial_number when UUIDs not yet assigned
        self._user_id: str = device.user_id
        self._router_id: str = device.router_id or device.serial_number

        self._session: aioboto3.Session | None = None
        self._client: Any | None = None
        self._client_ctx: Any | None = None
        self._logger = get_logger(__name__)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Open the aioboto3 S3 client.  Must be called before any upload."""
        self._session = aioboto3.Session(
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name=self._region,
        )
        # TLS is always on — aioboto3 defaults use_ssl=True (NFR10)
        self._client_ctx = self._session.client("s3")
        self._client = await self._client_ctx.__aenter__()
        self._logger.info(
            "S3Uploader started",
            extra={"bucket": self._bucket, "region": self._region},
        )

    async def stop(self) -> None:
        """Close the aioboto3 S3 client gracefully."""
        if self._client_ctx is not None:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except Exception:
                pass  # best effort
            self._client = None
            self._client_ctx = None
        self._logger.info("S3Uploader stopped")

    # ── Public upload API ──────────────────────────────────────────────────────

    async def upload_segment(self, camera_id: str, segment_path: Path) -> str:
        """Upload a ``.ts`` segment to S3.

        Files ≤ 5 MB use ``put_object``; larger files use multipart upload.

        Args:
            camera_id:     camera identifier — used as the third component of the
                           S3 key prefix.
            segment_path:  local filesystem path to the ``.ts`` file.

        Returns:
            The S3 key (not a full URL) of the uploaded object.

        Raises:
            S3TransientError: network error, timeout, HTTP 5xx, or throttling.
                              The caller should retry via ``@with_retry``.
            S3PermanentError: auth failure, 403/404, or missing bucket.
                              The caller must NOT retry.
        """
        if self._client is None:
            raise S3UploadError("S3Uploader has not been started — call start() first")

        key = self._make_key(camera_id, segment_path.name)
        try:
            size = segment_path.stat().st_size
        except OSError as exc:
            raise S3PermanentError(
                f"Cannot stat segment file '{segment_path}': {exc}"
            ) from exc

        self._logger.debug(
            "Uploading segment", extra={"camera_id": camera_id, "key": key, "size_bytes": size}
        )

        await self._upload(segment_path, key, _CONTENT_TYPE_TS, size)
        self._logger.info(
            "Segment uploaded", extra={"camera_id": camera_id, "key": key, "size_bytes": size}
        )
        return key

    async def upload_playlist(self, camera_id: str, playlist_path: Path) -> str:
        """Upload an HLS playlist (``.m3u8``) to S3 using ``put_object``.

        Returns:
            The S3 key of the uploaded playlist.
        """
        if self._client is None:
            raise S3UploadError("S3Uploader has not been started — call start() first")

        key = self._make_key(camera_id, playlist_path.name)
        try:
            size = playlist_path.stat().st_size
        except OSError as exc:
            raise S3PermanentError(
                f"Cannot stat playlist file '{playlist_path}': {exc}"
            ) from exc

        await self._upload(playlist_path, key, _CONTENT_TYPE_M3U8, size)
        return key

    async def upload_snapshot(
        self,
        camera_id: str,
        snapshot_path: Path,
        filename: str = "last_frame.jpg",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a last-frame JPEG (Story 6.3).

        ``metadata`` is attached as S3 object metadata — used to carry the
        no-detection contract (``source=router`` …, Story 6.4) so the consumer
        knows the image is raw (no detection).

        Returns:
            The S3 key of the uploaded snapshot.
        """
        if self._client is None:
            raise S3UploadError("S3Uploader has not been started — call start() first")

        key = self._make_key(camera_id, filename)
        try:
            size = snapshot_path.stat().st_size
        except OSError as exc:
            raise S3PermanentError(
                f"Cannot stat snapshot file '{snapshot_path}': {exc}"
            ) from exc

        await self._upload(snapshot_path, key, _CONTENT_TYPE_JPEG, size, metadata=metadata)
        self._logger.info(
            "Snapshot uploaded", extra={"camera_id": camera_id, "key": key}
        )
        return key

    def object_url(self, key: str) -> str:
        """Return the HTTPS S3 URL for an object key (TLS, NFR10)."""
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"

    # ── Private helpers ────────────────────────────────────────────────────────

    def _make_key(self, camera_id: str, filename: str) -> str:
        """Build the S3 object key from prefix components.

        Returns ``{user_id}/{router_id}/{camera_id}/{filename}`` with empty
        prefix components stripped so the key never starts with ``/``.
        """
        parts = [p for p in (self._user_id, self._router_id, camera_id, filename) if p]
        return "/".join(parts)

    async def _upload(
        self,
        path: Path,
        key: str,
        content_type: str,
        size: int,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Dispatch to put_object or multipart based on file size."""
        try:
            if size > _MULTIPART_THRESHOLD_BYTES:
                await self._upload_multipart(path, key, content_type, metadata)
            else:
                await self._upload_put(path, key, content_type, metadata)
        except (S3TransientError, S3PermanentError):
            raise  # already classified
        except botocore.exceptions.ClientError as exc:
            self._raise_typed(exc, key)
        except (
            botocore.exceptions.EndpointResolutionError,
            botocore.exceptions.ConnectTimeoutError,
            botocore.exceptions.ReadTimeoutError,
            botocore.exceptions.ConnectionError,
        ) as exc:
            raise S3TransientError(
                f"S3 connection error uploading '{key}': {exc}"
            ) from exc
        except OSError as exc:
            raise S3PermanentError(
                f"Cannot read local file '{path}': {exc}"
            ) from exc

    async def _upload_put(
        self, path: Path, key: str, content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Upload a small file (≤5 MB) in a single ``put_object`` call."""
        data: bytes = await asyncio.to_thread(path.read_bytes)
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        if metadata:
            kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
        await self._client.put_object(**kwargs)

    async def _upload_multipart(
        self, path: Path, key: str, content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Upload a large file (>5 MB) using S3 multipart upload.

        Aborts the multipart upload if any part fails, so no dangling
        incomplete uploads are left in S3.
        """
        create_kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": content_type,
        }
        if metadata:
            create_kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
        resp = await self._client.create_multipart_upload(**create_kwargs)
        upload_id: str = resp["UploadId"]

        parts: list[dict[str, Any]] = []
        try:
            file_size: int = path.stat().st_size
            offset = 0
            part_number = 1

            while offset < file_size:
                chunk: bytes = await asyncio.to_thread(
                    _read_chunk, path, offset, _MULTIPART_CHUNK_BYTES
                )
                if not chunk:
                    break

                part_resp = await self._client.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=chunk,
                )
                parts.append({"PartNumber": part_number, "ETag": part_resp["ETag"]})
                offset += len(chunk)
                part_number += 1

            await self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception:
            # Abort to prevent dangling multipart uploads
            try:
                await self._client.abort_multipart_upload(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                )
                self._logger.warning(
                    "Aborted dangling multipart upload",
                    extra={"key": key, "upload_id": upload_id},
                )
            except Exception:
                pass  # best effort — log but do not mask original exception
            raise  # re-raise the original failure for classification in _upload()

    def _raise_typed(
        self, exc: botocore.exceptions.ClientError, key: str
    ) -> None:
        """Classify a ``botocore.ClientError`` and raise the correct typed subclass.

        Raises:
            S3PermanentError: for 403/404/auth errors.
            S3TransientError: for 5xx/throttling/unknown.
        """
        error_code: str = exc.response.get("Error", {}).get("Code", "")
        http_status: int = (
            exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        )

        if error_code in _PERMANENT_ERROR_CODES or http_status in _PERMANENT_HTTP_STATUSES:
            raise S3PermanentError(
                f"Permanent S3 error (code={error_code!r}, http={http_status}) "
                f"for key '{key}': {exc}"
            ) from exc

        # 5xx / throttling (429) / unknown → transient
        raise S3TransientError(
            f"Transient S3 error (code={error_code!r}, http={http_status}) "
            f"for key '{key}': {exc}"
        ) from exc


# ── Module-level helper (runs in thread pool) ──────────────────────────────────

def _read_chunk(path: Path, offset: int, size: int) -> bytes:
    """Read ``size`` bytes from ``path`` starting at ``offset``."""
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size)
