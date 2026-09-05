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
from datetime import timedelta
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
        max_offer_rounds: int = 0,
    ) -> None:
        self._engine = engine
        self._assignments = assignments
        self._presence = presence
        self._notifier = notifier
        self._clock = clock
        self._offer_timeout_s = offer_timeout_s
        self._decisions = decisions
        #: How many times a caller may go round the whole floor (`D113`). **0 is no cap**,
        #: which is the default and the shipped configuration: a caller who is cut off has
        #: to start again from the menu, while a caller still holding can hang up whenever
        #: they choose. Read from `matching_weights.yaml`, not from `Settings` — `Q26` is
        #: what an env var the code never reads turns into.
        self._max_offer_rounds = max_offer_rounds
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

    def _live(self, session: CallSession, call: WaitingCall) -> WaitingCall:
        """The pool's entry with its wait brought up to now (`B26`).

        **Stored `waiting_s` is the admit-time value and never moves.** `tick()` has
        recomputed it since `B12`, but only into the list it hands the matcher — the copy
        in `_waiting` stays at whatever `admit()` was given. So everything that *reads*
        the pool for a screen (the offer card's "รอมาแล้ว", the queue strip's "รอนานสุด")
        showed a number frozen at the moment the caller arrived, while the matcher was
        scoring them on the real one. Two answers to "how long has this person waited",
        and the one the human could see was the wrong one.

        Derived here rather than written back, for `D78`'s reason: `call_sessions` already
        knows when this caller was queued, and a second copy kept in step by remembering
        to update it is a second copy that will one day disagree.
        """
        now = self._clock.now()
        elapsed = session.wait_seconds(now)
        return replace(
            call,
            waiting_s=elapsed,
            # The anchor a screen counts from. Credit included, so the demo's "already
            # waited 40 s" and a real 40-second wait are the same thing to the client and
            # neither of them needs to know which it is looking at.
            waiting_since=now - timedelta(seconds=elapsed + call.waiting_credit_s),
        )

    def waiting(self) -> list[WaitingCall]:
        return [self._live(session, call) for session, call in self._waiting.values()]

    def waiting_call(self, call_session_id: str) -> WaitingCall | None:
        """The pool's view of one caller — urgency and accrued wait, for the offer card."""
        entry = self._waiting.get(call_session_id)
        return self._live(entry[0], entry[1]) if entry else None

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
                self._live(*self._waiting[call_session_id]),
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
                if decision.kind is MatchKind.ALL_DECLINED:
                    self._circle_back(decision.call_session_id)
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
                    # `D113`. Both are facts about *this* offer that the agent cannot
                    # work out from the card, and both change what declining means.
                    "offer_round": self._assignments.rounds_for(session.call_session_id),
                    "sole_candidate": sole_candidate(decision),
                },
            )

        if unplaced:
            log.info("dispatch left callers waiting", count=len(unplaced), reasons=unplaced)
        return DispatchResult(offered=offered, decisions=decisions, unplaced=unplaced)

    def _circle_back(self, call_session_id: str) -> None:
        """Every qualified agent has declined. Clear the exclusions and try again (`D113`).

        Done here rather than inside the matcher because it is a policy about *offers*,
        and `AssignmentService` owns those. The matcher's job was to report the situation
        honestly, which `D108` made it do; acting on the report is this service's.

        **The re-offer happens on the NEXT tick, not this one.** A second solve inside the
        same tick would buy about a second for a caller who has already been round a whole
        floor, at the cost of two decision records for one moment — and the record of the
        exhausted round is the one a supervisor needs to be able to read cleanly.
        """
        rounds = self._assignments.rounds_for(call_session_id)
        cap = self._max_offer_rounds
        if cap and rounds >= cap:
            # Deliberately NOT a state change. `D25`'s voicemail path — record a message,
            # create a briefed callback — is P6 and does not exist, and moving the call to
            # `VOICEMAIL` would strand it in a state nothing handles, which is the
            # half-built guard `B7` keeps teaching. It stays reported as `ALL_DECLINED`,
            # which is true, and a supervisor sees a caller nothing will resolve.
            log.warning(
                "caller has been round the floor the maximum number of times",
                call_session_id=call_session_id,
                rounds=rounds,
                max_offer_rounds=cap,
            )
            return
        self._assignments.start_new_round(call_session_id)

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


def sole_candidate(decision: MatchingDecision) -> bool:
    """Is the chosen agent the ONLY one who could take this call right now (`D113`)?

    Counted from the decision's own candidates, where `hard_filter_failed is None` means
    the agent passed **the same filter the matcher used** — not a second computation of
    availability, which is how `B25` happened: `PresenceView.offerable` and
    `AgentPresence.is_available()` both answered a question the matcher never asked.

    Note what "could take this call" includes: an agent who declined earlier in this round
    is excluded (`D52`) and so does not count. That is the truthful reading for the agent
    holding the card — if they decline, it comes back to them.
    """
    return sum(1 for c in decision.candidates if c.fit.hard_filter_failed is None) == 1


__all__ = [
    "AgentNotifier",
    "DispatchResult",
    "DispatchService",
    "sole_candidate",
]


__all__ = [
    "AgentNotifier",
    "DispatchResult",
    "DispatchService",
    "InMemoryMatchingDecisionStore",
    "MatchingDecisionStore",
]
