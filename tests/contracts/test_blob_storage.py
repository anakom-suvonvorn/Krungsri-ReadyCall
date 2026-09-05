"""The contract every object store must pass — memory, a directory, and MinIO (`D110`).

The `D75` shape, applied to the blob port: one suite, three backends, and the fast one
must not enforce *less* than the real one or it is a fast path that lies. MinIO runs only
when the container is up, exactly as the Postgres contract suite does; the other two run
everywhere and are what CI proves the behaviour with.

Every backend here is wrapped in `EncryptingBlobStorage`, because that is the only way
`build_blob_storage` will ever hand one out. The tests that care about ciphertext reach
through `.inner` on purpose — asserting on the rendered result would pass on a store that
had quietly written plaintext.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from readycall.adapters.blob_storage.encrypting import MAGIC, EncryptingBlobStorage
from readycall.adapters.blob_storage.localfs import LocalFsBlobStorage
from readycall.adapters.blob_storage.memory import InMemoryBlobStorage
from readycall.adapters.keyring.local import KEY_BYTES, LocalKeyRing
from readycall.errors import PermanentError
from readycall.ports.blob_storage import BlobStorage

if TYPE_CHECKING:
    from readycall.adapters.blob_storage.s3 import S3BlobStorage

MASTER = b"k" * KEY_BYTES
WAV = b"RIFF....WAVEfmt " + bytes(range(256)) * 8


def _minio() -> S3BlobStorage | None:
    """The MinIO row, or None when the suite was not told to use it.

    Opt-in rather than auto-detected: a socket that answers on 9000 is not necessarily
    this project's MinIO, and a contract suite that quietly writes into somebody else's
    bucket is worse than one that skips.
    """
    if os.environ.get("READYCALL_TEST_MINIO", "").lower() not in {"1", "true", "yes"}:
        return None
    from readycall.adapters.blob_storage.s3 import S3BlobStorage

    return S3BlobStorage(
        bucket="readycall-test",
        endpoint_url=os.environ.get("BLOB_ENDPOINT_URL", "http://127.0.0.1:9000"),
        access_key=os.environ.get("BLOB_ACCESS_KEY", "readycall"),
        secret_key=os.environ.get("BLOB_SECRET_KEY", "readycall123"),
    )


@pytest.fixture(params=["memory", "localfs", "minio"])
async def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[EncryptingBlobStorage]:
    inner: BlobStorage
    if request.param == "memory":
        inner = InMemoryBlobStorage()
    elif request.param == "localfs":
        inner = LocalFsBlobStorage(tmp_path / "blobs")
    else:
        maybe = _minio()
        if maybe is None:
            pytest.skip("MinIO is not running - set READYCALL_TEST_MINIO=1 with the container up")
        await maybe.ensure_bucket()
        inner = maybe
    yield EncryptingBlobStorage(inner, LocalKeyRing(MASTER))
    # A shared bucket outlives the test, so the next run must not start with the last
    # run's objects in it — the same reason the Postgres suite gets its own database
    # (`D79`), one rung cheaper.
    await inner.delete_prefix("calls")
    await inner.delete_prefix("prompts")


async def test_a_stored_object_comes_back_byte_for_byte(store: EncryptingBlobStorage) -> None:
    stored = await store.put("calls/c1/intake.wav", WAV, content_type="audio/wav")
    assert stored.ref
    assert await store.get(stored.ref) == WAV


async def test_the_backend_never_sees_the_audio(store: EncryptingBlobStorage) -> None:
    """The single assertion this whole design exists for.

    Reaching through to `.inner` is the only way to make it: `store.get()` decrypts, so a
    test that used the front door would pass just as happily on a store writing plaintext.
    """
    stored = await store.put("calls/c1/intake.wav", WAV)
    raw = await store.inner.get(stored.ref)

    assert raw.startswith(MAGIC)
    assert WAV not in raw
    assert raw != WAV


async def test_it_names_the_key_that_protects_it(store: EncryptingBlobStorage) -> None:
    """`D14` asks for a per-recording key REF, and this is where it comes from."""
    stored = await store.put("calls/c1/intake.wav", WAV)
    assert stored.encryption_key_ref == "local:v1"


async def test_the_checksum_is_of_the_audio_not_the_ciphertext(
    store: EncryptingBlobStorage,
) -> None:
    """Two writes of the same recording produce different bytes and the same checksum.

    That is the property that makes the column worth having: GCM's nonce is fresh every
    time, so a checksum over the ciphertext could never answer "is this the same call".
    """
    first = await store.put("calls/c1/a.wav", WAV)
    second = await store.put("calls/c1/b.wav", WAV)

    assert first.checksum == second.checksum
    assert await store.inner.get(first.ref) != await store.inner.get(second.ref)


async def test_a_missing_object_raises_rather_than_returning_nothing(
    store: EncryptingBlobStorage,
) -> None:
    with pytest.raises(PermanentError):
        await store.get("calls/nope/missing.wav")


async def test_exists_and_delete(store: EncryptingBlobStorage) -> None:
    stored = await store.put("calls/c1/intake.wav", WAV)
    assert await store.exists(stored.ref) is True

    await store.delete(stored.ref)
    assert await store.exists(stored.ref) is False
    # Deleting twice is not an error: the retention job and an erasure request can
    # genuinely race, and a purge that fails on an already-purged object stops early.
    await store.delete(stored.ref)


async def test_delete_prefix_removes_a_whole_call(store: EncryptingBlobStorage) -> None:
    """What `D14`'s erasure job runs. Per call, so one customer's request is one prefix."""
    await store.put("calls/c1/intake.wav", WAV)
    await store.put("calls/c1/live.wav", WAV)
    kept = await store.put("calls/c2/intake.wav", WAV)

    removed = await store.delete_prefix("calls/c1")

    assert removed == 2
    assert await store.exists(kept.ref) is True


async def test_an_unencrypted_write_says_so(store: EncryptingBlobStorage) -> None:
    """Prompt clips are public artifacts and are not lied about."""
    stored = await store.put("prompts/greeting.wav", b"hello", encrypt=False)

    assert stored.encryption_key_ref is None
    assert await store.inner.get(stored.ref) == b"hello"
    # And it still reads back through the front door, because the reader keys off the
    # magic rather than off what it expected to find.
    assert await store.get(stored.ref) == b"hello"


async def test_a_tampered_object_fails_instead_of_returning_something(
    store: EncryptingBlobStorage,
) -> None:
    """GCM authenticates. A recording that has been altered must not open at all.

    The failure mode this prevents is the one `B20` taught: plausible bytes returned from
    a corrupt read are worse than an error, because nothing downstream can tell.
    """
    stored = await store.put("calls/c1/intake.wav", WAV)
    raw = bytearray(await store.inner.get(stored.ref))
    raw[-1] ^= 0xFF
    await store.inner.put("calls/c1/intake.wav", bytes(raw), encrypt=False)

    with pytest.raises(PermanentError):
        await store.get(stored.ref)
