"""The contract every voice-activity adapter must pass (`D3`, `D96`).

Same rule as the other ports: an adapter written on hackathon morning has to pass exactly
what the ones written today pass. `SileroVad` is included when the `ml` extra is installed
and **skipped, loudly, when it is not** — CI has no GPU, so the energy detector is what
keeps this suite meaningful there.

The assertions are deliberately weak on *accuracy* and strong on *contract*. How well a
detector separates speech from noise is a bake-off question with real audio in it (`D30`'s
shape); what every implementation must agree on is the frame size, the range, the ordering
and the reset — and those are the things that break the endpointer silently.
"""

from __future__ import annotations

import math

import pytest

from readycall.adapters.vad.energy import EnergyVad
from readycall.ports.vad import VoiceActivityDetector


def _speech_like(n: int, *, amplitude: float = 0.3) -> list[float]:
    """Not real speech — a few harmonics in the voice band, which is enough to be loud
    and structured where silence is neither."""
    return [
        amplitude
        * (
            math.sin(2 * math.pi * 220 * i / 16000)
            + 0.5 * math.sin(2 * math.pi * 700 * i / 16000)
            + 0.25 * math.sin(2 * math.pi * 1800 * i / 16000)
        )
        / 1.75
        for i in range(n)
    ]


def _silence(n: int, *, floor: float = 0.0008) -> list[float]:
    """Never digital zero: a real line has a noise floor, and a detector tuned against
    perfect silence falls over on the first actual phone call."""
    return [floor * math.sin(2 * math.pi * 50 * i / 16000) for i in range(n)]


@pytest.fixture(params=["energy", "silero"])
def vad(request: pytest.FixtureRequest) -> VoiceActivityDetector:
    if request.param == "energy":
        return EnergyVad()
    try:
        from readycall.adapters.vad.silero import SileroVad
    except ImportError:  # pragma: no cover - the `ml` extra is genuinely absent
        pytest.skip("silero needs the `ml` extra")
    # Deliberately NOT `except Exception` around the construction. The first version was,
    # and it turned a real load failure into a skip: the project's `filterwarnings=error`
    # made a third-party DeprecationWarning raise, the suite reported five green skips,
    # and the production detector was tested by nothing. A broad except around a thing
    # that is supposed to work is how a check quietly stops being one.
    return SileroVad()


def test_it_satisfies_the_protocol(vad: VoiceActivityDetector) -> None:
    assert isinstance(vad, VoiceActivityDetector)
    assert vad.info.name
    assert vad.frame_samples > 0


def test_the_probability_is_always_in_range(vad: VoiceActivityDetector) -> None:
    """The endpointer compares against a threshold and does no clamping of its own."""
    for samples in (
        _silence(vad.frame_samples),
        _speech_like(vad.frame_samples),
        [0.0] * vad.frame_samples,
        [1.0] * vad.frame_samples,
        [-1.0 if i % 2 else 1.0 for i in range(vad.frame_samples)],
    ):
        p = vad.speech_probability(samples)
        assert 0.0 <= p <= 1.0, f"{vad.info.name} returned {p}"


def test_speech_scores_higher_than_silence(vad: VoiceActivityDetector) -> None:
    """The weakest useful statement of what a VAD is for.

    Fed in order and with a warm-up, because both detectors are stateful — the energy one
    is adapting its noise floor and Silero is carrying an RNN state.
    """
    vad.reset()
    for _ in range(10):
        quiet = vad.speech_probability(_silence(vad.frame_samples))
    for _ in range(10):
        loud = vad.speech_probability(_speech_like(vad.frame_samples))

    assert loud > quiet
    assert quiet < 0.65, "silence must sit below the endpointer's threshold (`D9`)"


def test_reset_actually_forgets(vad: VoiceActivityDetector) -> None:
    """Leaving one caller's state in front of the next caller's first syllable is how a
    first word goes missing — and it looks like an STT fault, not a VAD one."""
    vad.reset()
    for _ in range(20):
        vad.speech_probability(_speech_like(vad.frame_samples, amplitude=0.9))
    after_loud = vad.speech_probability(_silence(vad.frame_samples))

    vad.reset()
    fresh = vad.speech_probability(_silence(vad.frame_samples))

    assert fresh <= after_loud + 0.35, "state survived a reset"


def test_it_is_safe_to_call_reset_twice(vad: VoiceActivityDetector) -> None:
    vad.reset()
    vad.reset()
    assert 0.0 <= vad.speech_probability(_silence(vad.frame_samples)) <= 1.0


def test_the_energy_floor_does_not_climb_over_a_long_utterance() -> None:
    """Specific to `EnergyVad`, and the bug its asymmetric attack/release exists to stop:
    a floor that follows loud frames quickly rises over its own head, and the speaker is
    muted part-way through a sentence."""
    vad = EnergyVad()
    vad.reset()
    probabilities = [vad.speech_probability(_speech_like(vad.frame_samples)) for _ in range(120)]
    assert probabilities[-1] >= 0.65, "the detector muted a continuously-speaking caller"
