"""A rating is a fact received about a call, not a phase the call passes through (`D46`).

The old model had `WRAP_UP -> RATING -> CLOSED`, which asserted an ordering that is simply
false: the customer rates in the IVR within seconds of hanging up, while the agent may
still be writing the wrap-up for another three minutes. These tests pin the properties that
made us drop the state, so nobody reintroduces it by adding "just one more" call state.
"""

from __future__ import annotations

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.clock import ManualClock
from readycall.domain import events as ev
from readycall.domain.enums import CallState, RatingSource
from readycall.domain.models import Rating
from readycall.services.call_orchestrator import CallOrchestrator, machine


def test_there_is_no_rating_call_state() -> None:
    """`D46`. Named explicitly so reintroducing it fails loudly rather than silently."""
    assert not hasattr(CallState, "RATING")
    assert "rating" not in {str(state) for state in CallState}


def test_wrap_up_closes_directly() -> None:
    assert machine.can(CallState.WRAP_UP, CallState.CLOSED)


def test_wrap_up_has_no_intermediate_step_before_closing() -> None:
    """Pins the shape, so a rating-like waypoint cannot be slipped back in.

    The failure this guards against is subtle: someone adds a state between WRAP_UP and
    CLOSED for a genuinely good reason, and the false ordering quietly returns with it.
    """
    assert machine.TRANSITIONS[CallState.WRAP_UP] == frozenset({CallState.CLOSED, CallState.FAILED})


async def test_a_rating_may_arrive_after_the_call_has_closed(
    orchestrator: CallOrchestrator, bus: InMemoryEventBus, clock: ManualClock
) -> None:
    """The property that justifies dropping the state.

    A late rating must attach to the record without needing the call to be re-opened,
    and without the bus complaining. Under the old model there was nowhere for it to go.
    """
    session = await orchestrator.start_cold_call()
    await orchestrator.enter_ivr(session)
    await orchestrator.enqueue(session, queue_id="q_service")
    await orchestrator.transition(session, CallState.MATCHED, reason="agent_available")
    await orchestrator.transition(session, CallState.OFFERED, reason="offered")
    await orchestrator.transition(session, CallState.IN_CALL, reason="accepted")
    await orchestrator.transition(session, CallState.WRAP_UP, reason="caller_hung_up")
    await orchestrator.transition(session, CallState.CLOSED, reason="wrapup_saved")

    assert session.is_terminal

    # ... and only now, well after closure, does the rating turn up.
    clock.advance(120.0)
    late = ev.RatingReceived(
        call_session_id=session.call_session_id,
        occurred_at=clock.now(),
        source=str(RatingSource.CUSTOMER_IVR),
        csat=5,
    )
    await bus.publish(late)
    await bus.drain()

    assert not bus.errors
    assert late.call_session_id == session.call_session_id
    assert session.state is CallState.CLOSED, "a late rating must not reopen a closed call"


def test_rating_model_accepts_both_sides_and_rejects_impossible_scores(
    clock: ManualClock,
) -> None:
    """Both sides rate (`D27`), and the same model carries either."""
    for source in (RatingSource.CUSTOMER_IVR, RatingSource.AGENT):
        rating = Rating(
            rating_id="rat_1",
            call_session_id="call_1",
            source=source,
            received_at=clock.now(),
            csat=4,
        )
        assert rating.source is source

    with pytest.raises(ValueError):
        Rating(
            rating_id="rat_2",
            call_session_id="call_1",
            source=RatingSource.CUSTOMER_IVR,
            received_at=clock.now(),
            csat=9,  # csat is 1-5
        )
