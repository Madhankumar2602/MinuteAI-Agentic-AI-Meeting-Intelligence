"""Object storage (Amazon S3, or an S3-compatible server locally) — ADR 0009.

Key layout, from the architecture review:

    users/{user_id}/meetings/{meeting_id}/source/{media_id}.{ext}      uploaded audio/video
    users/{user_id}/meetings/{meeting_id}/transcript/{media_id}.json    raw transcription output

The user id leads the key so one IAM prefix condition can scope access per
user, and deleting a user's or a meeting's data is a single prefix delete. The
filename a user uploads never appears in a key; keys are built only from
server-generated UUIDs and a validated extension, which rules out path traversal
and collisions.

Uploads go browser → storage directly through a presigned POST. The API never
handles the bytes. The POST policy makes storage *itself* enforce the exact key,
the content type, and the maximum size, so a client cannot upload a 5 GB file
or overwrite another object even if it ignores the frontend's checks.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from app.core.aws_clients import build_client
from app.core.config import settings
from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageUnavailableError(ServiceUnavailableError):
    code = "storage_unavailable"
    message = "File storage is temporarily unavailable."


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]
    key: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str | None
    etag: str


def media_key(
    *, user_id: uuid.UUID, meeting_id: uuid.UUID, media_id: uuid.UUID, extension: str
) -> str:
    return f"users/{user_id}/meetings/{meeting_id}/source/{media_id}.{extension}"


def raw_transcript_key(*, user_id: uuid.UUID, meeting_id: uuid.UUID, media_id: uuid.UUID) -> str:
    return f"users/{user_id}/meetings/{meeting_id}/transcript/{media_id}.json"


def mom_prefix(*, user_id: uuid.UUID, meeting_id: uuid.UUID) -> str:
    return f"users/{user_id}/meetings/{meeting_id}/mom/"


def meeting_prefix(*, user_id: uuid.UUID, meeting_id: uuid.UUID) -> str:
    return f"users/{user_id}/meetings/{meeting_id}/"


def _client(endpoint: str | None, setting_name: str = "S3_ENDPOINT_URL") -> Any:
    # build_client enforces STORAGE_BACKEND: in local mode the endpoint must be
    # local, explicit keys are required, and ~/.aws is never read (ADR 0010).
    return build_client(
        "s3",
        backend=settings.storage_backend,
        endpoint_url=endpoint,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key.get_secret_value(),
        setting_name=setting_name,
        config=Config(
            signature_version="s3v4",
            # Path-style (host/bucket/key) works with S3-compatible servers and
            # with real S3; virtual-hosted style needs wildcard DNS locally.
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        ),
    )


class ObjectStorage:
    def __init__(self, *, client: Any, signing_client: Any, bucket: str) -> None:
        self._client = client
        # Presigned URLs embed the host they were signed for. When the API
        # reaches storage via an internal hostname, signing must use the
        # public one or browsers receive a URL they cannot resolve.
        self._signing_client = signing_client
        self.bucket = bucket

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        method = getattr(self._client, operation)
        try:
            return await run_in_threadpool(lambda: method(**kwargs))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise
            logger.error("s3 call failed", extra={"operation": operation, "error_code": code})
            raise StorageUnavailableError(details={"operation": operation}) from exc
        except BotoCoreError as exc:
            logger.error(
                "s3 call failed", extra={"operation": operation, "error": type(exc).__name__}
            )
            raise StorageUnavailableError(details={"operation": operation}) from exc

    # ------------------------------------------------------------------
    # Bucket
    # ------------------------------------------------------------------

    async def ensure_bucket(self, *, cors_origins: list[str]) -> bool:
        """Create the bucket (dev) and apply CORS for browser uploads. Idempotent."""
        created = False
        try:
            await self._call("head_bucket", Bucket=self.bucket)
        except ClientError as exc:
            # _call passes 404s through unwrapped; anything else is re-raised.
            if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            await self._call("create_bucket", Bucket=self.bucket)
            created = True
            logger.info("s3 bucket created", extra={"bucket": self.bucket})

        await self._call(
            "put_bucket_cors",
            Bucket=self.bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": cors_origins,
                        "AllowedMethods": ["POST", "GET", "HEAD"],
                        "AllowedHeaders": ["*"],
                        "ExposeHeaders": ["ETag"],
                        "MaxAgeSeconds": 600,
                    }
                ]
            },
        )
        return created

    async def health_check(self) -> tuple[bool, str]:
        try:
            await self._call("head_bucket", Bucket=self.bucket)
            return True, f"reachable (bucket {self.bucket})"
        except Exception as exc:  # noqa: BLE001 - health checks never raise
            return False, type(exc).__name__

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def presigned_upload(
        self, *, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload:
        """Presigned POST whose policy pins key, content type, and size range.

        Signing is a local computation (no network call), so this is sync.
        """
        post = self._signing_client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=expires_in,
        )
        return PresignedUpload(
            url=post["url"], fields=post["fields"], key=key, expires_in=expires_in
        )

    def presigned_download(
        self,
        *,
        key: str,
        expires_in: int = 900,
        filename: str | None = None,
        inline: bool = True,
    ) -> str:
        """Short-lived GET URL.

        With ``filename``, storage answers with a Content-Disposition header:
        ``inline`` opens the file in the browser, otherwise it downloads under
        that name. The header is part of the signature, so it cannot be altered.
        """
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if filename:
            disposition = "inline" if inline else "attachment"
            params["ResponseContentDisposition"] = (
                f'{disposition}; filename="{filename}"; filename*=UTF-8{{quote(filename)}}'
            )
        return self._signing_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in
        )

    # ------------------------------------------------------------------
    # Objects
    # ------------------------------------------------------------------

    async def head(self, key: str) -> StoredObject | None:
        try:
            r = await self._call("head_object", Bucket=self.bucket, Key=key)
        except ClientError:
            return None
        return StoredObject(
            key=key,
            size_bytes=int(r["ContentLength"]),
            content_type=r.get("ContentType"),
            etag=r["ETag"].strip('"'),
        )

    async def read_prefix_bytes(self, key: str, length: int) -> bytes:
        r = await self._call(
            "get_object", Bucket=self.bucket, Key=key, Range=f"bytes=0-{length - 1}"
        )
        return await run_in_threadpool(r["Body"].read)

    async def download_to_file(self, key: str, destination: Path) -> None:
        await self._call("download_file", Bucket=self.bucket, Key=key, Filename=str(destination))

    async def put_json(self, key: str, payload: Any) -> None:
        await self._call(
            "put_object",
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

    async def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        await self._call(
            "put_object", Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
        )

    async def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = await self._call("list_objects_v2", **kwargs)
            keys.extend(o["Key"] for o in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")

    async def delete(self, key: str) -> None:
        await self._call("delete_object", Bucket=self.bucket, Key=key)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a prefix. Returns the number deleted."""
        deleted = 0
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = await self._call("list_objects_v2", **kwargs)
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                await self._call(
                    "delete_objects", Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True}
                )
                deleted += len(keys)
            if not page.get("IsTruncated"):
                return deleted
            token = page.get("NextContinuationToken")


@lru_cache
def _build_storage() -> ObjectStorage:
    internal = settings.s3_endpoint_url or None
    public = settings.s3_public_endpoint_url or internal
    client = _client(internal)
    signing = client if public == internal else _client(public, "S3_PUBLIC_ENDPOINT_URL")
    return ObjectStorage(client=client, signing_client=signing, bucket=settings.s3_bucket)


def get_storage() -> ObjectStorage:
    """FastAPI dependency; tests override it with storage on a test bucket."""
    return _build_storage()
