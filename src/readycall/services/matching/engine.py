"""The matching engine: waiting callers in, explainable assignments out.

One waiting pool, not one queue per agent (`D22`). Per-agent queues look tidy and behave
badly — a caller stuck behind one busy agent waits while three qualified people sit idle.

Every tick does the same five things:

1. **rescue anyone past the wait ceiling** — hand them any qualified free agent before the
   optimiser gets a say (`D93`). This runs FIRST on purpose, and the reason is `B13`: the
   ceiling used to live inside `_guard`, which only runs for a call the solver *already*
   chose, so it could never fire for the one caller it existed to rescue;
2. build the candidate matrix, excluding pairs that fail a **hard filter**;
3. score each surviving pair as `fit x urgency`;
4. solve the rest of the matrix at once (`solver.hungarian`);
5. emit a `MatchingDecision` per call — **including the ones it decided not to assign**,
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
            return [self._nobody_online(call, now, watch) for call in calls]

        # --- 1: rescue anyone past the wait ceiling (`D93`) ----------------------------
        rescued, reserved = self._rescue(calls, agents, presence, now=now)

        # --- 2 + 3: candidates and scores ---------------------------------------------
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

        # --- 4: solve what the rescue pass left behind ----------------------------------
        #
        # The full matrix above is still built for every pair, because `D18` needs the whole
        # candidate list on the record. What the solver sees is that matrix with the rescued
        # rows and the reserved columns knocked out, so it cannot hand the same agent twice.
        remaining = [
            [
                solver.IMPOSSIBLE if (i in rescued or j in reserved) else matrix[i][j]
                for j in range(len(agents))
            ]
            for i in range(len(calls))
        ]
        assignment = (
            solver.hungarian(remaining)
            if self._solver_name == "hungarian"
            else solver.greedy(remaining)
        )

        # --- 5: one explainable decision per call --------------------------------------
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
            chosen_index = rescued.get(index, assignment[index])
            if chosen_index is None:
                # WHY it went unplaced, not just THAT it did (`D50`). The solver returns no
                # column for two opposite reasons and used to report both as "no agent with
                # the skill is available" - which was simply false whenever a qualified
                # agent existed and had merely been won by a higher-scoring call.
                qualified = sum(1 for j in range(len(agents)) if breakdowns[index][j][2] is None)
                decisions.append(
                    MatchingDecision(
                        decision_id=ids.decision_id(),
                        call_session_id=call.call_session_id,
                        at=now,
                        kind=(
                            MatchKind.ALL_QUALIFIED_BUSY
                            if qualified
                            else MatchKind.NO_QUALIFIED_AGENT
                        ),
                        candidates=candidates,
                        urgency=urgency,
                        rationale_th=(
                            f"เจ้าหน้าที่ที่ตรงทักษะ {qualified} คนกำลังรับสายอื่นอยู่"
                            if qualified
                            else "ไม่มีเจ้าหน้าที่ที่มีทักษะ/ภาษาที่ตรงออนไลน์อยู่"
                        ),
                        weights_version=self._weights.version,
                        solver=self._solver_name,
                        decide_ms=watch.elapsed_ms(),
                    )
                )
                continue

            agent = agents[chosen_index]
            fit = breakdowns[index][chosen_index][0]
            if index in rescued:
                # Already past the ceiling and already given someone. Nothing in `_guard`
                # may take that back: not the deferral, not the anti-hot-spot check.
                kind, reason = MatchKind.FALLBACK, None
            else:
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

    # --- the wait ceiling ---------------------------------------------------------------

    def _rescue(
        self,
        calls: list[WaitingCall],
        agents: list[Agent],
        presence: dict[str, AgentPresence],
        *,
        now: datetime,
    ) -> tuple[dict[int, int], set[int]]:
        """Past `MAX_WAIT_BEFORE_ANY_AGENT_S`, hand them anyone qualified. Longest wait first.

        Returns `(call index -> agent index, agent indices now spoken for)`.

        **Why this runs before the solver and not inside `_guard`.** `_guard` is only reached
        for a call the solver has *already* picked an agent for, so the ceiling could never
        fire for a caller who lost the matrix — which is precisely the caller it exists to
        rescue (`B13`). Measured before the fix: a health caller **600 s** in lost the only
        free agent to a fresh motor caller who fitted better, and the ceiling never ran.

        **Why the LOWEST-fit qualified agent, not the best.** The config's promise is *"any
        qualified agent regardless of fit"*, and `require_skill` already guarantees every
        candidate here can actually help — fit only says how well. Taking the specialist
        would hand this caller the best help on the floor and move the starvation onto
        whoever actually needed that specialist. The guarantee is *somebody competent, now*;
        spending the scarcest agent on it buys this caller a little and costs the next one a
        lot.
        """
        ceiling = self._weights.max_wait_before_any_agent_s
        starved = sorted(
            (i for i, call in enumerate(calls) if call.total_wait_s >= ceiling),
            key=lambda i: calls[i].total_wait_s,
            reverse=True,
        )

        rescued: dict[int, int] = {}
        reserved: set[int] = set()
        for i in starved:
            call = calls[i]
            options = [
                (
                    score_fit(
                        call, agents[j], presence[agents[j].agent_id], self._weights, now=now
                    ).total,
                    agents[j].agent_id,
                    j,
                )
                for j in range(len(agents))
                if j not in reserved
                and hard_filter(call, agents[j], presence[agents[j].agent_id], self._weights)
                is None
            ]
            if not options:
                # Every qualified agent is busy or already spoken for. The caller falls
                # through to the solver, which will reach the same conclusion and record
                # `ALL_QUALIFIED_BUSY` — the honest answer, and not this pass's to invent.
                continue
            # Sorted by agent id after fit so the choice is deterministic on a tie, which
            # scenario replays depend on (`D35`).
            _, _, j = min(options)
            rescued[i] = j
            reserved.add(j)
            log.info(
                "wait ceiling reached - assigning any qualified agent",
                call_session_id=call.call_session_id,
                agent_id=agents[j].agent_id,
                waited_s=round(call.total_wait_s, 1),
                ceiling_s=ceiling,
            )
        return rescued, reserved

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
        # NOTE: the wait ceiling is NOT checked here any more. It used to be, and it was
        # unreachable for the caller who needed it: `_guard` only runs for a call the solver
        # already chose, so a starved caller who lost the matrix never got here (`B13`).
        # `_rescue()` now handles them before the solver runs, and a rescued call never
        # reaches this method at all.

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

    def _nobody_online(
        self, call: WaitingCall, now: datetime, watch: Stopwatch
    ) -> MatchingDecision:
        """An empty floor is the degenerate case of "nobody qualified" (`D50`).

        It keeps its own rationale because the operational response differs: a skill gap
        needs a differently-skilled agent, an empty floor needs *anyone*.
        """
        return MatchingDecision(
            decision_id=ids.decision_id(),
            call_session_id=call.call_session_id,
            at=now,
            kind=MatchKind.NO_QUALIFIED_AGENT,
            urgency=score_urgency(call, self._weights),
            rationale_th="ไม่มีเจ้าหน้าที่ออนไลน์",
            weights_version=self._weights.version,
            solver=self._solver_name,
            decide_ms=watch.elapsed_ms(),
        )


def urgency_of(value: str) -> Urgency:
    return Urgency(value)


__all__ = ["MatchingEngine"]
