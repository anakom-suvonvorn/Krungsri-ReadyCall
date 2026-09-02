"""Deciding where an utterance starts and ends. No model, no I/O, no clock.

The third machine in this codebase written this way, after `ivr/machine.py` and
`intake/hold.py`, and for the reason `B7` cost a session: everything here happens because
**time passed** — a pause became long enough to be an ending — and a rule that only fires
inside a live audio stream is a rule nobody can test. Probabilities go in, segments come
out, and the whole of `D9`'s inherited tuning is exercised against a list of floats.

**The parameters are inherited, not invented** (`D9`). They come from the team's earlier
Thai scam-detection project, where they were arrived at against real Thai telephone audio:

**threshold 0.65** - higher than Silero's own 0.5 default. Telephone lines carry hum,
keypad tones and traffic noise, and a false *start* costs a whole spurious transcription.

**min speech 500 ms** - below this it is a cough, a door, or a single particle that
Whisper will hallucinate a whole sentence around.

**min silence 100 ms** - Thai is not heavily stressed and speakers pause mid-clause;
ending on a longer gap would cut sentences in half.

**pad before ~120 ms** - the most load-bearing number here. Whisper clips the first
syllable without it, and in Thai that syllable frequently carries the tone that
distinguishes the word.

**pad after ~60 ms** - final consonants and the tail of a falling tone.

The asymmetry is deliberate and worth keeping: speech onset is abrupt and the detector is
always slightly late to it, while offset is gradual and it is slightly early.

**Speaking and being *finished* are different questions.** This emits a segment on every
pause past `min_silence_ms`, which is a *phrase*. Whether the caller has stopped talking
altogether is `INTAKE_SILENCE_TIMEOUT_S` (6 s) and belongs to `hold.py`, which already owns
it. Conflating them would either chop the recording at the first breath or hand Whisper
thirty seconds of audio to pad to a minute.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from readycall.media.audio import TARGET_SAMPLE_RATE


class SpeechState(StrEnum):
    SILENCE = "silence"  # nothing yet, or the last phrase is closed
    MAYBE_SPEECH = "maybe_speech"  # over threshold, not yet past min_speech_ms
    SPEECH = "speech"  # a real utterance is open
    MAYBE_DONE = "maybe_done"  # under threshold, not yet past min_silence_ms


@dataclass(frozen=True, slots=True)
class EndpointSettings:
    """`D9`'s numbers, in one place, tunable per deployment."""

    threshold: float = 0.65
    min_speech_ms: float = 500.0
    min_silence_ms: float = 100.0
    pad_before_ms: float = 120.0
    pad_after_ms: float = 60.0
    #: Hard stop for a caller who does not pause. Whisper pads every chunk to 30 s, so a
    #: segment longer than this buys nothing and costs latency the budget cannot afford
    #: (`ARCHITECTURE` §15: utterance end -> turn in under 1.5 s).
    max_segment_ms: float = 20000.0


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    """One endpointed utterance, in sample offsets from the start of the stream.

    Offsets rather than the audio itself: the ring buffer owns the samples, and copying a
    segment out of it is the caller's decision, made once, at the moment it is dispatched.
    """

    start_sample: int
    end_sample: int
    t_start_ms: int
    t_end_ms: int
    #: True when `max_segment_ms` ended it rather than a pause — the caller was still
    #: talking, so the next segment continues the same thought and a brief built from it
    #: should not read as a finished sentence.
    forced: bool = False

    @property
    def duration_ms(self) -> float:
        return self.t_end_ms - self.t_start_ms


@dataclass
class _Open:
    started_at_sample: int
    voiced_ms: float = 0.0
    silence_ms: float = 0.0


