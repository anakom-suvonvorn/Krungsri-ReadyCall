"""Encryption is a WRAPPER over the blob port, not a feature of each backend (`D110`).

`BlobStorage` has four implementations and will grow more. If each encrypted its own
objects there would be four pieces of cryptography to review, and the one nobody reviewed
would be the one holding a real recording. So there is exactly one: this class wraps any
other `BlobStorage` and hands the backend ciphertext it cannot read.

**Envelope encryption**, the shape every KMS uses (`ports/keyring.py`): a fresh AES-256
data key per object, the data key wrapped by a master key that stays in the ring, and the
wrapped key stored in the object's own header. So restoring one recording needs the ring
and that object, and nothing else.

The stored layout, which is deliberately self-describing:

    RCE1 | u16 len(key_ref) | key_ref | u16 len(wrapped) | wrapped | nonce(12) | ciphertext+tag

A blob that does not start with `RCE1` is returned untouched. That is not a hole — it is
how a store that already holds plaintext prompt clips keeps working — and it is why
`encrypt=False` still says `encryption_key_ref=None` rather than pretending.
"""

from __future__ import annotations

import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from readycall.adapters.keyring.local import NONCE_BYTES
from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.ports.blob_storage import BlobStorage, StoredObject
from readycall.ports.keyring import KeyRing

log = get_logger(__name__)

MAGIC = b"RCE1"
_HEADER = struct.Struct("!H")


def _frame(key_ref: str, wrapped: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    ref = key_ref.encode("utf-8")
    return b"".join(
        [MAGIC, _HEADER.pack(len(ref)), ref, _HEADER.pack(len(wrapped)), wrapped, nonce, ciphertext]
    )


def _unframe(blob: bytes) -> tuple[str, bytes, bytes, bytes]:
    """Split a framed blob. Raises `PermanentError` on anything malformed.

    Every length is checked before it is used to slice. A truncated object read with
    optimistic slicing returns a short, plausible, wrong buffer instead of an error, and
    this project has met that shape enough times to stop writing it (`B20`).
    """
    pos = len(MAGIC)
    try:
        (ref_len,) = _HEADER.unpack_from(blob, pos)
        pos += _HEADER.size
        if len(blob) < pos + ref_len:
            raise ValueError("truncated key_ref")
        key_ref = blob[pos : pos + ref_len].decode("utf-8")
        pos += ref_len
        (wrapped_len,) = _HEADER.unpack_from(blob, pos)
        pos += _HEADER.size
        wrapped = blob[pos : pos + wrapped_len]
        if len(wrapped) != wrapped_len:
            raise ValueError("truncated wrapped key")
        pos += wrapped_len
        nonce = blob[pos : pos + NONCE_BYTES]
        if len(nonce) != NONCE_BYTES:
            raise ValueError("truncated nonce")
        ciphertext = blob[pos + NONCE_BYTES :]
        if not ciphertext:
            raise ValueError("no ciphertext")
    except (struct.error, ValueError, UnicodeDecodeError) as exc:
        raise PermanentError(f"encrypted object is malformed: {exc}") from exc
    return key_ref, wrapped, nonce, ciphertext


class EncryptingBlobStorage:
    """Any `BlobStorage`, plus AES-256-GCM. Same port in, same port out."""

    def __init__(self, inner: BlobStorage, keyring: KeyRing) -> None:
        self._inner = inner
        self._keys = keyring

    @property
    def name(self) -> str:
        return f"encrypted:{self._inner.name}"

    @property
    def inner(self) -> BlobStorage:
        """The backend underneath. For tests that want to prove it holds ciphertext."""
        return self._inner

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        encrypt: bool = True,
    ) -> StoredObject:
        if not encrypt:
            # Prompt clips and other public artifacts. Logged rather than silent: on a
            # store that also holds recordings, an unencrypted write is worth seeing.
            log.info("blob written unencrypted", key=key, store=self._inner.name)
            return await self._inner.put(key, data, content_type=content_type, encrypt=False)

        data_key = self._keys.generate_data_key()
        # A fresh nonce per object. GCM's one unbreakable rule is that a (key, nonce) pair
        # is never reused, and a per-object data key makes that trivially true here.
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(data_key.plaintext).encrypt(nonce, data, None)
        blob = _frame(data_key.key_ref, data_key.wrapped, nonce, ciphertext)
        stored = await self._inner.put(key, blob, content_type=content_type, encrypt=False)
        # The checksum is of the PLAINTEXT, because the question it answers is "is this
        # the same recording", and re-encrypting the same bytes produces a different
        # ciphertext every time. `size_bytes` is what the store actually holds.
        return StoredObject(
            ref=stored.ref,
            size_bytes=stored.size_bytes,
            checksum=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            encryption_key_ref=data_key.key_ref,
        )

    async def get(self, ref: str) -> bytes:
        blob = await self._inner.get(ref)
        if not blob.startswith(MAGIC):
            return blob
        key_ref, wrapped, nonce, ciphertext = _unframe(blob)
        data_key = self._keys.unwrap(key_ref, wrapped)
        try:
            return AESGCM(data_key).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise PermanentError(
                f"object {ref} failed authentication - it has been altered"
            ) from exc

    async def exists(self, ref: str) -> bool:
        return await self._inner.exists(ref)

    async def delete(self, ref: str) -> None:
        await self._inner.delete(ref)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._inner.delete_prefix(prefix)


__all__ = ["MAGIC", "EncryptingBlobStorage"]
