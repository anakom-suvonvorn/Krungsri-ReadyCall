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
from pathlib import Path
from typing import Any

import yaml

from readycall.errors import ConfigError
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


def load_scripted_turns(path: Path) -> list[ScriptedTurn]:
    """Read the lines the stage-safe demo path speaks (`D107`).

    This module's own docstring has always claimed three jobs, and the second one — *the
    stage-safe demo path, when we would rather not bet on live ASR in a noisy room* — was
    not actually possible: `build_stt` constructed `ScriptedSttEngine([])`, so choosing
    the safe engine produced a transcript with nothing in it. A demo fallback that
    silently shows an empty panel is worse than none, because it looks like the system
    working and finding nothing to say.

    **Timings are deliberately not in the file.** `TranscriptionStream` stamps every turn
    with the *segment's* real start and end, so a scripted line inherits the timing of
    whatever audio was actually played — which is what makes the fallback look like a
    transcription rather than a slideshow. Keep the lines roughly the length of the
    utterances they stand in for: `D98`'s rate guard refuses more than
    `MAX_CHARS_PER_SECOND` of text for the seconds of audio it arrived on, and it does
    not care that this one came from a file.
    """
    if not path.exists():
        return []
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    out: list[ScriptedTurn] = []
    for index, entry in enumerate(raw.get("turns") or []):
        if isinstance(entry, str):
            out.append(ScriptedTurn(text=entry, t_start_ms=0, t_end_ms=0))
            continue
        if not isinstance(entry, dict) or "text" not in entry:
            raise ConfigError(f"{path}: turn {index} needs a `text`")
        out.append(
            ScriptedTurn(
                text=str(entry["text"]),
                t_start_ms=int(entry.get("t_start_ms", 0)),
                t_end_ms=int(entry.get("t_end_ms", 0)),
                confidence=float(entry.get("confidence", 0.93)),
            )
        )
    return out