class Endpointer:
    """Feed it one probability per frame, in order. It hands back finished segments."""

    def __init__(
        self,
        *,
        settings: EndpointSettings | None = None,
        frame_samples: int = 512,
        sample_rate: int = TARGET_SAMPLE_RATE,
    ) -> None:
        self._s = settings or EndpointSettings()
        self._frame_samples = frame_samples
        self._sample_rate = sample_rate
        self._frame_ms = frame_samples / sample_rate * 1000.0
        self._state = SpeechState.SILENCE
        self._cursor = 0  # samples consumed so far
        self._open: _Open | None = None

    @property
    def state(self) -> SpeechState:
        return self._state

    @property
    def in_speech(self) -> bool:
        return self._state in (SpeechState.SPEECH, SpeechState.MAYBE_DONE)

    def reset(self) -> None:
        self._state = SpeechState.SILENCE
        self._cursor = 0
        self._open = None

    def push(self, probability: float) -> SpeechSegment | None:
        """One frame's worth. Returns a segment when this frame *closed* one."""
        frame_start = self._cursor
        self._cursor += self._frame_samples
        voiced = probability >= self._s.threshold

        if self._state is SpeechState.SILENCE:
            if voiced:
                self._state = SpeechState.MAYBE_SPEECH
                self._open = _Open(started_at_sample=frame_start, voiced_ms=self._frame_ms)
            return None

        assert self._open is not None
        if self._state is SpeechState.MAYBE_SPEECH:
            if not voiced:
                # Too short to be speech. A cough, a keypad tone, a car horn — and
                # crucially NOT something to hand Whisper, which will confabulate a
                # sentence out of half a second of noise (`D9`'s repetition guard is for
                # the same failure one layer down).
                self._state = SpeechState.SILENCE
                self._open = None
                return None
            self._open.voiced_ms += self._frame_ms
            if self._open.voiced_ms >= self._s.min_speech_ms:
                self._state = SpeechState.SPEECH
            return self._force_if_too_long()

        if self._state is SpeechState.SPEECH:
            if voiced:
                self._open.voiced_ms += self._frame_ms
                return self._force_if_too_long()
            self._state = SpeechState.MAYBE_DONE
            self._open.silence_ms = self._frame_ms
            return self._force_if_too_long()

        # MAYBE_DONE
        if voiced:
            # A pause inside a sentence, which in Thai is entirely normal mid-clause.
            self._state = SpeechState.SPEECH
            self._open.silence_ms = 0.0
            self._open.voiced_ms += self._frame_ms
            return self._force_if_too_long()
        self._open.silence_ms += self._frame_ms
        if self._open.silence_ms >= self._s.min_silence_ms:
            return self._close(end_sample=self._cursor, forced=False)
        return self._force_if_too_long()

    def flush(self) -> SpeechSegment | None:
        """The stream ended. Emit whatever was open rather than losing it.

        This is what makes a dropped call still leave a usable transcript: the caller was
        mid-sentence, and half a sentence is worth much more to the agent than nothing.
        """
        if self._open is None or self._state is SpeechState.MAYBE_SPEECH:
            self._state = SpeechState.SILENCE
            self._open = None
            return None
        return self._close(end_sample=self._cursor, forced=True)

    def _force_if_too_long(self) -> SpeechSegment | None:
        assert self._open is not None
        span = self._cursor - self._open.started_at_sample
        if span / self._sample_rate * 1000.0 >= self._s.max_segment_ms:
            return self._close(end_sample=self._cursor, forced=True)
        return None

    def _close(self, *, end_sample: int, forced: bool) -> SpeechSegment:
        assert self._open is not None
        pad_before = int(self._s.pad_before_ms / 1000.0 * self._sample_rate)
        pad_after = int(self._s.pad_after_ms / 1000.0 * self._sample_rate)
        start = max(0, self._open.started_at_sample - pad_before)
        end = end_sample + pad_after
        self._state = SpeechState.SILENCE
        self._open = None
        return SpeechSegment(
            start_sample=start,
            end_sample=end,
            t_start_ms=int(start / self._sample_rate * 1000.0),
            t_end_ms=int(end / self._sample_rate * 1000.0),
            forced=forced,
        )


__all__ = [
    "EndpointSettings",
    "Endpointer",
    "SpeechSegment",
    "SpeechState",
]
