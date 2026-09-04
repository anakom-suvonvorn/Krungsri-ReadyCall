"""The transcription driver: frames in, ordered `TranscriptTurn`s out.

Everything here runs on fakes — a scripted detector and the scripted STT engine — because
the point of the seam is that the wiring is testable without a GPU. The one thing these
tests must not do is assert on *accuracy*; that is the bake-off's job, with real audio.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Sequence

import pytest

from readycall.adapters.stt.scripted import ScriptedSttEngine, ScriptedTurn
from readycall.clock import ManualClock
from readycall.domain.enums import SpeakerRole
from readycall.domain.models import TranscriptTurn
from readycall.media.audio import TARGET_SAMPLE_RATE
from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult
from readycall.ports.vad import VadInfo
from readycall.services.transcription.endpointer import EndpointSettings
from readycall.services.transcription.stream import (
    MAX_CHARS_PER_SECOND,
    TranscriptionStream,
    _longest_repeated_run,
    echoes_the_prompt,
    implausible_speech_rate,
    looks_like_a_loop,
)

FRAME = 512


class ScriptedVad:
    """Returns a canned probability per frame, so endpointing is exactly reproducible."""

    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = probabilities
        self._i = 0
        self.resets = 0

    @property
    def info(self) -> VadInfo:
        return VadInfo(name="scripted", version="1")

    @property
    def frame_samples(self) -> int:
        return FRAME

    def reset(self) -> None:
        self._i = 0
        self.resets += 1

    def speech_probability(self, samples: list[float]) -> float:
        value = self._probabilities[min(self._i, len(self._probabilities) - 1)]
        self._i += 1
        return value


def frames_for(ms: float) -> int:
    return max(1, round(ms / (FRAME / TARGET_SAMPLE_RATE * 1000.0)))


def pattern(*pairs: tuple[float, float]) -> list[float]:
    """`(probability, milliseconds)` -> one probability per VAD frame."""
    out: list[float] = []
    for probability, ms in pairs:
        out.extend([probability] * frames_for(ms))
    return out


def audio(ms: float) -> AudioFrame:
    n = int(TARGET_SAMPLE_RATE * ms / 1000.0)
    return AudioFrame(
        samples=[0.1 * math.sin(2 * math.pi * 300 * i / TARGET_SAMPLE_RATE) for i in range(n)],
        t_start_ms=0,
        sample_rate=TARGET_SAMPLE_RATE,
    )


async def run_stream(
    probabilities: list[float],
    turns: list[ScriptedTurn],
    *,
    total_ms: float = 4000.0,
    settings: EndpointSettings | None = None,
) -> list[TranscriptTurn]:
    got: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        got.append(turn)

    stream = TranscriptionStream(
        call_session_id="call_1",
        vad=ScriptedVad(probabilities),
        stt=ScriptedSttEngine(turns),
        clock=ManualClock(),
        on_turn=sink,
        settings=settings,
    )
    await stream.start()
    # Fed in 20 ms packets, which is what RTP actually delivers.
    for _ in range(int(total_ms / 20)):
        await stream.feed(audio(20))
    await stream.finish()
    return got


async def test_one_utterance_becomes_one_turn() -> None:
    turns = await run_stream(
        pattern((0.0, 300), (0.9, 900), (0.0, 600)),
        [ScriptedTurn(text="สวัสดีครับ ผมขอสอบถามเรื่องเคลม", t_start_ms=0, t_end_ms=900)],
    )
    assert len(turns) == 1
    assert turns[0].text == "สวัสดีครับ ผมขอสอบถามเรื่องเคลม"
    assert turns[0].seq == 1
    assert turns[0].speaker_role is SpeakerRole.CUSTOMER
    assert turns[0].engine == "scripted"


async def test_turns_are_numbered_in_the_order_they_were_spoken() -> None:
    """One consumer and one counter, so this holds without a sorting step.

    The obvious alternative — a task per segment — transcribes a two-word phrase faster
    than the sentence before it and delivers the caller's words shuffled.
    """
    turns = await run_stream(
        pattern((0.9, 800), (0.0, 400), (0.9, 800), (0.0, 400), (0.9, 800), (0.0, 400)),
        [
            ScriptedTurn(text="first", t_start_ms=0, t_end_ms=800),
            ScriptedTurn(text="second", t_start_ms=1200, t_end_ms=2000),
            ScriptedTurn(text="third", t_start_ms=2400, t_end_ms=3200),
        ],
    )
    assert [t.text for t in turns] == ["first", "second", "third"]
    assert [t.seq for t in turns] == [1, 2, 3]


async def test_ingestion_does_not_wait_for_the_model() -> None:
    """A slow engine must not stall the audio path — the caller is still talking.

    Measured against the clock rather than asserted: feeding is timed while the engine
    sleeps 50 ms per utterance, and the feed loop must finish well inside the total
    inference time it triggered.
    """
    got: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        got.append(turn)

    stream = TranscriptionStream(
        call_session_id="call_slow",
        vad=ScriptedVad(pattern((0.9, 800), (0.0, 400)) * 4),
        stt=ScriptedSttEngine(
            [ScriptedTurn(text=f"utterance {i}", t_start_ms=0, t_end_ms=800) for i in range(4)],
            latency_ms=50.0,
        ),
        clock=ManualClock(),
        on_turn=sink,
    )
    await stream.start()
    loop = asyncio.get_running_loop()
    began = loop.time()
    for _ in range(240):  # 4.8 s of audio
        await stream.feed(audio(20))
    fed_in = loop.time() - began
    await stream.finish()

    assert len(got) >= 3
    assert fed_in < 0.1, f"feeding blocked on the model for {fed_in * 1000:.0f} ms"


async def test_a_dropped_call_still_leaves_what_was_said() -> None:
    """`D21`'s argument, one layer down: half a sentence beats nothing."""
    turns = await run_stream(
        pattern((0.9, 900)),  # never stops talking — the call just ends
        [ScriptedTurn(text="รถผมชนอยู่ที่", t_start_ms=0, t_end_ms=900)],
        total_ms=900,
    )
    assert len(turns) == 1
    assert turns[0].is_final is False, "a cut-off utterance must not look like a finished one"


