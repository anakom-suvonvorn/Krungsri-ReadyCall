"""The matching engine: hard filters, scoring, the solver, and the guard rails.

The solver tests are here because I got it wrong first time in a way nothing else would
have caught: a Python tuple-assignment order bug made `hungarian()` return an *empty*
matching on every input. No exception, no warning — every caller just came back
`no_candidates`, which looks exactly like "nobody was available". Known-optimal small cases
are the only thing that catches that class of bug.
"""

from __future__ import annotations

import pytest

from readycall.adapters.agent_directory.fixtures import FixtureAgentDirectory
from readycall.clock import ManualClock
from readycall.domain.enums import (
    AgentIntent,
    AgentSystemState,
    CefrLevel,
    Language,
    MatchKind,
    Urgency,
)
from readycall.domain.models import Agent, AgentLanguage, AgentPresence, AgentSkill
from readycall.errors import ConfigError
from readycall.services.matching import solver
from readycall.services.matching.engine import MatchingEngine
from readycall.services.matching.scoring import (
    WaitingCall,
    hard_filter,
    score_fit,
    score_urgency,
)
from readycall.services.matching.weights import MatchingWeights
from tests.conftest import REPO_ROOT

ROSTER = REPO_ROOT / "mock" / "agents" / "agents.json"


@pytest.fixture
def weights() -> MatchingWeights:
    return MatchingWeights.load(REPO_ROOT / "config" / "matching_weights.yaml")


@pytest.fixture
def directory() -> FixtureAgentDirectory:
    return FixtureAgentDirectory(ROSTER)


def make_agent(agent_id: str = "A", skill: str = "claims.assist", prof: float = 0.9, **kw) -> Agent:
    return Agent(
        agent_id=agent_id,
        display_name=agent_id,
        team="t",
        languages=kw.pop(
            "languages", (AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),)
        ),
        skills=(AgentSkill(skill_code=skill, proficiency=prof),),
        **kw,
    )


def make_presence(agent_id: str = "A", load: int = 0, *, clock: ManualClock) -> AgentPresence:
    return AgentPresence(
        agent_id=agent_id,
        system_state=AgentSystemState.AVAILABLE,
        agent_intent=AgentIntent.READY,
        since=clock.now(),
        current_load=load,
    )


def make_call(**kw) -> WaitingCall:
    base = dict(
        call_session_id="call_1",
        queue_id="q_claims",
        required_skill="claims.assist",
        intent_code="motor.claim.notify",
        intent_urgency=Urgency.NORMAL,
        waiting_s=10.0,
        sla_seconds=30,
    )
    base.update(kw)
    return WaitingCall(**base)  # type: ignore[arg-type]


# --- the solver --------------------------------------------------------------------------


def test_hungarian_finds_the_known_optimum() -> None:
    """Greedy takes 5 then is stuck with 2 (=7); the optimum is 5+4=9."""
    matrix = [[1.0, 5.0], [4.0, 2.0]]
    assert solver.total_score(matrix, solver.hungarian(matrix)) == pytest.approx(9.0)


def test_hungarian_beats_greedy_on_the_classic_trap() -> None:
    """The one-scarce-agent case: greedy hands them to whoever asks first."""
    matrix = [[9.0, 8.0], [8.5, 1.0]]
    hun, gre = solver.best_greedy_gap(matrix)
    assert hun == pytest.approx(16.5)
    assert gre == pytest.approx(10.0)
    assert hun > gre


def test_hungarian_never_scores_worse_than_greedy() -> None:
    """The property that actually matters, over pseudo-random matrices."""
    import random

    rng = random.Random(7)
    for _ in range(40):
        rows, cols = rng.randint(1, 7), rng.randint(1, 7)
        matrix = [[rng.uniform(0, 1) for _ in range(cols)] for _ in range(rows)]
        hun, gre = solver.best_greedy_gap(matrix)
        assert hun >= gre - 1e-9


def test_hungarian_assigns_something_when_anything_is_possible() -> None:
    """The regression guard for the tuple-assignment bug: an empty matching is not 'no fit'."""
    matrix = [[1.0, 2.0], [3.0, 4.0]]
    assert any(a is not None for a in solver.hungarian(matrix))


def test_hungarian_refuses_impossible_pairs() -> None:
    matrix = [[solver.IMPOSSIBLE, 5.0], [4.0, solver.IMPOSSIBLE]]
    assert solver.hungarian(matrix) == [1, 0]


