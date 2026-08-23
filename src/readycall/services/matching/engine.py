"""The matching engine: waiting callers in, explainable assignments out.

One waiting pool, not one queue per agent (`D22`). Per-agent queues look tidy and behave
badly — a caller stuck behind one busy agent waits while three qualified people sit idle.

Every tick does the same four things:

1. build the candidate matrix, excluding pairs that fail a **hard filter**;
2. score each surviving pair as `fit x urgency`;
3. solve the whole matrix at once (`solver.hungarian`);
4. emit a `MatchingDecision` per call — **including the ones it decided not to assign**,
   and including every candidate it considered.

That last point is the difference between a system people leave switched on and one they
turn off. "Why did I get this call?" and "why is this caller still waiting?" both have to be
answerable from stored data (`D18`).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from readycall import ids
from readycall.clock import Clock, Stopwatch
from readycall.domain.enums import MatchKind, Urgency
from readycall.domain.models import (
    Agent,
    AgentPresence,
    FitBreakdown,
    MatchCandidate,
    MatchingDecision,
)
from readycall.logging import get_logger
from readycall.ports.agent_directory import AgentDirectory
from readycall.services.matching import solver
from readycall.services.matching.scoring import (
    WaitingCall,
    hard_filter,
    rationale_th,
    score_fit,
    score_urgency,
)
from readycall.services.matching.weights import MatchingWeights

log = get_logger(__name__)


class MatchingEngine:
    def __init__(
        self,
        *,
        directory: AgentDirectory,
        weights: MatchingWeights,
        clock: Clock,
        solver_name: str = "hungarian",
    ) -> None:
        self._directory = directory
        self._weights = weights
        self._clock = clock
        self._solver_name = solver_name
        #: Rolling window of recent assignments, for the anti-hot-spot check.
        self._recent: deque[str] = deque(maxlen=weights.hot_spot_window_calls)

    async def match(
        self,
        calls: list[WaitingCall],
        presence: dict[str, AgentPresence],
    ) -> list[MatchingDecision]:
        watch = Stopwatch(self._clock)
        now = self._clock.now()
        if not calls:
            return []

        agents = [a for a in await self._directory.list_agents() if a.agent_id in presence]
        if not agents:
            return [self._no_candidates(call, now, watch) for call in calls]

        # --- 1 + 2: candidates and scores ---------------------------------------------
        matrix: list[list[float]] = []
        breakdowns: list[list[tuple[FitBreakdown, float, str | None]]] = []
        for call in calls:
            row_scores: list[float] = []
            row_detail: list[tuple[FitBreakdown, float, str | None]] = []
            urgency = score_urgency(call, self._weights)
            for agent in agents:
                who = presence[agent.agent_id]
                failed = hard_filter(call, agent, who, self._weights)
                fit = score_fit(call, agent, who, self._weights, now=now)
                if failed is not None:
                    row_scores.append(solver.IMPOSSIBLE)
                else:
                    row_scores.append(fit.total * urgency.total)
                row_detail.append((fit, urgency.total, failed))
            matrix.append(row_scores)
            breakdowns.append(row_detail)

        # --- 3: solve the whole matrix at once -----------------------------------------
        assignment = (
            solver.hungarian(matrix) if self._solver_name == "hungarian" else solver.greedy(matrix)
        )

        # --- 4: one explainable decision per call --------------------------------------
        decisions: list[MatchingDecision] = []
        for index, call in enumerate(calls):
            urgency = score_urgency(call, self._weights)
            candidates = tuple(
                MatchCandidate(
                    agent_id=agents[j].agent_id,
                    fit=breakdowns[index][j][0].model_copy(
                        update={"hard_filter_failed": breakdowns[index][j][2]}
                    ),
                    score=matrix[index][j] if matrix[index][j] > solver.IMPOSSIBLE else 0.0,
                )
                for j in range(len(agents))
            )
            chosen_index = assignment[index]
            if chosen_index is None:
                decisions.append(
                    MatchingDecision(
                        decision_id=ids.decision_id(),
                        call_session_id=call.call_session_id,
                        at=now,
                        kind=MatchKind.NO_CANDIDATES,
                        candidates=candidates,
                        urgency=urgency,
                        rationale_th="ไม่มีเจ้าหน้าที่ที่มีทักษะ/ภาษาที่ตรงและว่างอยู่",
                        weights_version=self._weights.version,
                        solver=self._solver_name,
                        decide_ms=watch.elapsed_ms(),
                    )
                )
                continue

            agent = agents[chosen_index]
            fit = breakdowns[index][chosen_index][0]
            kind, reason = self._guard(call, agent, fit, candidates, agents, breakdowns[index])

            decisions.append(
                MatchingDecision(
                    decision_id=ids.decision_id(),
                    call_session_id=call.call_session_id,
                    at=now,
                    kind=kind,
                    candidates=candidates,
                    urgency=urgency,
                    chosen_agent_id=agent.agent_id if kind is not MatchKind.DEFER else None,
                    total_score=matrix[index][chosen_index],
                    deferred_for_agent_id=reason if kind is MatchKind.DEFER else None,
                    rationale_th=rationale_th(call, agent, fit, urgency),
                    weights_version=self._weights.version,
                    solver=self._solver_name,
                    decide_ms=watch.elapsed_ms(),
                )
            )
            if kind in (MatchKind.ASSIGN, MatchKind.FALLBACK):
                self._recent.append(agent.agent_id)

        log.info(
            "matching tick",
            calls=len(calls),
            agents=len(agents),
            assigned=sum(1 for d in decisions if d.chosen_agent_id),
            decide_ms=round(watch.elapsed_ms(), 2),
        )
        return decisions

    # --- guard rails --------------------------------------------------------------------

    def _guard(
        self,
        call: WaitingCall,
        agent: Agent,
        fit: FitBreakdown,
        candidates: tuple[MatchCandidate, ...],
        agents: list[Agent],
        detail: list[tuple[FitBreakdown, float, str | None]],
    ) -> tuple[MatchKind, str | None]:
        """Decide whether to assign, fall back, or hold — and say which."""
        # Past the ceiling, take anyone qualified. A perfect match nobody is available for
        # is worth less than a good one who is.
        if call.total_wait_s >= self._weights.max_wait_before_any_agent_s:
            return MatchKind.FALLBACK, None

        # Anti-hot-spot: stop one strong agent absorbing every hard call in a shift.
        if len(self._recent) == self._recent.maxlen:
            share = sum(1 for a in self._recent if a == agent.agent_id) / len(self._recent)
            if share > self._weights.hot_spot_max_share:
                log.info("hot-spot guard tripped", agent_id=agent.agent_id, share=round(share, 2))
                return MatchKind.FALLBACK, None

        if not self._weights.defer_enabled:
            return MatchKind.ASSIGN, None
        if call.intent_urgency.weight >= self._weights.defer_never_above_urgency.weight:
            # Never make a caller at a crash scene wait for a better-matched agent.
            return MatchKind.ASSIGN, None
        if call.total_wait_s >= self._weights.defer_max_wait_s:
            return MatchKind.ASSIGN, None

        # Only hold if somebody clearly better is *about to* free up. Anything less and
        # "wait for someone better" is how a caller gets forgotten.
        better = [
            (agents[j].agent_id, detail[j][0].total)
            for j in range(len(agents))
            if detail[j][2] is None
            and detail[j][0].total - fit.total >= self._weights.defer_min_fit_gap
        ]
        if better:
            best = max(better, key=lambda t: t[1])
            return MatchKind.DEFER, best[0]
        return MatchKind.ASSIGN, None

    def _no_candidates(
        self, call: WaitingCall, now: datetime, watch: Stopwatch
    ) -> MatchingDecision:
        return MatchingDecision(
            decision_id=ids.decision_id(),
            call_session_id=call.call_session_id,
            at=now,
            kind=MatchKind.NO_CANDIDATES,
            urgency=score_urgency(call, self._weights),
            rationale_th="ไม่มีเจ้าหน้าที่ออนไลน์",
            weights_version=self._weights.version,
            solver=self._solver_name,
            decide_ms=watch.elapsed_ms(),
        )


def urgency_of(value: str) -> Urgency:
    return Urgency(value)


__all__ = ["MatchingEngine"]