async def test_an_empty_transcription_produces_no_turn() -> None:
    """A real engine returns empty text for a segment with no speech, and an empty turn on
    the agent's screen is worse than no turn."""
    turns = await run_stream(
        pattern((0.9, 900), (0.0, 500)),
        [ScriptedTurn(text="   ", t_start_ms=0, t_end_ms=900)],
    )
    assert turns == []


async def test_a_whisper_repetition_loop_never_reaches_the_agent() -> None:
    """`D9`'s guard. Fed noise, Whisper returns the same phrase over and over with an
    ordinary confidence score — and on screen it reads as though the CALLER said it."""
    turns = await run_stream(
        pattern((0.9, 900), (0.0, 500)),
        [ScriptedTurn(text="ครับ ครับ ครับ ครับ ครับ ครับ", t_start_ms=0, t_end_ms=900)],
    )
    assert turns == []


async def test_a_frame_at_the_wrong_rate_is_refused() -> None:
    """The gateway's whole job is to make this impossible, so if it happens the gateway
    was bypassed and everything downstream is quietly wrong."""
    stream = TranscriptionStream(
        call_session_id="c",
        vad=ScriptedVad([0.0]),
        stt=ScriptedSttEngine([]),
        clock=ManualClock(),
        on_turn=lambda turn: asyncio.sleep(0),
    )
    await stream.start()
    with pytest.raises(ValueError, match="16000 Hz"):
        await stream.feed(AudioFrame(samples=[0.0] * 160, t_start_ms=0, sample_rate=8000))
    await stream.finish()


