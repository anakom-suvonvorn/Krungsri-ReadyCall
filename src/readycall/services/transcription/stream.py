"""One call leg's audio, turned into `TranscriptTurn`s in order.

This is the only part of the transcription stack that does I/O, which is the same split
`services/ivr/` and `services/intake/` already use: the rules live in a machine with no
side effects (`endpointer.py`), and the driver knows how to wait.

**Ingestion never waits for the model.** A finished segment goes onto a queue and a single
consumer task transcribes them. Two things fall out of that, both of which matter:

* **frames keep being accepted while Whisper is busy.** Awaiting the model inline would
  stall the audio path for the length of an inference, and on this GPU that is most of a
  second — during which the caller is still talking and the frames have nowhere to go.
* **turns stay in order, without a sorting step.** One consumer, one sequence counter. The
  obvious alternative — a task per segment — transcribes a two-word phrase faster than the
  sentence before it and delivers the caller's words shuffled.

**Per-leg, not per-call** (`D26`). Each leg is forked separately, so the speaker label is
structural rather than inferred, and there is no diarisation model to be wrong.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from readycall import ids
from readycall.clock import Clock
from readycall.domain.enums import SpeakerRole
from readycall.domain.models import TranscriptTurn
from readycall.logging import get_logger
from readycall.media.audio import TARGET_SAMPLE_RATE, rms
from readycall.ports.stt import AudioFrame, SttEngine, SttHint
from readycall.ports.vad import VoiceActivityDetector
from readycall.services.transcription.endpointer import (
    Endpointer,
    EndpointSettings,
    SpeechSegment,
)

log = get_logger(__name__)

TurnSink = Callable[[TranscriptTurn], Awaitable[None]]


#: Punctuation Whisper sprinkles into hallucinated Thai. Stripped before the repetition
#: check, because "ผู้เอาประกัน, ผู้เอาประกัน" is the same failure as the
#: unpunctuated version, and the first version of the guard let it straight through (`B14`).
#: Built from code points rather than written as a literal: an en dash and an em dash
#: are indistinguishable from a hyphen in most editors, and a lint rule is right to
#: refuse them inside a string that is doing comparisons.
_STRIP = " \t\n.,!?;:'\"()[]{}<>-" + "".join(chr(c) for c in (0x2013, 0x2014, 0x2026))


def echoes_the_prompt(text: str, hint: SttHint | None) -> bool:
    """Whisper handing our own vocabulary hint back as if the caller had said it (`B14`).

    Measured, not theorised. Fed a non-speech segment with an
    `initial_prompt` of insurance terms, faster-whisper returned three of those terms,
    in the hint's own order, from audio containing no speech at all.

    **This is worse than an ordinary hallucination**, because the words it invents are
    exactly the domain terms that make a brief look credible. An agent reading a claim
    term on screen has no way to know the caller never said it. `D16` bans the model
    from producing coverage figures for the same reason; this is that hazard one layer
    down, in the transcript rather than in the brief.

    The hint stays - it genuinely helps on real speech - but a transcription that is
    *mostly* hint vocabulary is refused.
    """
    if hint is None or not hint.vocabulary:
        return False
    words = [w.strip(_STRIP) for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return False
    vocabulary = {v.strip(_STRIP) for v in hint.vocabulary}
    from_hint = sum(1 for w in words if w in vocabulary)
    return from_hint / len(words) >= 0.5


def looks_like_a_loop(text: str, *, min_repeats: int = 3) -> bool:
    """Whisper's silence-loop failure mode, caught before it reaches the agent (`D9`).

    Fed near-silence or noise, Whisper does not return empty — it returns the same short
    phrase over and over ("ครับ ครับ ครับ ครับ …"), with a perfectly ordinary confidence
    score. It is the single most recognisable way this model fails, the reference project
    hit it, and an agent's screen showing a wall of one repeated word is worse than a
    screen showing nothing: it looks like the *caller* said that.

    Deliberately here rather than in the adapter. Any engine that produces this belongs
    behind the same guard, and the transcript is what we are protecting, not one vendor.
    """
    words = [w.strip(_STRIP) for w in text.split()]
    words = [w for w in words if w]
    if len(words) < min_repeats:
        return False
    # A single token repeated for the whole segment.
    if len(set(words)) == 1:
        return True
    # Or a short phrase tiled over and over — the more common shape in practice.
    for size in (2, 3):
        if len(words) >= size * min_repeats and len(words) % size == 0:
            chunks = {tuple(words[i : i + size]) for i in range(0, len(words), size)}
            if len(chunks) == 1:
                return True
    return False


class TranscriptionStream:
    def __init__(
        self,
        *,
        call_session_id: str,
        vad: VoiceActivityDetector,
        stt: SttEngine,
        clock: Clock,
        on_turn: TurnSink,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
        settings: EndpointSettings | None = None,
        hint: SttHint | None = None,
        min_segment_rms: float = 0.002,
    ) -> None:
        self._call_session_id = call_session_id
        self._vad = vad
        self._stt = stt
        self._clock = clock
        self._on_turn = on_turn
        self._speaker_role = speaker_role
        self._hint = hint
        self._min_rms = min_segment_rms
        self._endpointer = Endpointer(
            settings=settings, frame_samples=vad.frame_samples, sample_rate=TARGET_SAMPLE_RATE
        )
        #: Samples not yet released, and the absolute index of `_buffer[0]`. A ring buffer
        #: in the sense that matters: it never grows without bound, because everything
        #: before the oldest sample any future segment could need is dropped.
        self._buffer: list[float] = []
        self._base = 0
        #: Leftovers when an incoming frame is not a whole multiple of the VAD's frame
        #: size. Silero is exact about its input length, and telephony framing has no
        #: reason to agree with it.
        self._pending: list[float] = []
        self._queue: asyncio.Queue[SpeechSegment | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._seq = 0
        self._closed = False

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start(self) -> None:
        if self._worker is not None:  # pragma: no cover - the driver starts once
            return
        self._vad.reset()
        self._endpointer.reset()
        self._worker = asyncio.create_task(self._consume())

    async def feed(self, frame: AudioFrame) -> None:
        """One normalised frame from the media gateway.

        Reframes to whatever the detector demands, because the two have no reason to
        agree: telephony hands out 20 ms packets and Silero wants 32 ms.
        """
        if self._closed:
            return
        if frame.sample_rate != TARGET_SAMPLE_RATE:
            # The gateway's whole job is to make this impossible, so if it happens the
            # gateway was bypassed and everything downstream is quietly wrong.
            raise ValueError(
                f"expected {TARGET_SAMPLE_RATE} Hz from the media gateway, "
                f"got {frame.sample_rate} - normalise before feeding a stream"
            )
        self._pending.extend(frame.samples)
        size = self._vad.frame_samples
        while len(self._pending) >= size:
            chunk = self._pending[:size]
            del self._pending[:size]
            self._buffer.extend(chunk)
            segment = self._endpointer.push(self._vad.speech_probability(chunk))
            if segment is not None:
                await self._queue.put(segment)
                self._trim(segment.end_sample)

    async def finish(self) -> None:
        """The leg ended. Emit whatever was open and drain the queue.

        Called on hang-up and on the agent accepting (`D21`), so it has to be safe twice
        and it has to keep a half-finished sentence rather than discarding it.
        """
        if self._closed:
            return
        self._closed = True
        segment = self._endpointer.flush()
        if segment is not None:
            await self._queue.put(segment)
        await self._queue.put(None)
        if self._worker is not None:
            await self._worker
            self._worker = None

    def _trim(self, consumed_to: int) -> None:
        """Drop what no future segment can reach back to.

        Keeping one `max_segment_ms` of history is enough: the furthest any close can look
        back is the start of the currently-open utterance plus its leading pad.
        """
        keep_from = max(self._base, consumed_to - int(TARGET_SAMPLE_RATE * 30))
        if keep_from > self._base:
            del self._buffer[: keep_from - self._base]
            self._base = keep_from

    def _slice(self, segment: SpeechSegment) -> list[float]:
        start = max(0, segment.start_sample - self._base)
        end = max(start, segment.end_sample - self._base)
        return self._buffer[start:end]

    async def _consume(self) -> None:
        while True:
            segment = await self._queue.get()
            if segment is None:
                return
            try:
                await self._transcribe(segment)
            except Exception:  # pragma: no cover - one bad utterance must not end the call
                # `D12`: nothing here may delay or drop a call. A segment that fails to
                # transcribe costs one sentence of the brief, and the call is unaffected.
                log.exception(
                    "transcription failed for one segment",
                    call_session_id=self._call_session_id,
                    t_start_ms=segment.t_start_ms,
                )

    async def _transcribe(self, segment: SpeechSegment) -> None:
        samples = self._slice(segment)
        if not samples:  # pragma: no cover - only if trimming raced a very long segment
            return

        # **Never hand Whisper near-silence** (`B14`). Measured on this GPU with
        # faster-whisper tiny: 155 ms for a segment with real energy in it, and
        # **8578 ms** for one second of digital silence - which it also fills with
        # invented Thai. So a VAD false positive is not merely a junk turn, it is a
        # 55x latency bomb that blocks every real utterance queued behind it.
        #
        # `D9` documented the hallucination and not the cost. This gate is two
        # multiplications per segment and it removes both.
        level = rms(samples)
        if level < self._min_rms:
            log.info(
                "skipped a silent segment before the model saw it",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                rms=round(level, 5),
            )
            return
        frames: Sequence[AudioFrame] = [
            AudioFrame(
                samples=samples,
                t_start_ms=segment.t_start_ms,
                sample_rate=TARGET_SAMPLE_RATE,
            )
        ]
        result = await self._stt.transcribe_utterance(frames, hint=self._hint)
        text = result.text.strip()
        if not text:
            return
        if looks_like_a_loop(text):
            log.info(
                "dropped a repetition-loop transcription",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                sample=text[:40],
            )
            return
        if echoes_the_prompt(text, self._hint):
            log.info(
                "dropped a transcription that is mostly our own vocabulary hint",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                sample=text[:40],
            )
            return

        self._seq += 1
        turn = TranscriptTurn(
            turn_id=ids.turn_id(),
            call_session_id=self._call_session_id,
            seq=self._seq,
            speaker_role=self._speaker_role,
            text=text,
            t_start_ms=segment.t_start_ms,
            t_end_ms=segment.t_end_ms,
            asr_confidence=result.confidence,
            engine=result.engine,
            engine_version=result.engine_version,
            is_final=not segment.forced,
        )
        await self._on_turn(turn)


__all__ = [
    "TranscriptionStream",
    "TurnSink",
    "echoes_the_prompt",
    "looks_like_a_loop",
]
