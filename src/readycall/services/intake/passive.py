"""`PassiveRecordIntake` — the v1 experience: listen, keep every word, say nothing.

The pitched behaviour, and the least clever of the three strategies (`D10`). It has no
opinion about what the caller says; it accumulates turns and hands them over. All the
judgement happens later, in analysis, where it can be versioned and audited.

Two properties are worth stating because they are easy to lose:

**Turns are published as they arrive.** A dropped call, a crashed worker, or a caller who
hangs up mid-sentence still leaves everything said up to that point, because nothing is
held back waiting for a tidy ending. `is_partial` then tells the brief that the sentence
was cut, rather than the brief quietly presenting a fragment as a finished thought.

**`finalize` is idempotent.** The agent pressing Accept and the caller hanging up genuinely
race — `D21` puts them within a second or two of each other by design — and both paths end
the intake. The second caller gets the same result, not a second `intake.finalized` event
and not an exception in the middle of a live call.
"""

from __future__ import annotations

from datetime import datetime

from readycall.clock import Clock
from readycall.domain import events as ev
from readycall.domain.enums import DegradationReason, FinalizeReason, IntakeStrategyKind
from readycall.domain.models import CallSession, IntakeResult, TranscriptTurn
from readycall.ids import intake_id as new_intake_id
from readycall.logging import get_logger
from readycall.ports.event_bus import EventBus

log = get_logger(__name__)


class PassiveRecordIntake:
    """One instance per call — it holds that call's turns."""

    def __init__(self, *, clock: Clock, bus: EventBus) -> None:
        self._clock = clock
        self._bus = bus
        self._session: CallSession | None = None
        self._intake_id: str | None = None
        self._started_at: datetime | None = None
        self._turns: list[TranscriptTurn] = []
        self._result: IntakeResult | None = None

    @property
    def kind(self) -> IntakeStrategyKind:
        return IntakeStrategyKind.PASSIVE

    @property
    def running(self) -> bool:
        return self._intake_id is not None and self._result is None

    @property
    def intake_id(self) -> str | None:
        return self._intake_id

    @property
    def turns(self) -> tuple[TranscriptTurn, ...]:
        return tuple(self._turns)

    @property
    def result(self) -> IntakeResult | None:
        """The finalised result, or None while it is still running."""
        return self._result

    async def start(self, session: CallSession) -> str:
        if self._intake_id is not None:  # pragma: no cover - the driver starts once
            return self._intake_id
        self._session = session
        self._intake_id = new_intake_id()
        self._started_at = self._clock.now()
        await self._bus.publish(
            ev.IntakeStarted(
                call_session_id=session.call_session_id,
                occurred_at=self._started_at,
                trace_id=session.trace_id,
                intake_id=self._intake_id,
                strategy=str(self.kind),
            )
        )
        log.info(
            "intake started",
            call_session_id=session.call_session_id,
            intake_id=self._intake_id,
            strategy=str(self.kind),
        )
        return self._intake_id

    async def on_turn(self, turn: TranscriptTurn) -> None:
        if not self.running or self._session is None:
            # A turn arriving after the intake closed is not an error: the transcriber
            # was mid-utterance when the agent accepted. It belongs to the live call now.
            log.info("transcript turn arrived after intake closed", turn_id=turn.turn_id)
            return
        self._turns.append(turn)
        await self._bus.publish(
            ev.TranscriptTurnAdded(
                call_session_id=self._session.call_session_id,
                occurred_at=self._clock.now(),
                trace_id=self._session.trace_id,
                turn_id=turn.turn_id,
                seq=turn.seq,
                speaker_role=str(turn.speaker_role),
                text=turn.text,
                t_start_ms=turn.t_start_ms,
                t_end_ms=turn.t_end_ms,
                asr_confidence=turn.asr_confidence,
            )
        )

    async def finalize(
        self,
        reason: FinalizeReason,
        *,
        degraded: DegradationReason = DegradationReason.NONE,
    ) -> IntakeResult:
        if self._result is not None:
            return self._result
        if self._session is None or self._started_at is None or self._intake_id is None:
            raise RuntimeError("finalize() before start()")  # pragma: no cover

        ended_at = self._clock.now()
        result = IntakeResult(
            intake_id=self._intake_id,
            call_session_id=self._session.call_session_id,
            strategy=self.kind,
            started_at=self._started_at,
            ended_at=ended_at,
            finalize_reason=reason,
            # Cut off with more to say. `D21` makes this the *expected* ending for a busy
            # queue, not an error — but the brief has to render it differently.
            is_partial=reason is FinalizeReason.OFFER_ACCEPTED,
            turns=tuple(self._turns),
            # Passed in, never guessed. No turns can mean the caller said nothing,
            # or that STT was down, and only the driver knows which - a strategy that
            # invented `stt_unavailable` here would put a false claim on the screen.
            degraded=degraded,
        )
        self._result = result
        await self._bus.publish(
            ev.IntakeFinalized(
                call_session_id=self._session.call_session_id,
                occurred_at=ended_at,
                trace_id=self._session.trace_id,
                intake_id=self._intake_id,
                finalize_reason=reason,
                is_partial=result.is_partial,
                turn_count=len(self._turns),
            )
        )
        log.info(
            "intake finalized",
            call_session_id=self._session.call_session_id,
            intake_id=self._intake_id,
            reason=str(reason),
            partial=result.is_partial,
            turns=len(self._turns),
        )
        return result


__all__ = ["PassiveRecordIntake"]
