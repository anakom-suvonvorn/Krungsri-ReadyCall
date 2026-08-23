"""Request and response DTOs for the public API.

These are deliberately *not* the domain models. Two reasons, and the first is the one that
matters here:

* **A request DTO defines what a client is allowed to say.** `CreateIntentRequest` has no
  `customer_id` field, so there is no way for a caller to assert an identity even by
  accident — the omission is the enforcement of `D4`, not a comment about it.
* A response DTO stops internal shape leaking into a public contract. `CallIntent` carries
  `correlation_token_hash`; the response carries the *token*, once, and never again.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- POST /v1/calls/intents -------------------------------------------------------------


class CreateIntentRequest(ApiModel):
    """What the app sends when the customer taps Contact.

    Note what is absent: `customer_id`. It comes from the session (`D4`).
    """

    product_code: str | None = Field(
        default=None, max_length=64, description="the plan they tapped Contact from"
    )
    plan_id: str | None = Field(default=None, max_length=64)
    entry_screen: str | None = Field(
        default=None,
        max_length=128,
        description="the screen the tap came from, e.g. 'coverage.hospitalisation'",
    )
    app_intent: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "intent hint derived from the screen (`D41`). A hint, weighted like a menu "
            "answer - speech may still override it later."
        ),
    )
    preferred_channel: str = Field(default="pstn", max_length=16)


class CreateIntentResponse(ApiModel):
    intent_id: str
    #: The bearer credential the call carries. Returned **once**; only its hash is stored.
    correlation_token: str
    dial_target: str
    expires_at: datetime


class IntentStatusResponse(ApiModel):
    """What the app (or the simulator) can see about an intent afterwards.

    This is what makes the pitch's central claim visible rather than asserted: the context
    was assembled *before the phone rang*, and here is how long it took and where every
    field came from.
    """

    intent_id: str
    customer_id: str
    expires_at: datetime
    expired: bool
    context_ready: bool
    context_build_ms: float | None = None
    snapshot_id: str | None = None
    policies_found: int = 0
    interactions_found: int = 0
    provenance_fields: int = 0
    degraded: str | None = None


# --- POST /v1/app/context-events ---------------------------------------------------------


class ContextEventRequest(ApiModel):
    """A screen the customer looked at, so *"viewed hospitalisation coverage"* is real."""

    product_code: str | None = Field(default=None, max_length=64)
    section: str = Field(max_length=128)
    dwell_ms: int = Field(default=0, ge=0, le=60 * 60 * 1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextEventResponse(ApiModel):
    accepted: bool
    events_held: int


# --- GET /v1/app/contact-reasons ---------------------------------------------------------


class ContactReason(ApiModel):
    """One reason a customer might be calling, for the in-app picker.

    These come from the **same `menus.yaml` the IVR reads** (`D28`). If the app offered a
    different list from the keypad, a customer would get different options depending on
    which door they came through, and the intent taxonomy would quietly fork in two.
    """

    intent_code: str
    label_th: str
    #: The keypad digit this reason corresponds to in the IVR. Shown so the app and the
    #: phone menu are visibly the same menu.
    key: str | None = None


class ContactReasonsResponse(ApiModel):
    product_line: str
    reasons: tuple[ContactReason, ...]


# --- demo only ---------------------------------------------------------------------------


class AppPlan(ApiModel):
    """A policy the customer actually holds, for the simulator's plan list.

    Demo scaffolding: the real Krungsri app already knows the customer's plans from its own
    screens and would never ask us for them. Kept under `/v1/demo/` so the public `/v1`
    surface stays exactly what a real client would call.
    """

    product_code: str
    product_line: str
    name_th: str
    policy_no_masked: str
    status: str


class DemoPersona(ApiModel):
    customer_id: str
    display_name: str
    summary: str
    product_code: str | None = None
    entry_screen: str | None = None
    app_intent: str | None = None


class DemoLoginRequest(ApiModel):
    customer_id: str = Field(max_length=64)


class DemoLoginResponse(ApiModel):
    customer_id: str
    display_name: str


# --- shared ------------------------------------------------------------------------------


class HealthResponse(ApiModel):
    status: str
    version: str
    core_data_provider: str
    session_resolver: str
    intents_loaded: int


class ErrorResponse(ApiModel):
    detail: str


# --- the agent workstation (D32) ---------------------------------------------------------
#
# Same rule as the customer side, applied to staff: **no request body carries an
# `agent_id`**. It comes from the agent session cookie. A workstation that could name its
# own agent id would let any signed-in agent accept another agent's offer, end their call,
# or attest an identity in their name - and the disclosure log would record it as them.


class AgentLoginRequest(ApiModel):
    """DEMO ONLY. Real deployments authenticate against the bank's staff directory."""

    agent_id: str = Field(max_length=32)


class AgentSkillOut(ApiModel):
    skill_code: str
    label_th: str
    proficiency: float


