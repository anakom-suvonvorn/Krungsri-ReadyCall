"""Agent presence and the offer handshake.

Most of these tests exist to stop a specific "simplification" from coming back. The rules
in `D45` all *look* like they could be tidied — end after-call work on Ready, auto-save
the form, expire ACW on a timer — and each of those tidyings is a bug with a customer on
the other end of it. So they are asserted rather than commented.
"""

from __future__ import annotations

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.clock import ManualClock
from readycall.domain.enums import (
    AgentIntent,
    AgentSystemState,
    CallState,
    EntryChannel,
    OfferOutcome,
    ProductLine,
    Urgency,
)
from readycall.domain.models import AgentPresence, CallSession
from readycall.errors import PermanentError
from readycall.services.agents.assignment import AssignmentService, OfferPolicy
from readycall.services.agents.presence import PresenceService
from readycall.services.call_orchestrator.orchestrator import CallOrchestrator
from readycall.services.call_orchestrator.repository import InMemoryCallSessionRepository
from readycall.services.matching.scoring import WaitingCall, hard_filter
from readycall.services.matching.weights import MatchingWeights
from tests.conftest import REPO_ROOT
from tests.unit.test_matching import make_agent


@pytest.fixture
def presence(clock: ManualClock, bus: InMemoryEventBus) -> PresenceService:
    return PresenceService(clock=clock, bus=bus, heartbeat_ttl_s=30.0)


@pytest.fixture
def orchestrator(clock: ManualClock, bus: InMemoryEventBus) -> CallOrchestrator:
    return CallOrchestrator(repository=InMemoryCallSessionRepository(), bus=bus, clock=clock)


@pytest.fixture
def assignments(
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    bus: InMemoryEventBus,
    clock: ManualClock,
) -> AssignmentService:
    return AssignmentService(
        orchestrator=orchestrator,
        presence=presence,
        bus=bus,
        clock=clock,
        policy=OfferPolicy(timeout_s=20.0),
    )


async def matched_call(orchestrator: CallOrchestrator, clock: ManualClock) -> CallSession:
    """A call sitting at MATCHED, which is where an offer may be made."""
    session = CallSession(
        call_session_id="call_t1",
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.CONNECTING,
        created_at=clock.now(),
        trace_id="trace_t1",
        product_line=ProductLine.MOTOR,
    )
    await orchestrator.transition(session, CallState.QUEUED, reason="queued")
    await orchestrator.transition(session, CallState.MATCHED, reason="agent_available")
    return session


# --- presence: the two axes ------------------------------------------------------------


async def test_signing_in_does_not_make_you_ready(presence: PresenceService) -> None:
    """`D51`: the platform never asserts a state the person did not choose."""
    who = await presence.sign_in("A001", session_id="s1")
    assert who.system_state is AgentSystemState.AVAILABLE
    assert who.agent_intent is AgentIntent.NOT_READY
    assert presence.view("A001") is not None
    assert not presence.view("A001").offerable  # type: ignore[union-attr]


async def test_declaring_ready_is_what_makes_you_offerable(presence: PresenceService) -> None:
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    assert presence.view("A001").offerable  # type: ignore[union-attr]


async def test_last_call_stays_logged_in_but_takes_nobody_new(presence: PresenceService) -> None:
    """`D45`'s prose says (ready, last_call) — the prose is wrong, the code is right."""
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.LAST_CALL)
    view = presence.view("A001")
    assert view is not None
    assert view.presence.system_state is AgentSystemState.AVAILABLE
    assert not view.offerable


async def test_an_agent_cannot_declare_not_ready_for_themselves(
    presence: PresenceService,
) -> None:
    """ "I am not ready" is really break/lunch/admin, and that difference is the data."""
    await presence.sign_in("A001", session_id="s1")
    with pytest.raises(PermanentError):
        await presence.declare("A001", AgentIntent.NOT_READY)


# --- after-call work: D45 ---------------------------------------------------------------


async def test_acw_is_measured_from_the_disconnect_not_from_a_form(
    presence: PresenceService, clock: ManualClock
) -> None:
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")

    await presence.begin_after_call_work("A001", call_session_id="call_1")
    clock.advance(30)
    assert presence.acw_elapsed_s("A001") == pytest.approx(30.0)