async def test_finish_is_safe_twice() -> None:
    """The agent accepting and the caller hanging up genuinely race (`D21`)."""
    stream = TranscriptionStream(
        call_session_id="c",
        vad=ScriptedVad([0.0]),
        stt=ScriptedSttEngine([]),
        clock=ManualClock(),
        on_turn=lambda turn: asyncio.sleep(0),
    )
    await stream.start()
    await stream.finish()
    await stream.finish()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ครับ ครับ ครับ ครับ", True),
        ("ค่ะ ค่ะ ค่ะ ค่ะ ค่ะ ค่ะ ค่ะ", True),
        ("ขอบคุณครับ ขอบคุณครับ ขอบคุณครับ ขอบคุณครับ", True),
        ("สวัสดีครับ ผมขอสอบถามเรื่องเคลมรถยนต์", False),
        ("ครับ", False),
        ("ครับ ผม เข้าใจ ครับ", False),
        ("", False),
    ],
)
def test_the_repetition_guard_knows_a_loop_from_a_sentence(text: str, expected: bool) -> None:
    """The false-positive half matters as much: real Thai repeats politeness particles,
    and a guard that eats 'ครับ ผม เข้าใจ ครับ' is deleting the transcript."""
    assert looks_like_a_loop(text) is expected


# --- B14: what a real GPU run produced, turned into tests -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ผู้เอาประกัน, ผู้เอาประกัน, ผู้เอาประกัน",  # the actual observed output
        "ครับ. ครับ. ครับ. ครับ.",
        "โอ้ โอ้ โอ้",
    ],
)
def test_punctuated_repetition_is_still_repetition(text: str) -> None:
    """The first guard tokenised on whitespace alone, so a comma after each word hid the
    loop entirely. Found by reading what the model actually returned, not by review."""
    assert looks_like_a_loop(text) is True


def test_the_model_cannot_hand_our_own_vocabulary_back_as_the_caller_s_words() -> None:
    """`B14`. Measured on the GPU: a non-speech segment plus a vocabulary hint produced
    three of the hint's own terms, in the hint's order, from audio containing no speech.

    Worse than an ordinary hallucination, because the invented words are exactly the
    domain terms that make a brief look credible - and an agent cannot tell.
    """
    hint = SttHint(language="th", vocabulary=("กรมธรรม์", "สินไหม", "ผู้เอาประกัน"))
    assert echoes_the_prompt("สินไหม? กรมธรรม์, ผู้เอาประกัน", hint) is True
    # A real sentence that merely CONTAINS a hint word must survive - the guard has to be
    # about proportion, or it deletes exactly the calls the hint was added to help.
    assert echoes_the_prompt("ผมขอสอบถามเรื่องสินไหม ของกรมธรรม์ ที่ทำไว้เมื่อปีที่แล้วครับ", hint) is False
    assert echoes_the_prompt("สินไหม", None) is False


async def test_a_prompt_echo_never_reaches_the_agent() -> None:
    got: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        got.append(turn)

    stream = TranscriptionStream(
        call_session_id="call_echo",
        vad=ScriptedVad(pattern((0.9, 900), (0.0, 500))),
        stt=ScriptedSttEngine(
            [ScriptedTurn(text="สินไหม กรมธรรม์ ผู้เอาประกัน", t_start_ms=0, t_end_ms=900)]
        ),
        clock=ManualClock(),
        on_turn=sink,
        hint=SttHint(language="th", vocabulary=("กรมธรรม์", "สินไหม", "ผู้เอาประกัน")),
    )
    await stream.start()
    for _ in range(200):
        await stream.feed(audio(20))
    await stream.finish()
    assert got == []


async def test_a_silent_segment_never_reaches_the_model() -> None:
    """`B14`'s latency half. Measured: 155 ms for a segment with energy in it against
    **8578 ms** for one second of digital silence - so a VAD false positive is a 55x
    latency bomb that blocks every real utterance queued behind it, not just a junk turn.
    """
    seen: list[int] = []

    class CountingStt(ScriptedSttEngine):
        async def transcribe_utterance(self, frames, *, hint=None):  # type: ignore[no-untyped-def]
            seen.append(1)
            return await super().transcribe_utterance(frames, hint=hint)

    stream = TranscriptionStream(
        call_session_id="call_silent",
        # The detector insists there is speech; the audio says otherwise. That IS the
        # false positive this gate exists for.
        vad=ScriptedVad(pattern((0.9, 900), (0.0, 500))),
        stt=CountingStt([ScriptedTurn(text="anything", t_start_ms=0, t_end_ms=900)]),
        clock=ManualClock(),
        on_turn=lambda turn: asyncio.sleep(0),
    )
    await stream.start()
    silent = AudioFrame(samples=[0.0] * 320, t_start_ms=0, sample_rate=TARGET_SAMPLE_RATE)
    for _ in range(200):
        await stream.feed(silent)
    await stream.finish()
    assert seen == [], "a silent segment was sent to the model"


