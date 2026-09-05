"""BlobStorage port — recordings and rendered prompt clips.

Recordings are encrypted at rest with a per-object key reference (`D14`), and the
retention job deletes by prefix. `localfs` exists for dev convenience only and must
never hold a real recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    ref: str
    size_bytes: int
    checksum: str
    content_type: str
    encryption_key_ref: str | None = None


@runtime_checkable
class BlobStorage(Protocol):
    @property
    def name(self) -> str: ...

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        encrypt: bool = True,
    ) -> StoredObject: ...

    async def get(self, ref: str) -> bytes: ...

    async def exists(self, ref: str) -> bool: ...

    async def delete(self, ref: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> int:
        """Used by retention/erasure. Returns how many objects were removed."""
        ...


@runtime_checkable
class ProvisionableBlobStorage(Protocol):
    """A store whose container has to be made before anything can be written (`D110`).

    A capability, the same shape as `ReplayableSttEngine` and `BatchSttEngine` (`D101`,
    `D107`), and for the same reason: only the S3 adapter has one, and putting
    `ensure_bucket()` on `BlobStorage` would oblige a dict and a directory to grow a
    meaningless no-op.

    Called once at startup rather than lazily on the first `put`, because the first
    recording of the day is not the moment to discover the credentials are wrong — and
    because `flush_pending` treats a write failure as transient and would retry a
    misconfiguration forever.
    """

    async def ensure_bucket(self) -> None: ...
