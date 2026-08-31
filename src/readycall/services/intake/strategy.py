"""The `IntakeStrategy` seam (`D10`) — the shape every pre-call experience shares.

v1 records the caller and transcribes them. The intended evolution is a voice agent that
*talks* to them while they wait. Those are wildly different experiences and they must be
interchangeable, because everything downstream — the brief builder, the agent screen, the
analysis passes — has to work identically whichever ran.

**Turns in, not frames in.** `ARCHITECTURE.md` §7 originally drew `start(session, media)`,
handing each strategy the raw audio. It takes a `TranscriptTurn` instead (`D88`): the media
gateway and the transcriber sit on the far side of the seam, so a strategy is a pure
function of what was *said*. That is what lets the whole seam be built and tested with no
audio, no GPU and no telephony — which is exactly the position this phase is in.

A strategy is deliberately **not** told how the hold is going. It does not know about
keypresses, queue position, or whether an agent is close to free; it is handed turns and
asked to stop. Everything about *when* lives in `hold.py`, which is the thing with the
rules in it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from readycall.domain.enums import DegradationReason, FinalizeReason, IntakeStrategyKind
from readycall.domain.models import CallSession, IntakeResult, TranscriptTurn


@runtime_checkable
class IntakeStrategy(Protocol):
    @property
    def kind(self) -> IntakeStrategyKind:
        """Which experience this is. Recorded on the result so a brief can say so."""
        ...

    @property
    def running(self) -> bool: ...

    async def start(self, session: CallSession) -> str:
        """Open an intake for this call. Returns the `intake_id`."""
        ...

    async def on_turn(self, turn: TranscriptTurn) -> None:
        """One transcribed utterance. Called as each lands, never in a batch at the end —
        a dropped call must still leave the turns that arrived before it dropped."""
        ...

    async def finalize(
        self,
        reason: FinalizeReason,
        *,
        degraded: DegradationReason = DegradationReason.NONE,
    ) -> IntakeResult:
        """Stop, and produce what every strategy produces. Idempotent: the agent
        accepting and the caller hanging up can race, and both call this."""
        ...


__all__ = ["IntakeStrategy"]