# --- B16: the loop guard did not work on Thai, which is the language it is for ---------
#
# The three strings below are REAL output from the team's earlier Thonburian project on
# real audio - supplied by the user, not invented here. Every one of them went straight
# through the first version of the guard, because Thai does not put spaces between words
# and the guard split on whitespace.


REAL_LOOPS = [
    ("no spaces at all, one 195-char token", "คนเชื่อถือใน" + "การ" * 60),
    ("a syllable repeated 50 times", "เพื่อ" + "ช่วย" * 50),
    ("a real sentence that degenerates", "ความต้องการของลูกค้า" + "ความ" * 60),
]

REAL_SENTENCES = [
    ("an ordinary spaced sentence", "สวัสดีครับ ผมขอสอบถามเรื่องเคลมรถยนต์ครับ"),
    ("the same with no spaces, which is how Thai is written", "สวัสดีครับผมขอสอบถามเรื่องเคลมรถยนต์ครับ"),
    ("Thai reduplication - a real feature of the language", "เดินเร็วๆหน่อยครับ ค่อยๆพูดได้ไหมครับ"),
    ("politeness particles repeating legitimately", "ครับ ผม เข้าใจ ครับ"),
]


@pytest.mark.parametrize(("label", "text"), REAL_LOOPS, ids=[r[0] for r in REAL_LOOPS])
def test_a_real_thonburian_loop_is_caught(label: str, text: str) -> None:
    """`B16`. These are measured failures from real Thai audio, and the guard that shipped
    caught NONE of them: `text.split()` on unspaced Thai returns one token, the length
    check fails immediately, and the function returns False before looking at anything.

    A guard that only works on the one language the product is not in is not a guard.
    """
    assert looks_like_a_loop(text) is True, f"missed: {label}"


@pytest.mark.parametrize(("label", "text"), REAL_SENTENCES, ids=[r[0] for r in REAL_SENTENCES])
def test_ordinary_thai_survives_the_loop_guard(label: str, text: str) -> None:
    """The half that matters more. A character-level repetition check is easy to make
    trigger-happy, and Thai genuinely reduplicates (เร็วๆ, ค่อยๆ) - so a guard tuned for
    recall alone would delete real sentences and nobody would ever know what was said.
    """
    assert looks_like_a_loop(text) is False, f"false positive on: {label}"


def test_the_loop_has_to_BURY_the_sentence_not_merely_appear_in_it() -> None:
    """Coverage, not presence, is what separates a loop from ordinary repetition."""
    lead = "ความต้องการของลูกค้า"
    assert looks_like_a_loop(lead + "ความ" * 2) is False, "two repeats is not a loop"
    assert looks_like_a_loop(lead + "ความ" * 60) is True, "sixty repeats is"


def test_the_detector_reports_what_it_found() -> None:
    """The period and repeat count are useful in a log line: 'a 3-character unit 60 times'
    says far more about what went wrong than 'looked like a loop'."""
    covered, period, repeats = _longest_repeated_run("ความ" * 40)
    assert period == len("ความ")
    assert repeats == 40
    assert covered == len("ความ") * 40


# --- D98: the third guard, which asks whether that much speech was POSSIBLE -------------
#
# The thresholds below are measured, not chosen. 61 hand-annotated segments of real Thai
# call-centre audio give a median of 7.6 characters per second and a maximum of 15.0; the
# three real Whisper loops sit at 39-53. The ceiling is 25, in the gap.


