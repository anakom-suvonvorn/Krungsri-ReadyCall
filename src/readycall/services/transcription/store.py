"""`transcript_turns`, and the subscriber that fills it (`D114`).

`ARCHITECTURE` §6 has asked for this since P0 — *"turns are persisted **incrementally** — a
dropped call still leaves a usable transcript"* — and `DATA_MODEL` §6 has carried the
column list just as long, under a warning that nothing wrote them. This is the write.

**A fourth subscriber, not a line inside the third.** `ARCHITECTURE` §14 lists Analysis,
Agent Delivery and call-progress as consumers of `transcript.turn`; persistence is another
one, and it gets its own. Putting the write inside `TranscriptDeliveryService` would tie a
storage failure to the agent's screen, and the screen is the half that must not be blocked
(`D12`). Here, a failed write costs a durable row and nothing else.

**Incremental, per turn, deliberately.** The alternative — write the whole transcript when
the intake finalises — is one write instead of a dozen and loses everything when the thing
that goes wrong is the call ending badly, which is exactly the case the durable copy is
for. A dropped call leaves the sentences it produced.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from readycall.domain import events as ev
from readycall.domain.enums import SpeakerRole
from readycall.domain.models import TranscriptTurn
from readycall.logging import get_logger
from readycall.ports.event_bus import EventBus

log = get_logger(__name__)


class TranscriptStore(Protocol):
    async def append(self, turn: TranscriptTurn) -> None: ...

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[TranscriptTurn]:
        """Every turn for these calls, **ordered by call then `seq`**.

        Ordered here rather than by the caller: `seq` is the only thing that makes a
        transcript a transcript, and a store that returned insertion order would be
        correct today and wrong the moment two writes raced.
        """
        ...

    async def delete_for_call(self, call_session_id: str) -> int:
        """What a PDPA erasure request runs against the text half (`D14`)."""
        ...


class InMemoryTranscriptStore:
    """The fake. Same contract suite as the real one (`D3`, `D75`)."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, TranscriptTurn]] = {}

    async def append(self, turn: TranscriptTurn) -> None:
        # Keyed by `turn_id`, so a re-delivered event overwrites rather than duplicating.
        # The bus is at-least-once by design (`D15`) and a transcript with a sentence in
        # it twice reads as the caller having repeated themselves.
        self._rows.setdefault(turn.call_session_id, {})[turn.turn_id] = turn

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[TranscriptTurn]:
        out: list[TranscriptTurn] = []
        for call_session_id in call_session_ids:
            out.extend(self._rows.get(call_session_id, {}).values())
        return sorted(out, key=lambda t: (t.call_session_id, t.seq))

    async def delete_for_call(self, call_session_id: str) -> int:
        return len(self._rows.pop(call_session_id, {}))


class TranscriptRecorder:
    """Takes `transcript.turn` off the bus and writes it down. That is the whole job."""

    def __init__(self, *, store: TranscriptStore) -> None:
        self._store = store
        self.written = 0
        self.failed = 0

    def subscribe(self, bus: EventBus) -> None:
        bus.subscribe(ev.TranscriptTurnAdded.name, self._on_turn)

    async def _on_turn(self, event: ev.Event) -> None:
        if not isinstance(event, ev.TranscriptTurnAdded):  # pragma: no cover - topic guard
            return
        turn = TranscriptTurn(
            turn_id=event.turn_id,
            call_session_id=event.call_session_id,
            seq=event.seq,
            speaker_role=SpeakerRole(event.speaker_role),
            text=event.text,
            t_start_ms=event.t_start_ms,
            t_end_ms=event.t_end_ms,
            asr_confidence=event.asr_confidence,
            engine=event.engine,
            engine_version=event.engine_version,
            is_final=event.is_final,
            intake_id=event.intake_id,
        )
        try:
            await self._store.append(turn)
        except Exception:
            # The durable copy is the thing that fails here, and the call is not (`D12`).
            # Counted as well as logged, because "the transcript on screen is complete and
            # the one in the database is not" is a state somebody has to be able to see.
            self.failed += 1
            log.exception(
                "could not persist a transcript turn",
                call_session_id=event.call_session_id,
                turn_id=event.turn_id,
            )
            return
        self.written += 1


__all__ = ["InMemoryTranscriptStore", "TranscriptRecorder", "TranscriptStore"]
