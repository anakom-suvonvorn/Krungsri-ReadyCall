"""A whole floor, doing everything at once, checked against invariants rather than outcomes.

**Why this exists.** `B25` and `B26` were both found by the user clicking around a running
workstation, and neither could have been found any other way by the suite as it stood:
every existing test sets one or two agents `ready` and walks one caller through one happy
path. The faults live in the *combinations* — an agent who signed out while a caller was
queued, one desk ringing while three more callers arrive, a decline landing in the same
tick as a new arrival.

**It asserts invariants, not results.** Which agent gets which caller depends on the
matcher's scoring and is allowed to change; what may never happen is a caller offered to
two desks, or a desk holding two callers, or anybody being rung who said they were not
ready. Writing it the other way round — pinning exact assignments — produces a test that
breaks every time a weight changes and proves nothing about safety.

**It is deterministic.** A fixed seed and a `ManualClock`, so a failure is reproducible and
a green run means something. That is the difference between this and a soak test: no
sleeping, no wall clock, no flakes. `--seed` variants belong in a separate, occasional job.

The `divide and conquer` note in the user's proposal is taken seriously: this is the shared
harness, and each scenario below is a small named situation rather than one giant
simulation nobody can debug when it fails.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, sweep_once
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.domain.enums import AgentSystemState
from tests.conftest import REPO_ROOT

#: Agents and the queue they can serve, from `mock/agents/agents.json`.
MOTOR = ("A001", "A002", "A003")
HEALTH = ("A004", "A005", "A006")
INTENTS = {
    "motor": ("motor.claim.accident", "motor.policy.renew", "motor.roadside_assist"),
    "health": ("health.ipd.preauth", "health.claim.status", "health.coverage.query"),
}


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC))


@pytest.fixture
def app(clock: ManualClock) -> Any:
    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        # Driven by hand: a background sweeper would make a failure depend on when the
        # loop happened to run, which is the flake this whole file is written to avoid.
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )
    with TestClient(create_app(settings, clock=clock)) as client:
        yield client


class Floor:
    """One `TestClient` per agent, so sessions do not overwrite each other.

    The single-client shortcut used elsewhere in the suite re-issues the agent cookie on
    every `demo-login`, which is fine when one agent acts at a time and silently wrong the
    moment two are meant to be signed in together — the exact situation being tested.
    """

    def __init__(self, app: Any, agent_ids: tuple[str, ...]) -> None:
        self.app = app
        self.clients: dict[str, Any] = {}
        for agent_id in agent_ids:
            client = TestClient(app.app)
            assert (
                client.post("/v1/agent/demo-login", json={"agent_id": agent_id}).status_code == 200
            )
            self.clients[agent_id] = client

    @property
    def container(self) -> Any:
        return self.app.app.state.container

    def me(self, agent_id: str) -> dict[str, Any]:
        response = self.clients[agent_id].get("/v1/agent/me")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def declare(self, agent_id: str, intent: str) -> None:
        self.clients[agent_id].post("/v1/agent/state", json={"agent_intent": intent})

    def accept(self, agent_id: str, assignment_id: str) -> None:
        self.clients[agent_id].post(f"/v1/agent/offers/{assignment_id}/accept")

    def decline(self, agent_id: str, assignment_id: str, *, stop: bool = False) -> None:
        self.clients[agent_id].post(
            f"/v1/agent/offers/{assignment_id}/decline",
            json={"reason": "busy", "stop_offering": stop},
        )

    def hang_up(self, agent_id: str, call_session_id: str) -> None:
        self.clients[agent_id].post(f"/v1/agent/calls/{call_session_id}/end", json={})

    def wrap_up(self, agent_id: str, call_session_id: str) -> None:
        self.clients[agent_id].post(
            f"/v1/agent/calls/{call_session_id}/wrapup",
            json={"disposition": "resolved", "notes": "-"},
        )

    async def heartbeat(self) -> None:
        """What the open WebSocket does every ten seconds (`D51`).

        Without it, advancing the clock past `AGENT_PRESENCE_TTL_S` drops every agent as
        a closed laptop — correct behaviour, and not what this test is about. A test that
        moves time has to move everything time affects, or it is measuring the wrong
        thing and will read as a bug in whatever it happens to be pointed at.
        """
        for agent_id in self.clients:
            await self.container.presence.heartbeat(agent_id)

    def place(self, line: str, rng: random.Random, **kw: Any) -> str:
        response = self.clients[next(iter(self.clients))].post(
            "/v1/demo/calls",
            json={
                "intent_code": rng.choice(INTENTS[line]),
                "intake_keys": ["2"],
                "ignore_hours": True,
                "waited_s": rng.choice([0, 15, 40]),
                **kw,
            },
        )
        assert response.status_code == 200, response.text
        return str(response.json()["call_session_id"])


# --- the invariants ----------------------------------------------------------------------


async def check_invariants(floor: Floor, *, seen_waits: dict[str, float]) -> None:
    """Everything that must be true no matter what anybody clicked.

    Deliberately about **safety**, not about who got what: the matcher is allowed to
    change its mind about the best agent; it is never allowed to ring somebody who is on
    lunch, or hand one caller to two desks.
    """
    container = floor.container
    presence = container.presence.snapshot()

    open_by_agent: dict[str, list[str]] = {}
    open_by_call: dict[str, list[str]] = {}
    for agent_id in floor.clients:
        for assignment in container.assignments.for_agent(agent_id):
            if container.assignments.open_offer_for(assignment.call_session_id) is assignment:
                open_by_agent.setdefault(agent_id, []).append(assignment.call_session_id)
                open_by_call.setdefault(assignment.call_session_id, []).append(agent_id)

    for agent_id, calls in open_by_agent.items():
        assert len(calls) == 1, (
            f"{agent_id} is ringing for {len(calls)} callers at once: {calls}. "
            "One desk collecting the whole queue is `B25`."
        )
    for call_session_id, agents in open_by_call.items():
        assert len(agents) == 1, (
            f"{call_session_id} is ringing at {agents} simultaneously - two agents can "
            "both accept and one of them loses a caller mid-greeting"
        )

    for agent_id, who in presence.items():
        agent = await container.agents.get_agent(agent_id)
        assert who.current_load <= agent.max_concurrent, (
            f"{agent_id} is carrying {who.current_load} calls, over their limit of "
            f"{agent.max_concurrent}"
        )
        if agent_id in open_by_agent:
            # The one `B25` was about: an agent's own screen said `offerable: false` and
            # the phone rang anyway. `OFFERING` is the state the offer itself puts them
            # in, so it is what "was allowed to be rung" looks like afterwards.
            assert who.system_state is AgentSystemState.OFFERING, (
                f"{agent_id} is holding an offer while {who.system_state} / "
                f"{who.agent_intent} - they were rung when they could not take a call"
            )

    # `D52`: a missed or declined offer excludes that agent for the life of the call.
    for call_session_id, agents in open_by_call.items():
        excluded = container.assignments.excluded_agents(call_session_id)
        assert not set(agents) & set(excluded), (
            f"{call_session_id} was re-offered to {set(agents) & set(excluded)}, who "
            "already turned it down"
        )

    # `B26`: a caller's wait is a clock, and clocks do not run backwards.
    for call in container.dispatch.waiting():
        previous = seen_waits.get(call.call_session_id)
        if previous is not None:
            assert call.total_wait_s >= previous, (
                f"{call.call_session_id}'s wait went backwards: {previous} -> {call.total_wait_s}"
            )
        seen_waits[call.call_session_id] = call.total_wait_s


# --- the scenarios -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_busy_motor_floor_holds_its_invariants(app: Any, clock: ManualClock) -> None:
    """Three agents, twelve callers, and everybody behaving unhelpfully.

    The walk is random but seeded: agents go ready and unready, accept, decline, decline
    and pause, hang up and file, while callers keep arriving. What is asserted after
    *every single step* is the invariant set, not the outcome.
    """
    rng = random.Random(20260905)
    floor = Floor(app, MOTOR)
    seen_waits: dict[str, float] = {}

    for agent_id in MOTOR:
        floor.declare(agent_id, "ready")

    placed: list[str] = []
    for _step in range(60):
        action = rng.random()
        if action < 0.25 and len(placed) < 12:
            placed.append(floor.place("motor", rng))
        else:
            agent_id = rng.choice(MOTOR)
            snapshot = floor.me(agent_id)
            offer = snapshot["offer"]
            state = snapshot["presence"]["system_state"]
            if offer is not None:
                roll = rng.random()
                if roll < 0.45:
                    floor.accept(agent_id, offer["assignment_id"])
                elif roll < 0.8:
                    floor.decline(agent_id, offer["assignment_id"])
                else:
                    floor.decline(agent_id, offer["assignment_id"], stop=True)
            elif state == "on_call":
                call_id = snapshot["active_call_session_id"]
                floor.hang_up(agent_id, call_id)
                if rng.random() < 0.7:
                    floor.wrap_up(agent_id, call_id)
                floor.declare(agent_id, "ready")
            elif rng.random() < 0.3:
                floor.declare(agent_id, rng.choice(["ready", "break", "lunch", "admin"]))

        if rng.random() < 0.4:
            clock.advance(rng.choice([1, 5, 12, 25]))
            await floor.heartbeat()
        await sweep_once(floor.container)
        await check_invariants(floor, seen_waits=seen_waits)

    assert placed, "the walk never placed a caller - the seed or the weights moved"


@pytest.mark.asyncio
async def test_two_lines_do_not_leak_into_each_other(app: Any, clock: ManualClock) -> None:
    """Motor and health at once. A skill filter that works on an empty floor is not tested."""
    rng = random.Random(7)
    floor = Floor(app, MOTOR + HEALTH)
    seen_waits: dict[str, float] = {}
    for agent_id in MOTOR + HEALTH:
        floor.declare(agent_id, "ready")

    for _ in range(8):
        floor.place("motor", rng)
        floor.place("health", rng)
        await sweep_once(floor.container)
        await check_invariants(floor, seen_waits=seen_waits)

    container = floor.container
    for agent_id in MOTOR + HEALTH:
        agent = await container.agents.get_agent(agent_id)
        skills = {s.skill_code for s in agent.skills}
        for assignment in container.assignments.for_agent(agent_id):
            session = await container.calls.get(assignment.call_session_id)
            queue = container.pack.queues[session.queue_id]
            assert queue.required_skill in skills, (
                f"{agent_id} was offered {session.queue_id}, which needs "
                f"{queue.required_skill}; they have {sorted(skills)}"
            )


@pytest.mark.asyncio
async def test_the_floor_emptying_mid_call_strands_nobody(app: Any, clock: ManualClock) -> None:
    """Everyone goes to lunch while callers are waiting, then one comes back.

    The shape that produced `B25`'s worst symptom: agents leaving without the queue
    noticing. What has to hold is that the callers are still there, still accruing wait,
    and reach the first person who returns.
    """
    rng = random.Random(11)
    floor = Floor(app, MOTOR)
    seen_waits: dict[str, float] = {}
    for agent_id in MOTOR:
        floor.declare(agent_id, "ready")

    calls = [floor.place("motor", rng) for _ in range(4)]
    await sweep_once(floor.container)
    await check_invariants(floor, seen_waits=seen_waits)

    for agent_id in MOTOR:
        offer = floor.me(agent_id)["offer"]
        if offer is not None:
            floor.decline(agent_id, offer["assignment_id"])
        floor.declare(agent_id, "lunch")

    clock.advance(90)
    await floor.heartbeat()  # they are at lunch, not gone: the tab is still open
    await sweep_once(floor.container)
    await check_invariants(floor, seen_waits=seen_waits)

    container = floor.container
    still_waiting = {c.call_session_id for c in container.dispatch.waiting()}
    assert still_waiting, "an empty floor must not lose the people already in the queue"
    for call_session_id in still_waiting:
        decision = container.dispatch.last_decision_for(call_session_id)
        assert decision is not None and decision.chosen_agent_id is None
        assert str(decision.kind) in {
            "no_agent_available",
            "all_declined",
            "all_qualified_busy",
        }, f"a floor at lunch reported {decision.kind}, which sends somebody to fix the roster"

    floor.declare("A001", "ready")
    await sweep_once(floor.container)
    await check_invariants(floor, seen_waits=seen_waits)
    assert floor.me("A001")["offer"] is not None, (
        "the first agent back must be rung; the callers have been waiting 90 seconds"
    )
    assert all(call in {*calls} for call in still_waiting)
