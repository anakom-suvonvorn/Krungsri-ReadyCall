"""The recording, from a frame off the wire to an encrypted object (`D110`).

What this suite is built to catch, in the order the failures would actually hurt:

* **audio stored without consent.** The caller who pressed 2 is the whole reason the
  consent check sits at `close()` rather than at `open()`, and the assertion is on the
  *store being empty*, not on a flag.
* **a row pointing at an object that was never written.** The upload can fail; the row
  must not exist until it has not.
* **the upload happening on the accept path.** `close()` must be cheap and synchronous;
  everything slow belongs to `flush_pending()`, and a test that could not tell the
  difference would let the two drift back together.
* **a buffer that outlives the call.** An abandoned caller leaves no request behind, so
  the only thing that can seal their recording is the state change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from readycall.adapters.blob_storage.encrypting import MAGIC, EncryptingBlobStorage
from readycall.adapters.blob_storage.memory import InMemoryBlobStorage
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.adapters.keyring.local import KEY_BYTES, LocalKeyRing
from readycall.clock import ManualClock
from readycall.config import load_settings
from readycall.domain import events as ev
from readycall.domain.enums import (
    CallState,
    ConsentScope,
    EntryChannel,
    RecordingPhase,
    SpeakerRole,
)
from readycall.domain.models import CallSession, Consent
from readycall.errors import TransientError
from readycall.media.audio import AudioFormat, Encoding
from readycall.media.gateway import MediaGateway
from readycall.ports.blob_storage import StoredObject
from readycall.services.call_orchestrator.repository import InMemoryCallSessionRepository
from readycall.services.recording.service import RecordingService
from readycall.services.recording.store import InMemoryRecordingStore

CALL = "cs_rec_1"
NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
#: One telephony packet: 320 samples of 16-bit PCM, which at the default 8 kHz is 40 ms
#: of audio and becomes 640 samples once the gateway resamples to 16 kHz.
PACKET = bytes(640)


def _session(*, consented: bool | None = True, call_session_id: str = CALL) -> CallSession:
    consents: tuple[Consent, ...] = ()
    if consented is not None:
        consents = (
            Consent(
                scope=ConsentScope.RECORDING,
                granted=consented,
                granted_at=NOW,
                basis="ivr_keypress_1" if consented else "ivr_keypress_2",
                channel="ivr",
            ),
        )
    return CallSession(
        call_session_id=call_session_id,
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.INTAKE_ACTIVE,
        created_at=NOW,
        intake_id="ik_1",
        consents=consents,
    )


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(start=NOW)


@pytest.fixture
def blob() -> EncryptingBlobStorage:
    return EncryptingBlobStorage(InMemoryBlobStorage(), LocalKeyRing(b"m" * KEY_BYTES))


@pytest.fixture
def gateway() -> MediaGateway:
    return MediaGateway()


@pytest.fixture
def store() -> InMemoryRecordingStore:
    return InMemoryRecordingStore()


@pytest.fixture
def recorder(
    blob: EncryptingBlobStorage,
    clock: ManualClock,
    gateway: MediaGateway,
    store: InMemoryRecordingStore,
) -> RecordingService:
    return RecordingService(
        blob=blob,
        clock=clock,
        settings=load_settings(),
        gateway=gateway,
        store=store,
    )


async def _speak(gateway: MediaGateway, packets: int = 25) -> None:
    for _ in range(packets):
        await gateway.push(CALL, PACKET)


# --- the happy path ---------------------------------------------------------------------


async def test_a_consented_intake_reaches_storage_encrypted(
    recorder: RecordingService, gateway: MediaGateway, blob: EncryptingBlobStorage
) -> None:
    recorder.open(CALL)
    await _speak(gateway)

    assert recorder.close(_session()) is True
    written = await recorder.flush_pending()

    assert len(written) == 1
    recording = written[0]
    assert recording.phase is RecordingPhase.INTAKE
    assert recording.encryption_key_ref == "local:v1"
    assert recording.duration_s == pytest.approx(1.0, abs=0.01)  # 25 packets x 40 ms
    # Through the front door it is a playable WAV; underneath it is not.
    assert (await blob.get(recording.storage_ref)).startswith(b"RIFF")
    assert (await blob.inner.get(recording.storage_ref)).startswith(MAGIC)


async def test_the_row_carries_the_retention_promise(
    recorder: RecordingService, gateway: MediaGateway
) -> None:
    """`D14`'s 90 days, written at upload time rather than computed at purge time."""
    recorder.open(CALL)
    await _speak(gateway)
    recorder.close(_session())

    recording = (await recorder.flush_pending())[0]

    assert recording.delete_after == NOW + timedelta(days=90)


# --- consent (D14) ----------------------------------------------------------------------


