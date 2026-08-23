"""The call state machine — pure rules, no I/O.

One explicit transition table (`ARCHITECTURE.md` §4). Everything about a call's
progress goes through here, and the orchestrator is its **only** writer, because two
writers of `call_sessions.state` is the classic contact-centre race.

Two entries in this table encode real decisions rather than mechanics:

* `QUEUED -> MATCHED` and `INTAKE_ACTIVE -> MATCHED` both exist. Leaving the queue is
  driven by agent availability alone, never by whether intake finished (`D12`). If a
  caller is mid-sentence when an agent frees up, we match anyway and the intake
  finalises as partial during the offer window (`D21`).
* `OFFERED -> MATCHED` exists so a declined or timed-out offer re-matches to somebody
  else instead of stranding the caller (RONA, `D33`).
* There is **no rating state** (`D46`). `WRAP_UP` goes straight to `CLOSED`. The customer
  rates in the IVR seconds after hanging up while the agent may still be typing, so the
  two are concurrent; a rating state after wrap-up asserted an ordering that is simply
  false. A rating arrives as an event and attaches to the call record whenever it lands,
  including after the call is closed.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from readycall.domain.enums import TERMINAL_STATES, CallState
from readycall.errors import IllegalTransition

S = CallState

_TRANSITIONS: Final[dict[CallState, frozenset[CallState]]] = {
    # App path only. A cold call from a windscreen sticker starts at CONNECTING (D19).
    S.INTENT_CREATED: frozenset({S.CONNECTING, S.FAILED}),
    S.CONNECTING: frozenset({S.IVR, S.QUEUED, S.ABANDONED, S.FAILED}),
    S.IVR: frozenset({S.QUEUED, S.VOICEMAIL, S.ABANDONED, S.FAILED}),
    S.QUEUED: frozenset({S.INTAKE_ACTIVE, S.MATCHED, S.VOICEMAIL, S.ABANDONED, S.FAILED}),
    S.INTAKE_ACTIVE: frozenset({S.INTAKE_COMPLETE, S.MATCHED, S.ABANDONED, S.FAILED}),
    S.INTAKE_COMPLETE: frozenset({S.MATCHED, S.ABANDONED, S.FAILED}),
    S.MATCHED: frozenset({S.OFFERED, S.MATCHED, S.QUEUED, S.ABANDONED, S.FAILED}),
    S.OFFERED: frozenset({S.IN_CALL, S.MATCHED, S.ABANDONED, S.FAILED}),
    S.IN_CALL: frozenset({S.WRAP_UP, S.TRANSFERRED, S.FAILED}),
    S.WRAP_UP: frozenset({S.CLOSED, S.FAILED}),
    # terminal
    S.CLOSED: frozenset(),
    S.ABANDONED: frozenset(),
    S.VOICEMAIL: frozenset(),
    S.TRANSFERRED: frozenset(),
    S.FAILED: frozenset(),
}

TRANSITIONS: Final = MappingProxyType(_TRANSITIONS)

#: States a call may legally start in. `INTENT_CREATED` for the app path;
#: `CONNECTING` for everything else (`D19`).
START_STATES: Final[frozenset[CallState]] = frozenset({S.INTENT_CREATED, S.CONNECTING})


def can(from_state: CallState, to_state: CallState) -> bool:
    return to_state in TRANSITIONS[from_state]


def assert_can(from_state: CallState, to_state: CallState) -> None:
    if not can(from_state, to_state):
        raise IllegalTransition(from_state, to_state)


def is_terminal(state: CallState) -> bool:
    return state in TERMINAL_STATES


def reachable_from(state: CallState) -> frozenset[CallState]:
    return TRANSITIONS[state]


def validate_table() -> None:
    """Self-check the table. Called by startup validation and by a unit test.

    Catches the two ways this table rots: a state nobody can reach (dead code), and a
    non-terminal state with no way out (a call that can never close).
    """
    missing = set(CallState) - set(TRANSITIONS)
    if missing:
        raise AssertionError(f"states missing from the transition table: {sorted(missing)}")

    for state, targets in TRANSITIONS.items():
        if state in TERMINAL_STATES:
            if targets:
                raise AssertionError(f"terminal state {state} has outgoing transitions")
        elif not targets:
            raise AssertionError(f"non-terminal state {state} has no way out")

    incoming = {t for targets in TRANSITIONS.values() for t in targets}
    orphans = set(CallState) - incoming - START_STATES
    if orphans:
        raise AssertionError(f"unreachable states: {sorted(orphans)}")


def path_exists(from_state: CallState, to_state: CallState) -> bool:
    """Breadth-first reachability. Used by tests to assert a call can always close."""
    seen = {from_state}
    frontier = [from_state]
    while frontier:
        current = frontier.pop()
        if current == to_state:
            return True
        for nxt in TRANSITIONS[current]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False
