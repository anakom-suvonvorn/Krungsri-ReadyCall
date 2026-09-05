"""Build the blob store from config, and always with the encryption on top (`D110`).

One function, and it is the only place a `BlobStorage` should be constructed outside a
test. That is the enforcement point for the rule the wrapper exists to keep: **a recording
is never handed to a backend in plaintext**. A caller who builds `LocalFsBlobStorage`
directly has opted out of that, which is why nothing in `services/` does.

The name in `BlobStorageName` and the object it produces:

| `BLOB_STORAGE` | backend | survives a restart |
|---|---|---|
| `memory` (default) | a dict | no — and the key is ephemeral too, which is coherent |
| `localfs` | files under `BLOB_ROOT` | yes, given `RECORDING_MASTER_KEY` |
| `minio` / `s3` | the S3 adapter | yes, given the key and a reachable bucket |
"""

from __future__ import annotations

from readycall.adapters.blob_storage.encrypting import EncryptingBlobStorage
from readycall.adapters.blob_storage.localfs import LocalFsBlobStorage
from readycall.adapters.blob_storage.memory import InMemoryBlobStorage
from readycall.adapters.keyring.local import LocalKeyRing, decode_master_key
from readycall.config import BlobStorageName, Settings
from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.blob_storage import BlobStorage
from readycall.ports.keyring import KeyRing

log = get_logger(__name__)


def build_keyring(settings: Settings) -> KeyRing:
    raw = settings.recording_master_key
    return LocalKeyRing(decode_master_key(raw) if raw else None)


def build_blob_storage(settings: Settings, keyring: KeyRing | None = None) -> BlobStorage:
    keys = keyring or build_keyring(settings)
    inner: BlobStorage
    match settings.blob_storage:
        case BlobStorageName.MEMORY:
            inner = InMemoryBlobStorage()
        case BlobStorageName.LOCALFS:
            inner = LocalFsBlobStorage(settings.blob_root)
        case BlobStorageName.MINIO | BlobStorageName.S3:
            try:
                import boto3  # noqa: F401 - presence check, before anything opens
            except ImportError as exc:
                raise ConfigError(
                    f"BLOB_STORAGE={settings.blob_storage.value} needs the `s3` extra:\n"
                    f"  uv sync --extra s3\n"
                    f"or set BLOB_STORAGE=localfs, which needs nothing and still encrypts."
                ) from exc
            from readycall.adapters.blob_storage.s3 import S3BlobStorage

            inner = S3BlobStorage(
                bucket=settings.blob_bucket,
                endpoint_url=settings.blob_endpoint_url,
                access_key=settings.blob_access_key,
                secret_key=settings.blob_secret_key,
                region=settings.blob_region,
            )
    store = EncryptingBlobStorage(inner, keys)
    log.info(
        "blob storage ready",
        backend=inner.name,
        keyring=keys.name,
        ephemeral_key=keys.is_ephemeral,
    )
    return store


__all__ = ["build_blob_storage", "build_keyring"]