def test_more_calls_than_agents_leaves_some_unassigned() -> None:
    matrix = [[1.0], [2.0], [3.0]]
    assignment = solver.hungarian(matrix)
    assert sum(1 for a in assignment if a is not None) == 1


@pytest.mark.parametrize("matrix", [[], [[]]])
def test_solver_handles_empty_input(matrix: list[list[float]]) -> None:
    assert all(a is None for a in solver.hungarian(matrix))


# --- hard filters ------------------------------------------------------------------------


def test_missing_skill_excludes_rather_than_down_ranks(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """A failed hard filter is not a low score — it is not a candidate."""
    agent = make_agent(skill="health.service")
    assert hard_filter(make_call(), agent, make_presence(clock=clock), weights) == "skill"


def test_language_is_graded_not_a_yes_no_flag(weights: MatchingWeights, clock: ManualClock) -> None:
    """`D38`: A2 English cannot carry a complex claim conversation."""
    weak = make_agent(
        languages=(
            AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),
            AgentLanguage(language=Language.EN, level=CefrLevel.A2),
        )
    )
    strong = make_agent(
        languages=(
            AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),
            AgentLanguage(language=Language.EN, level=CefrLevel.B2),
        )
    )
    english = make_call(acceptable_languages=(Language.EN,))
    assert hard_filter(english, weak, make_presence(clock=clock), weights) == "language"
    assert hard_filter(english, strong, make_presence(clock=clock), weights) is None


def test_a_full_agent_is_excluded(weights: MatchingWeights, clock: ManualClock) -> None:
    agent = make_agent(max_concurrent=1)
    presence = make_presence(load=1, clock=clock)
    assert hard_filter(make_call(), agent, presence, weights) == "at_capacity"


# --- scoring -----------------------------------------------------------------------------


def test_urgency_multiplies_so_waiting_eventually_wins(weights: MatchingWeights) -> None:
    """`D22`: the answer to starvation. A long wait must outrank a slightly better fit."""
    fresh = score_urgency(make_call(waiting_s=1.0), weights)
    stale = score_urgency(make_call(waiting_s=300.0), weights)
    assert stale.total > fresh.total
    assert stale.total <= weights.urgency_max


def test_urgency_is_relative_to_the_queues_own_sla(weights: MatchingWeights) -> None:
    """40s is nothing on a 120s policy question and near-breach on a 45s pre-auth."""
    slow = score_urgency(make_call(waiting_s=40.0, sla_seconds=120), weights)
    fast = score_urgency(make_call(waiting_s=40.0, sla_seconds=45), weights)
    assert fast.total > slow.total


def test_urgency_never_makes_skill_irrelevant(weights: MatchingWeights) -> None:
    """Clamped: a caller at a crash scene still must not reach someone unqualified."""
    desperate = score_urgency(
        make_call(waiting_s=9999.0, intent_urgency=Urgency.CRITICAL, is_vulnerable=True), weights
    )
    assert desperate.total <= weights.urgency_max


