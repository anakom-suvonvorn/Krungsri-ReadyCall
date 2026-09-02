"""Endpointing: where an utterance starts and stops, driven by nothing but a list of floats.

The whole point of `endpointer.py` having no model in it is that `D9`'s inherited
constants become assertable. These tests are the reason those numbers cannot quietly rot
into something that sounds fine on one recording.
"""

from __future__ import annotations

import pytest

from readycall.services.transcription.endpointer import (
    Endpointer,
    EndpointSettings,
    SpeechState,
)

FRAME = 512
RATE = 16000
FRAME_MS = FRAME / RATE * 1000.0  # 32 ms


def drive(ep: Endpointer, pattern: list[tuple[float, int]]) -> list:
    """`(probability, how many frames)` pairs in, closed segments out."""
    out = []
    for probability, count in pattern:
        for _ in range(count):
            segment = ep.push(probability)
            if segment is not None:
                out.append(segment)
    return out


def frames_for(ms: float) -> int:
    return max(1, round(ms / FRAME_MS))


def test_a_short_noise_burst_is_not_an_utterance() -> None:
    """`D9`'s 500 ms minimum. Below it, this is a cough or a door — and handing it to
    Whisper is how you get a hallucinated sentence with an ordinary confidence score."""
    ep = Endpointer(frame_samples=FRAME)
    segments = drive(ep, [(0.0, 5), (0.9, frames_for(200)), (0.0, 20)])
    assert segments == []
    assert ep.state is SpeechState.SILENCE


def test_speech_past_the_minimum_produces_one_segment() -> None:
    ep = Endpointer(frame_samples=FRAME)
    segments = drive(ep, [(0.0, 5), (0.9, frames_for(900)), (0.0, frames_for(300))])
    assert len(segments) == 1
    assert segments[0].duration_ms > 500
    assert not segments[0].forced


def test_a_pause_mid_sentence_does_not_split_it() -> None:
    """Thai speakers pause mid-clause. Ending on a short gap cuts sentences in half."""
    ep = Endpointer(frame_samples=FRAME)
    segments = drive(
        ep,
        [
            (0.9, frames_for(700)),
            (0.0, 1),  # 32 ms — under the 100 ms minimum silence
            (0.9, frames_for(700)),
            (0.0, frames_for(400)),
        ],
    )
    assert len(segments) == 1, "one sentence with a breath in it is one sentence"


def test_the_threshold_is_065_not_silero_s_own_default() -> None:
    """A telephone line hums, and a false START costs a whole spurious transcription."""
    ep = Endpointer(frame_samples=FRAME)
    assert drive(ep, [(0.55, frames_for(2000)), (0.0, frames_for(400))]) == []
    assert drive(ep, [(0.70, frames_for(900)), (0.0, frames_for(400))]) != []


def test_the_leading_pad_is_applied_and_is_the_reason_first_syllables_survive() -> None:
    """The single most load-bearing constant in `D9`. In Thai the first syllable often
    carries the tone that distinguishes the word, and Whisper clips it without this."""
    settings = EndpointSettings(pad_before_ms=120.0, pad_after_ms=60.0)
    ep = Endpointer(settings=settings, frame_samples=FRAME)

    quiet = frames_for(500)
    segments = drive(ep, [(0.0, quiet), (0.9, frames_for(900)), (0.0, frames_for(300))])

    speech_began_at = quiet * FRAME
    assert segments[0].start_sample < speech_began_at, "no leading pad was applied"
    pad = (speech_began_at - segments[0].start_sample) / RATE * 1000.0
    assert pad == pytest.approx(120.0, abs=FRAME_MS)


def test_the_pad_never_runs_off_the_front_of_the_stream() -> None:
    """Someone talking from the first frame is ordinary — they were mid-sentence when the
    recording opened."""
    ep = Endpointer(frame_samples=FRAME)
    segments = drive(ep, [(0.9, frames_for(900)), (0.0, frames_for(300))])
    assert segments[0].start_sample == 0


def test_a_caller_who_never_pauses_is_cut_at_the_ceiling() -> None:
    """Whisper pads every chunk to 30 s, so an unbounded segment costs latency the budget
    (`ARCHITECTURE` §15) cannot spend and buys nothing."""
    settings = EndpointSettings(max_segment_ms=2000.0)
    ep = Endpointer(settings=settings, frame_samples=FRAME)
    segments = drive(ep, [(0.9, frames_for(5000))])

    assert len(segments) >= 2
    assert all(s.forced for s in segments)
    assert all(s.duration_ms <= 2400 for s in segments)


def test_forced_segments_are_marked_not_final() -> None:
    """`forced` travels to `TranscriptTurn.is_final`, so the brief does not present half a
    thought as a finished sentence."""
    ep = Endpointer(settings=EndpointSettings(max_segment_ms=1000.0), frame_samples=FRAME)
    segments = drive(ep, [(0.9, frames_for(3000))])
    assert segments[0].forced is True


def test_flush_keeps_a_half_finished_sentence() -> None:
    """The dropped-call case. Half a sentence is worth far more to the agent than nothing,
    which is the same argument `D21` makes for a partial intake."""
    ep = Endpointer(frame_samples=FRAME)
    assert drive(ep, [(0.9, frames_for(800))]) == []  # still open
    flushed = ep.flush()
    assert flushed is not None
    assert flushed.forced


def test_flush_on_a_burst_too_short_to_be_speech_emits_nothing() -> None:
    ep = Endpointer(frame_samples=FRAME)
    drive(ep, [(0.9, frames_for(100))])
    assert ep.flush() is None


def test_reset_forgets_the_open_utterance() -> None:
    ep = Endpointer(frame_samples=FRAME)
    drive(ep, [(0.9, frames_for(800))])
    ep.reset()
    assert ep.state is SpeechState.SILENCE
    assert ep.flush() is None


def test_two_sentences_separated_by_a_real_pause_are_two_segments() -> None:
    ep = Endpointer(frame_samples=FRAME)
    segments = drive(
        ep,
        [
            (0.9, frames_for(800)),
            (0.0, frames_for(400)),
            (0.9, frames_for(800)),
            (0.0, frames_for(400)),
        ],
    )
    assert len(segments) == 2
    assert segments[0].t_end_ms <= segments[1].t_start_ms + 200
