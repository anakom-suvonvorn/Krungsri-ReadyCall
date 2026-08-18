"""TtsEngine port.

Note the asymmetry, which is deliberate (`D24`): `synthesize()` is called at **build
time** by `scripts/build_prompts.py` to render every IVR line into a cached clip, and
`stream()` exists only for the future conversational intake. During a v1 call, nothing
here is invoked at all — the telephony provider plays a file.

That is why the port exists now even though v1 barely uses it: it makes
`ConversationalAgentIntake` a drop-in rather than a redesign (`D10`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class VoiceSpec:
    voice: str
    language: str = "th"
    rate: float = 1.0
    pitch: float = 0.0


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    storage_ref: str
    duration_ms: float
    sample_rate: int
    text_hash: str
    engine: str


@runtime_checkable
class TtsEngine(Protocol):
    @property
    def name(self) -> str: ...

    async def synthesize(self, text: str, voice: VoiceSpec) -> SynthesizedAudio:
        """Render one line to a stored clip. Build time, not call time."""
        ...

    def stream(self, text_chunks: AsyncIterator[str], voice: VoiceSpec) -> AsyncIterator[bytes]:
        """Low-latency synthesis for a talking AI intake. Unused in v1."""
        ...

    async def close(self) -> None: ...
