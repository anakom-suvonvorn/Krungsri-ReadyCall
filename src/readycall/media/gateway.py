"""The media gateway: raw audio in per call leg, normalised frames out to whoever asked.

`ARCHITECTURE` §6 gives this component one sentence — *"normalises to 16 kHz mono float32
frames, writes the encrypted recording to object storage, and fans frames to the
transcriber"* — and this is two thirds of it. The third, the encrypted recording, is
`services/recording/` (`D110`): it **subscribes** here rather than living here, because
this file fans frames out and the recorder is just another consumer of them. Writing the
object from inside the gateway would put a bucket, a key ring and a retention policy
behind the boundary that lets P5 swap Asterisk for Twilio.

**Legs are separate streams, and that is the whole design** (`D26`). Asterisk forks each
leg, so `open_leg(call, CUSTOMER)` and `open_leg(call, AGENT)` are two independent
pipelines with two independent detectors. Speaker identity is therefore structural — there
is no diarisation model to be wrong about who spoke, and no speaker-swap bug to chase.

**Nothing here interprets content.** It reframes, resamples, counts, and hands on. The
moment this file starts caring what was said, the boundary that lets P5 swap Asterisk for
Twilio has gone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from readycall.domain.enums import SpeakerRole
from readycall.logging import get_logger
from readycall.media.audio import TARGET_SAMPLE_RATE, AudioFormat, normalise
from readycall.ports.stt import AudioFrame

log = get_logger(__name__)

FrameSink = Callable[[AudioFrame], Awaitable[None]]


@dataclass
class MediaLeg:
    """One direction of one call. Owns its own clock, in samples."""

    call_session_id: str
    speaker_role: SpeakerRole
    fmt: AudioFormat
    sinks: list[FrameSink] = field(default_factory=list)
    #: Samples emitted so far, at the TARGET rate. This is the leg's time base, and it is
    #: counted rather than read from a clock on purpose: the audio's own sample count is
    #: what a transcript timestamp has to agree with, and a wall clock drifts against it
    #: (`B3`/`B8`'s family — two clocks that must not be mixed).
    samples_out: int = 0
    frames_in: int = 0
    closed: bool = False

    @property
    def position_ms(self) -> int:
        return int(self.samples_out / TARGET_SAMPLE_RATE * 1000.0)


class MediaGateway:
    """One per process. Holds every open leg."""

    def __init__(self) -> None:
        self._legs: dict[tuple[str, SpeakerRole], MediaLeg] = {}

    def open_leg(
        self,
        call_session_id: str,
        *,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
        fmt: AudioFormat | None = None,
    ) -> MediaLeg:
        key = (call_session_id, speaker_role)
        # **Idempotent since `D110`, and it is a correctness fix rather than a
        # convenience.** Two independent consumers now open a leg — the transcriber and
        # the recorder — and neither may depend on the other running (`D12`). Replacing
        # the leg would silently discard the first one's `sinks`, so whichever opened
        # first would stop receiving audio while still believing it was subscribed:
        # `B24`'s shape, a component that is correct, running, and fed nothing.
        existing = self._legs.get(key)
        if existing is not None and not existing.closed:
            if fmt is not None and fmt != existing.fmt:
                # The second opener disagrees about what is on the wire. The first one
                # wins because its sinks are already attached to that interpretation,
                # and a silent disagreement here decodes A-law as µ-law: loud, plausible
                # garbage nobody would blame on the codec (`D96`).
                log.warning(
                    "media leg already open with a different format",
                    call_session_id=call_session_id,
                    speaker=str(speaker_role),
                    kept=str(existing.fmt.encoding),
                    ignored=str(fmt.encoding),
                )
            return existing
        leg = MediaLeg(
            call_session_id=call_session_id,
            speaker_role=speaker_role,
            fmt=fmt or AudioFormat(),
        )
        self._legs[key] = leg
        log.info(
            "media leg opened",
            call_session_id=call_session_id,
            speaker=str(speaker_role),
            encoding=str(leg.fmt.encoding),
            sample_rate=leg.fmt.sample_rate,
        )
        return leg

    def subscribe(
        self,
        call_session_id: str,
        sink: FrameSink,
        *,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
    ) -> None:
        leg = self._leg(call_session_id, speaker_role)
        leg.sinks.append(sink)

    async def push(
        self,
        call_session_id: str,
        payload: bytes,
        *,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
    ) -> AudioFrame | None:
        """One packet off the wire. Normalised once, handed to every sink."""
        leg = self._leg(call_session_id, speaker_role)
        if leg.closed:
            return None
        frame = normalise(payload, leg.fmt, t_start_ms=leg.position_ms)
        leg.frames_in += 1
        leg.samples_out += len(frame.samples)
        for sink in leg.sinks:
            # One failing consumer must not stop the others, and must not stop the audio.
            # `D12` again: the call is never blocked on anything downstream of it.
            try:
                await sink(frame)
            except Exception:  # pragma: no cover - defensive, exercised by the fake
                log.exception(
                    "media sink failed",
                    call_session_id=call_session_id,
                    speaker=str(speaker_role),
                )
        return frame

    async def close_leg(
        self, call_session_id: str, *, speaker_role: SpeakerRole = SpeakerRole.CUSTOMER
    ) -> MediaLeg | None:
        leg = self._legs.pop((call_session_id, speaker_role), None)
        if leg is None:
            return None
        leg.closed = True
        log.info(
            "media leg closed",
            call_session_id=call_session_id,
            speaker=str(speaker_role),
            frames=leg.frames_in,
            audio_ms=leg.position_ms,
        )
        return leg

    def leg_ids(self) -> tuple[tuple[str, SpeakerRole], ...]:
        return tuple(self._legs)

    def _leg(self, call_session_id: str, speaker_role: SpeakerRole) -> MediaLeg:
        leg = self._legs.get((call_session_id, speaker_role))
        if leg is None:
            raise KeyError(f"no open {speaker_role} leg for {call_session_id} - open_leg() first")
        return leg


__all__ = ["FrameSink", "MediaGateway", "MediaLeg"]
