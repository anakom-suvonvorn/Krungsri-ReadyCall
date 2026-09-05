"""A directory on disk. Only ever reached through `EncryptingBlobStorage` (`D110`).

`InMemoryBlobStorage`'s docstring used to say a local store was *"deliberately not"* built,
because a real recording must never land unencrypted on a dev machine (`D14`). That was
the right call while nothing encrypted. It is the wrong call now: encryption is a wrapper
the factory always applies, so what lands here is ciphertext with no key beside it, and the
property the old refusal was protecting is now true by construction rather than by absence.

`build_blob_storage` is what enforces "only through the wrapper". Constructing this class
directly is a test's business, and a test that writes plaintext to it is testing the
backend rather than the system.

**Writes are atomic.** A recording is written to a temporary name in the same directory
and renamed, because a half-written object with a row in `audio_recordings` pointing at it
is a recording that exists, opens, and is wrong (`nothing is done until the external system
confirms it`).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.ports.blob_storage import StoredObject

log = get_logger(__name__)

_SCHEME = "file://"


class LocalFsBlobStorage:
    """Objects as files under one root. Keys may contain `/` and become directories."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "localfs"

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        """Resolve a key inside the root, and refuse anything that escapes it.

        The comparison is between *resolved* paths, not between strings, for the reason
        `D107` gives about the demo endpoint: rejecting `..` by inspecting the text is the
        version of this check that keeps getting bypassed.
        """
        candidate = (self._root / key.removeprefix(_SCHEME)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise PermanentError(f"blob key escapes the store root: {key!r}")
        return candidate

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        encrypt: bool = True,
    ) -> StoredObject:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
            tmp.write_bytes(data)
            os.replace(tmp, path)

        await asyncio.to_thread(_write)
        return StoredObject(
            ref=f"{_SCHEME}{key}",
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            # Never claims a key of its own: this class does not encrypt, and saying it
            # did would be the one lie that matters in an audit.
            encryption_key_ref=None,
        )

    async def get(self, ref: str) -> bytes:
        path = self._path(ref)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise PermanentError(f"no such object: {ref}") from exc

    async def exists(self, ref: str) -> bool:
        return await asyncio.to_thread(self._path(ref).is_file)

    async def delete(self, ref: str) -> None:
        path = self._path(ref)

        def _unlink() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_unlink)

    async def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)

        def _purge() -> int:
            if base.is_dir():
                removed = 0
                for child in sorted(base.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                        removed += 1
                    elif child.is_dir():
                        child.rmdir()
                base.rmdir()
                return removed
            # A prefix that is not a directory still has to work: it is how a caller
            # deletes `calls/abc` when the objects are `calls/abc-intake.wav`.
            parent = base.parent
            if not parent.is_dir():
                return 0
            removed = 0
            for child in parent.iterdir():
                if child.is_file() and child.name.startswith(base.name):
                    child.unlink(missing_ok=True)
                    removed += 1
            return removed

        return await asyncio.to_thread(_purge)


__all__ = ["LocalFsBlobStorage"]
