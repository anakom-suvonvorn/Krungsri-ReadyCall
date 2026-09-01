"""Turning matching decisions into offers on real desks.

The matcher answers *who should take this call* (`D22`); this service is what actually
rings a workstation, and what re-rings a different one when nobody picks up. It is the
join between three things that are otherwise deliberately independent:

* `MatchingEngine`, which is pure and knows nothing about sockets;
* `AssignmentService`, which owns the handshake;
* the agent's browser, reached through an `AgentNotifier` — a callback, not an import.

That last point is the reason this file exists rather than the logic living in the API
router: a service must not import from `api/`. The hub is passed in, so a dispatch tick
is fully testable with a list-appending fake and no FastAPI at all.

**A tick is idempotent and safe to run often.** It only offers calls that are `MATCHED`
with no open offer, and it re-reads presence every time, so an agent who went to lunch
between two ticks is simply not a candidate on the second.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from readycall.clock import Clock
from readycall.domain.enums import CallState, MatchKind
from readycall.domain.models import CallSession, MatchingDecision
from readycall.logging import get_logger
from readycall.services.agents.assignment import AssignmentService
from readycall.services.agents.presence import PresenceService
from readycall.services.matching.engine import MatchingEngine
from readycall.services.matching.scoring import WaitingCall

log = get_logger(__name__)


class MatchingDecisionStore(Protocol):
    """The durable record of every matching decision (`D18`, `D22`, `D78`).

    Append-only, and **including the calls that were not assigned** (`D50`). The whole
    justification for storing a row per decision is that someone can ask *"why is this
    caller still waiting?"* and get a true answer months later — which means the rows we
    keep have to include the ones where the answer is "nobody qualified was free".
    """

    async def append(self, decision: MatchingDecision) -> None: ...

    async def latest_for_calls(
        self, call_session_ids: Sequence[str]
    ) -> dict[str, MatchingDecision]: ...


class InMemoryMatchingDecisionStore:
    """The fake, held to the same contract suite (`D3`)."""

    def __init__(self) -> None:
        self._rows: list[MatchingDecision] = []

    async def append(self, decision: MatchingDecision) -> None:
        self._rows.append(decision)

    async def latest_for_calls(
        self, call_session_ids: Sequence[str]
    ) -> dict[str, MatchingDecision]:
        wanted = set(call_session_ids)
        latest: dict[str, MatchingDecision] = {}
        for row in sorted(self._rows, key=lambda r: r.at):
            if row.call_session_id in wanted:
                latest[row.call_session_id] = row
        return latest

    def all(self) -> list[MatchingDecision]:
        return list(self._rows)


class AgentNotifier(Protocol):
    """How a decision reaches a screen. Implemented by `AgentHub` in the API layer."""

    async def send(self, agent_id: str, kind: str, payload: dict[str, Any]) -> Any: ...

    async def broadcast(self, kind: str, payload: dict[str, Any]) -> None: ...


@dataclass
class DispatchResult:
    offered: list[str]
    decisions: list[MatchingDecision]
    #: Calls the matcher could not place, with the reason it gave (`D50`).
    unplaced: dict[str, str]


class DispatchService:
    def __init__(
        self,
        *,
        engine: MatchingEngine,
        assignments: AssignmentService,
        presence: PresenceService,
        notifier: AgentNotifier,
        clock: Clock,
        offer_timeout_s: float = 20.0,
        decisions: MatchingDecisionStore | None = None,
    ) -> None:
        self._engine = engine
        self._assignments = assignments
        self._presence = presence
        self._notifier = notifier
        self._clock = clock
        self._offer_timeout_s = offer_timeout_s
        self._decisions = decisions
        #: The pool. **Not a table** (`D78`): it is rebuilt from `call_sessions` in
        #: `queued`/`matched`, because a second copy of "who is waiting" is a second thing
        #: that can disagree with the call's own state.
        self._waiting: dict[str, tuple[CallSession, WaitingCall]] = {}
        #: The newest decision per call, for the offer card. The full history is durable.
        self._last_decision: dict[str, MatchingDecision] = {}

    # --- the waiting pool ------------------------------------------------------------

    def admit(self, session: CallSession, call: WaitingCall) -> None:
        self._waiting[session.call_session_id] = (session, call)

    def restore(
        self,
        entries: Sequence[tuple[CallSession, WaitingCall]],
        *,
        decisions: dict[str, MatchingDecision] | None = None,
    ) -> int:
        """Re-admit callers who were still waiting, and their last rationale.

        The pool is **not** loaded from a table of its own — the caller passes in what was
        derived from `call_sessions` (`D78`). This method exists so that derivation has one
        landing place, and so the last decision comes back with it: the offer card renders
        the matcher's one-line rationale, and a restored offer with no explanation would be
        the one screen in this product that cannot say why it is showing you something.
        """
        for session, call in entries:
            self._waiting[session.call_session_id] = (session, call)
        for call_session_id, decision in (decisions or {}).items():
            if call_session_id in self._waiting:
                self._last_decision[call_session_id] = decision
        if entries:
            log.info("waiting pool restored", callers=len(entries))
        return len(entries)

    def release(self, call_session_id: str) -> None:
        self._waiting.pop(call_session_id, None)

    def waiting(self) -> list[WaitingCall]:
        return [call for _, call in self._waiting.values()]

    def waiting_call(self, call_session_id: str) -> WaitingCall | None:
        """The pool's view of one caller — urgency and accrued wait, for the offer card."""
        entry = self._waiting.get(call_session_id)
        return entry[1] if entry else None

    def last_decision_for(self, call_session_id: str) -> MatchingDecision | None:
        return self._last_decision.get(call_session_id)

    # --- one tick ---------------------------------------------------------------------

    async def tick(self) -> DispatchResult:
        offerable = [
            call_session_id
            for call_session_id, (session, _) in self._waiting.items()
            if session.state is CallState.MATCHED
            and self._assignments.open_offer_for(call_session_id) is None
        ]
        if not offerable:
            return DispatchResult(offered=[], decisions=[], unplaced={})

        now = self._clock.now()
        calls = [
            # Rebuilt each tick so an agent who has since declined is excluded (`D52`),
            # **and so the caller's accrued wait is current** (`B12`). `replace` rather
            # than mutation: `WaitingCall` is frozen, and the matcher holding a reference
            # that changes under it is a bug waiting to happen.
            #
            # `waiting_s` used to be whatever was passed at `admit()` and never moved
            # again, which fed `score_urgency` a constant: `wait_pressure` stayed at 0,
            # `sla_risk` never fired, and neither did the wait ceiling that drops a caller
            # to any-qualified-agent. The whole of `D22`'s anti-starvation was written,
            # correct, and driven by nothing.
            replace(
                self._waiting[call_session_id][1],
                waiting_s=self._waiting[call_session_id][0].wait_seconds(now),
                excluded_agent_ids=self._assignments.excluded_agents(call_session_id),
            )
            for call_session_id in offerable
        ]
        decisions = await self._engine.match(calls, self._presence.snapshot())

        offered: list[str] = []
        unplaced: dict[str, str] = {}
        for decision in decisions:
            self._last_decision[decision.call_session_id] = decision
            if self._decisions is not None:
                # Every decision, not only the assignments (`D50`). A supervisor asking
                # "why is this caller still waiting" is asking about a row we would
                # otherwise have thrown away.
                await self._decisions.append(decision)
            entry = self._waiting.get(decision.call_session_id)
            if entry is None:
                continue
            session, _ = entry

            if decision.chosen_agent_id is None or decision.kind is MatchKind.DEFER:
                unplaced[decision.call_session_id] = str(decision.kind)
                continue

            assignment = await self._assignments.offer(
                session,
                agent_id=decision.chosen_agent_id,
                timeout_s=self._offer_timeout_s,
            )
            offered.append(decision.chosen_agent_id)
            await self._notifier.send(
                decision.chosen_agent_id,
                "offer",
                {
                    "assignment_id": assignment.assignment_id,
                    "call_session_id": session.call_session_id,
                    "accept_mode": assignment.accept_mode,
                    "timeout_s": self._offer_timeout_s,
                    "offered_at": assignment.offered_at.isoformat(),
                    "queue_id": session.queue_id,
                    "rationale_th": decision.rationale_th,
                },
            )

        if unplaced:
            log.info("dispatch left callers waiting", count=len(unplaced), reasons=unplaced)
        return DispatchResult(offered=offered, decisions=decisions, unplaced=unplaced)

    async def expire_offers(self) -> list[str]:
        """RONA sweep: resolve offers whose timeout has passed (`D33`).

        Runs on a timer in the API process. Kept here rather than in `AssignmentService`
        because "which offers are stale" is a question about the *pool*, and the handshake
        service deliberately has no pool.
        """
        now = self._clock.now()
        timed_out: list[str] = []
        for call_session_id, (session, _) in list(self._waiting.items()):
            assignment = self._assignments.open_offer_for(call_session_id)
            if assignment is None:
                continue
            if (now - assignment.offered_at).total_seconds() < self._offer_timeout_s:
                continue
            await self._assignments.timeout(session, assignment_id=assignment.assignment_id)
            await self._notifier.send(
                assignment.agent_id,
                "offer_revoked",
                {"assignment_id": assignment.assignment_id, "reason": "timeout"},
            )
            timed_out.append(assignment.assignment_id)
        return timed_out


__all__ = ["AgentNotifier", "DispatchResult", "DispatchService"]


__all__ = [
    "AgentNotifier",
    "DispatchResult",
    "DispatchService",
    "InMemoryMatchingDecisionStore",
    "MatchingDecisionStore",
]
