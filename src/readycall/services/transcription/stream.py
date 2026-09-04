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

**Three guards sit between the model and the agent** (`B14`, `B16`, `D98`), and they are
deliberately of different kinds: one reads the shape of the text, one compares it to our
own vocabulary hint, and one asks whether that much speech was physically possible in
the time available. A failure that dodges one rarely dodges all three.

**Per-leg, not per-call** (`D26`). Each leg is forked separately, so the speaker label is
structural rather than inferred, and there is no diarisation model to be wrong.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Sequence

from readycall import ids
from readycall.clock import Clock
from readycall.domain.enums import SpeakerRole
from readycall.domain.models import TranscriptTurn
from readycall.logging import get_logger
from readycall.media.audio import TARGET_SAMPLE_RATE, rms
from readycall.ports.stt import AudioFrame, BatchSttEngine, SttEngine, SttHint, SttResult
from readycall.ports.vad import VoiceActivityDetector
from readycall.services.transcription.endpointer import (
    Endpointer,
    EndpointSettings,
    SpeechSegment,
)

log = get_logger(__name__)

TurnSink = Callable[[TranscriptTurn], Awaitable[None]]

#: How far the transcriber may fall behind the live audio before the oldest queued
#: segment's audio is released anyway (`B20`). Two minutes is far past any recoverable
#: state — the intake recording itself caps at `INTAKE_MAX_DURATION_S` — so reaching this
#: means the model has effectively stopped, and the choice is between losing the oldest
#: sentence and growing the buffer until the process dies. It is not a latency budget
#: (`ARCHITECTURE` §15 owns that at 1.5 s); it is the point past which we stop pretending.
_MAX_BACKLOG_SAMPLES = TARGET_SAMPLE_RATE * 120

#: When nothing has closed a segment for this long, trim anyway. `_trim` otherwise runs
#: only on a segment close, so a leg where nobody speaks never trims at all. Comfortably
#: above the 30 s history window and the backlog cap, so this only ever fires on a path
#: that would otherwise grow without limit.
_TRIM_WHEN_IDLE_SAMPLES = TARGET_SAMPLE_RATE * 150

#: How many queued utterances may be sent to the model in one GPU pass (`D101`).
#:
#: **This only ever fires when the model is already behind**, because a stream that is
#: keeping up has at most one segment waiting. So it costs nothing in the common case and
#: helps exactly in the case that produced the 23-59 s latencies.
#:
#: 4 matches the team's earlier Thai project, which reached its speed this way on this
#: class of hardware. It is capped rather than unbounded because each clip in a batch is
#: still padded to Whisper's 30 s window, so a batch of twenty would ask a 4 GiB card for
#: twenty windows of activations at once and fail in the least helpful way possible.
MAX_BATCH = 4


#: Punctuation Whisper sprinkles into hallucinated Thai. Stripped before the repetition
#: check, because "ผู้เอาประกัน, ผู้เอาประกัน" is the same failure as the
#: unpunctuated version, and the first version of the guard let it straight through (`B14`).
#: Built from code points rather than written as a literal: an en dash and an em dash
#: are indistinguishable from a hyphen in most editors, and a lint rule is right to
#: refuse them inside a string that is doing comparisons.
_STRIP = " \t\n.,!?;:'\"()[]{}<>-" + "".join(chr(c) for c in (0x2013, 0x2014, 0x2026))


#: The Thai digit words, spoken one at a time — how anyone reads out a phone number.
#:
#: **This is a fact about the language, not about insurance**, so it belongs here rather
#: than in `config/` — the same reasoning that puts `_STRIP` above and the "Thai has no
#: spaces" rule in this file. `D28` bans *domain* literals in `services/`; a numeral is
#: not one. `โท` is included because Thai speakers say it for 2 specifically when reading
#: digits aloud, to avoid confusion with `สาม`.
_THAI_DIGIT_WORDS = frozenset(
    {
        "ศูนย์",  # 0
        "หนึ่ง",  # 1
        "เอ็ด",  # 1, in compounds
        "สอง",  # 2
        "โท",  # 2, when read as a digit
        "สาม",  # 3
        "สี่",  # 4
        "ห้า",  # 5
        "หก",  # 6
        "เจ็ด",  # 7
        "แปด",  # 8
        "เก้า",  # 9
    }
)