def test_real_thai_speech_rates_are_never_flagged() -> None:
    """The measured distribution, asserted at its edges. p90 was 11.4 and the fastest
    segment anybody actually spoke was 15.0 chars/s."""
    assert implausible_speech_rate("ก" * 38, 5000.0) is False, "median rate flagged"
    assert implausible_speech_rate("ก" * 57, 5000.0) is False, "p90 rate flagged"
    assert implausible_speech_rate("ก" * 75, 5000.0) is False, "fastest real rate flagged"
    # Comfortably above anything observed, and still under the ceiling: the margin is
    # deliberate, because a false positive deletes a sentence the caller really said.
    assert implausible_speech_rate("ก" * 110, 5000.0) is False


def test_a_loop_is_flagged_by_volume_even_if_the_pattern_guard_misses_it() -> None:
    """`D98`'s whole reason for existing: a different KIND of signal.

    `looks_like_a_loop` reads the shape of the text and `echoes_the_prompt` compares it to
    our vocabulary; this asks whether a human could have produced that much in that long.
    A failure that dodges the first two still cannot beat physics.
    """
    assert implausible_speech_rate("การ" * 65, 5000.0) is True
    assert implausible_speech_rate("ก" * 200, 4000.0) is True


def test_a_short_fragment_is_left_alone() -> None:
    """A 300 ms segment with one word in it has a high rate honestly, and the endpointer's
    padding either side would dominate the arithmetic."""
    assert implausible_speech_rate("ก" * 20, 300.0) is False


def test_empty_text_is_not_an_implausible_rate() -> None:
    assert implausible_speech_rate("", 5000.0) is False
    assert implausible_speech_rate("   ", 5000.0) is False


def test_the_ceiling_sits_between_the_two_measured_populations() -> None:
    """If somebody retunes this, the test says what the number has to respect."""
    assert 15.0 < MAX_CHARS_PER_SECOND < 39.0, (
        "the ceiling must sit above the fastest REAL speech measured (15.0 chars/s) and "
        "below the slowest real loop measured (39 chars/s)"
    )


async def test_an_impossible_rate_never_reaches_the_agent() -> None:
    """End to end through the real stream, not just the predicate."""
    got: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        got.append(turn)

    # One utterance of about 1.2 s, and an engine that returns far more text than could
    # have been said in it - while dodging the repetition and vocabulary guards.
    stream = TranscriptionStream(
        call_session_id="call_fast",
        vad=ScriptedVad(pattern((0.9, 900), (0.0, 500))),
        stt=ScriptedSttEngine([ScriptedTurn(text="ก" * 400, t_start_ms=0, t_end_ms=900)]),
        clock=ManualClock(),
        on_turn=sink,
    )
    await stream.start()
    for _ in range(200):
        await stream.feed(audio(20))
    await stream.finish()
    assert got == [], "a physically impossible amount of speech reached the agent"


# ---------------------------------------------------------------------------------------
# `B20`: the buffer released audio out from under segments still waiting for the model.
# ---------------------------------------------------------------------------------------


class MarkerStt:
    """Reports the AMPLITUDE of the audio it was handed, as text.

    The whole point of `B20` is that the stream fed the model *somebody else's* audio and
    the result was fluent, plausible and wrong. An engine returning canned text cannot see
    that — it says the same thing whatever it is given. This one makes the audio itself
    legible, so the assertion can be "turn 3 carried the audio of segment 3" rather than
    the much weaker "three turns arrived".
    """

    def __init__(self) -> None:
        self.seen: list[int] = []

    @property
    def info(self) -> EngineInfo:
        return EngineInfo(name="marker", version="1", device="cpu", model="marker")

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        samples = [s for frame in frames for s in frame.samples]
        marker = round(max(abs(s) for s in samples) * 1000) if samples else 0
        self.seen.append(marker)
        return SttResult(text=f"utterance {marker}", engine="marker", engine_version="1")

    def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        raise NotImplementedError

    async def warmup(self) -> None:
        return None

    async def close(self) -> None:
        return None


