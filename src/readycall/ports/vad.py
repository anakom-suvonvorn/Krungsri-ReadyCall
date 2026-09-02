"""VoiceActivityDetector port — is somebody speaking in these samples?

The ninth port, and it earns the slot for the same reason `SttEngine` does (`D96`): it is a
**vendor model** behind a boundary we want to swap. Silero is the default (`D9`), but the
alternatives are real — WebRTC's VAD, a plain energy gate, and the "VAD" a telephony
provider may already be doing upstream — and a bake-off between them is the same shape as
`D30`'s STT one.

**The port returns a probability, not a decision.** Where an utterance *starts and ends* is
endpointing, and it is a stateful judgement with thresholds, minimum durations and padding
in it (`services/transcription/endpointer.py`). Keeping that out of the adapter means the
tuning `D9` inherited — threshold 0.65, 500 ms minimum speech, 100 ms minimum silence,
~120/60 ms padding — lives in one testable place instead of once per vendor, and can be
exercised against a list of floats with no model loaded at all.

**Frame size is the model's to declare, not ours.** Silero v5 wants exactly 512 samples at
16 kHz and silently misbehaves on anything else, so `frame_samples` is part of the contract
and the caller reframes to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class VadInfo:
    name: str
    version: str
    device: str = "cpu"


@runtime_checkable
class VoiceActivityDetector(Protocol):
    @property
    def info(self) -> VadInfo: ...

    @property
    def frame_samples(self) -> int:
        """Exactly how many 16 kHz samples one call wants. Silero v5: 512 (32 ms)."""
        ...

    def speech_probability(self, samples: Sequence[float]) -> float:
        """0.0 (certainly silence) to 1.0 (certainly speech), for ONE frame.

        Implementations are stateful across calls — Silero carries an RNN hidden state —
        so frames must be fed in order, and `reset()` called between calls.
        """
        ...

    def reset(self) -> None:
        """Forget everything about the previous stream.

        Not optional housekeeping: leaving one caller's hidden state in front of the next
        caller's first syllable is how the first word of a call goes missing.
        """
        ...


__all__ = ["VadInfo", "VoiceActivityDetector"]
