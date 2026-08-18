"""The orchestrator: transitions, the transition log, events, and consent."""

from __future__ import annotations

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.clock import ManualClock
from readycall.domain.enums import CallState, ConsentScope, EntryChannel, ProductLine
from readycall.errors import IllegalTransition, PermanentError
from readycall.services.call_orchestrator import CallOrchestrator


async def test_cold_call_starts_without_a_customer(orchestrator: CallOrchestrator) -> None:
    """`D19`: the base case carries no identity at all when it arrives."""
    session = await orchestrator.start_cold_call(
        entry_channel=EntryChannel.PRODUCT_DID,
        dialled_did="+6621234111",
        caller_number="+66898887777",
        product_line=ProductLine.MOTOR,
    )
    assert session.state is CallState.CONNECTING
    assert session.customer_id is None
    assert session.intent_id is None
    assert session.product_line is ProductLine.MOTOR


async def test_in_app_call_must_carry_an_intent(orchestrator: CallOrchestrator) -> None:
    with pytest.raises(PermanentError):
        await orchestrator.start_cold_call(entry_channel=EntryChannel.IN_APP)


async def test_transition_log_records_order_time_and_reason(
    orchestrator: CallOrchestrator, clock: ManualClock
) -> None:
    """The transition log is what makes the timeline demonstrable (`D18`)."""
    session = await orchestrator.start_cold_call()
    await orchestrator.enter_ivr(session)
    clock.advance(9.0)
    await orchestrator.enqueue(session, queue_id="q_general")

    assert [t.to_state for t in session.transitions] == [
        CallState.CONNECTING,
        CallState.IVR,
        CallState.QUEUED,
    ]
    assert session.transitions[0].from_state is None
    assert session.transitions[-1].reason == "queued"
    elapsed = (session.transitions[-1].at - session.transitions[0].at).total_seconds()
    assert elapsed == pytest.approx(9.0)
    assert session.queued_at is not None


async def test_illegal_transition_leaves_the_session_untouched(
    orchestrator: CallOrchestrator,
) -> None:
    session = await orchestrator.start_cold_call()
    with pytest.raises(IllegalTransition):
        await orchestrator.transition(session, CallState.IN_CALL, reason="nope")
    assert session.state is CallState.CONNECTING
    assert len(session.transitions) == 1


async def test_terminal_transition_stamps_end_and_emits_call_ended(
    orchestrator: CallOrchestrator, bus: InMemoryEventBus, clock: ManualClock
) -> None:
    session = await orchestrator.start_cold_call()
    clock.advance(30.0)
    await orchestrator.abandon(session, reason="caller_hung_up")

    assert session.is_terminal
    assert session.ended_at is not None
    assert session.end_reason == "caller_hung_up"
    await bus.drain()
    names = bus.names(session.call_session_id)
    assert names.count("call.ended") == 1
    ended = next(e for e in bus.recorded() if e.name == "call.ended")
    assert ended.duration_s == pytest.approx(30.0)  # type: ignore[attr-defined]


async def test_no_consent_means_no_intake_but_the_call_still_proceeds(
    orchestrator: CallOrchestrator,
) -> None:
    """`D14`: consent gates the intake, never the call."""
    session = await orchestrator.start_cold_call()
    assert not session.may_run_intake

    await orchestrator.record_consent(
        session, scope=ConsentScope.RECORDING, granted=True, basis="ivr_keypress"
    )
    assert not session.may_run_intake  # recording alone is not enough

    await orchestrator.record_consent(
        session, scope=ConsentScope.AI_PROCESSING, granted=True, basis="ivr_keypress"
    )
    assert session.may_run_intake

    # And the call can still reach an agent regardless of any of that.
    await orchestrator.enter_ivr(session)
    await orchestrator.enqueue(session, queue_id="q_general")
    await orchestrator.transition(session, CallState.MATCHED, reason="agent_available")
    assert session.state is CallState.MATCHED


async def test_refused_consent_is_recorded_not_dropped(
    orchestrator: CallOrchestrator, bus: InMemoryEventBus
) -> None:
    """A refusal is evidence and must be auditable, not an absence (`D14`)."""
    session = await orchestrator.start_cold_call()
    await orchestrator.record_consent(
        session, scope=ConsentScope.RECORDING, granted=False, basis="ivr_keypress"
    )
    assert len(session.consents) == 1
    assert session.consents[0].granted is False
    assert not session.has_consent(ConsentScope.RECORDING)
    await bus.drain()
    assert "consent.recorded" in bus.names(session.call_session_id)


async def test_every_event_carries_the_call_id_and_trace_id(
    orchestrator: CallOrchestrator, bus: InMemoryEventBus
) -> None:
    session = await orchestrator.start_cold_call()
    await orchestrator.enter_ivr(session)
    await orchestrator.enqueue(session, queue_id="q_general")
    await bus.drain()

    recorded = bus.recorded(session.call_session_id)
    assert recorded, "no events recorded"
    for event in recorded:
        assert event.call_session_id == session.call_session_id
        assert event.trace_id == session.trace_id
        assert event.occurred_at is not None


async def test_timeline_is_human_readable(orchestrator: CallOrchestrator) -> None:
    session = await orchestrator.start_cold_call()
    await orchestrator.enter_ivr(session)
    rows = CallOrchestrator.timeline(session)
    assert rows[0].startswith("+   0.00s  (start) connecting")
    assert "connecting -> ivr" in rows[1]