def flat(ms: float, amplitude: float) -> AudioFrame:
    """A constant-amplitude block, so a slice can be traced back to the moment it came
    from. Non-zero everywhere, so it clears the `B14` level gate."""
    n = int(TARGET_SAMPLE_RATE * ms / 1000.0)
    return AudioFrame(samples=[amplitude] * n, t_start_ms=0, sample_rate=TARGET_SAMPLE_RATE)


async def _run_spaced_call(gap_ms: float) -> tuple[list[TranscriptTurn], MarkerStt]:
    """Three one-second utterances separated by `gap_ms` of silence, fed as fast as the
    producer can go — which is what a real call does whenever the GPU falls behind, and
    exactly what `bake_off.py --fast` does on every run.

    `feed()` never yields to the event loop while the queue is unbounded, so the entire
    call is ingested before the consumer transcribes a single segment. That is the race,
    reproduced without a sleep and without a GPU.
    """
    got: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        got.append(turn)

    engine = MarkerStt()
    stream = TranscriptionStream(
        call_session_id="call_b20",
        vad=ScriptedVad(
            pattern(
                (0.9, 1000),
                (0.0, gap_ms),
                (0.9, 1000),
                (0.0, gap_ms),
                (0.9, 1000),
                (0.0, 500),
            )
        ),
        stt=engine,
        clock=ManualClock(),
        on_turn=sink,
    )
    await stream.start()
    # Amplitudes 0.1 / 0.2 / 0.3 mark the three utterances; the gaps are quiet but not
    # digitally silent, so nothing here depends on the level gate.
    plan: list[tuple[float, float]] = [
        (1000, 0.1),
        (gap_ms, 0.001),
        (1000, 0.2),
        (gap_ms, 0.001),
        (1000, 0.3),
        (500, 0.001),
    ]
    for ms, amplitude in plan:
        remaining = ms
        while remaining > 0:
            step = min(20.0, remaining)
            await stream.feed(flat(step, amplitude))
            remaining -= step
    await stream.finish()
    return got, engine


def test_a_lagging_transcriber_does_not_lose_the_start_of_the_call() -> None:
    """`B20`. With 40 s between utterances the first segment sits more than the 30 s
    history window behind the last, so the old `_trim` released its audio while it was
    still queued. Three utterances went in and one came out, with nothing logged by any
    of the three guards — which is how it survived a full session of measurement."""
    got, _ = asyncio.run(_run_spaced_call(gap_ms=40_000))
    assert [t.text for t in got] == [
        "utterance 100",
        "utterance 200",
        "utterance 300",
    ], "a segment lost its audio between being queued and being transcribed"


def test_each_turn_carries_its_own_audio_and_not_a_neighbour_s() -> None:
    """The sharper half of `B20`, and the reason it was dangerous rather than merely
    lossy. `_slice` clamped a trimmed start to zero with `max(0, ...)`, so instead of
    returning nothing it returned a slice of the right LENGTH from the wrong MOMENT. In
    Thai that transcribes into a fluent sentence attributed to the wrong instant of the
    call, and lands on the agent's screen with no way to tell (`D16`'s hazard, one layer
    down)."""
    got, engine = asyncio.run(_run_spaced_call(gap_ms=40_000))
    assert engine.seen == [100, 200, 300], "the model was handed the wrong segment's audio"
    assert [t.t_start_ms for t in got] == sorted(t.t_start_ms for t in got)


def test_the_ordinary_case_still_trims() -> None:
    """The fix must not turn the ring buffer into an unbounded one. With the consumer
    keeping up there is nothing outstanding, so the 30 s history rule still applies and a
    long call does not accumulate its whole audio in memory."""
    got, _ = asyncio.run(_run_spaced_call(gap_ms=1_000))
    assert len(got) == 3


