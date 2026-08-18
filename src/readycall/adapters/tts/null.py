"""NullTtsEngine - records what would have been said, synthesises nothing.

Enough for P0 because during a v1 call nothing calls TTS at all: prompts are rendered
at build time and played as files (`D24`). This adapter lets tests assert *which*
prompt line was requested without needing a voice vendor.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from readycall.ports.tts import SynthesizedAudio, VoiceSpec


class NullTtsEngine:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "null"

    async def synthesize(self, text: str, voice: VoiceSpec) -> SynthesizedAudio:
        self.requested.append((text, voice.voice))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        # ~150 ms per Thai character is a crude but useful stand-in: it keeps prompt
        # durations plausible so queue-announcement timing tests are not nonsense.
        return SynthesizedAudio(
            storage_ref=f"prompt://{digest}",
            duration_ms=max(400.0, len(text) * 150.0),
            sample_rate=16000,
            text_hash=digest,
            engine="null",
        )

    async def stream(
        self, text_chunks: AsyncIterator[str], voice: VoiceSpec
    ) -> AsyncIterator[bytes]:
        async for chunk in text_chunks:
            yield chunk.encode("utf-8")

    async def close(self) -> None:
        return None
