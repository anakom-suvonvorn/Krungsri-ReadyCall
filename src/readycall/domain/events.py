"""Event schemas.

Every event carries `call_session_id`, `trace_id`, `occurred_at` and a
`schema_version` (`ARCHITECTURE.md` §14). Consumers are idempotent on `event_id`,
and replaying a call's stream must reproduce its final state — that property is what
lets the scenario runner exercise the whole system without a telephone.

Adding a field is fine; changing the meaning of one means bumping `schema_version`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from readycall import ids
from readycall.domain.enums import (
    AssuranceLevel,
    CallState,
    ConsentScope,
    DegradationReason,
    FinalizeReason,
    MatchKind,
    OfferOutcome,
)


class Event(BaseModel):
    """Base envelope. `name` is the routing key on the bus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ClassVar[str] = "event"
    schema_version: ClassVar[int] = 1

    event_id: str = Field(default_factory=ids.event_id)
    call_session_id: str
    occurred_at: datetime
    trace_id: str | None = None

    @property
    def topic(self) -> str:
        return self.name


# --- intent & identity -----------------------------------------------------------------


class IntentCreated(Event):
    name: ClassVar[str] = "intent.created"
    intent_id: str
    customer_id: str
    product_code: str | None = None


class IdentityResolved(Event):
    name: ClassVar[str] = "identity.resolved"
    customer_id: str | None
    assurance: AssuranceLevel
    method: str


class ContextSnapshotReady(Event):
    name: ClassVar[str] = "context.snapshot.ready"
    snapshot_id: str
    customer_id: str | None
    build_ms: float
    degraded: DegradationReason = DegradationReason.NONE


# --- call lifecycle --------------------------------------------------------------------


class CallInitiated(Event):
    name: ClassVar[str] = "call.initiated"
    entry_channel: str
    telephony_call_id: str | None = None
    caller_number: str | None = None
    dialled_did: str | None = None


class CallStateChanged(Event):
    name: ClassVar[str] = "call.state.changed"
    from_state: CallState | None
    to_state: CallState
    reason: str


class CallQueued(Event):
    name: ClassVar[str] = "call.queued"
    queue_id: str
    position: int | None = None
    estimated_wait_s: float | None = None


class CallEnded(Event):
    name: ClassVar[str] = "call.ended"
    end_reason: str
    duration_s: float | None = None


# --- consent & intake ------------------------------------------------------------------


class ConsentRecorded(Event):
    name: ClassVar[str] = "consent.recorded"
    scope: ConsentScope
    granted: bool
    basis: str


class IntakeStarted(Event):
    name: ClassVar[str] = "intake.started"
    intake_id: str
    strategy: str


class TranscriptTurnAdded(Event):
    """One utterance, on the bus. Consumed by Analysis, Agent Delivery and the recorder.

    ⚠️ **The event has to carry everything a consumer needs, because it is the only
    carrier.** `engine`, `engine_version`, `is_final` and `intake_id` were added by `D114`
    when the turns became durable: `transcript_turns` has had those columns on paper since
    P0, and a subscriber that persists what it is given cannot invent what it was not.

    `is_final=False` is not a detail — it means the endpointer cut the utterance at
    `max_segment_ms` rather than at a pause, so the caller was still talking and a brief
    built from it must not read as a finished thought.
    """

    name: ClassVar[str] = "transcript.turn"
    turn_id: str
    seq: int
    speaker_role: str
    text: str
    t_start_ms: int
    t_end_ms: int
    asr_confidence: float | None = None
    engine: str | None = None
    engine_version: str | None = None
    is_final: bool = True
    intake_id: str | None = None


class IntakeFinalized(Event):
    name: ClassVar[str] = "intake.finalized"
    intake_id: str
    finalize_reason: FinalizeReason
    is_partial: bool
    turn_count: int


# --- analysis --------------------------------------------------------------------------


class BriefUpdated(Event):
    name: ClassVar[str] = "analysis.brief.updated"
    brief_id: str
    version: int
    kind: str
    intent_code: str | None = None
    confidence: float | None = None
    degraded: DegradationReason = DegradationReason.NONE


# --- matching & the offer handshake ----------------------------------------------------


class AgentPresenceChanged(Event):
    name: ClassVar[str] = "agent.presence.changed"
    call_session_id: str = "-"  # presence is not call-scoped; keeps the envelope uniform
    agent_id: str
    system_state: str
    agent_intent: str


class MatchingDecided(Event):
    name: ClassVar[str] = "matching.decided"
    decision_id: str
    kind: MatchKind
    chosen_agent_id: str | None = None
    candidate_count: int = 0


class MatchingDeferred(Event):
    name: ClassVar[str] = "matching.deferred"
    decision_id: str
    deferred_for_agent_id: str
    expected_free_in_s: float
    fit_gap: float


class CallOffered(Event):
    name: ClassVar[str] = "call.offered"
    assignment_id: str
    agent_id: str
    accept_mode: str
    timeout_s: float


class OfferResolved(Event):
    """Accepted, declined, timed out (RONA), or cancelled (`D33`)."""

    name: ClassVar[str] = "offer.resolved"
    assignment_id: str
    agent_id: str
    outcome: OfferOutcome
    time_to_accept_ms: float | None = None
    reason: str | None = None


# --- post-call -------------------------------------------------------------------------


class WrapupSaved(Event):
    name: ClassVar[str] = "wrapup.saved"
    agent_id: str
    disposition: str
    was_edited: bool
    acw_seconds: float | None = None
    #: True when this was filed from the backlog rather than during after-call work
    #: (`D87`). A wrap-up written twenty minutes later is still a real wrap-up, but it is
    #: not the same thing as one written while the call was fresh — and a metric that
    #: cannot tell them apart would quietly report the ACW story as better than it is.
    filed_late: bool = False


class RatingReceived(Event):
    name: ClassVar[str] = "rating.received"
    source: str
    csat: int | None = None
    nps: int | None = None


# --- registry --------------------------------------------------------------------------

EVENT_TYPES: tuple[type[Event], ...] = (
    IntentCreated,
    IdentityResolved,
    ContextSnapshotReady,
    CallInitiated,
    CallStateChanged,
    CallQueued,
    CallEnded,
    ConsentRecorded,
    IntakeStarted,
    TranscriptTurnAdded,
    IntakeFinalized,
    BriefUpdated,
    AgentPresenceChanged,
    MatchingDecided,
    MatchingDeferred,
    CallOffered,
    OfferResolved,
    WrapupSaved,
    RatingReceived,
)

EVENTS_BY_NAME: dict[str, type[Event]] = {e.name: e for e in EVENT_TYPES}


def decode(name: str, payload: dict[str, Any]) -> Event:
    """Rebuild a typed event from the wire (`memory`/`redis`/`kafka` adapters).

    An unknown name is a hard error: silently dropping an event we do not understand
    is how a replay stops reproducing the truth.
    """
    try:
        event_type = EVENTS_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown event name: {name!r}") from exc
    return event_type.model_validate(payload)


AnyEvent = Event
Topic = Literal["*"] | str