async def test_a_caller_who_declined_is_never_stored(
    recorder: RecordingService, gateway: MediaGateway, store: InMemoryRecordingStore
) -> None:
    """Pressing 2 means their words are analysed in memory and their audio is not kept.

    The assertion is that **nothing was written at all** — not that a flag was set. A
    check that passes because a field says `granted=False` would still pass on a system
    that had uploaded the audio first.
    """
    recorder.open(CALL)
    await _speak(gateway)

    assert recorder.close(_session(consented=False)) is False
    assert await recorder.flush_pending() == []
    assert await store.for_calls([CALL]) == []


async def test_a_caller_who_said_nothing_at_all_is_never_stored(
    recorder: RecordingService, gateway: MediaGateway
) -> None:
    """Ignoring the offer is not the same fact as declining it (`D88`), and neither is
    consent. The recording is kept only on an explicit yes."""
    recorder.open(CALL)
    await _speak(gateway)

    assert recorder.close(_session(consented=None)) is False


async def test_silence_is_not_stored_as_a_recording(
    recorder: RecordingService, gateway: MediaGateway
) -> None:
    """A leg that opened and carried no frames leaves nothing behind."""
    recorder.open(CALL)

    assert recorder.close(_session()) is False


# --- what must not happen on the accept path ----------------------------------------------


async def test_close_does_not_touch_storage(
    recorder: RecordingService, gateway: MediaGateway, blob: EncryptingBlobStorage
) -> None:
    """`close()` runs inside the accept request; the upload does not (`D12`, `D110`).

    Written as a behaviour rather than a timing: after `close()` the store holds nothing
    and the work is *queued*. If somebody moves the upload back into `close()`, this
    fails rather than getting slower and being missed.
    """
    recorder.open(CALL)
    await _speak(gateway)

    recorder.close(_session())

    assert recorder.pending_count() == 1
    assert await blob.inner.exists("mem://calls/cs_rec_1/intake-customer.wav") is False


async def test_a_failed_upload_keeps_its_place_and_writes_no_row(
    clock: ManualClock, gateway: MediaGateway, store: InMemoryRecordingStore
) -> None:
    """Nothing is recorded until the store confirms it.

    The opposite — a row written first — is a recording that looks retrievable, satisfies
    an audit and plays nothing.
    """

    class Flaky:
        name = "flaky"

        def __init__(self) -> None:
            self.attempts = 0

        async def put(self, key: str, data: bytes, **kw: object) -> StoredObject:
            self.attempts += 1
            if self.attempts == 1:
                raise TransientError("bucket asleep")
            return StoredObject(
                ref=f"mem://{key}", size_bytes=len(data), checksum="c", content_type="audio/wav"
            )

        async def get(self, ref: str) -> bytes:  # pragma: no cover - unused here
            raise AssertionError

        async def exists(self, ref: str) -> bool:  # pragma: no cover - unused here
            return False

        async def delete(self, ref: str) -> None:  # pragma: no cover - unused here
            return None

        async def delete_prefix(self, prefix: str) -> int:  # pragma: no cover - unused here
            return 0

    flaky = Flaky()
    recorder = RecordingService(
        blob=flaky, clock=clock, settings=load_settings(), gateway=gateway, store=store
    )
    recorder.open(CALL)
    await _speak(gateway)
    recorder.close(_session())

    assert await recorder.flush_pending() == []
    assert await store.for_calls([CALL]) == []
    assert recorder.pending_count() == 1

    written = await recorder.flush_pending()

    assert len(written) == 1
    assert len(await store.for_calls([CALL])) == 1
    assert recorder.pending_count() == 0


# --- endings that are not Accept ----------------------------------------------------------


async def test_an_abandoned_caller_still_has_their_recording_sealed(
    blob: EncryptingBlobStorage,
    clock: ManualClock,
    gateway: MediaGateway,
    store: InMemoryRecordingStore,
) -> None:
    """Nobody presses Accept for a caller who hangs up while waiting.

    Without the terminal-state subscription the buffer would sit open for the life of the
    process, holding audio that is never stored and never freed.
    """
    calls = InMemoryCallSessionRepository()
    session = _session()
    await calls.save(session)
    bus = InMemoryEventBus()
    recorder = RecordingService(
        blob=blob, clock=clock, settings=load_settings(), gateway=gateway, store=store, calls=calls
    )
    recorder.subscribe(bus)

    recorder.open(CALL)
    await _speak(gateway)
    await bus.publish(
        ev.CallStateChanged(
            call_session_id=CALL,
            occurred_at=NOW,
            from_state=CallState.INTAKE_ACTIVE,
            to_state=CallState.ABANDONED,
            reason="caller_hung_up",
        )
    )
    await bus.drain()

    assert recorder.open_leg_ids() == ()
    assert len(await recorder.flush_pending()) == 1


