"""The state machine's rules, including the ones that encode decisions.

Several of these assert *design* rather than mechanics — if someone "tidies up" the
transition table later, the test says which decision they just broke.
"""

from __future__ import annotations

import pytest

from readycall.domain.enums import TERMINAL_STATES, CallState
from readycall.errors import IllegalTransition
from readycall.services.call_orchestrator import machine

S = CallState


def test_table_is_internally_consistent() -> None:
    machine.validate_table()


@pytest.mark.parametrize("start", sorted(machine.START_STATES))
def test_every_call_can_reach_closed(start: CallState) -> None:
    assert machine.path_exists(start, S.CLOSED)


@pytest.mark.parametrize("state", sorted(set(CallState) - TERMINAL_STATES))
def test_every_live_state_can_reach_a_terminal_state(state: CallState) -> None:
    assert any(machine.path_exists(state, terminal) for terminal in TERMINAL_STATES)


def test_leaving_the_queue_does_not_require_intake_to_finish() -> None:
    """`D12`: agent availability drives the queue, never AI completeness.

    Both routes out of QUEUED must exist — with intake and without it — and a caller
    who is mid-sentence must still be matchable (`D21`).
    """
    assert machine.can(S.QUEUED, S.MATCHED)
    assert machine.can(S.QUEUED, S.INTAKE_ACTIVE)
    assert machine.can(S.INTAKE_ACTIVE, S.MATCHED)


def test_declined_offer_re_matches_rather_than_stranding_the_caller() -> None:
    """`D33` RONA: a declined or timed-out offer goes back to matching."""
    assert machine.can(S.OFFERED, S.MATCHED)
    assert machine.can(S.MATCHED, S.MATCHED)


def test_a_cold_call_needs_no_intent_state() -> None:
    """`D19`: CONNECTING is a legal start state, so no app is required."""
    assert S.CONNECTING in machine.START_STATES
    assert machine.path_exists(S.CONNECTING, S.IN_CALL)


def test_voicemail_is_reachable_from_the_queue() -> None:
    """`D25`: after-hours callers get the intake pipeline, not a dead line."""
    assert machine.can(S.QUEUED, S.VOICEMAIL)
    assert machine.can(S.IVR, S.VOICEMAIL)


def test_terminal_states_are_dead_ends() -> None:
    for state in TERMINAL_STATES:
        assert machine.reachable_from(state) == frozenset()
        assert machine.is_terminal(state)


def test_illegal_transitions_raise() -> None:
    with pytest.raises(IllegalTransition):
        machine.assert_can(S.QUEUED, S.IN_CALL)  # cannot skip the offer handshake
    with pytest.raises(IllegalTransition):
        machine.assert_can(S.CLOSED, S.QUEUED)  # no resurrection
    with pytest.raises(IllegalTransition):
        machine.assert_can(S.CONNECTING, S.IN_CALL)  # no bypassing the queue


def test_an_answered_call_cannot_be_marked_abandoned() -> None:
    """Abandonment means the caller gave up waiting. Once answered it is impossible,
    and conflating the two would quietly corrupt the abandonment metric."""
    assert not machine.can(S.IN_CALL, S.ABANDONED)
    assert not machine.can(S.WRAP_UP, S.ABANDONED)