@pytest.mark.parametrize(
    "declared",
    [AgentIntent.READY, AgentIntent.BREAK, AgentIntent.LUNCH, AgentIntent.ADMIN],
)
async def test_any_declaration_ends_after_call_work(
    presence: PresenceService, clock: ManualClock, declared: AgentIntent
) -> None:
    """Not only Ready (`D45`). Lunch ends ACW *and* leaves them un-offerable."""
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    await presence.begin_after_call_work("A001", call_session_id="call_1")
    clock.advance(42)

    after = await presence.declare("A001", declared)
    assert after.system_state is AgentSystemState.AVAILABLE
    assert after.agent_intent is declared
    assert presence.acw_elapsed_s("A001") is None
    closing = presence.state_log("A001")[-1]
    assert closing.acw_seconds == pytest.approx(42.0)
    assert closing.set_by == "agent"


async def test_time_alone_never_ends_after_call_work(
    presence: PresenceService, clock: ManualClock
) -> None:
    """The regression guard for the auto-ready `D45` removed.

    Nothing here calls a timer, because there is no timer to call — the point is that
    advancing the clock a long way changes no state at all. An agent who walks away stays
    in after-call work, which is the honest signal.
    """
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    await presence.begin_after_call_work("A001", call_session_id="call_1")

    clock.advance(60 * 60)
    await presence.heartbeat("A001")  # still at their desk, tab open

    view = presence.view("A001")
    assert view is not None
    assert view.presence.system_state is AgentSystemState.AFTER_CALL_WORK
    assert not view.offerable
    assert view.long_acw, "a long ACW must be visible..."
    assert view.acw_seconds == pytest.approx(3600.0), "...and still be counting"