async def test_wrap_up_does_not_seal_anything(
    blob: EncryptingBlobStorage,
    clock: ManualClock,
    gateway: MediaGateway,
    store: InMemoryRecordingStore,
) -> None:
    """Only a TERMINAL state ends a call. `WRAP_UP` is after-call work, not an ending."""
    calls = InMemoryCallSessionRepository()
    await calls.save(_session())
    bus = InMemoryEventBus()
    recorder = RecordingService(
        blob=blob, clock=clock, settings=load_settings(), gateway=gateway, store=store, calls=calls
    )
    recorder.subscribe(bus)
    recorder.open(CALL)
    await _speak(gateway)

    await bus.publish(
        ev.CallStateChanged(
            call_session_id=CALL,
            occurred_at=NOW,
            from_state=CallState.IN_CALL,
            to_state=CallState.WRAP_UP,
            reason="media_disconnect",
        )
    )
    await bus.drain()

    assert recorder.open_leg_ids() == ((CALL, SpeakerRole.CUSTOMER),)


# --- retention and erasure (D14) ----------------------------------------------------------


async def test_purge_removes_the_audio_before_the_row(
    recorder: RecordingService,
    gateway: MediaGateway,
    blob: EncryptingBlobStorage,
    clock: ManualClock,
    store: InMemoryRecordingStore,
) -> None:
    recorder.open(CALL)
    await _speak(gateway)
    recorder.close(_session())
    recording = (await recorder.flush_pending())[0]

    clock.advance(91 * 24 * 3600)
    gone = await recorder.purge_expired()

    assert [r.recording_id for r in gone] == [recording.recording_id]
    assert await blob.exists(recording.storage_ref) is False
    assert await store.for_calls([CALL]) == []


async def test_a_recording_inside_its_retention_is_left_alone(
    recorder: RecordingService, gateway: MediaGateway, clock: ManualClock
) -> None:
    recorder.open(CALL)
    await _speak(gateway)
    recorder.close(_session())
    await recorder.flush_pending()

    clock.advance(89 * 24 * 3600)

    assert await recorder.purge_expired() == []


async def test_erasing_a_call_takes_the_object_and_the_row(
    recorder: RecordingService,
    gateway: MediaGateway,
    blob: EncryptingBlobStorage,
    store: InMemoryRecordingStore,
) -> None:
    """What a PDPA erasure request runs, and it does not wait for retention."""
    recorder.open(CALL)
    await _speak(gateway)
    recorder.close(_session())
    recording = (await recorder.flush_pending())[0]

    removed = await recorder.erase_call(CALL)

    assert removed >= 1
    assert await blob.exists(recording.storage_ref) is False
    assert await store.for_calls([CALL]) == []


# --- the gateway they share ---------------------------------------------------------------


async def test_the_recorder_and_the_transcriber_share_one_leg(
    recorder: RecordingService, gateway: MediaGateway
) -> None:
    """Opening a leg twice must not silently unsubscribe the first opener (`D110`).

    Before the gateway was made idempotent, the second `open_leg` replaced the first and
    threw away its sinks — so whichever service opened first would have been correct,
    running, and fed nothing: `B24` exactly.
    """
    heard: list[int] = []

    async def transcriber_sink(frame: object) -> None:
        heard.append(1)

    gateway.open_leg(CALL, fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=8000))
    gateway.subscribe(CALL, transcriber_sink)
    recorder.open(CALL)
    await gateway.push(CALL, PACKET)

    assert heard == [1]
    assert recorder.close(_session()) is True


async def test_the_first_openers_format_wins(
    recorder: RecordingService, gateway: MediaGateway
) -> None:
    """And it is the one whose sinks are already attached to that interpretation.

    A second opener quietly changing the sample rate would not fail — it would produce a
    recording at the wrong speed, which reads as a bad line rather than as a bug (`D96`).
    """
    gateway.open_leg(CALL, fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=16000))

    recorder.open(CALL, fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=8000))
    await gateway.push(CALL, PACKET)
    recorder.close(_session())
    recording = (await recorder.flush_pending())[0]

    # 320 samples read at 16 kHz is 20 ms. Had the recorder's 8 kHz won, the same bytes
    # would have become 40 ms of audio - the call played at half speed.
    assert recording.duration_s == pytest.approx(0.02, abs=0.005)


# --- switched off -------------------------------------------------------------------------


async def test_recording_can_be_turned_off_entirely(
    blob: EncryptingBlobStorage,
    clock: ManualClock,
    gateway: MediaGateway,
    store: InMemoryRecordingStore,
) -> None:
    """`RECORDING_ENABLED=false` puts the system back where `D9` left it: audio is
    analysed per utterance, in memory, and never reaches a disk."""
    recorder = RecordingService(
        blob=blob,
        clock=clock,
        settings=load_settings(recording_enabled=False),
        gateway=gateway,
        store=store,
    )
    gateway.open_leg(CALL)

    recorder.open(CALL)
    await _speak(gateway)

    assert recorder.open_leg_ids() == ()
    assert recorder.close(_session()) is False