#: How many times the SAME digit word may repeat before it stops being a phone number
#: and starts being a loop (`B21`).
#:
#: **Measured against the dataset's own answer keys.** Real Thai phone numbers in the 12
#: prepared calls contain runs of up to **five** identical digit words — `0989999934` is
#: `เก้า` five times in a row, and there are three separate calls with a run of five. The
#: general `min_repeats` of 3 was therefore deleting real customer phone numbers, which is
#: close to the worst single thing this system could do to a transcript: the number is the
#: one item on the agent's screen that has to be exact.
#:
#: 10 is deliberately generous. A Thai mobile number is 10 digits, so ten identical
#: digits is the arithmetic ceiling on a legitimate run; a Whisper loop repeats dozens of
#: times, so nothing that matters is given up by being lenient here.
DIGIT_MIN_REPEATS = 10


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


#: Characters of Thai per second of speech, above which nobody is actually talking.
#:
#: **Measured, not guessed** (`D98`), against 61 hand-annotated segments of real Thai
#: call-centre audio from the dataset:
#:
#:     real speech   median 7.6   p90 11.4   max observed 15.0
#:     the three real Whisper loops              39 - 53
#:
#: 25 sits in the gap: 1.7x above the fastest real speech anyone recorded, and well below
#: the slowest loop. It is generous on purpose — a false positive here deletes a sentence
#: the caller actually said, which is worse than passing a loop to the pattern guards.
MAX_CHARS_PER_SECOND = 25.0


def implausible_speech_rate(
    text: str,
    duration_ms: float,
    *,
    max_chars_per_second: float = MAX_CHARS_PER_SECOND,
    min_duration_ms: float = 700.0,
) -> bool:
    """Did the model return more text than a human could physically have said (`D98`)?

    The third kind of guard, and deliberately unlike the other two. `looks_like_a_loop`
    and `echoes_the_prompt` both inspect the *shape of the text*; this one asks whether the
    **amount** of it is possible at all, given how long the caller was speaking. A loop that
    happens to avoid both patterns still cannot beat physics.

    **Why this rather than timing the model**, which was the original proposal. Watching how
    long a transcription took would work — a looping decode is slow because it emits more
    tokens — but it needs a per-model, per-GPU baseline this project would have almost no
    samples for on demo day (`D85`'s shrinkage problem in a new place), and it measures the
    symptom one step further from the cause. Output length per second of speech needs no
    calibration, no history, and no clock, and it is the quantity the timing was standing in
    for.

    `min_duration_ms` keeps very short segments out of it: a 200 ms fragment with one word
    in it produces a high rate honestly, and the padding either side would dominate.
    """
    stripped = "".join(ch for ch in text if not ch.isspace())
    if not stripped or duration_ms < min_duration_ms:
        return False
    return len(stripped) / (duration_ms / 1000.0) > max_chars_per_second


