"""KeyRing port — where the key that protects a recording comes from.

The tenth port, and it exists because `D14` promises *"per-recording key refs"* and a
key ref is only meaningful if something outside this process owns the key. In production
that is a KMS or a vault; here it is a master key in the environment. Both answer the
same two questions, which is the whole contract:

1. **give me a fresh key for this one object**, and the wrapped form to store beside it;
2. **unwrap this key** so the object can be read again.

That is **envelope encryption**, and it is the shape every KMS uses: the master key never
leaves the ring, each object gets its own data key, and the ciphertext carries the wrapped
data key rather than the key itself. It is also what makes `D14`'s erasure job cheap in the
limit — destroy a master key and everything wrapped with it is unreadable, whether or not
the delete finished.

**The port does not encrypt anything.** Encryption lives in one place
(`adapters/blob_storage/encrypting.py`) so that a MinIO bucket, a local directory and an
in-memory dict get identical guarantees from identical code. A ring that also encrypted
would be a second implementation to review, and the second one is always the weaker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DataKey:
    """One object's key, in both the form you use and the form you store.

    `plaintext` is 32 bytes for AES-256 and must never be written anywhere. `wrapped` is
    what goes into the blob header, and `key_ref` names the **master** key that wrapped
    it — `local:v1`, `kms:arn:...` — so a future ring can rotate masters and still read
    everything written under the old one.
    """

    key_ref: str
    plaintext: bytes
    wrapped: bytes


@runtime_checkable
class KeyRing(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def key_ref(self) -> str:
        """The master key this ring is currently wrapping new objects with."""
        ...

    @property
    def is_ephemeral(self) -> bool:
        """True when the master key dies with the process.

        Load-bearing rather than informational: an ephemeral key against a durable store
        writes ciphertext that nothing will ever read again, which is worse than writing
        nothing at all. `Settings` refuses that combination at startup.
        """
        ...

    def generate_data_key(self) -> DataKey:
        """A fresh random data key, plus its wrapped form. One per object."""
        ...

    def unwrap(self, key_ref: str, wrapped: bytes) -> bytes:
        """Recover a data key. Raises `PermanentError` if this ring cannot."""
        ...


__all__ = ["DataKey", "KeyRing"]