async def test_saving_the_wrapup_does_not_end_after_call_work(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    """Two different statements: 'I finished your form' and 'I am done' (`D45`)."""
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")
    await assignments.accept(session, assignment_id=offer.assignment_id)
    clock.advance(120)
    await assignments.end_call(session, assignment_id=offer.assignment_id)
    clock.advance(20)

    await assignments.save_wrapup(
        session, assignment_id=offer.assignment_id, disposition="advice_given"
    )

    assert session.state is CallState.CLOSED, "the call RECORD closes"
    who = presence.get("A001")
    assert who is not None
    assert who.system_state is AgentSystemState.AFTER_CALL_WORK, "the AGENT is still wrapping"
    assert presence.acw_elapsed_s("A001") == pytest.approx(20.0)


async def test_the_agent_may_declare_before_saving(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    """Save and declare are independent and may happen in either order (`D45`)."""
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")
    await assignments.accept(session, assignment_id=offer.assignment_id)
    await assignments.end_call(session, assignment_id=offer.assignment_id)
    clock.advance(15)

    await presence.declare("A001", AgentIntent.READY)
    recorded = await assignments.note_acw_ended(
        assignment_id=offer.assignment_id, declared_intent="ready"
    )
    assert recorded.acw_ended_by == "ready"
    assert session.state is CallState.WRAP_UP, "the record is still open, and that is honest"

    await assignments.save_wrapup(
        session, assignment_id=offer.assignment_id, disposition="advice_given"
    )
    assert session.state is CallState.CLOSED


# --- the offer handshake -----------------------------------------------------------------


async def test_accept_bridges_immediately(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)

    offer = await assignments.offer(session, agent_id="A001")
    assert session.state is CallState.OFFERED
    assert presence.get("A001").system_state is AgentSystemState.OFFERING  # type: ignore[union-attr]

    clock.advance(7)
    accepted = await assignments.accept(session, assignment_id=offer.assignment_id)
    assert accepted.outcome is OfferOutcome.ACCEPTED
    assert accepted.time_to_accept_ms == pytest.approx(7000.0)
    assert accepted.bridged_at == accepted.accepted_at, "already-connected channel, no dial-out"
    assert session.state is CallState.IN_CALL


async def test_a_timeout_takes_the_agent_out_of_rotation(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    """RONA (`D33`): an empty desk must not black-hole the queue."""
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")

    clock.advance(20)
    resolved = await assignments.timeout(session, assignment_id=offer.assignment_id)

    assert resolved.outcome is OfferOutcome.TIMEOUT
    assert session.state is CallState.MATCHED, "the caller keeps their place"
    who = presence.get("A001")
    assert who is not None
    assert who.agent_intent is AgentIntent.NOT_READY
    rona = presence.state_log("A001")[-1]
    assert rona.set_by == "platform", "so a supervisor can tell this from 'they chose break'"
    assert rona.reason == "rona_missed_offer"


async def test_declining_leaves_an_honest_agent_ready(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    """A decline is a person telling us something; a timeout is the absence of one."""
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")

    await assignments.decline(session, assignment_id=offer.assignment_id, reason="wrong_language")
    who = presence.get("A001")
    assert who is not None
    assert who.agent_intent is AgentIntent.READY
    assert who.system_state is AgentSystemState.AVAILABLE


@pytest.mark.parametrize("path", ["decline", "timeout"])
async def test_both_rejections_exclude_the_agent_from_this_call(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
    path: str,
) -> None:
    """Otherwise the next tick re-picks the same desk and the caller waits forever."""
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")

    if path == "decline":
        await assignments.decline(session, assignment_id=offer.assignment_id)
    else:
        await assignments.timeout(session, assignment_id=offer.assignment_id)

    assert assignments.excluded_agents(session.call_session_id) == ("A001",)


async def test_the_exclusion_is_a_hard_filter_with_its_own_reason() -> None:
    """It must show up in the decision record, not vanish into a low score (`D22`)."""
    weights = MatchingWeights.load(REPO_ROOT / "config" / "matching_weights.yaml")
    clock = ManualClock()
    agent = make_agent("A001", skill="claims.assist", prof=0.9)
    who = AgentPresence(
        agent_id="A001",
        system_state=AgentSystemState.AVAILABLE,
        agent_intent=AgentIntent.READY,
        since=clock.now(),
    )
    call = WaitingCall(
        call_session_id="c1",
        queue_id="q_claims",
        required_skill="claims.assist",
        intent_code="motor.claim.notify",
        intent_urgency=Urgency.NORMAL,
        waiting_s=10.0,
        sla_seconds=30,
        excluded_agent_ids=("A001",),
    )
    assert hard_filter(call, agent, who, weights) == "already_offered"


async def test_a_cancelled_offer_blames_nobody(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")

    resolved = await assignments.cancel(session, assignment_id=offer.assignment_id)
    assert resolved.outcome is OfferOutcome.CANCELLED
    assert session.state is CallState.ABANDONED
    assert presence.get("A001").agent_intent is AgentIntent.READY  # type: ignore[union-attr]
    assert assignments.excluded_agents(session.call_session_id) == ()


async def test_an_offer_cannot_be_resolved_twice(
    assignments: AssignmentService,
    orchestrator: CallOrchestrator,
    presence: PresenceService,
    clock: ManualClock,
) -> None:
    """The accept/timeout race: the agent presses Accept as the timer fires."""
    session = await matched_call(orchestrator, clock)
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    offer = await assignments.offer(session, agent_id="A001")
    await assignments.accept(session, assignment_id=offer.assignment_id)

    with pytest.raises(PermanentError, match="already resolved"):
        await assignments.timeout(session, assignment_id=offer.assignment_id)


# --- heartbeat ---------------------------------------------------------------------------


async def test_a_closed_laptop_signs_itself_out(
    presence: PresenceService, clock: ManualClock
) -> None:
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)

    clock.advance(10)
    await presence.heartbeat("A001")
    assert await presence.sweep() == []

    clock.advance(31)
    assert await presence.sweep() == ["A001"]
    assert presence.get("A001").system_state is AgentSystemState.OFFLINE  # type: ignore[union-attr]


async def test_an_agent_on_a_call_is_never_dropped_for_a_missing_heartbeat(
    presence: PresenceService, clock: ManualClock
) -> None:
    """`B34`. The bug the user hit by looking at another window mid-call.

    A missing heartbeat is a claim about the browser TAB, and browsers throttle a hidden
    tab's timers to roughly one a minute — so a broker reading something else while
    talking to a customer was being marked absent. Nothing then picked the call up:
    `sweep`'s caller only logs what it returns, so the call stayed IN_CALL with an
    ACCEPTED assignment and an OFFLINE agent, which is unendable.

    Note this rule already existed for the agent's OWN action —
    `test_signing_out_mid_call_is_refused` — and the platform path simply did not have it.
    """
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")

    clock.advance(3600)  # an hour of silence from the tab

    assert await presence.sweep() == []
    assert presence.get("A001").system_state is AgentSystemState.ON_CALL  # type: ignore[union-attr]


async def test_after_call_work_is_still_droppable(
    presence: PresenceService, clock: ManualClock
) -> None:
    """The exemption is deliberately narrow: it covers a LIVE call, not the paperwork.

    In ACW the customer has already gone, so a genuinely closed laptop should free the
    desk — and the unfiled wrap-up lands in `D87`'s backlog rather than being lost.
    """
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    await presence.begin_after_call_work("A001", call_session_id="call_1")

    clock.advance(3600)

    assert await presence.sweep() == ["A001"]


async def test_signing_out_mid_call_is_refused(
    presence: PresenceService,
) -> None:
    """Closing the tab does not end the call, and presence must not pretend it did."""
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    with pytest.raises(PermanentError):
        await presence.sign_out("A001")


# --- the standing-instruction model (D59) ------------------------------------------------


async def test_only_forward_looking_intents_can_be_declared_mid_call(
    presence: PresenceService,
) -> None:
    """`agent_intent` is a standing instruction, not a status (`D59`).

    An agent may decide mid-conversation that this is their last call. They cannot be at
    lunch, because what they are doing right now is talking to a customer.
    """
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")

    assert set(presence.declarable_intents("A001")) == {
        AgentIntent.READY,
        AgentIntent.LAST_CALL,
        AgentIntent.DRAINING,
    }
    await presence.declare("A001", AgentIntent.LAST_CALL)
    with pytest.raises(PermanentError, match="cannot be declared while"):
        await presence.declare("A001", AgentIntent.LUNCH)


async def test_last_call_is_spent_when_that_call_ends(
    presence: PresenceService, clock: ManualClock
) -> None:
    """The one standing instruction with a built-in end condition (`D59`).

    "Finish the current call, then stop" — so when the call ends it has been carried out,
    and leaving it set would mean the agent silently stays un-offerable under an
    instruction that has already been honoured. It becomes `NOT_READY`, never a concrete
    state, because the platform still may not assert what the person is doing (`D45`).
    """
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    await presence.declare("A001", AgentIntent.LAST_CALL)

    await presence.begin_after_call_work("A001", call_session_id="call_1")
    who = presence.get("A001")
    assert who is not None
    assert who.agent_intent is AgentIntent.NOT_READY
    closing = presence.state_log("A001")[-1]
    assert closing.reason == "last_call_fulfilled"
    assert closing.set_by == "platform"


async def test_draining_survives_a_call(presence: PresenceService) -> None:
    """DRAINING has no end condition — it means "no new callers" until the person says so."""
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    await presence.declare("A001", AgentIntent.DRAINING)
    await presence.begin_after_call_work("A001", call_session_id="call_1")

    who = presence.get("A001")
    assert who is not None
    assert who.agent_intent is AgentIntent.DRAINING


async def test_after_call_work_asks_for_a_declaration(
    presence: PresenceService, clock: ManualClock
) -> None:
    """The screen must stop showing the old instruction as if it were current.

    That is what made the status control look broken: saving a wrap-up left `พร้อมรับสาย`
    highlighted while the agent was, correctly, still in after-call work.
    """
    await presence.sign_in("A001", session_id="s1")
    await presence.declare("A001", AgentIntent.READY)
    await presence.begin_call("A001", call_session_id="call_1")
    await presence.begin_after_call_work("A001", call_session_id="call_1")

    view = presence.view("A001")
    assert view is not None
    assert view.awaiting_declaration is True
    assert view.acw_since is not None, "the client ticks its own clock from this anchor"
    assert len(view.declarable) == 7, "every intent is meaningful once the call has ended"