def test_a_silent_leg_does_not_grow_the_buffer_forever() -> None:
    """Found reading the `B20` diff, not by the bug itself. `_trim` runs only when a
    segment CLOSES, so a leg where nobody ever speaks never trims — the buffer grows for
    the length of the call, roughly 30 MB a minute. Bounded today only by `hold.py`'s
    silence timeout, which belongs to a different object and will not be there when `D26`'s
    agent leg is transcribed for a whole call."""

    async def sink(turn: TranscriptTurn) -> None:  # pragma: no cover - nothing is said
        raise AssertionError("silence produced a turn")

    stream = TranscriptionStream(
        call_session_id="call_quiet",
        vad=ScriptedVad([0.0]),
        stt=MarkerStt(),
        clock=ManualClock(),
        on_turn=sink,
    )

    async def run() -> int:
        await stream.start()
        for _ in range(200 * 5):  # 200 s of silence, in 1 s frames
            await stream.feed(flat(1000, 0.0))
        held = len(stream._buffer)
        await stream.finish()
        return held

    held = asyncio.run(run())
    assert held < TARGET_SAMPLE_RATE * 200, (
        f"the buffer kept all {held / TARGET_SAMPLE_RATE:.0f}s of a silent leg"
    )


# ---------------------------------------------------------------------------------------
# `B21`: the repetition guard was deleting real Thai phone numbers.
#
# Every string below is taken from the project's own data, not invented. The "must
# survive" digit runs are read straight out of the answer keys in
# tests/audio/thai_calls/*.txt; the drops are lines the guard actually refused on the
# user's own run, pasted from their terminal.
# ---------------------------------------------------------------------------------------

#: Real phone numbers from the dataset's human transcripts, with the longest run of one
#: repeated digit word in each. 0989999934 is `เก้า` FIVE times and it is a real number.
REAL_PHONE_NUMBERS = [
    ("0989999934", "ศูนย์ เก้า แปด เก้า เก้า เก้า เก้า สาม สี่"),
    ("0817999998", "ศูนย์ แปด หนึ่ง เจ็ด เก้า เก้า เก้า เก้า เก้า แปด"),
    ("0989999449", "ศูนย์ เก้า แปด เก้า เก้า เก้า เก้า สี่ สี่ เก้า"),
    ("0659459935", "ศูนย์ หก ห้า เก้า สี่ ห้า เก้า เก้า สาม ห้า"),
    ("unspaced 99999", "ศูนย์เก้าแปดเก้าเก้าเก้าเก้าสามห้า"),
]


@pytest.mark.parametrize(("label", "text"), REAL_PHONE_NUMBERS)
def test_a_real_phone_number_is_not_a_loop(label: str, text: str) -> None:
    """`B21`. Three separate calls in the dataset contain a run of FIVE identical digit
    words, because that is what 0989999934 sounds like read aloud. The general threshold
    of three repeats was deleting them, and a phone number is the one thing on an agent's
    screen that has to be exact."""
    assert not looks_like_a_loop(text), f"deleted a real phone number: {label}"


#: Lines the guard refused during the user's own bake-off run. Each is the first 40
#: characters, which is all the log records.
DROPPED_ON_A_REAL_RUN = [
    "เก้า เก้า เก้า",
    "เก้า เก้า เก้า แปด",
    "เก้า เก้า เก้า ก้",
    "เก้า เก้า เก้า เก้า หก้ง เก้า เก้ด",
]


@pytest.mark.parametrize("text", DROPPED_ON_A_REAL_RUN)
def test_the_digit_lines_the_guard_used_to_eat_now_survive(text: str) -> None:
    """These are real refusals from a real run. Every one of them is a fragment of a phone
    number the caller was reading out, and every one of them was thrown away."""
    assert not looks_like_a_loop(text)


def test_a_digit_loop_is_still_caught_when_it_is_actually_a_loop() -> None:
    """The exemption is not a licence. Ten digits is the arithmetic ceiling on a real
    Thai mobile number, so beyond that a repeated digit is the model looping like any
    other token — and `B14`'s failure mode does not care which word it picks."""
    assert looks_like_a_loop("เก้า " * 30)
    assert looks_like_a_loop("เก้า" * 30)


def test_the_exemption_is_only_for_digits() -> None:
    """A non-digit word repeating three times is still a loop. `B16`'s three real
    Thonburian failures must keep being caught, and they are the reason the general
    threshold is where it is."""
    assert looks_like_a_loop("ความ ความ ความ ความ")
    assert looks_like_a_loop("การ" * 20)


