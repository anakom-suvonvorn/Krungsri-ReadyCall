"""A key ring backed by one master key, from the environment or from nowhere.

Dev-grade on purpose, and it says so. `LocalKeyRing` does exactly what a KMS does — wrap
and unwrap data keys with a master — but the master sits in `Settings` instead of in an
HSM, so anyone who can read the process environment can read the recordings. P7 replaces
this with a vault adapter behind the same two methods and nothing else changes.

**Two modes, and the difference is a startup decision rather than a detail.** Given
`RECORDING_MASTER_KEY` (32 bytes, base64) the ring is durable: restart the process and
yesterday's recordings still open. Given nothing, it generates a master at import and
**says so loudly** — which keeps the promise that this system runs with no keys, no
services and no GPU, while making it impossible to believe the recordings survive. A
durable blob store with an ephemeral key is refused in `Settings`, because ciphertext
nobody can ever read is worse than an honest gap (`D110`).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from readycall.errors import ConfigError, PermanentError
from readycall.logging import get_logger
from readycall.ports.keyring import DataKey

log = get_logger(__name__)

#: AES-256. The data key is this long; so is the master.
KEY_BYTES = 32
#: GCM's nonce. 96 bits is the size the mode is specified for.
NONCE_BYTES = 12
#: Bumped when the wrapping scheme changes, so an old blob can still name what wrapped it.
LOCAL_KEY_VERSION = "v1"


def decode_master_key(raw: str) -> bytes:
    """Base64 in, 32 bytes out. Raises `ConfigError` on anything else.

    A configuration mistake here produces recordings nobody can read, and it would not
    surface until somebody tried to play one back — so it is checked at startup, where a
    wrong key is a refusal to boot rather than a surprise in a month.
    """
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ConfigError("RECORDING_MASTER_KEY is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise ConfigError(
            f"RECORDING_MASTER_KEY must decode to {KEY_BYTES} bytes, got {len(key)}. "
            f'Generate one with: python -c "import base64,os;'
            f'print(base64.b64encode(os.urandom(32)).decode())"'
        )
    return key


class LocalKeyRing:
    """Envelope encryption with one master key held in this process."""

    def __init__(self, master_key: bytes | None = None) -> None:
        if master_key is None:
            self._master = os.urandom(KEY_BYTES)
            self._ephemeral = True
            log.warning(
                "recording master key generated for this process only - "
                "recordings written now cannot be read after a restart. "
                "Set RECORDING_MASTER_KEY to keep them.",
                key_ref=self.key_ref,
            )
        else:
            if len(master_key) != KEY_BYTES:
                raise ConfigError(f"master key must be {KEY_BYTES} bytes, got {len(master_key)}")
            self._master = master_key
            self._ephemeral = False

    @property
    def name(self) -> str:
        return "local"

    @property
    def key_ref(self) -> str:
        return f"local:{LOCAL_KEY_VERSION}"

    @property
    def is_ephemeral(self) -> bool:
        return self._ephemeral

    def generate_data_key(self) -> DataKey:
        data_key = os.urandom(KEY_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        # The nonce travels inside `wrapped` rather than beside it: a wrapped key that
        # cannot be unwrapped without a second field is a field somebody will forget to
        # store, and the failure is silent until a playback.
        wrapped = nonce + AESGCM(self._master).encrypt(nonce, data_key, None)
        return DataKey(key_ref=self.key_ref, plaintext=data_key, wrapped=wrapped)

    def unwrap(self, key_ref: str, wrapped: bytes) -> bytes:
        if key_ref != self.key_ref:
            raise PermanentError(
                f"this ring holds {self.key_ref!r} and cannot unwrap {key_ref!r} - "
                "the object was written under a different master key"
            )
        if len(wrapped) <= NONCE_BYTES:
            raise PermanentError("wrapped key is truncated")
        nonce, blob = wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:]
        try:
            return AESGCM(self._master).decrypt(nonce, blob, None)
        except Exception as exc:
            raise PermanentError(
                "could not unwrap the data key - wrong master key, or the object is corrupt"
            ) from exc


__all__ = ["KEY_BYTES", "LOCAL_KEY_VERSION", "LocalKeyRing", "decode_master_key"]
