"""The offer/accept handshake, RONA, and after-call work (`D33`, `D45`).

This service owns the `Assignment` record — the thing that answers "how long did that
agent take to pick up, and how long did the wrap-up cost". It never writes call state
itself; it asks `CallOrchestrator`, which stays the single writer (`D18`).

The four ways an offer ends are deliberately four outcomes, not two:

| outcome     | what actually happened                   | what we do to the agent          |
|-------------|------------------------------------------|----------------------------------|
| `ACCEPTED`  | they pressed Accept                      | `ON_CALL`                        |
| `DECLINED`  | they pressed Decline — they *are* there  | back to `AVAILABLE`, still READY |
| `TIMEOUT`   | nobody pressed anything (RONA)           | `NOT_READY`, `set_by=platform`   |
| `CANCELLED` | the caller hung up while it was ringing  | back to `AVAILABLE`, still READY |

Declining and timing out look similar and are not. A decline is a person telling us
something; a timeout is the absence of a person. Only the second one may take an agent
out of rotation, and conflating them either punishes an honest agent or lets an empty
desk absorb the queue.

**Either way the agent is excluded from re-matching this call.** Without that, the global
matcher re-picks the same best agent a millisecond later and the caller watches the same
desk not answer, forever.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from readycall import ids
from readycall.clock import Clock
from readycall.domain import events as ev
from readycall.domain.enums import CallState, OfferOutcome
from readycall.domain.models import Assignment, CallSession
from readycall.errors import PermanentError
from readycall.logging import call_context, get_logger
from readycall.ports.event_bus import EventBus
from readycall.services.agents.presence import PresenceService
from readycall.services.call_orchestrator.orchestrator import CallOrchestrator

log = get_logger(__name__)


@dataclass
class OfferPolicy:
    """Per-agent / per-queue settings (`D33`). Both modes are supported by config."""

    #: `manual` — the agent presses Accept (the default, and what a stage demo shows).
    #: `auto` — the call connects with a beep; busy centres genuinely run this way.
    accept_mode: str = "manual"
    timeout_s: float = 20.0
    #: Purely a *visibility* threshold (`D45`). Nothing expires, auto-saves or
    #: auto-readies when it passes; the workstation raises a long-ACW indicator.
    long_acw_after_s: float = 45.0


class AssignmentStore(Protocol):
    """The durable half of the handshake (`D78`).

    `save` is an upsert: an assignment is **one offer moving through its outcomes**, not a
    sequence of statements, so it is updated in place. The two logs beside it
    (`agent_state_log`, `identity_attestations`) append instead, because there the
    sequence *is* the record.
    """

    async def save(self, assignment: Assignment) -> None: ...

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Assignment]: ...


class InMemoryAssignmentStore:
    """Dies with the process, and is still worth having.

    It keeps the write-through path on the default configuration, so every test exercises
    the code that persistence depends on rather than leaving it to run only when somebody
    starts a container. That is `D75`'s SQLite argument one level up, and the failure it
    guards against is `B7`: code that was correct and never ran.
    """

    def __init__(self) -> None:
        self._rows: dict[str, Assignment] = {}

    async def save(self, assignment: Assignment) -> None:
        self._rows[assignment.assignment_id] = assignment

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Assignment]:
        wanted = set(call_session_ids)
        return [a for a in self._rows.values() if a.call_session_id in wanted]


@dataclass
class _CallOffers:
    """Per-call memory the matcher needs on the next tick."""

    excluded_agent_ids: set[str] = field(default_factory=set)
    attempts: int = 0


class AssignmentService:
    def __init__(
        self,
        *,
        orchestrator: CallOrchestrator,
        presence: PresenceService,
        bus: EventBus,
        clock: Clock,
        policy: OfferPolicy | None = None,
        store: AssignmentStore | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._presence = presence
        self._bus = bus
        self._clock = clock
        self._policy = policy or OfferPolicy()
        self._store_backend = store
        self._assignments: dict[str, Assignment] = {}
        self._open_by_call: dict[str, str] = {}
        self._history: dict[str, _CallOffers] = {}

    # --- restore -----------------------------------------------------------------------

    async def restore(self, call_session_ids: Sequence[str]) -> int:
        """Reload the handshake state for calls that are still live. Returns how many.

        Bounded to the given calls on purpose: a shift's worth of closed assignments is
        history, and history belongs in SQL rather than in a process's memory. What has to
        come back is only what the next tick will ask about.

        **Rebuilding `D52`'s exclusion set is the part that matters.** Without it a restart
        forgets that agent A001 already let this offer time out, the global matcher
        re-solves, reaches the same optimum, and offers the same caller to the same silent
        desk — the exact loop `D52` exists to break, reintroduced by a process restart. A
        `CANCELLED` offer excludes nobody, because nobody did anything wrong.
        """
        if self._store_backend is None:
            return 0
        rows = await self._store_backend.for_calls(call_session_ids)
        for assignment in rows:
            self._assignments[assignment.assignment_id] = assignment
            record = self._history.setdefault(assignment.call_session_id, _CallOffers())
            record.attempts += 1
            if assignment.outcome is OfferOutcome.PENDING:
                self._open_by_call[assignment.call_session_id] = assignment.assignment_id
            elif assignment.outcome in {OfferOutcome.DECLINED, OfferOutcome.TIMEOUT}:
                record.excluded_agent_ids.add(assignment.agent_id)
        if rows:
            log.info("assignments restored", assignments=len(rows), calls=len(call_session_ids))
        return len(rows)

    # --- reading -----------------------------------------------------------------------

    def get(self, assignment_id: str) -> Assignment | None:
        return self._assignments.get(assignment_id)

    def open_offer_for(self, call_session_id: str) -> Assignment | None:
        assignment_id = self._open_by_call.get(call_session_id)
        return self._assignments.get(assignment_id) if assignment_id else None

    def for_agent(self, agent_id: str) -> list[Assignment]:
        return [a for a in self._assignments.values() if a.agent_id == agent_id]

    def excluded_agents(self, call_session_id: str) -> tuple[str, ...]:
        """Agents this call must not be offered to again — feeds the matcher's filters.

        Returned as a hard-filter input rather than a score penalty, so the reason
        surfaces in the decision record instead of vanishing into a number (`D22`).
        """
        record = self._history.get(call_session_id)
        return tuple(sorted(record.excluded_agent_ids)) if record else ()

    # --- the handshake -------------------------------------------------------------------

    async def offer(
        self,
        session: CallSession,
        *,
        agent_id: str,
        accept_mode: str | None = None,
        timeout_s: float | None = None,
    ) -> Assignment:
        if session.state is not CallState.MATCHED:
            raise PermanentError(f"cannot offer a call in {session.state}; matching must run first")
        if self._open_by_call.get(session.call_session_id):
            raise PermanentError(f"call {session.call_session_id} already has an open offer")

        now = self._clock.now()
        mode = accept_mode or self._policy.accept_mode
        assignment = Assignment(
            assignment_id=ids.assignment_id(),
            call_session_id=session.call_session_id,
            agent_id=agent_id,
            offered_at=now,
            accept_mode=mode,
            outcome=OfferOutcome.PENDING,
        )
        self._assignments[assignment.assignment_id] = assignment
        self._open_by_call[session.call_session_id] = assignment.assignment_id
        self._history.setdefault(session.call_session_id, _CallOffers()).attempts += 1
        if self._store_backend is not None:
            # Durable *before* the desk rings. An offer that reached a screen and not the
            # store would come back from a restart as a call nobody was ever offered.
            await self._store_backend.save(assignment)

        await self._presence.begin_offer(agent_id, call_session_id=session.call_session_id)
        await self._orchestrator.transition(
            session, CallState.OFFERED, reason=f"offered_to:{agent_id}"
        )
        await self._bus.publish(
            ev.CallOffered(
                call_session_id=session.call_session_id,
                occurred_at=now,
                trace_id=session.trace_id,
                assignment_id=assignment.assignment_id,
                agent_id=agent_id,
                accept_mode=mode,
                timeout_s=timeout_s if timeout_s is not None else self._policy.timeout_s,
            )
        )
        return assignment

    async def accept(self, session: CallSession, *, assignment_id: str) -> Assignment:
        assignment = self._require_open(assignment_id)
        now = self._clock.now()
        assignment = assignment.model_copy(
            update={
                "outcome": OfferOutcome.ACCEPTED,
                "accepted_at": now,
                # The customer channel is already up and sitting in a holding bridge, so
                # bridging it to the agent is instantaneous - there is no dial-out delay
                # between "agent free" and "talking" (ARCHITECTURE 9).
                "bridged_at": now,
            }
        )
        await self._store(assignment, still_open=False)
        await self._presence.begin_call(
            assignment.agent_id, call_session_id=session.call_session_id
        )
        await self._orchestrator.transition(session, CallState.IN_CALL, reason="agent_accepted")
        await self._publish_resolution(session, assignment)
        return assignment

    async def decline(
        self, session: CallSession, *, assignment_id: str, reason: str = "declined"
    ) -> Assignment:
        """The agent said no. They are at their desk, so they stay READY."""
        return await self._reject(
            session,
            assignment_id=assignment_id,
            outcome=OfferOutcome.DECLINED,
            reason=reason,
            take_out_of_rotation=False,
        )

    async def timeout(self, session: CallSession, *, assignment_id: str) -> Assignment:
        """RONA. Nobody answered, so stop offering to that desk (`D33`)."""
        return await self._reject(
            session,
            assignment_id=assignment_id,
            outcome=OfferOutcome.TIMEOUT,
            reason="offer_timeout",
            take_out_of_rotation=True,
        )

    async def cancel(
        self, session: CallSession, *, assignment_id: str, reason: str = "caller_hung_up"
    ) -> Assignment:
        """The caller gave up while it was ringing. Nobody did anything wrong."""
        assignment = self._require_open(assignment_id)
        assignment = assignment.model_copy(
            update={"outcome": OfferOutcome.CANCELLED, "decline_reason": reason}
        )
        await self._store(assignment, still_open=False)
        await self._presence.release_offer(assignment.agent_id, reason="offer_cancelled")
        await self._orchestrator.transition(session, CallState.ABANDONED, reason=reason)
        await self._publish_resolution(session, assignment)
        return assignment

    # --- the call itself, and what follows -------------------------------------------------

    async def end_call(
        self,
        session: CallSession,
        *,
        assignment_id: str,
        reason: str = "caller_hung_up",
        at: datetime | None = None,
    ) -> Assignment:
        """Media disconnected. **This is where the ACW clock starts** (`D45`)."""
        assignment = self._require(assignment_id)
        if assignment.outcome is not OfferOutcome.ACCEPTED:
            raise PermanentError(f"assignment {assignment_id} was never accepted")
        disconnected_at = at or self._clock.now()
        assignment = assignment.model_copy(
            update={"ended_at": disconnected_at, "acw_started_at": disconnected_at}
        )
        await self._store(assignment, still_open=False)
        await self._orchestrator.transition(session, CallState.WRAP_UP, reason=reason)
        await self._presence.begin_after_call_work(
            assignment.agent_id,
            call_session_id=session.call_session_id,
            disconnected_at=disconnected_at,
        )
        return assignment

    async def save_wrapup(
        self,
        session: CallSession,
        *,
        assignment_id: str,
        disposition: str,
        was_edited: bool = True,
    ) -> Assignment:
        """Saving the form closes the **call record**. It does not end ACW (`D45`).

        These are two different statements — "I finished your form" and "I am done with
        this call" — and only the second one is about availability. The workstation
        offers a combined *Save & Ready* button so the common case is still one click,
        but that button makes two calls, and either may happen without the other.
        """
        assignment = self._require(assignment_id)
        await self._orchestrator.transition(session, CallState.CLOSED, reason="wrapup_saved")
        await self._bus.publish(
            ev.WrapupSaved(
                call_session_id=session.call_session_id,
                occurred_at=self._clock.now(),
                trace_id=session.trace_id,
                agent_id=assignment.agent_id,
                disposition=disposition,
                was_edited=was_edited,
                # Deliberately the *live* elapsed value, which may be None if the agent
                # already declared. Recording "ACW so far" is honest; asserting a final
                # duration at save time would re-import the bug D45 removed.
                acw_seconds=self._presence.acw_elapsed_s(assignment.agent_id),
            )
        )
        return assignment

    async def close_unwrapped(self, session: CallSession, *, assignment_id: str) -> Assignment:
        """End a call whose agent left after-call work without filing a wrap-up.

        `D45` says ACW ends when the **person** says so, and nothing may auto-save a
        wrap-up on their behalf. Both still hold: this writes no `call_wrapups` row and
        invents no disposition. What it does is stop the CALL from sitting in `WRAP_UP`
        for ever, which is a different thing entirely and was a real bug (`B10`) — the
        call stayed active, so the workstation kept rendering that customer's identity and
        brief, and once a later call closed, the stale one surfaced again and never left.

        The absence of a wrap-up row is still the record that none was filed — exactly
        what `D45` wanted to preserve. The transition reason says so out loud.
        """
        assignment = self._require(assignment_id)
        await self._orchestrator.transition(
            session, CallState.CLOSED, reason="acw_ended_without_wrapup"
        )
        return assignment

    async def note_acw_ended(self, *, assignment_id: str, declared_intent: str) -> Assignment:
        """Record the ACW close-out on the assignment after the agent declared.

        `PresenceService.declare` is what actually ends after-call work; this only copies
        the result onto the assignment so the measured number lives next to the call it
        belongs to.
        """
        assignment = self._require(assignment_id)
        if assignment.acw_started_at is None:
            raise PermanentError(f"assignment {assignment_id} never entered after-call work")
        ended = self._clock.now()
        assignment = assignment.model_copy(
            update={
                "acw_ended_at": ended,
                # The value is the intent the agent declared - `ready`, `lunch`, ... -
                # never "done_button" or "timer". A timer cannot end after-call work
                # (`D45`), so it can never be the reason one ended.
                "acw_ended_by": declared_intent,
            }
        )
        await self._store(assignment, still_open=False)
        return assignment

    # --- internals ---------------------------------------------------------------------------

    async def _reject(
        self,
        session: CallSession,
        *,
        assignment_id: str,
        outcome: OfferOutcome,
        reason: str,
        take_out_of_rotation: bool,
    ) -> Assignment:
        assignment = self._require_open(assignment_id)
        assignment = assignment.model_copy(update={"outcome": outcome, "decline_reason": reason})
        await self._store(assignment, still_open=False)

        # Both paths exclude the agent from this call. Otherwise the very next matching
        # tick hands the same caller to the same desk, indefinitely.
        self._history.setdefault(session.call_session_id, _CallOffers()).excluded_agent_ids.add(
            assignment.agent_id
        )

        if take_out_of_rotation:
            await self._presence.mark_not_responding(
                assignment.agent_id, call_session_id=session.call_session_id
            )
        else:
            await self._presence.release_offer(assignment.agent_id, reason=reason)

        # Back to MATCHED, not QUEUED: the caller keeps their place and their accrued
        # wait, and the next tick re-solves with this agent excluded.
        await self._orchestrator.transition(session, CallState.MATCHED, reason=f"rona:{reason}")
        await self._publish_resolution(session, assignment)
        return assignment

    async def _publish_resolution(self, session: CallSession, assignment: Assignment) -> None:
        with call_context(session.call_session_id, trace_id=session.trace_id):
            log.info(
                "offer resolved",
                assignment_id=assignment.assignment_id,
                agent_id=assignment.agent_id,
                outcome=str(assignment.outcome),
            )
        await self._bus.publish(
            ev.OfferResolved(
                call_session_id=session.call_session_id,
                occurred_at=self._clock.now(),
                trace_id=session.trace_id,
                assignment_id=assignment.assignment_id,
                agent_id=assignment.agent_id,
                outcome=assignment.outcome,
                time_to_accept_ms=assignment.time_to_accept_ms,
                reason=assignment.decline_reason,
            )
        )

    async def _store(self, assignment: Assignment, *, still_open: bool) -> None:
        """Update the projection, then the durable row (`D78`)."""
        self._assignments[assignment.assignment_id] = assignment
        if not still_open:
            self._open_by_call.pop(assignment.call_session_id, None)
        if self._store_backend is not None:
            await self._store_backend.save(assignment)

    def _require(self, assignment_id: str) -> Assignment:
        assignment = self._assignments.get(assignment_id)
        if assignment is None:
            raise PermanentError(f"unknown assignment {assignment_id}")
        return assignment

    def _require_open(self, assignment_id: str) -> Assignment:
        assignment = self._require(assignment_id)
        if assignment.outcome is not OfferOutcome.PENDING:
            raise PermanentError(
                f"offer {assignment_id} was already resolved as {assignment.outcome}"
            )
        return assignment


__all__ = ["AssignmentService", "OfferPolicy"]


__all__ = [
    "AssignmentService",
    "AssignmentStore",
    "InMemoryAssignmentStore",
    "OfferPolicy",
]