def _longest_run(text: str, *, max_period: int = 12) -> tuple[int, int, int, int]:
    """`(characters covered, period, repeat count, start index)` for the longest run.

    The start index is what lets a caller recover the repeating **unit** itself, which
    `B21` needs: a run of `เก้า` is somebody reading a phone number and a run of `การ` is
    the model having stopped transcribing, and they cannot be told apart by counting.

    O(n^2) in the worst case and that is fine: these strings are one utterance long, and
    the alternative (a suffix automaton) is a lot of machinery to save microseconds on a
    path that already spent 150 ms in a neural network.
    """
    n = len(text)
    best = (0, 0, 0, 0)
    for period in range(1, min(max_period, n // 2) + 1):
        i = 0
        while i + period <= n:
            unit = text[i : i + period]
            repeats = 1
            j = i + period
            while j + period <= n and text[j : j + period] == unit:
                repeats += 1
                j += period
            if repeats >= 2 and repeats * period > best[0]:
                best = (repeats * period, period, repeats, i)
            i = j if repeats >= 2 else i + 1
    return best


def _longest_repeated_run(text: str, *, max_period: int = 12) -> tuple[int, int, int]:
    """`(characters covered, period, repeat count)`. See `_longest_run`."""
    covered, period, repeats, _start = _longest_run(text, max_period=max_period)
    return covered, period, repeats


def _repeats_needed(unit: str, default: int) -> int:
    """How many repeats of `unit` it takes before this is a loop rather than speech.

    A digit gets a far higher bar (`B21`). Somebody reading `0989999934` aloud says
    `เก้า` five times in a row and means it, and the transcript of a phone number is the
    one thing on an agent's screen that has to be exact. Everything else keeps the general
    threshold, which `B16` set against three real Thonburian loops.
    """
    return DIGIT_MIN_REPEATS if unit.strip(_STRIP) in _THAI_DIGIT_WORDS else default


def looks_like_a_loop(
    text: str,
    *,
    min_repeats: int = 3,
    min_coverage: float = 0.45,
) -> bool:
    """Whisper's repetition failure, caught before it reaches the agent (`D9`, `B14`, `B16`).

    Fed noise, near-silence, or simply a hard patch of audio, Whisper stops transcribing and
    starts looping — with a perfectly ordinary confidence score. An agent's screen showing a
    wall of one repeated word is worse than a blank one: it reads as though the *caller*
    said that.

    **Two detectors, because Thai broke the first one** (`B16`). The original split on
    whitespace, which works only when the model happens to punctuate its own nonsense.
    Real Thonburian output on real audio does not:

        "คนเชื่อถือในการการการการการการ…"      one token, 195 characters, no spaces
        "เพื่อช่วยช่วยช่วยช่วยช่วย…"              one token
        "ความต้องการของลูกค้าความความความ…"     one token

    **Thai does not put spaces between words.** A whitespace-token guard is therefore close
    to useless on the language this system exists for, and the three examples above — real
    output from the team's earlier project, not hypotheses — all sailed straight through it.
    So the character-level check is the primary one and the token check is the fallback for
    output that does happen to be spaced.

    `min_coverage` is what keeps ordinary Thai safe. Reduplication is a real feature of the
    language (เร็วๆ, ค่อยๆ) and short repeats are normal; a *loop* buries the sentence,
    covering nearly all of it.
    """
    stripped = "".join(ch for ch in text if not ch.isspace())
    if len(stripped) >= 12:
        covered, period, repeats, start = _longest_run(stripped)
        unit = stripped[start : start + period]
        if repeats >= _repeats_needed(unit, min_repeats) and (
            covered / len(stripped) >= min_coverage
        ):
            return True

    # The spaced case: the model punctuated its own loop, which is what it did on the
    # silence test that produced `B14`.
    words = [w.strip(_STRIP) for w in text.split()]
    words = [w for w in words if w]
    if len(words) < min_repeats:
        return False
    if len(set(words)) == 1:
        return len(words) >= _repeats_needed(words[0], min_repeats)
    for size in (2, 3):
        if len(words) >= size * min_repeats and len(words) % size == 0:
            chunks = {tuple(words[i : i + size]) for i in range(0, len(words), size)}
            if len(chunks) == 1:
                unit_words = words[:size]
                needed = max(_repeats_needed(w, min_repeats) for w in unit_words)
                if len(words) // size >= needed:
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
        max_batch: int = MAX_BATCH,
    ) -> None:
        self._call_session_id = call_session_id
        self._vad = vad
        self._stt = stt
        self._clock = clock
        self._on_turn = on_turn
        self._speaker_role = speaker_role
        self._hint = hint
        self._min_rms = min_segment_rms
        self._max_batch = max(1, max_batch)
        self._endpointer = Endpointer(
            settings=settings, frame_samples=vad.frame_samples, sample_rate=TARGET_SAMPLE_RATE
        )
        #: Samples not yet released, and the absolute index of `_buffer[0]`. A ring buffer
        #: in the sense that matters: it never grows without bound, because everything
        #: before the oldest sample any future segment could need is dropped.
        #:
        #: **"Any future segment" includes the ones already queued** (`B20`). The first
        #: version trimmed to 30 s behind the newest segment the moment it was queued,
        #: which is correct only while the consumer keeps up. When it lagged, the audio
        #: was released out from under segments still waiting to be transcribed.
        self._buffer: list[float] = []
        self._base = 0
        #: The start sample of every queued-but-not-yet-transcribed segment, oldest first.
        #: One consumer draining in FIFO order (see `_consume`) means the head of this is
        #: the oldest sample anything still needs, and nothing below it can be reached.
        self._awaiting: deque[int] = deque()
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
                self._awaiting.append(segment.start_sample)
                await self._queue.put(segment)
                self._trim(segment.end_sample)
            elif len(self._buffer) > _TRIM_WHEN_IDLE_SAMPLES:
                # Trimming only on a segment CLOSE leaves one path that never trims at
                # all: a leg where nobody speaks. No segment closes, so `_trim` is never
                # called, and the buffer grows for the length of the call — about 30 MB a
                # minute. Bounded in the intake path only because `hold.py` ends a
                # recording after `INTAKE_SILENCE_TIMEOUT_S`, which is a bound belonging to
                # a different object and will not be there when `D26`'s agent leg is
                # transcribed for a whole call. Found reading the `B20` diff.
                self._trim(self._base + len(self._buffer))

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
            self._awaiting.append(segment.start_sample)
            await self._queue.put(segment)
        await self._queue.put(None)
        if self._worker is not None:
            await self._worker
            self._worker = None

    def _trim(self, consumed_to: int) -> None:
        """Drop what nothing can reach back to any more.

        Two claims on the history, and the floor is the older of them (`B20`):

        * the **open utterance**, which can look back one `max_segment_ms` plus its
          leading pad — the 30 s window this always kept;
        * every **queued segment the consumer has not reached yet**, whose audio is still
          owed to the model. That claim is the one the first version did not have, and
          under an unpaced feed it released a caller's first four sentences before Whisper
          was ever shown them.

        The backlog is bounded rather than trusted: if the consumer falls further behind
        than `_MAX_BACKLOG_SAMPLES`, the oldest claims are abandoned **loudly** and their
        segments will report missing audio in `_slice`. Growing without limit is the other
        way to lose a call, and `D12` forbids applying backpressure to ingestion — the
        caller keeps talking whatever the GPU is doing.
        """
        history_floor = consumed_to - int(TARGET_SAMPLE_RATE * 30)
        while self._awaiting and consumed_to - self._awaiting[0] > _MAX_BACKLOG_SAMPLES:
            abandoned = self._awaiting.popleft()
            log.warning(
                "transcription backlog exceeded - abandoning the audio of a queued segment",
                call_session_id=self._call_session_id,
                segment_start_ms=int(abandoned / TARGET_SAMPLE_RATE * 1000),
                backlog_s=round((consumed_to - abandoned) / TARGET_SAMPLE_RATE, 1),
            )
        floor = min(self._awaiting[0], history_floor) if self._awaiting else history_floor
        keep_from = max(self._base, floor)
        if keep_from > self._base:
            del self._buffer[: keep_from - self._base]
            self._base = keep_from

    def _slice(self, segment: SpeechSegment) -> list[float]:
        """The segment's own audio, or nothing at all — never somebody else's.

        The first version clamped with `max(0, start - base)`. When the audio had been
        trimmed away that did not return empty, it returned **`_buffer[0:n]`** — a slice of
        roughly the right length taken from the wrong moment in the call, which transcribes
        into perfectly plausible Thai attributed to the wrong instant (`B20`). A missing
        sentence is a gap; a confidently wrong one is on the agent's screen.
        """
        if segment.start_sample < self._base:
            return []
        start = segment.start_sample - self._base
        end = max(start, segment.end_sample - self._base)
        return self._buffer[start:end]

    async def _consume(self) -> None:
        while True:
            segment = await self._queue.get()
            if segment is None:
                return
            batch = [segment]
            # Take whatever else is ALREADY waiting — never wait for more to arrive
            # (`D101`). Waiting would trade latency the stream does not own for
            # throughput it does not always need; taking what is there is free.
            while len(batch) < self._max_batch:
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    # The sentinel must stay last. Put it back and stop batching.
                    self._queue.put_nowait(None)
                    break
                batch.append(nxt)
            try:
                await self._transcribe_batch(batch)
            except Exception:  # pragma: no cover - one bad utterance must not end the call
                # `D12`: nothing here may delay or drop a call. A segment that fails to
                # transcribe costs one sentence of the brief, and the call is unaffected.
                log.exception(
                    "transcription failed",
                    call_session_id=self._call_session_id,
                    t_start_ms=batch[0].t_start_ms,
                    segments=len(batch),
                )
            finally:
                # Release each segment's claim on the buffer whatever happened to it,
                # including a failure — a claim that outlives its segment pins the audio
                # of the whole call and turns one bad utterance into a memory leak.
                for _ in batch:
                    if self._awaiting:
                        self._awaiting.popleft()

    async def _transcribe_batch(self, batch: list[SpeechSegment]) -> None:
        """Prepare, dispatch, publish — in that order, and in input order throughout.

        The three stages are separated because only the middle one can be batched. The
        level gate (`B14`) has to run per segment BEFORE the model, and the three guards
        (`B14`, `B16`, `D98`, `B21`) have to run per segment AFTER it. Only the inference
        in between is a single call.
        """
        prepared: list[tuple[SpeechSegment, Sequence[AudioFrame], float]] = []
        for segment in batch:
            ready = self._prepare(segment)
            if ready is not None:
                prepared.append((segment, *ready))
        if not prepared:
            return

        results = await self._dispatch([frames for _seg, frames, _lvl in prepared])
        for (segment, _frames, level), result in zip(prepared, results, strict=True):
            await self._publish(segment, result, level)

    async def _dispatch(self, utterances: list[Sequence[AudioFrame]]) -> list[SttResult]:
        """One GPU pass when the engine can do it, a plain loop when it cannot.

        `BatchSttEngine` is a capability rather than a requirement (`D101`), so an engine
        without a batch path is slower here and identical in every other way — including,
        importantly, in what it returns.
        """
        if len(utterances) > 1 and isinstance(self._stt, BatchSttEngine):
            return await self._stt.transcribe_batch(utterances, hint=self._hint)
        return [
            await self._stt.transcribe_utterance(frames, hint=self._hint) for frames in utterances
        ]

    def _prepare(self, segment: SpeechSegment) -> tuple[Sequence[AudioFrame], float] | None:
        """The audio for one segment, or `None` if it must not reach the model at all.

        Everything here happens BEFORE any inference, which is the point: `B14`'s level
        gate exists because one second of digital silence costs 8.6 s and comes back with
        invented Thai, and a gate that ran after the model would save neither.
        """
        samples = self._slice(segment)
        if not samples:
            # Loud on purpose (`B20`). This returned silently for a week, and it is the
            # path that swallowed four of a caller's six sentences with no drop recorded
            # by any of the three guards and nothing in the log to look at.
            log.warning(
                "no audio left for a segment - it was trimmed before the model reached it",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                t_end_ms=segment.t_end_ms,
            )
            return None

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
            return None
        frames: Sequence[AudioFrame] = [
            AudioFrame(
                samples=samples,
                t_start_ms=segment.t_start_ms,
                sample_rate=TARGET_SAMPLE_RATE,
            )
        ]
        return frames, level

    async def _publish(self, segment: SpeechSegment, result: SttResult, level: float) -> None:
        """The four guards, then the turn. All per segment, whether or not it was batched.

        Batching must not weaken a single check — the guards are the reason a hallucination
        does not reach an agent's screen, and a failure that arrived alongside three good
        utterances is exactly as dangerous as one that arrived alone.
        """
        text = result.text.strip()
        if not text:
            # Also loud (`B20`). An empty result is a legitimate outcome — the detector
            # fired on a door slam — but it is indistinguishable from a swallowed sentence
            # unless it says so, and telling those two apart is most of what a bad CER
            # investigation consists of.
            log.info(
                "the model returned nothing for a segment",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                duration_ms=int(segment.duration_ms),
                rms=round(level, 5),
            )
            return
        if looks_like_a_loop(text):
            log.info(
                "dropped a repetition-loop transcription",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                sample=text[:40],
            )
            return
        rate_ms = float(segment.t_end_ms - segment.t_start_ms)
        if implausible_speech_rate(text, rate_ms):
            log.info(
                "dropped a transcription nobody could have said that fast",
                call_session_id=self._call_session_id,
                t_start_ms=segment.t_start_ms,
                chars=len("".join(text.split())),
                seconds=round(rate_ms / 1000.0, 2),
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
    "MAX_CHARS_PER_SECOND",
    "TranscriptionStream",
    "TurnSink",
    "echoes_the_prompt",
    "implausible_speech_rate",
    "looks_like_a_loop",
]
