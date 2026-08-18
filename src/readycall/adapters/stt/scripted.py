"""ScriptedSttEngine - replays known transcript turns with realistic timings.

Three jobs, all of them real (`INTEGRATIONS.md` section 2):

* every test and scenario run, so nothing is blocked on a GPU;
* the stage-safe demo path, when we would rather not bet on live ASR in a noisy room;
* a fixed reference when measuring the analysis stages, so a changed brief is
  attributable to the prompt and not to a different transcription.

It honours the same per-utterance interface as the real engines, including the
`warmup()` call, so swapping to Thonburian changes one env var.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult


@dataclass(frozen=True, slots=True)
class ScriptedTurn:
    """One line of a scripted intake, with the timing it should appear to take."""

    text: str
    t_start_ms: int
    t_end_ms: int
    confidence: float = 0.93


class ScriptedSttEngine:
    def __init__(
        self,
        turns: Sequence[ScriptedTurn],
        *,
        latency_ms: float = 0.0,
        version: str = "scripted-1",
    ) -> None:
        self._turns = list(turns)
        self._cursor = 0
        self._latency_ms = latency_ms
        self._version = version
        self.warmed = False

    @property
    def info(self) -> EngineInfo:
        return EngineInfo(
            name="scripted",
            version=self._version,
            device="none",
            model="scripted",
            expected_latency_ms=self._latency_ms,
        )

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self._turns)

    async def warmup(self) -> None:
        self.warmed = True

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        if self.exhausted:
            # Silence rather than invention: a real engine returns empty text for a
            # segment with no speech, and Whisper inventing text on silence is a known
            # failure mode we must not imitate (`D9`).
            return SttResult(text="", confidence=0.0, engine="scripted", is_final=True)
        turn = self._turns[self._cursor]
        self._cursor += 1
        if self._latency_ms:
            await asyncio.sleep(self._latency_ms / 1000.0)
        return SttResult(
            text=turn.text,
            confidence=turn.confidence,
            t_start_ms=turn.t_start_ms,
            t_end_ms=turn.t_end_ms,
            engine="scripted",
            engine_version=self._version,
            is_final=True,
        )

    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        async for _frame in frames:
            result = await self.transcribe_utterance([])
            if result.text:
                yield result

    async def close(self) -> None:
        return None

    def reset(self) -> None:
        self._cursor = 0