def test_continuity_expires_and_requires_a_good_outcome(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """Continuity must not become a rut."""
    from datetime import timedelta

    agent = make_agent("A007")
    presence = make_presence("A007", clock=clock)
    now = clock.now()

    recent_good = make_call(last_agent_id="A007", last_contact_at=now - timedelta(days=3))
    stale_call = make_call(last_agent_id="A007", last_contact_at=now - timedelta(days=400))
    bad_call = make_call(
        last_agent_id="A007", last_contact_at=now - timedelta(days=3), last_outcome_good=False
    )

    assert score_fit(recent_good, agent, presence, weights, now=now).continuity == 1.0
    assert score_fit(stale_call, agent, presence, weights, now=now).continuity == 0.0
    assert score_fit(bad_call, agent, presence, weights, now=now).continuity == 0.0


# --- the engine --------------------------------------------------------------------------


async def test_every_decision_is_explainable(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    """`D18`: 'why did I get this call' must be answerable from stored data."""
    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    presence = {
        a.agent_id: make_presence(a.agent_id, clock=clock) for a in await directory.list_agents()
    }

    decisions = await engine.match([make_call()], presence)
    decision = decisions[0]

    assert decision.rationale_th
    assert decision.weights_version == weights.version
    assert decision.solver == "hungarian"
    assert decision.candidates, "every candidate considered must be kept, not just the winner"
    assert decision.urgency is not None
    assert decision.decide_ms is not None


async def test_excluded_candidates_record_why(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    presence = {
        a.agent_id: make_presence(a.agent_id, clock=clock) for a in await directory.list_agents()
    }

    decision = (await engine.match([make_call()], presence))[0]
    excluded = [c for c in decision.candidates if c.fit.hard_filter_failed is not None]
    assert excluded, "most of a 15-agent roster cannot handle a motor claim"
    assert all(c.fit.hard_filter_failed == "skill" for c in excluded)


async def test_no_agents_online_is_a_decision_not_a_crash(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    decision = (await engine.match([make_call()], {}))[0]
    assert decision.kind is MatchKind.NO_QUALIFIED_AGENT
    assert decision.chosen_agent_id is None


class ListDirectory:
    """An `AgentDirectory` over a fixed list, for contrived rosters."""

    name = "list"

    def __init__(self, agents: list[Agent]) -> None:
        self._agents = agents

    async def get_agent(self, agent_id: str) -> Agent | None:
        return next((a for a in self._agents if a.agent_id == agent_id), None)

    async def list_agents(self, *, active_only: bool = True) -> list[Agent]:
        return [a for a in self._agents if a.is_active or not active_only]

    async def agents_with_skill(self, skill_code: str) -> list[Agent]:
        return [a for a in self._agents if a.proficiency_for(skill_code) > 0.0]


async def test_losing_the_contest_for_an_agent_is_not_a_roster_gap(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """`D50`: the call that merely lost must not be told nobody is qualified.

    One qualified agent, two callers who both need them. Exactly one can be placed. The
    other is a CAPACITY problem — the wrong answer here ("no agent with this skill is
    available") sends a supervisor hiring for a skill they already have on the floor.
    """
    only_one = make_agent("A1", skill="claims.assist", prof=0.9)
    engine = MatchingEngine(directory=ListDirectory([only_one]), weights=weights, clock=clock)
    presence = {"A1": make_presence("A1", clock=clock)}

    winner = make_call(call_session_id="c_win", waiting_s=120.0)
    loser = make_call(call_session_id="c_lose", waiting_s=5.0)
    by_id = {d.call_session_id: d for d in await engine.match([winner, loser], presence)}

    assert by_id["c_win"].chosen_agent_id == "A1"
    assert by_id["c_lose"].chosen_agent_id is None
    assert by_id["c_lose"].kind is MatchKind.ALL_QUALIFIED_BUSY
    # ...and the evidence is right there in the decision: a candidate who passed every
    # hard filter. That contradiction is what made the old label detectably wrong.
    assert any(c.fit.hard_filter_failed is None for c in by_id["c_lose"].candidates)


async def test_a_skill_nobody_holds_is_a_roster_gap(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    # A skill that is NOT `make_call`'s default. It read `motor.policy` before `D117`,
    # which the rename mapped onto the same code the default call now asks for - so the
    # "wrong skill" agent became a perfectly qualified one and the gap disappeared.
    wrong_skill = make_agent("A1", skill="life.advice", prof=0.9)
    engine = MatchingEngine(directory=ListDirectory([wrong_skill]), weights=weights, clock=clock)
    presence = {"A1": make_presence("A1", clock=clock)}

    decision = (await engine.match([make_call()], presence))[0]
    assert decision.kind is MatchKind.NO_QUALIFIED_AGENT
    assert all(c.fit.hard_filter_failed is not None for c in decision.candidates)


async def test_unplaced_kind_always_agrees_with_the_candidate_list(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    """The invariant, over a full contended load rather than one contrived pair.

    A decision that says "nobody qualified" while listing a qualified candidate is a lie,
    and it is a lie a reader can catch — so a test can too. Under-staffing the floor makes
    both kinds occur in the same tick.
    """
    roster = await directory.list_agents()
    presence = {a.agent_id: make_presence(a.agent_id, clock=clock) for a in roster[:3]}
    calls = [
        make_call(
            call_session_id=f"c{i}",
            required_skill=skill,
            queue_id="q_service",
            # Past `defer_max_wait_s` (60) so nothing is held back, and well under the
            # 180 s fallback ceiling. Without this the contended motor call is DEFERred
            # and the ALL_QUALIFIED_BUSY branch never runs.
            waiting_s=float(70 + i * 7),
        )
        # Four motor calls against three motor-capable agents guarantees one loses the
        # contest outright; `life`/`health` have nobody at all on this cut-down floor.
        for i, skill in enumerate(
            # Four against the contended skill, then two skills this cut-down floor does
            # not hold at all - so ALL_QUALIFIED_BUSY and NO_QUALIFIED_AGENT both occur in
            # one tick, which is the whole point. All six were briefly the SAME skill after
            # `D117`'s rename, and a test with no variety cannot see a difference.
            ["claims.assist"] * 4 + ["life.advice", "health.advice"],
        )
    ]

    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    decisions = await engine.match(calls, presence)
    # `chosen_agent_id is None` covers THREE meanings, not two: a deferred call is being
    # held on purpose for a better agent, which is neither a roster gap nor a capacity
    # one. (The first draft of this test missed that and failed on a DEFER.)
    unplaced = [d for d in decisions if d.chosen_agent_id is None and d.kind is not MatchKind.DEFER]
    assert unplaced, "three agents cannot cover six calls across three skills"

    kinds = set()
    for d in unplaced:
        had_qualified = any(c.fit.hard_filter_failed is None for c in d.candidates)
        expected = MatchKind.ALL_QUALIFIED_BUSY if had_qualified else MatchKind.NO_QUALIFIED_AGENT
        assert d.kind is expected, f"{d.call_session_id}: {d.kind} contradicts its candidates"
        kinds.add(d.kind)
    assert kinds == {MatchKind.ALL_QUALIFIED_BUSY, MatchKind.NO_QUALIFIED_AGENT}, (
        "this fixture is meant to exercise BOTH reasons; if it stops doing so the test "
        "has quietly become weaker than it looks"
    )


async def test_past_the_wait_ceiling_takes_anyone_qualified(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    presence = {
        a.agent_id: make_presence(a.agent_id, clock=clock) for a in await directory.list_agents()
    }

    long_wait = make_call(waiting_s=weights.max_wait_before_any_agent_s + 10)
    decision = (await engine.match([long_wait], presence))[0]
    assert decision.kind is MatchKind.FALLBACK


# --- D93 / B13: the ceiling has to survive CONTENTION, which is the only case that matters -
#
# The test above passes with one caller and a whole floor free, which is why the bug lived:
# with nobody to lose to, the solver picks them anyway and `_guard` stamps FALLBACK on a
# decision it did not actually cause. Every test below puts a second caller in the way.


def _dual_skill_agent(agent_id: str, health: float, motor: float) -> Agent:
    """One agent, two product lines - 6 of the 15 real agents look like this."""
    return Agent(
        agent_id=agent_id,
        display_name=agent_id,
        team="mixed",
        languages=(AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),),
        skills=(
            AgentSkill(skill_code="health.service", proficiency=health),
            AgentSkill(skill_code="claims.assist", proficiency=motor),
        ),
    )


def _health_call(cid: str, waiting_s: float, **kw: object) -> WaitingCall:
    return make_call(
        call_session_id=cid,
        queue_id="q_service_health",
        required_skill="health.service",
        intent_code="health.service.policy",
        waiting_s=waiting_s,
        **kw,
    )


async def test_a_starved_caller_beats_a_fresher_one_who_fits_better(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """`B13`. The ceiling used to live in `_guard`, which only runs for a call the solver
    ALREADY chose - so it could never fire for a caller who lost the matrix, which is
    exactly the caller it exists to rescue.

    Measured before the fix: a health caller 600 s in scored 0.600 against a fresh motor
    caller's 2.420 for the one shared agent, and lost. The config has always promised the
    opposite - "past this wait, drop to ANY qualified agent regardless of fit".
    """
    only_one = _dual_skill_agent("A_dual", health=0.10, motor=1.0)
    engine = MatchingEngine(directory=ListDirectory([only_one]), weights=weights, clock=clock)
    presence = {"A_dual": make_presence("A_dual", clock=clock)}

    starved = _health_call("call_starved", waiting_s=600.0)
    fresh = make_call(call_session_id="call_fresh", waiting_s=0.0, intent_urgency=Urgency.CRITICAL)

    by_call = {d.call_session_id: d for d in await engine.match([starved, fresh], presence)}

    assert by_call["call_starved"].kind is MatchKind.FALLBACK
    assert by_call["call_starved"].chosen_agent_id == "A_dual"
    # And the caller who lost is told the honest reason: a capacity shortfall, not a
    # roster gap - someone qualified exists, they were just taken (`D50`).
    assert by_call["call_fresh"].kind is MatchKind.ALL_QUALIFIED_BUSY
    assert by_call["call_fresh"].chosen_agent_id is None


async def test_the_rescue_leaves_the_specialist_for_whoever_needs_them(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """`D93` takes the LOWEST-fit qualified agent, not the best.

    `require_skill` already guarantees everyone in this pool can help; fit only says how
    well. Handing a starved caller the specialist would buy them a little and move the
    starvation onto whoever actually needed that specialist.
    """
    weak = make_agent("A_weak", skill="health.service", prof=0.35)
    strong = make_agent("A_strong", skill="health.service", prof=0.98)
    engine = MatchingEngine(directory=ListDirectory([strong, weak]), weights=weights, clock=clock)
    presence = {
        "A_weak": make_presence("A_weak", clock=clock),
        "A_strong": make_presence("A_strong", clock=clock),
    }

    starved = _health_call("call_starved", waiting_s=400.0)
    decision = (await engine.match([starved], presence))[0]

    assert decision.kind is MatchKind.FALLBACK
    assert decision.chosen_agent_id == "A_weak", "the specialist stays free"


async def test_two_starved_callers_are_rescued_longest_wait_first(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    only_one = make_agent("A_only", skill="health.service", prof=0.8)
    engine = MatchingEngine(directory=ListDirectory([only_one]), weights=weights, clock=clock)
    presence = {"A_only": make_presence("A_only", clock=clock)}

    newer = _health_call("call_newer", waiting_s=200.0)
    older = _health_call("call_older", waiting_s=900.0)

    by_call = {d.call_session_id: d for d in await engine.match([newer, older], presence)}

    assert by_call["call_older"].chosen_agent_id == "A_only"
    assert by_call["call_newer"].chosen_agent_id is None
    assert by_call["call_newer"].kind is MatchKind.ALL_QUALIFIED_BUSY


async def test_the_rescue_never_hands_out_an_unqualified_agent(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """ "Any qualified agent" is not "anyone". The hard filters still run first: a caller
    waiting an hour must not be connected to someone who cannot help them (`D22`).
    """
    wrong_skill = make_agent("A_motor", skill="claims.assist", prof=1.0)
    engine = MatchingEngine(directory=ListDirectory([wrong_skill]), weights=weights, clock=clock)
    presence = {"A_motor": make_presence("A_motor", clock=clock)}

    starved = _health_call("call_starved", waiting_s=3600.0)
    decision = (await engine.match([starved], presence))[0]

    assert decision.chosen_agent_id is None
    assert decision.kind is MatchKind.NO_QUALIFIED_AGENT, "a roster gap; waiting cannot fix it"


async def test_a_rescued_caller_is_never_held_back_by_a_guard(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """Deferral and the anti-hot-spot check both exist to improve a match. Neither may
    apply to someone already past the ceiling - that is the point of being past it.
    """
    weak = make_agent("A_weak", skill="health.service", prof=0.30)
    strong = make_agent("A_strong", skill="health.service", prof=0.99)
    engine = MatchingEngine(directory=ListDirectory([strong, weak]), weights=weights, clock=clock)
    presence = {
        "A_weak": make_presence("A_weak", clock=clock),
        "A_strong": make_presence("A_strong", clock=clock),
    }

    # A fit gap far past `defer_min_fit_gap`, which would normally trigger a DEFER.
    starved = _health_call("call_starved", waiting_s=500.0)
    decision = (await engine.match([starved], presence))[0]

    assert decision.kind is MatchKind.FALLBACK
    assert decision.chosen_agent_id is not None


# --- D94 / Q25: the ceiling is per urgency tier ------------------------------------------
#
# `D93` made the ceiling reachable; it was still ONE number, which meant a routine caller
# 181s in outranked a fresh emergency for the last qualified agent. Every test here needs
# contention to mean anything, same lesson as `B13`.


def test_the_ceiling_falls_as_urgency_rises(weights: MatchingWeights) -> None:
    """The shipped table, read back as the promise it encodes."""
    assert weights.ceiling_for(Urgency.CRITICAL) == 60.0
    assert weights.ceiling_for(Urgency.HIGH) == 120.0
    assert weights.ceiling_for(Urgency.NORMAL) == 180.0
    assert weights.ceiling_for(Urgency.LOW) == 270.0
    # The default is what an unnamed tier would fall back to, and it must still be the
    # old single value so `D93`'s behaviour for `normal` is unchanged.
    assert weights.max_wait_before_any_agent_s == 180.0


async def test_an_emergency_reaches_the_ceiling_before_a_routine_caller_does(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """`Q25`, the case that prompted this. One shared agent, both callers qualified.

    At 90 s the emergency is past its own 60 s ceiling and the routine caller is not yet
    past their 180 s one — so the emergency is rescued and the routine caller waits. Under
    a single 180 s ceiling NEITHER was rescued, and the routine caller won on raw score
    whenever their fit was better.
    """
    only_one = _dual_skill_agent("A_dual", health=1.0, motor=0.10)
    engine = MatchingEngine(directory=ListDirectory([only_one]), weights=weights, clock=clock)
    presence = {"A_dual": make_presence("A_dual", clock=clock)}

    # The routine caller is the BETTER fit (1.0 vs 0.10) and has waited longer, so they
    # win the matrix outright. Only the per-tier ceiling can save the emergency.
    routine = _health_call("call_routine", waiting_s=150.0)
    emergency = make_call(
        call_session_id="call_emergency",
        waiting_s=90.0,
        intent_urgency=Urgency.CRITICAL,
    )

    by_call = {d.call_session_id: d for d in await engine.match([routine, emergency], presence)}

    assert by_call["call_emergency"].kind is MatchKind.FALLBACK
    assert by_call["call_emergency"].chosen_agent_id == "A_dual"
    assert by_call["call_routine"].chosen_agent_id is None
    assert by_call["call_routine"].kind is MatchKind.ALL_QUALIFIED_BUSY


async def test_when_both_are_past_their_ceiling_the_more_urgent_goes_first(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """Both are owed the guarantee and only one agent exists. Urgency decides.

    Note the routine caller has waited FOUR TIMES longer and still loses — that is the
    deliberate cost of this ordering, recorded in `D94` and worth re-reading if a patient
    caller ever appears to be stuck.
    """
    only_one = _dual_skill_agent("A_dual", health=1.0, motor=1.0)
    engine = MatchingEngine(directory=ListDirectory([only_one]), weights=weights, clock=clock)
    presence = {"A_dual": make_presence("A_dual", clock=clock)}

    routine = _health_call("call_routine", waiting_s=400.0)
    emergency = make_call(
        call_session_id="call_emergency", waiting_s=100.0, intent_urgency=Urgency.CRITICAL
    )

    by_call = {d.call_session_id: d for d in await engine.match([routine, emergency], presence)}

    assert by_call["call_emergency"].chosen_agent_id == "A_dual"
    assert by_call["call_routine"].chosen_agent_id is None


async def test_within_one_tier_the_longest_wait_still_wins(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """`D93`'s ordering survives inside a tier — urgency only breaks ties BETWEEN tiers."""
    only_one = make_agent("A_only", skill="health.service", prof=0.8)
    engine = MatchingEngine(directory=ListDirectory([only_one]), weights=weights, clock=clock)
    presence = {"A_only": make_presence("A_only", clock=clock)}

    newer = _health_call("call_newer", waiting_s=200.0)
    older = _health_call("call_older", waiting_s=900.0)

    by_call = {d.call_session_id: d for d in await engine.match([newer, older], presence)}

    assert by_call["call_older"].chosen_agent_id == "A_only"
    assert by_call["call_newer"].chosen_agent_id is None


async def test_a_patient_caller_gets_longer_before_fit_is_abandoned(
    weights: MatchingWeights, clock: ManualClock
) -> None:
    """The table cuts both ways, and this is the half that is easy to forget.

    A `low` caller at 200 s is past the OLD single ceiling of 180 s but not past their own
    270 s one, so they are not rescued — they stay in the matrix where fit still counts,
    which is the right answer for somebody who is not in any hurry.
    """
    weak = make_agent("A_weak", skill="health.service", prof=0.30)
    strong = make_agent("A_strong", skill="health.service", prof=0.99)
    engine = MatchingEngine(directory=ListDirectory([strong, weak]), weights=weights, clock=clock)
    presence = {
        "A_weak": make_presence("A_weak", clock=clock),
        "A_strong": make_presence("A_strong", clock=clock),
    }

    patient = _health_call("call_patient", waiting_s=200.0, intent_urgency=Urgency.LOW)
    decision = (await engine.match([patient], presence))[0]

    # Not a rescue, so the optimiser chose — and it takes the BEST agent, where a rescue
    # would deliberately have taken the worst.
    assert decision.chosen_agent_id == "A_strong"
    assert decision.kind is not MatchKind.FALLBACK


def test_a_ceiling_that_rises_with_urgency_is_refused_at_startup(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Getting the table backwards is silent: every call still routes, and the caller at
    the crash scene simply waits. So it fails the boot instead."""
    source = (REPO_ROOT / "config" / "matching_weights.yaml").read_text(encoding="utf-8")
    broken = source.replace("critical: 60", "critical: 600")
    path = tmp_path / "backwards.yaml"
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(ConfigError, match="must not rise with urgency"):
        MatchingWeights.load(path)


def test_an_unknown_urgency_in_the_table_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A typo would otherwise leave that tier silently on the default."""
    source = (REPO_ROOT / "config" / "matching_weights.yaml").read_text(encoding="utf-8")
    path = tmp_path / "typo.yaml"
    path.write_text(source.replace("critical: 60", "urgent: 60"), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown urgency"):
        MatchingWeights.load(path)


async def test_a_critical_caller_is_never_deferred(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    """Holding someone at a crash scene for a better-matched agent is indefensible."""
    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    presence = {
        a.agent_id: make_presence(a.agent_id, clock=clock) for a in await directory.list_agents()
    }

    critical = make_call(intent_urgency=Urgency.CRITICAL, waiting_s=2.0)
    decision = (await engine.match([critical], presence))[0]
    assert decision.kind is not MatchKind.DEFER


async def test_two_calls_do_not_get_the_same_agent(
    directory: FixtureAgentDirectory, weights: MatchingWeights, clock: ManualClock
) -> None:
    engine = MatchingEngine(directory=directory, weights=weights, clock=clock)
    presence = {
        a.agent_id: make_presence(a.agent_id, clock=clock) for a in await directory.list_agents()
    }

    calls = [make_call(call_session_id="c1"), make_call(call_session_id="c2")]
    chosen = [d.chosen_agent_id for d in await engine.match(calls, presence) if d.chosen_agent_id]
    assert len(chosen) == len(set(chosen))


# --- the roster and the weights ------------------------------------------------------------


async def test_no_skill_is_held_by_only_one_agent(directory: FixtureAgentDirectory) -> None:
    """`D22`: a skill one person holds means their lunch break closes a queue."""
    from readycall.domainpack import DomainPack

    pack = DomainPack.load(REPO_ROOT / "config")
    thin = {
        code: len(await directory.agents_with_skill(code))
        for code in pack.skills
        if len(await directory.agents_with_skill(code)) < 2
    }
    assert not thin, f"single-point-of-failure skills: {thin}"


def test_weights_reject_an_urgency_multiplier_below_one(tmp_path) -> None:
    """Below 1.0 would make waiting *lower* a score — the opposite of the intent."""
    import yaml

    raw = yaml.safe_load(
        (REPO_ROOT / "config" / "matching_weights.yaml").read_text(encoding="utf-8")
    )
    raw["urgency"]["min_multiplier"] = 0.5
    bad = tmp_path / "w.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="min_multiplier"):
        MatchingWeights.load(bad)


def test_weights_reject_a_negative_weight(tmp_path) -> None:
    import yaml

    raw = yaml.safe_load(
        (REPO_ROOT / "config" / "matching_weights.yaml").read_text(encoding="utf-8")
    )
    raw["fit"]["continuity"] = -1.0
    bad = tmp_path / "w.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="negative"):
        MatchingWeights.load(bad)


# --- B12: the accrued wait has to be LIVE, not whatever was passed at admit -------------


class _RecordingEngine:
    """Stands in for `MatchingEngine` and keeps what the dispatcher handed it."""

    def __init__(self) -> None:
        self.seen: list[list[WaitingCall]] = []

    async def match(self, calls, presence):
        self.seen.append(list(calls))
        return []


class _NoAssignments:
    def open_offer_for(self, call_session_id: str):
        return None

    def excluded_agents(self, call_session_id: str) -> tuple[str, ...]:
        return ()


class _NoPresence:
    def snapshot(self) -> dict[str, AgentPresence]:
        return {}


class _NoNotifier:
    async def send(self, agent_id: str, kind: str, payload: dict) -> None:
        return None

    async def broadcast(self, kind: str, payload: dict) -> None:
        return None


async def test_a_waiting_caller_accrues_urgency_while_only_the_clock_moves() -> None:
    """`B12`. `WaitingCall` is frozen and `tick()` rebuilds it — but it used to refresh
    only `excluded_agent_ids`, so `waiting_s` stayed at whatever `admit()` was given.

    That fed `score_urgency` a constant: `wait_pressure` never rose, `sla_risk` never
    fired, and neither did the ceiling that drops a caller to any-qualified-agent. All of
    `D22`'s anti-starvation was written, correct, and driven by nothing — `B7`'s family.

    The test moves nothing but the clock, which is the only way to catch it.
    """
    from readycall.domain.enums import CallState, EntryChannel
    from readycall.domain.models import CallSession
    from readycall.services.agents.dispatch import DispatchService

    clock = ManualClock()
    engine = _RecordingEngine()
    dispatch = DispatchService(
        engine=engine,  # type: ignore[arg-type]
        assignments=_NoAssignments(),  # type: ignore[arg-type]
        presence=_NoPresence(),  # type: ignore[arg-type]
        notifier=_NoNotifier(),  # type: ignore[arg-type]
        clock=clock,
    )

    session = CallSession(
        call_session_id="call_1",
        trace_id="trace_1",
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.MATCHED,
        created_at=clock.now(),
        queued_at=clock.now(),
        queue_id="q_service_health",
    )
    dispatch.admit(
        session,
        WaitingCall(
            call_session_id="call_1",
            queue_id="q_service_health",
            required_skill="health.service",
            intent_code="health.service.policy",
            intent_urgency=Urgency.NORMAL,
            waiting_s=0.0,
            sla_seconds=120,
        ),
    )

    await dispatch.tick()
    clock.advance(200)
    await dispatch.tick()

    first, second = engine.seen[0][0], engine.seen[1][0]
    assert first.total_wait_s == 0.0
    assert second.total_wait_s == pytest.approx(200.0), "the wait must be recomputed, not frozen"

    weights = MatchingWeights.load(REPO_ROOT / "config" / "matching_weights.yaml")
    assert score_urgency(first, weights).sla_risk == 0.0
    assert score_urgency(second, weights).sla_risk == 1.0
    assert second.total_wait_s >= weights.max_wait_before_any_agent_s, (
        "past the ceiling the guard must be able to fall back to any qualified agent"
    )


async def test_a_demo_callers_pre_accrued_wait_survives_the_refresh() -> None:
    """`waited_s` on the demo endpoint exists so a rehearsal can show a caller near their
    SLA without waiting. It rides on `waiting_credit_s` now, which is the field for wait
    that survives being re-scored — otherwise `B12`'s fix would silently delete it."""
    from readycall.domain.enums import CallState, EntryChannel
    from readycall.domain.models import CallSession
    from readycall.services.agents.dispatch import DispatchService

    clock = ManualClock()
    engine = _RecordingEngine()
    dispatch = DispatchService(
        engine=engine,  # type: ignore[arg-type]
        assignments=_NoAssignments(),  # type: ignore[arg-type]
        presence=_NoPresence(),  # type: ignore[arg-type]
        notifier=_NoNotifier(),  # type: ignore[arg-type]
        clock=clock,
    )
    session = CallSession(
        call_session_id="call_2",
        trace_id="trace_2",
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.MATCHED,
        created_at=clock.now(),
        queued_at=clock.now(),
        queue_id="q_service_health",
    )
    dispatch.admit(
        session,
        WaitingCall(
            call_session_id="call_2",
            queue_id="q_service_health",
            required_skill="health.service",
            intent_code="health.service.policy",
            intent_urgency=Urgency.NORMAL,
            waiting_s=0.0,
            waiting_credit_s=95.0,
            sla_seconds=120,
        ),
    )

    await dispatch.tick()
    clock.advance(30)
    await dispatch.tick()

    assert engine.seen[0][0].total_wait_s == pytest.approx(95.0)
    assert engine.seen[1][0].total_wait_s == pytest.approx(125.0), "credit + live elapsed"
