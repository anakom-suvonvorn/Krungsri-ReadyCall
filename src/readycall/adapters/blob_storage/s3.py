"""MinIO and anything else that speaks S3, behind the same port.

One adapter covers both `minio` and `s3` in `BlobStorageName`, because the difference is
an endpoint URL and a credential — MinIO exists precisely to be S3 on a laptop. Splitting
them would produce two copies of the same code that drift apart, and `B23`'s lesson is
that a config name which cannot actually select anything is worse than no name at all.

**boto3 is synchronous, so every call runs in a thread** (`asyncio.to_thread`). The
alternative — an async S3 client — is a heavier dependency for calls that happen once per
recording, off the request path (`services/recording/service.py` uploads from the sweep,
never from the accept). The convention this project follows is that anything touching the
network is async at the seam; how the adapter gets there is the adapter's business.

**`boto3` lives in the `s3` extra and is imported inside `__init__`**, the same shape as
the STT adapters: this module is imported by the factory on a CI box with no AWS SDK, and
an import at the top would take the whole process down for a backend nobody selected.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from readycall.errors import ConfigError, PermanentError, TransientError
from readycall.logging import get_logger
from readycall.ports.blob_storage import StoredObject

log = get_logger(__name__)

_SCHEME = "s3://"


class S3BlobStorage:
    """S3-compatible object storage. MinIO is the local case."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - the factory checks first
            raise ConfigError(
                "BLOB_STORAGE=minio/s3 needs the `s3` extra: uv sync --extra s3"
            ) from exc
        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    @property
    def name(self) -> str:
        return "s3"

    @property
    def bucket(self) -> str:
        return self._bucket

    async def ensure_bucket(self) -> None:
        """Create the bucket if it is missing. Called once, at startup.

        Doing it lazily on the first `put` would put a bucket creation inside a call, and
        the first recording of the day is not the moment to discover the credentials are
        wrong.
        """

        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except Exception:
                self._client.create_bucket(Bucket=self._bucket)

        try:
            await asyncio.to_thread(_ensure)
        except Exception as exc:
            raise TransientError(f"object storage is not reachable: {exc}") from exc

    def _key(self, ref: str) -> str:
        if ref.startswith(_SCHEME):
            rest = ref[len(_SCHEME) :]
            bucket, _, key = rest.partition("/")
            if bucket != self._bucket:
                raise PermanentError(f"ref {ref!r} is not in bucket {self._bucket!r}")
            return key
        return ref

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        encrypt: bool = True,
    ) -> StoredObject:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            # Transient on purpose: a bucket that is briefly unreachable must not be
            # recorded as a permanent failure that stops the retry.
            raise TransientError(f"could not write {key} to {self._bucket}: {exc}") from exc
        return StoredObject(
            ref=f"{_SCHEME}{self._bucket}/{key}",
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            encryption_key_ref=None,
        )

    async def get(self, ref: str) -> bytes:
        key = self._key(ref)

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            raise PermanentError(f"no such object: {ref}") from exc

    async def exists(self, ref: str) -> bool:
        key = self._key(ref)

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
            except Exception:
                return False
            return True

        return await asyncio.to_thread(_head)

    async def delete(self, ref: str) -> None:
        key = self._key(ref)

        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        await asyncio.to_thread(_delete)

    async def delete_prefix(self, prefix: str) -> int:
        key_prefix = self._key(prefix)

        def _purge() -> int:
            paginator = self._client.get_paginator("list_objects_v2")
            removed = 0
            for page in paginator.paginate(Bucket=self._bucket, Prefix=key_prefix):
                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if not keys:
                    continue
                self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
                removed += len(keys)
            return removed

        return await asyncio.to_thread(_purge)


__all__ = ["S3BlobStorage"]
