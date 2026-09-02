"""EnergyVad — a dependency-free voice detector, and the reason the audio path has tests.

Honest about what it is: **RMS against an adaptive noise floor**. It cannot tell speech
from a television, and on a noisy line it will over-trigger. It is not the production
detector — `D9` picked Silero for that — and it is not pretending to be.

What it *is* for, and both matter:

* **CI has no GPU and no `ml` extra** (`uv sync --frozen`), so without this the endpointer,
  the gateway and the whole transcription wiring would only ever run on one laptop. That is
  `B7`'s shape — a path nobody exercises — and it is exactly how `D9`'s inherited padding
  constants would rot without anybody noticing.
* **It is the degradation rung.** If Silero fails to load on demo morning, the call still
  gets endpointed, slightly worse. `ARCHITECTURE` §16 has no row for "VAD unavailable"
  because there does not have to be one.

**The noise floor adapts, and only downward-ish.** A fixed threshold is useless across a
mobile in traffic and a landline in an office — they differ by tens of dB. The floor tracks
quiet frames quickly and loud ones slowly, so a long sentence cannot drag the floor up over
itself and mute the speaker mid-utterance.
"""

from __future__ import annotations

from collections.abc import Sequence

from readycall.media.audio import rms
from readycall.ports.vad import VadInfo


class EnergyVad:
    def __init__(
        self,
        *,
        frame_samples: int = 512,
        speech_over_floor: float = 3.5,
        floor_attack: float = 0.25,
        floor_release: float = 0.01,
        initial_floor: float = 0.005,
    ) -> None:
        self._frame_samples = frame_samples
        self._ratio = speech_over_floor
        #: How fast the floor follows a QUIETER frame (fast — a line that just went quiet
        #: is new information) versus a LOUDER one (slow — otherwise a sustained sentence
        #: raises the floor over its own head and the speaker is cut off mid-word).
        self._attack = floor_attack
        self._release = floor_release
        self._initial = initial_floor
        self._floor = initial_floor
        #: Below this ratio a frame is treated as line noise and is allowed to move the
        #: floor. Deliberately lower than `speech_over_floor`, so the band between them —
        #: "maybe somebody, maybe not" — updates nothing rather than guessing.
        self._speech_ratio_floor = 1.0 + (speech_over_floor - 1.0) * 0.4

    @property
    def info(self) -> VadInfo:
        return VadInfo(name="energy", version="1", device="cpu")

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    def reset(self) -> None:
        self._floor = self._initial

    def speech_probability(self, samples: Sequence[float]) -> float:
        level = rms(samples)
        floor = max(self._floor, 1e-6)
        ratio = level / floor

        # Update the floor AFTER deciding, so a frame is never judged against a floor it
        # just moved itself.
        #
        # **A noise floor is estimated from NON-speech, and only from non-speech.** The
        # first version of this adapted on every frame, quickly downward and slowly
        # upward, on the theory that "slowly" was slow enough. It was not: over a few
        # seconds of continuous speech the floor still climbed over its own head and the
        # detector muted a caller mid-sentence — caught by
        # `test_the_energy_floor_does_not_climb_over_a_long_utterance`, which was written
        # before the bug was known to be there.
        #
        # So a frame that looks like speech does not move the floor at all. A genuine
        # change in line level is picked up on the next pause, and a caller who never
        # pauses hits `max_segment_ms` in the endpointer regardless.
        if ratio < self._speech_ratio_floor:
            rate = self._attack if level < self._floor else self._release
            self._floor = (1.0 - rate) * self._floor + rate * level

        if ratio <= 1.0:
            return 0.0
        # Map the ratio onto 0..1 so it can be compared against the same threshold the
        # real detector uses — the endpointer must not need to know which VAD it has.
        # At exactly `speech_over_floor` this returns 0.75, comfortably over `D9`'s 0.65.
        scaled = (ratio - 1.0) / (self._ratio - 1.0) * 0.75
        return min(1.0, scaled)


__all__ = ["EnergyVad"]
