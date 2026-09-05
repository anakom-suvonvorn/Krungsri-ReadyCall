"""Two things the matcher and the screen both got wrong, found by the user clicking around.

Both are `B7`'s family and both survived a 655-test suite, because every existing test set
its agents `ready` first and read a wait exactly once.

* **`B25`** — `hard_filter` never asked whether the agent could take a call *right now*.
  `AgentPresence.is_available()` existed, said exactly that, and was called by nothing. A
  caller could be offered to somebody who had never pressed ready, was on lunch, or had
  signed out and closed the tab — and a signed-out agent then held the offer for its whole
  RONA timeout, so on a small floor every caller paid 20 seconds per ghost.
* **`B26`** — the pool's stored `waiting_s` is the admit-time value. `tick()` recomputed it
  for the matcher (`B12`) and never wrote it back, so the offer card and the queue strip
  showed a wait frozen at the moment the caller arrived while the matcher scored them on
  the real one.

The HTTP half matters as much as the unit half: `B25` is only visible when something
*drives* the matcher, which is the whole shape of the bug.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, sweep_once
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.domain.enums import (
    AgentIntent,
    AgentSystemState,
    CefrLevel,
    Language,
    MatchKind,
    Urgency,
)
from readycall.domain.models import Agent, AgentLanguage, AgentPresence, AgentSkill
from readycall.services.matching.engine import MatchingEngine
from readycall.services.matching.scoring import WaitingCall, hard_filter
from readycall.services.matching.weights import MatchingWeights
from tests.conftest import REPO_ROOT

NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def weights() -> MatchingWeights:
    return MatchingWeights.load(REPO_ROOT / "config" / "matching_weights.yaml")


def make_agent(agent_id: str = "A", skill: str = "motor.claim") -> Agent:
    return Agent(
        agent_id=agent_id,
        display_name=agent_id,
        team="t",
        languages=(AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),),
        skills=(AgentSkill(skill_code=skill, proficiency=0.9),),
    )


def make_presence(
    *,
    system_state: AgentSystemState = AgentSystemState.AVAILABLE,
    agent_intent: AgentIntent = AgentIntent.READY,
    load: int = 0,
) -> AgentPresence:
    return AgentPresence(
        agent_id="A",
        system_state=system_state,
        agent_intent=agent_intent,
        since=NOW,
        current_load=load,
    )


def make_call(**kw: Any) -> WaitingCall:
    base: dict[str, Any] = dict(
        call_session_id="call_1",
        queue_id="q_motor_claim",
        required_skill="motor.claim",
        intent_code="motor.claim.accident",
        intent_urgency=Urgency.NORMAL,
        waiting_s=10.0,
        sla_seconds=30,
    )
    base.update(kw)
    return WaitingCall(**base)


# --- B25, at the filter -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("presence_kw", "expected"),
    [
        ({"system_state": AgentSystemState.OFFLINE}, "offline"),
        ({"agent_intent": AgentIntent.NOT_READY}, "not_ready"),
        ({"agent_intent": AgentIntent.BREAK}, "not_ready"),
        ({"agent_intent": AgentIntent.LUNCH}, "not_ready"),
        # `LAST_CALL` and `DRAINING` mean "finish what I have, give me nothing new"
        # (`D33`, `D59`) — they are logged in and must not be offered a new caller.
        ({"agent_intent": AgentIntent.LAST_CALL}, "not_ready"),
        ({"agent_intent": AgentIntent.DRAINING}, "not_ready"),
        ({"system_state": AgentSystemState.ON_CALL}, "busy"),
        ({"system_state": AgentSystemState.AFTER_CALL_WORK}, "busy"),
        # The one that starved a whole floor: an agent already holding an offer collected
        # every other waiting caller in the same tick, and none of them reached anybody
        # else until each offer timed out.
        ({"system_state": AgentSystemState.OFFERING}, "busy"),
    ],
)
def test_an_agent_who_cannot_take_a_call_now_is_filtered_out(
    presence_kw: dict[str, Any], expected: str, weights: MatchingWeights
) -> None:
    failed = hard_filter(make_call(), make_agent(), make_presence(**presence_kw), weights)
    assert failed == expected


def test_a_ready_available_agent_still_passes(weights: MatchingWeights) -> None:
    """The other direction, because a filter that rejects everybody also passes the tests
    above. `B13`'s lesson: never assert only the half you are fixing."""
    assert hard_filter(make_call(), make_agent(), make_presence(), weights) is None