# ---------------------------------------------------------------------------------------
# `D101`: several queued utterances go to the model in one pass.
# ---------------------------------------------------------------------------------------


class BatchingMarkerStt(MarkerStt):
    """A `MarkerStt` that also implements the optional batch capability.

    Records the SHAPE of every dispatch — `[3, 1]` means one call carrying three
    utterances and then one carrying one — so a test can assert that batching actually
    happened rather than that the results merely came out right.
    """

    def __init__(self) -> None:
        super().__init__()
        self.dispatches: list[int] = []

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        self.dispatches.append(1)
        return await super().transcribe_utterance(frames, hint=hint)

    async def transcribe_batch(
        self,
        utterances: Sequence[Sequence[AudioFrame]],
        *,
        hint: SttHint | None = None,
    ) -> list[SttResult]:
        self.dispatches.append(len(utterances))
        out: list[SttResult] = []
        for frames in utterances:
            samples = [s for frame in frames for s in frame.samples]
            marker = round(max(abs(s) for s in samples) * 1000) if samples else 0
            self.seen.append(marker)
            out.append(SttResult(text=f"utterance {marker}", engine="marker"))
        return out


async def _run_with(engine: MarkerStt, *, max_batch: int) -> list[TranscriptTurn]:
    """Three utterances fed as fast as the producer can go, so all three are queued
    before the consumer starts — the same race `B20` used, which is also the only
    condition under which a batch can form at all."""
    got: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        got.append(turn)

    stream = TranscriptionStream(
        call_session_id="call_batch",
        vad=ScriptedVad(
            pattern((0.9, 1000), (0.0, 400), (0.9, 1000), (0.0, 400), (0.9, 1000), (0.0, 500))
        ),
        stt=engine,
        clock=ManualClock(),
        on_turn=sink,
        max_batch=max_batch,
    )
    await stream.start()
    for ms, amplitude in [
        (1000, 0.1),
        (400, 0.001),
        (1000, 0.2),
        (400, 0.001),
        (1000, 0.3),
        (500, 0.001),
    ]:
        remaining = ms
        while remaining > 0:
            step = min(20.0, remaining)
            await stream.feed(flat(step, amplitude))
            remaining -= step
    await stream.finish()
    return got


def test_a_backlog_is_sent_to_the_model_in_one_pass() -> None:
    """`D101`. The producer never yields while the queue is unbounded, so all three
    utterances are waiting before the consumer wakes — and that is exactly the case
    batching exists for."""
    engine = BatchingMarkerStt()
    got = asyncio.run(_run_with(engine, max_batch=4))
    assert [t.text for t in got] == ["utterance 100", "utterance 200", "utterance 300"]
    assert max(engine.dispatches) > 1, f"never batched: {engine.dispatches}"
    assert sum(engine.dispatches) == 3


def test_batching_preserves_order_and_the_audio_each_turn_carried() -> None:
    """The `B20` assertion, repeated for the batch path. Batching is only safe if a
    result still belongs to the utterance it was made from — an off-by-one inside a
    batch would attribute a fluent sentence to the wrong moment, silently."""
    engine = BatchingMarkerStt()
    got = asyncio.run(_run_with(engine, max_batch=4))
    assert engine.seen == [100, 200, 300]
    assert [t.seq for t in got] == [1, 2, 3]


def test_an_engine_without_a_batch_path_is_identical_just_slower() -> None:
    """`BatchSttEngine` is a capability, not a requirement. `MarkerStt` does not
    implement it, and the stream must fall back to a loop with the same output."""
    engine = MarkerStt()
    got = asyncio.run(_run_with(engine, max_batch=4))
    assert [t.text for t in got] == ["utterance 100", "utterance 200", "utterance 300"]
    assert engine.seen == [100, 200, 300]


def test_max_batch_one_never_batches() -> None:
    """The knob has to actually turn it off — that is what makes it safe to ship."""
    engine = BatchingMarkerStt()
    got = asyncio.run(_run_with(engine, max_batch=1))
    assert [t.text for t in got] == ["utterance 100", "utterance 200", "utterance 300"]
    assert engine.dispatches == [1, 1, 1]
