"""The recording: frames in from the gateway, one encrypted object out (`D110`).

`ARCHITECTURE` §6 gives the media gateway three jobs — normalise, *write the encrypted
recording*, and fan out to the transcriber. Two of them shipped with `D96`; this is the
third, and it is the last piece of P3.

**It is a sink, not a second gateway.** The gateway already fans normalised frames to
whoever subscribed, and the transcriber is one such subscriber. Adding a second one costs
nothing and keeps the boundary the gateway's docstring defends: nothing here interprets
content, and the recorder could not tell you a word of what was said.

**The upload does not happen on the accept path.** `close()` seals the buffer — cheap, in
memory, and safe to race with the hang-up (`D21`) — and the bytes reach storage from
`sweep_once`. Uploading inline would put an object-store round trip between an agent
pressing Accept and the caller hearing them, which is `D12`'s rule in a place it has not
had to be applied before. It also makes the slow half deterministic to test: drive
`flush_pending()`, do not sleep.

**Consent is checked here, at the last possible moment.** A caller who pressed 2 has
frames flowing (the offer window IS the recording window, `D21`) and no `recording`
consent, so their audio is analysed in memory and **never stored**. Checking at `open()`
would be too early — consent arrives on a keypress that can land after the leg does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from readycall.clock import Clock
from readycall.config import Settings
from readycall.domain.enums import ConsentScope, RecordingPhase, SpeakerRole
from readycall.domain.models import AudioRecording, CallSession
from readycall.errors import TransientError
from readycall.ids import generate
from readycall.logging import get_logger
from readycall.media.audio import TARGET_SAMPLE_RATE, AudioFormat, encode_wav
from readycall.media.gateway import MediaGateway
from readycall.ports.blob_storage import BlobStorage
from readycall.ports.stt import AudioFrame
from readycall.services.recording.store import InMemoryRecordingStore, RecordingStore

log = get_logger(__name__)

#: 180 s of intake at 16 kHz float is ~11 MB in Python floats, which is the real bound
#: here. A leg that runs past it keeps recording and stops accumulating, so a stuck
#: media source cannot take the process down — the same shape as `B20`'s backlog cap,
#: and it is logged for the same reason.
MAX_SAMPLES = TARGET_SAMPLE_RATE * 600


@dataclass
class _Open:
    call_session_id: str
    speaker_role: SpeakerRole
    phase: RecordingPhase
    samples: list[float] = field(default_factory=list)
    dropped: int = 0


@dataclass
class _Sealed:
    call_session_id: str
    speaker_role: SpeakerRole
    phase: RecordingPhase
    samples: list[float]
    intake_id: str | None


class RecordingService:
    def __init__(
        self,
        *,
        blob: BlobStorage,
        clock: Clock,
        settings: Settings,
        gateway: MediaGateway,
        store: RecordingStore | None = None,
    ) -> None:
        self._blob = blob
        self._clock = clock
        self._settings = settings
        self._gateway = gateway
        self._store: RecordingStore = store or InMemoryRecordingStore()
        self._open: dict[tuple[str, SpeakerRole], _Open] = {}
        self._pending: list[_Sealed] = []

    # --- while the audio is flowing -----------------------------------------------------

    def open(
        self,
        call_session_id: str,
        *,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
        phase: RecordingPhase = RecordingPhase.INTAKE,
        fmt: AudioFormat | None = None,
    ) -> None:
        """Start keeping this leg's audio. Idempotent, like `TranscriptionService.open`."""
        if not self._settings.recording_enabled:
            return
        key = (call_session_id, speaker_role)
        if key in self._open:
            return
        # `open_leg` is idempotent (`D110`), so whichever of the transcriber and the
        # recorder gets here first opens it and the other joins. Neither may depend on
        # the other being on: a transcriber that is down must not stop the recording,
        # and a caller who declined analysis may still be recorded.
        self._gateway.open_leg(call_session_id, speaker_role=speaker_role, fmt=fmt)
        held = _Open(call_session_id, speaker_role, phase)
        self._open[key] = held

        async def sink(frame: AudioFrame) -> None:
            if len(held.samples) >= MAX_SAMPLES:
                held.dropped += len(frame.samples)
                return
            held.samples.extend(frame.samples)

        self._gateway.subscribe(call_session_id, sink, speaker_role=speaker_role)
        log.info(
            "recording opened",
            call_session_id=call_session_id,
            speaker=str(speaker_role),
            phase=str(phase),
        )

    def close(
        self,
        session: CallSession,
        *,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
    ) -> bool:
        """Stop keeping it, and queue the upload. Returns whether anything was queued.

        Safe to call twice, and safe to call for a call that was never recorded — the
        accept and the hang-up race by design (`D21`) and both routes end here.
        """
        held = self._open.pop((session.call_session_id, speaker_role), None)
        if held is None:
            return False
        if held.dropped:
            log.warning(
                "recording truncated at the buffer cap",
                call_session_id=session.call_session_id,
                dropped_samples=held.dropped,
            )
        if not held.samples:
            log.info("recording discarded, no audio", call_session_id=session.call_session_id)
            return False
        if not self._consented(session):
            # The caller pressed 2, or never answered the offer. Their words were still
            # transcribed in memory for the brief, and that is what they consented to
            # decline — storing the audio anyway would be the breach `D14` is about.
            log.info(
                "recording discarded, no consent",
                call_session_id=session.call_session_id,
                seconds=round(len(held.samples) / TARGET_SAMPLE_RATE, 1),
            )
            return False
        self._pending.append(
            _Sealed(
                call_session_id=session.call_session_id,
                speaker_role=speaker_role,
                phase=held.phase,
                samples=held.samples,
                intake_id=session.intake_id,
            )
        )
        return True

    # --- and then, from the sweep -------------------------------------------------------

    async def flush_pending(self) -> list[AudioRecording]:
        """Upload whatever is sealed. Driven by `sweep_once`, never by a request (`B7`).

        A failed upload keeps its place in the queue and is retried on the next sweep;
        nothing is recorded until the store confirms the write, so a row in
        `audio_recordings` always points at an object that exists.
        """
        written: list[AudioRecording] = []
        remaining: list[_Sealed] = []
        for sealed in self._pending:
            try:
                written.append(await self._upload(sealed))
            except TransientError as exc:
                log.warning(
                    "recording upload failed, will retry",
                    call_session_id=sealed.call_session_id,
                    error=str(exc),
                )
                remaining.append(sealed)
            except Exception:
                # Permanent: a malformed key, a bucket that does not exist. Retrying
                # forever would grow this list for the life of the process, and the
                # audio is not recoverable from here anyway.
                log.exception(
                    "recording upload failed permanently",
                    call_session_id=sealed.call_session_id,
                )
        self._pending = remaining
        return written

    async def _upload(self, sealed: _Sealed) -> AudioRecording:
        now = self._clock.now()
        data = encode_wav(sealed.samples)
        key = f"calls/{sealed.call_session_id}/{sealed.phase.value}-{sealed.speaker_role.value}.wav"
        stored = await self._blob.put(key, data, content_type="audio/wav")
        recording = AudioRecording(
            recording_id=generate("rec"),
            call_session_id=sealed.call_session_id,
            phase=sealed.phase,
            storage_ref=stored.ref,
            created_at=now,
            duration_s=round(len(sealed.samples) / TARGET_SAMPLE_RATE, 3),
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            sample_rate=TARGET_SAMPLE_RATE,
            encryption_key_ref=stored.encryption_key_ref,
            # Written now, from the setting as it stands now (`D14`). Computing it at
            # purge time would mean lowering the retention silently shortened the life
            # of audio already held, and raising it silently extended it.
            delete_after=now + timedelta(days=self._settings.recording_retention_days),
            intake_id=sealed.intake_id,
        )
        # The row goes in only after the object is confirmed. A reference written first
        # would name an object that may never arrive — a recording that looks
        # retrievable, satisfies an audit, and plays nothing.
        await self._store.save(recording)
        log.info(
            "recording stored",
            call_session_id=sealed.call_session_id,
            ref=recording.storage_ref,
            seconds=recording.duration_s,
            bytes=recording.size_bytes,
            key_ref=recording.encryption_key_ref,
        )
        return recording

    # --- retention (D14) ----------------------------------------------------------------

    async def purge_expired(self, *, limit: int = 500) -> list[AudioRecording]:
        """Delete every recording past its `delete_after`. Returns what went.

        **The object first, then the row.** The other order can leave an object with no
        row pointing at it, which is audio nobody knows they are holding — the worst of
        the two failures, because it is invisible to every report. A failed object
        delete keeps its row and is retried next run.
        """
        now = self._clock.now()
        gone: list[AudioRecording] = []
        for recording in await self._store.due_for_deletion(now=now, limit=limit):
            try:
                await self._blob.delete(recording.storage_ref)
            except Exception:
                log.exception(
                    "could not delete a recording's audio, keeping the row",
                    recording_id=recording.recording_id,
                    ref=recording.storage_ref,
                )
                continue
            await self._store.delete(recording.recording_id)
            gone.append(recording)
        if gone:
            log.info("recordings purged", count=len(gone), retention_days=self._retention_days)
        return gone

    async def erase_call(self, call_session_id: str) -> int:
        """Everything for one call, now. This is what a PDPA erasure request runs.

        By prefix as well as by row, because a row that was never written for an object
        that was (an upload that succeeded and a save that did not) is precisely the case
        an erasure request must not miss.
        """
        removed = 0
        for recording in await self._store.for_calls([call_session_id]):
            await self._blob.delete(recording.storage_ref)
            await self._store.delete(recording.recording_id)
            removed += 1
        orphans = await self._blob.delete_prefix(f"calls/{call_session_id}")
        log.info("call audio erased", call_session_id=call_session_id, rows=removed, blobs=orphans)
        return removed + orphans

    async def for_call(self, call_session_id: str) -> list[AudioRecording]:
        return await self._store.for_calls([call_session_id])

    # --- helpers ------------------------------------------------------------------------

    @property
    def _retention_days(self) -> int:
        return self._settings.recording_retention_days

    def _consented(self, session: CallSession) -> bool:
        return any(c.scope is ConsentScope.RECORDING and c.granted for c in session.consents)

    def pending_count(self) -> int:
        return len(self._pending)

    def open_leg_ids(self) -> tuple[tuple[str, SpeakerRole], ...]:
        return tuple(self._open)


__all__ = ["MAX_SAMPLES", "RecordingService"]
