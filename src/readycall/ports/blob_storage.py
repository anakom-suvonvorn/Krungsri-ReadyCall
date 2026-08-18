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