# --- B25, at the engine: the reason has to stay honest -----------------------------------


@pytest.mark.asyncio
async def test_a_floor_on_lunch_is_not_reported_as_an_empty_roster(
    clock: ManualClock, weights: MatchingWeights
) -> None:
    """`D108`, and it is why `D50`'s two kinds became three.

    Making availability a hard filter would otherwise have told a supervisor *"nobody with
    the skill is online"* while four people with the skill sat signed in on their break —
    the exact conflation `D50` was written to stop, in a new place.
    """

    class OneAgent:
        async def list_agents(self) -> list[Agent]:
            return [make_agent()]

    engine = MatchingEngine(directory=OneAgent(), weights=weights, clock=clock)  # type: ignore[arg-type]

    on_lunch = {"A": make_presence(agent_intent=AgentIntent.LUNCH)}
    [decision] = await engine.match([make_call()], on_lunch)
    assert decision.kind is MatchKind.NO_AGENT_AVAILABLE
    assert decision.chosen_agent_id is None
    assert "ยังไม่พร้อมรับสาย" in (decision.rationale_th or "")

    wrong_skill = {"A": make_presence()}
    [decision] = await engine.match([make_call(required_skill="health.ipd")], wrong_skill)
    assert decision.kind is MatchKind.NO_QUALIFIED_AGENT, (
        "a real roster gap must still read as a roster gap"
    )


# --- B25, over HTTP: the half that a unit test cannot see --------------------------------


@pytest.fixture
def client(clock: ManualClock) -> Any:
    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