class AgentPresenceOut(ApiModel):
    agent_id: str
    display_name: str
    system_state: str
    agent_intent: str
    #: Derived, never stored: AVAILABLE + READY + under capacity + within schedule.
    offerable: bool
    since: datetime
    current_load: int
    max_concurrent: int
    skills: tuple[AgentSkillOut, ...] = ()
    #: Present only while in after-call work. Counts up from the media disconnect (`D45`).
    acw_seconds: float | None = None
    long_acw: bool = False


class DeclareStateRequest(ApiModel):
    """`NOT_READY` is rejected: a person is on a break, at lunch, or doing admin (`D51`)."""

    agent_intent: str = Field(max_length=32)


class OfferOut(ApiModel):
    assignment_id: str
    call_session_id: str
    accept_mode: str
    timeout_s: float
    offered_at: datetime
    #: Enough to decide whether to press Accept — never the whole brief, which is gated
    #: on assurance and rendered server-side after the offer is taken (`D42`).
    queue_id: str
    queue_label_th: str
    intent_code: str | None = None
    intent_label_th: str | None = None
    urgency: str = "normal"
    waited_s: float = 0.0
    assurance: str = "l0_anonymous"
    #: The one-line Thai rationale from the matcher: *why this agent* (`D22`).
    rationale_th: str | None = None


class DeclineOfferRequest(ApiModel):
    reason: str = Field(default="declined", max_length=64)


class EndCallRequest(ApiModel):
    reason: str = Field(default="caller_hung_up", max_length=64)


class WrapupRequest(ApiModel):
    """Saving this closes the **call record**. It does not end after-call work (`D45`)."""

    disposition: str = Field(max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    follow_up_required: bool = False
    #: Whether the agent changed the AI's draft. The honest input to "did the draft help".
    was_edited: bool = True


class AttestIdentityRequest(ApiModel):
    """The agent's three-way control (`D42`). Only a person may use it."""

    outcome: str = Field(max_length=32, description="confirmed | not_this_person | third_party")
    challenge: str | None = Field(
        default=None, max_length=64, description="required for `confirmed`; the NAME only"
    )
    relationship: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class IdentityOut(ApiModel):
    assurance: str
    customer_id: str | None
    method: str
    may_disclose_policy_details: bool
    #: Set when a third party was declared: the workstation shows an authority-check step.
    authority_check_required: bool = False


class CaptureOut(ApiModel):
    """Never carries the digits. `masked` is the only representation that leaves here
    for anything but the agent's own live panel (`D44`)."""

    capture_id: str
    state: str
    length: int
    masked: str
    labelled_as: str | None = None
    lookups: tuple[dict[str, Any], ...] = ()


class CaptureKeysRequest(ApiModel):
    """DTMF arriving from telephony. Digits only — `*` and `#` are control keys."""

    digits: str = Field(max_length=32, pattern=r"^[0-9]*$")


class CaptureLabelRequest(ApiModel):
    labelled_as: str = Field(max_length=64)


class CaptureLookupRequest(ApiModel):
    kind: str = Field(max_length=32, description="policy_number | claim_number | citizen_id_last4")


class QueueOut(ApiModel):
    queue_id: str
    label_th: str
    sla_seconds: int
    is_open: bool
    closed_reason: str | None = None
    next_open_at: datetime | None = None
    waiting: int = 0
    longest_wait_s: float = 0.0


class WorkstationSnapshot(ApiModel):
    """Everything the workstation needs to render itself from cold.

    The REST fallback for the WebSocket: a client that has been away longer than the
    replay buffer re-fetches this rather than trying to catch up event by event.
    """

    presence: AgentPresenceOut
    offer: OfferOut | None = None
    active_call_session_id: str | None = None
    identity: IdentityOut | None = None
    brief: dict[str, Any] | None = None
    captures: tuple[CaptureOut, ...] = ()
    queues: tuple[QueueOut, ...] = ()
    server_time: datetime | None = None


class PlaceCallRequest(ApiModel):
    """DEMO: a caller arriving, standing in for telephony until P5.

    No `customer_id` here either (`D4`). The `correlation_token` is what binds this call
    to the intent the app created — the same bearer credential a real dialled call would
    carry — and everything else is what a phone line genuinely knows: the number it came
    from, the number it dialled, and which keys were pressed.
    """

    correlation_token: str | None = Field(default=None, max_length=128)
    caller_number: str | None = Field(default=None, max_length=24)
    did: str | None = Field(default=None, max_length=24)
    intent_code: str | None = Field(default=None, max_length=64)
    #: Menu keypresses, in order. At P3 these come from real DTMF.
    keys: tuple[str, ...] = ()
    #: Seconds already waited, so a demo can show a caller near their SLA without waiting.
    waited_s: float = Field(default=0.0, ge=0.0, le=3600.0)


class PlaceCallResponse(ApiModel):
    call_session_id: str
    state: str
    queue_id: str
    queue_open: bool
    assurance: str
    closed_reason: str | None = None
    next_open_at: datetime | None = None
    offered_to: str | None = None
    #: Which of `D50`'s two reasons applies, when nobody could take the call.
    unplaced_reason: str | None = None
