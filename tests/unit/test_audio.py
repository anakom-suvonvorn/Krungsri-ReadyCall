"""Audio normalisation: the one place that knows a phone call is not 16 kHz mono float32.

These are unusually pedantic tests for what looks like arithmetic, and the reason is that
every failure mode here is **silent and sounds like a model problem**. A-law decoded as
µ-law is loud distorted speech, not an exception. A dropped half-sample smears everything
after it. Both would be blamed on Whisper.
"""

from __future__ import annotations

import math
from itertools import pairwise

import pytest

from readycall.media.audio import (
    TARGET_SAMPLE_RATE,
    AudioFormat,
    Encoding,
    decode,
    normalise,
    resample,
    rms,
    to_mono,
)


def test_pcm16_decodes_to_the_symmetric_unit_range() -> None:
    payload = (-32768).to_bytes(2, "little", signed=True) + (32767).to_bytes(
        2, "little", signed=True
    )
    samples = decode(payload, AudioFormat(encoding=Encoding.PCM16, sample_rate=16000))
    assert samples[0] == pytest.approx(-1.0)
    assert samples[1] == pytest.approx(1.0, abs=1e-4)


def test_silence_decodes_to_silence_in_both_companding_laws() -> None:
    """The idle code differs between the two, and both must land on ~0.

    This is the cheapest possible check that the tables are not swapped: µ-law idle is
    0xFF and A-law idle is 0xD5, and each decodes to near-zero only under its own law.
    """
    ulaw = decode(bytes([0xFF] * 8), AudioFormat(encoding=Encoding.PCM8_ULAW))
    alaw = decode(bytes([0xD5] * 8), AudioFormat(encoding=Encoding.PCM8_ALAW))
    assert all(abs(s) < 0.01 for s in ulaw)
    assert all(abs(s) < 0.01 for s in alaw)


def test_the_two_companding_laws_are_not_the_same_table() -> None:
    """If someone 'simplifies' one into the other, this is what catches it.

    Decoding A-law bytes as µ-law does not raise — it produces plausible, loud, wrong
    audio, and the first thing anybody blames is the microphone or the model.
    """
    payload = bytes(range(0, 256, 8))
    as_ulaw = decode(payload, AudioFormat(encoding=Encoding.PCM8_ULAW))
    as_alaw = decode(payload, AudioFormat(encoding=Encoding.PCM8_ALAW))
    assert as_ulaw != as_alaw


def test_companding_covers_the_full_range_without_overflowing() -> None:
    for encoding in (Encoding.PCM8_ULAW, Encoding.PCM8_ALAW):
        samples = decode(bytes(range(256)), AudioFormat(encoding=encoding))
        assert len(samples) == 256
        assert all(-1.001 <= s <= 1.001 for s in samples)
        # A law that only ever returned small values would pass every other test here.
        assert max(abs(s) for s in samples) > 0.9


def test_a_partial_sample_is_refused_rather_than_dropped() -> None:
    """Silently trimming would shift every following sample by half a period."""
    with pytest.raises(ValueError, match="whole number"):
        decode(b"\x01\x02\x03", AudioFormat(encoding=Encoding.PCM16))


def test_stereo_is_averaged_not_picked() -> None:
    """On a conference leg the channels can carry different speakers, and taking channel
    0 would delete a person from the transcript."""
    assert to_mono([1.0, 0.0, 0.5, 0.5], channels=2) == [0.5, 0.5]


def test_resampling_8k_to_16k_doubles_the_length_and_keeps_the_tone() -> None:
    src = 8000
    tone = [math.sin(2 * math.pi * 440 * n / src) for n in range(src)]
    out = resample(tone, src_rate=src, dst_rate=TARGET_SAMPLE_RATE)

    assert len(out) == pytest.approx(2 * src, rel=0.01)

    # Zero crossings are the cheap way to assert the pitch survived: a resampler that
    # doubles the length but also doubles the frequency passes a length check.
    def crossings(xs: list[float]) -> int:
        return sum(1 for a, b in pairwise(xs) if a <= 0 < b)

    assert crossings(out) == pytest.approx(crossings(tone), rel=0.02)


def test_normalise_is_the_whole_job_in_one_call() -> None:
    """8 kHz stereo µ-law in — the least convenient thing a phone can hand us."""
    payload = bytes([0xFF, 0xFF] * 160)  # 160 stereo frames of near-silence
    frame = normalise(payload, AudioFormat(encoding=Encoding.PCM8_ULAW, channels=2))

    assert frame.sample_rate == TARGET_SAMPLE_RATE
    assert len(frame.samples) == pytest.approx(320, rel=0.02)  # 20 ms at 16 kHz
    assert frame.duration_ms == pytest.approx(20.0, rel=0.05)


def test_rms_of_silence_is_zero_and_of_full_scale_is_one() -> None:
    assert rms([]) == 0.0
    assert rms([0.0] * 100) == 0.0
    assert rms([1.0, -1.0] * 50) == pytest.approx(1.0)