def place(client: Any, **kw: Any) -> dict[str, Any]:
    body = {
        "intent_code": "motor.claim.accident",
        "intake_keys": ["2"],
        "ignore_hours": True,
        **kw,
    }
    response = client.post("/v1/demo/calls", json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def me(client: Any) -> dict[str, Any]:
    return client.get("/v1/agent/me").json()  # type: ignore[no-any-return]


def test_signing_in_is_not_saying_you_are_ready(client: Any) -> None:
    """The user found this by clicking: sign in, do not press ready, get rung anyway."""
    client.post("/v1/agent/demo-login", json={"agent_id": "A002"})
    snapshot = me(client)
    assert snapshot["presence"]["offerable"] is False

    placed = place(client)
    assert placed["offered_to"] is None
    assert placed["unplaced_reason"] == "no_agent_available"
    assert me(client)["offer"] is None, (
        "the screen said `offerable: false` and the matcher rang the desk anyway - the "
        "two must not be able to disagree"
    )


@pytest.mark.asyncio
async def test_an_agent_who_signed_out_is_not_rung(client: Any) -> None:
    """The worst version, because it is silent: a closed tab held every caller.

    Signing out sets `OFFLINE` correctly — nothing was reading it. The offers went to a
    browser that no longer existed and were only released by the RONA timeout, one call
    and twenty seconds at a time.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    client.post("/v1/agent/logout")

    placed = place(client)
    assert placed["offered_to"] is None

    # And the caller is still there for somebody who IS available.
    client.post("/v1/agent/demo-login", json={"agent_id": "A002"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    await sweep_once(client.app.state.container)
    assert me(client)["offer"] is not None, "the caller must reach the agent who is here"


@pytest.mark.asyncio
async def test_one_desk_does_not_collect_every_waiting_caller(client: Any) -> None:
    """`OFFERING` is `busy`. Without that, one agent is handed all of them at once.

    This is what made the user's spam-the-test-call-button sequence look broken: four
    callers, one ringing desk, three of them invisible until their offers timed out.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    first = place(client)
    assert first["offered_to"] == "A001"
    for _ in range(3):
        later = place(client)
        assert later["offered_to"] is None, "A001 is already ringing; they cannot take another"
        assert later["unplaced_reason"] in {"no_agent_available", "all_qualified_busy"}


# --- B26: the wait on the screen ---------------------------------------------------------


def strip(client: Any) -> dict[str, Any]:
    return next(q for q in me(client)["queues"] if q["queue_id"] == "q_motor_claim")


@pytest.mark.asyncio
async def test_the_queue_strips_longest_wait_moves_with_the_clock(
    client: Any, clock: ManualClock
) -> None:
    """It was frozen at the admit-time value while the matcher used the live one."""
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place(client, waited_s=40)

    offer = me(client)["offer"]
    assert offer is not None
    assert strip(client)["longest_wait_s"] == pytest.approx(40.0), "the credit is the start"
    # Decline, so the caller is simply waiting: two minutes with an offer open would be
    # two minutes of RONA and heartbeat expiry, which is a different test.
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/decline", json={"reason": "busy"})

    clock.advance(120)
    await sweep_once(client.app.state.container)

    assert strip(client)["longest_wait_s"] == pytest.approx(160.0), (
        "two minutes passed and the strip still showed the arrival value"
    )


@pytest.mark.asyncio
async def test_the_offer_cards_wait_moves_too(client: Any, clock: ManualClock) -> None:
    """Same pool, same bug, the place an agent actually reads it.

    Ten seconds, deliberately: `OFFER_TIMEOUT_S` is 20 and the heartbeat TTL is 30, so a
    longer jump would test RONA instead of the number on the card.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = place(client, waited_s=40)

    assert me(client)["offer"]["waited_s"] == pytest.approx(40.0)
    clock.advance(10)

    offer = me(client)["offer"]
    assert offer is not None, "still ringing at 10s of a 20s timeout"
    assert offer["call_session_id"] == placed["call_session_id"]
    assert offer["waited_s"] == pytest.approx(50.0), (
        "the caller has been waiting ten seconds longer and the card has to say so"
    )


@pytest.mark.asyncio
async def test_a_caller_everyone_declined_says_so_instead_of_blaming_the_roster(
    client: Any,
) -> None:
    """`D108`'s fourth kind, and the one that does not resolve by waiting (`Q31`).

    Every qualified agent turning a caller down used to report `no_qualified_agent` — the
    same answer as an empty roster, which sends whoever is watching the queue to fix
    entirely the wrong thing.
    """
    for agent_id in ("A001", "A002", "A003", "A015"):
        client.post("/v1/agent/demo-login", json={"agent_id": agent_id})
        client.post("/v1/agent/state", json={"agent_intent": "ready"})

    placed = place(client)
    call_id = placed["call_session_id"]
    container = client.app.state.container

    for _ in range(6):  # more rounds than there are motor agents
        await sweep_once(container)
        assignment = container.assignments.open_offer_for(call_id)
        if assignment is None:
            break
        session = await container.calls.get(call_id)
        await container.assignments.decline(
            session, assignment_id=assignment.assignment_id, reason="busy"
        )

    await sweep_once(container)
    decision = container.dispatch.last_decision_for(call_id)
    assert decision is not None
    assert decision.kind is MatchKind.ALL_DECLINED, (
        f"every qualified agent declined; got {decision.kind} - {decision.rationale_th}"
    )
    assert "ปฏิเสธ" in (decision.rationale_th or "")


# --- D109: decline, and stop being rung ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_decline_leaves_the_agent_ready(client: Any) -> None:
    """The default has to stay the default: they turned down THIS caller, not all of them."""
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place(client)

    offer = me(client)["offer"]
    client.post(
        f"/v1/agent/offers/{offer['assignment_id']}/decline",
        json={"reason": "busy"},
    )
    presence = me(client)["presence"]
    assert presence["agent_intent"] == "ready"
    assert presence["offerable"] is True


@pytest.mark.asyncio
async def test_decline_and_stop_offering_lands_where_a_missed_offer_lands(client: Any) -> None:
    """`D109`. The same ending as letting the card ring out, chosen instead of waited for.

    And the *reason* differs on purpose: `rona_missed_offer` means nobody picked up,
    `declined_and_stopped` means somebody made a choice. The screen says a different
    sentence for each, which is `D59`'s whole point.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place(client)

    offer = me(client)["offer"]
    snapshot = client.post(
        f"/v1/agent/offers/{offer['assignment_id']}/decline",
        json={"reason": "busy", "stop_offering": True},
    ).json()

    presence = snapshot["presence"]
    assert presence["agent_intent"] == "not_ready"
    assert presence["offerable"] is False
    assert presence["intent_reason"] == "declined_and_stopped"


@pytest.mark.asyncio
async def test_declining_with_stop_is_not_immediately_rung_again(client: Any) -> None:
    """The actual complaint: decline, and the next caller arrives a second later.

    Two callers waiting and one agent, so without the fix the re-match in the decline
    route hands them the second one before the first snapshot has even rendered.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place(client)
    offer = me(client)["offer"]
    place(client)  # a second caller, waiting behind the first

    after = client.post(
        f"/v1/agent/offers/{offer['assignment_id']}/decline",
        json={"reason": "busy", "stop_offering": True},
    ).json()
    assert after["offer"] is None, "asked not to be rung, and was rung again in the same request"

    await sweep_once(client.app.state.container)
    assert me(client)["offer"] is None, "nor on the next tick"


# --- B27: two reasons the screen was late ------------------------------------------------


@pytest.mark.asyncio
async def test_pressing_ready_rings_the_desk_without_waiting_for_the_sweep(
    client: Any,
) -> None:
    """The user's report: press ready with somebody already queued, and nothing happens.

    It resolved on the next sweep, so it was never *broken* — but a caller sitting in
    front of an agent who is free for up to a second is a second nobody has to spend, and
    the matcher costs under 50 ms. Note there is **no `sweep_once` in this test**: that is
    the assertion.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    placed = place(client)
    assert placed["offered_to"] is None, "nobody is ready yet"

    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    offer = me(client)["offer"]
    assert offer is not None, "a caller was already waiting; the desk should ring now"
    assert offer["call_session_id"] == placed["call_session_id"]


@pytest.mark.asyncio
async def test_signing_in_again_starts_the_push_sequence_over(client: Any) -> None:
    """`B27`, server half. A sign-in is not a reconnect.

    The outbox exists so a **reconnect** replays the gap it missed (`D68`). Signing in is
    a new session at that desk, and replaying the previous one hands the new arrival
    offers that were resolved before they sat down.

    ⚠️ **This is the smaller half of `B27` and it is the half Python can see.** The bug
    the user hit is in `useSocket.ts`: `seq` is per *agent* on the server and `lastSeq` is
    per *tab*, so signing out of A and into B in one tab left A's high-water mark in place
    and every one of B's messages — the offer included — was discarded as a replay
    already applied. The card then appeared only when the ten-second heartbeat triggered a
    refresh, on a twenty-second ring. That fix is one line of TypeScript and **no test in
    this suite covers it**; it was verified in a browser instead, and this note is here so
    nobody reads the green tick as coverage.
    """
    hub = client.app.state.container.hub

    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place(client)
    offer = me(client)["offer"]
    assert offer is not None
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/decline", json={"reason": "busy"})
    assert hub.pending_for("A001"), "the channel should have climbed above zero"
    client.post("/v1/agent/logout")

    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    assert hub.pending_for("A001") == [], (
        "a fresh session was handed the previous session's outbox to replay"
    )
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    messages = hub.pending_for("A001")
    assert messages and messages[0]["seq"] == 1, (
        f"the new session starts at seq {messages[0]['seq'] if messages else None}, not 1"
    )


@pytest.mark.asyncio
async def test_the_wait_is_sent_as_an_anchor_not_only_as_a_number(client: Any) -> None:
    """A duration sent as a scalar can only change when a snapshot arrives (`B27`).

    Which, for a caller who is simply waiting, is never — so the figure sat still while
    the call timer and the ACW timer beside it ticked. Same fix those two already had
    (`D68`, `B8`): send the instant, let the client count.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A001"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place(client, waited_s=40)

    offer = me(client)["offer"]
    assert offer["waited_since"] is not None
    started = datetime.fromisoformat(offer["waited_since"])
    # 40 seconds of credit means the anchor sits 40 seconds in the past, so the client
    # counting from it lands on the same number the server would have sent.
    assert (NOW - started).total_seconds() == pytest.approx(40.0, abs=1.0)

    queue = next(q for q in me(client)["queues"] if q["queue_id"] == "q_motor_claim")
    assert queue["longest_wait_since"] is not None
    assert queue["longest_wait_since"] == offer["waited_since"], (
        "one caller, so the strip and the card must be counting from the same instant"
    )
