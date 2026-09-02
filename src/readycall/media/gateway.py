"""The media gateway: raw audio in per call leg, normalised frames out to whoever asked.

`ARCHITECTURE` §6 gives this component one sentence — *"normalises to 16 kHz mono float32
frames, writes the encrypted recording to object storage, and fans frames to the
transcriber"* — and this is the first two thirds of it. **The encrypted recording is not
built**: it needs blob storage and per-recording key refs that P7 owns, and writing audio
to disk before that exists would be the one thing `D9` was careful to avoid.

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
