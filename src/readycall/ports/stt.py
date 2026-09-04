"""SttEngine port — Thai speech to text, streaming-first (`D9`).

Deliberately *not* the shape of the team's earlier batch pipeline. That one took a
file, ran VAD over the whole thing, wrote chunks to disk and produced a CSV. Here the
audio arrives while the customer is still on hold, so the interface is per-utterance
and in-memory: no PII on local disk, and text exists before the queue pops.

Implementations load the model **once** in a long-lived worker. A 5-20 second model
load can never sit in the call path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """16 kHz mono float32, always. The media gateway normalises before anyone sees it."""

    samples: Sequence[float]
    t_start_ms: int
    sample_rate: int = 16000

    @property
    def duration_ms(self) -> float:
        return len(self.samples) / self.sample_rate * 1000.0


@dataclass(frozen=True, slots=True)
class SttHint:
    """Domain nudges that measurably help on insurance jargon (IPD/OPD, ค่าห้อง, สินไหม)."""

    language: str = "th"
    vocabulary: tuple[str, ...] = ()
    product_line: str | None = None


@dataclass(frozen=True, slots=True)
class SttResult:
    text: str
    confidence: float | None = None
    t_start_ms: int = 0
    t_end_ms: int = 0
    engine: str = "unknown"
    engine_version: str = "unknown"
    is_final: bool = True


@dataclass(frozen=True, slots=True)
class EngineInfo:
    name: str
    version: str
    device: str
    model: str
    expected_latency_ms: float | None = None
    extra: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class SttEngine(Protocol):
    @property
    def info(self) -> EngineInfo: ...

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        """Transcribe one VAD-delimited utterance.

        Callers pass segments already padded (~120 ms before / 60 ms after) — that
        padding measurably stops Whisper clipping the first syllable, and is the one
        thing worth keeping verbatim from the earlier project (`D9`).
        """
        ...

    def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        """Continuous mode, emitting partial then final results."""
        ...

    async def warmup(self) -> None:
        """Load the model and run one throwaway inference.

        Called at worker start so the first real utterance is not the slow one.
        """
        ...

    async def close(self) -> None: ...


@runtime_checkable
class BatchSttEngine(Protocol):
    """An engine that can transcribe several utterances in **one** GPU pass.

    **A capability, not a requirement.** It is deliberately a second protocol rather than
    another method on `SttEngine`: most engines have no batch path worth writing, and
    forcing every adapter to grow a fake one would make the port lie about what they can
    do. `TranscriptionStream` checks with `isinstance` and falls back to a plain loop, so
    an engine that does not implement this is slower and identical in every other respect.

    **Why it is worth having at all.** Whisper encodes a fixed 30-second window whatever
    you hand it, so a 1.5-second utterance costs what a 25-second one does — measured on
    real calls, this project asks the GPU to encode **2.7x more audio than the call
    contains**. Batching does not remove that waste; it overlaps it, which is the half of
    the problem that can be fixed without changing what the model sees.

    The team's earlier Thai project reached its speed exactly this way (`batch_size=4`
    through the HF pipeline). It had the easy version of the problem — a file on disk,
    every chunk available up front. Streaming only has a batch to form when the model has
    fallen behind, which is precisely when the speed-up is needed and never when it is not.

    **Ordering is the caller's guarantee, not the engine's**: results come back in the same
    order as the utterances went in, one for one.
    """

    async def transcribe_batch(
        self,
        utterances: Sequence[Sequence[AudioFrame]],
        *,
        hint: SttHint | None = None,
    ) -> list[SttResult]:
        """Transcribe several VAD-delimited utterances together, in input order."""
        ...
