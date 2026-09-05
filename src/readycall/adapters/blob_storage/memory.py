"""In-memory BlobStorage. Tests and the scenario runner only.

Deliberately not `localfs`: a real recording must never land unencrypted on a dev
machine (`D14`), and an in-memory store makes that impossible by construction.
"""

from __future__ import annotations

import hashlib

from readycall.errors import PermanentError
from readycall.ports.blob_storage import StoredObject


class InMemoryBlobStorage:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._meta: dict[str, StoredObject] = {}

    @property
    def name(self) -> str:
        return "memory"

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        encrypt: bool = True,
    ) -> StoredObject:
        ref = f"mem://{key}"
        self._objects[ref] = data
        stored = StoredObject(
            ref=ref,
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            encryption_key_ref="mem-key-1" if encrypt else None,
        )
        self._meta[ref] = stored
        return stored

    async def get(self, ref: str) -> bytes:
        try:
            return self._objects[ref]
        except KeyError as exc:
            raise PermanentError(f"no such object: {ref}") from exc

    async def exists(self, ref: str) -> bool:
        return ref in self._objects

    async def delete(self, ref: str) -> None:
        self._objects.pop(ref, None)
        self._meta.pop(ref, None)

    async def delete_prefix(self, prefix: str) -> int:
        full = f"mem://{prefix}" if not prefix.startswith("mem://") else prefix
        doomed = [r for r in self._objects if r.startswith(full)]
        for ref in doomed:
            await self.delete(ref)
        return len(doomed)
