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

from datetime import date, datetime
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
    #: The anchor the client ticks its own clock from, so the number moves every second
    #: instead of only when something else refreshes the snapshot.
    acw_since: datetime | None = None
    long_acw: bool = False
    #: Which intents may be declared right now (`D59`). The server decides; the screen
    #: greys out the rest rather than letting a click fail with a 400.
    declarable: tuple[str, ...] = ()
    #: True while in after-call work. The standing instruction is unchanged underneath,
    #: but the agent owes a declaration, so the control must not render the old choice as
    #: though it were current.
    awaiting_declaration: bool = False
    #: Why the intent is what it is: `signed_in`, `rona_missed_offer`,
    #: `last_call_fulfilled`, `agent_declared`. `not_ready` alone cannot distinguish
    #: "just arrived" from "your last call is done", and those need different screens.
    intent_reason: str = ""


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
    #: Required when `challenge == "other"` — what the agent actually did, in their words.
    #: Real verification does not fit a closed list (`D57`).
    challenge_note: str | None = Field(default=None, max_length=500)
    #: Required for `third_party`: who is actually on the phone. The disclosure log has to
    #: name them, not merely record that somebody-not-the-policyholder called.
    caller_name: str | None = Field(default=None, max_length=128)
    relationship: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    #: Deliberately re-attesting after an earlier attestation on this call. The client
    #: sets it only when the agent explicitly reopens the control, so a double-click
    #: cannot silently rewrite a disclosure record (`D60`).
    amend: bool = False


class IdentityOut(ApiModel):
    assurance: str
    customer_id: str | None
    method: str
    may_disclose_policy_details: bool
    #: Set when a third party was declared: the workstation shows an authority-check step.
    authority_check_required: bool = False
    #: True once the agent has attested anything on this call. The control then locks —
    #: all three outcomes, including the one that was chosen. An attestation is a signed
    #: statement in a disclosure log, not a toggle (`D60`).
    attested: bool = False
    #: What they attested, for the panel to show back: confirmed / not_this_person /
    #: third_party.
    attested_outcome: str | None = None
    third_party_name: str | None = None
    relationship: str | None = None
    #: How many attestations this call has accumulated. Amending **appends** (`D60`), so
    #: this only ever grows, and the client needs it: the panel's "reopened for editing"
    #: flag is local, and without a server-side signal that a new attestation landed it
    #: had nothing to reset itself on — so one press of *amend* unlocked the control for
    #: the rest of the call, which is the opposite of what `D60` decided.
    attestation_count: int = 0


class CaptureOut(ApiModel):
    """What the agent's own panel renders — **including the digits** (`D58`).

    `D44`'s inverted default is *"masked in transcripts and logs"*, and the first
    implementation over-read it into "masked everywhere", which deleted the feature: the
    agent asked the caller to key these digits and has to read them back. `masked` is
    carried alongside for anything that is not the agent's live screen.

    This is a different question from the disclosure gate (`D53`). That governs what *we*
    reveal from the bank's records; these digits are the caller's own input, typed a
    second ago, to the person they are speaking to.
    """

    capture_id: str
    state: str
    length: int
    digits: str
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
    #: When the current call was answered. The call timer is drawn from this, so a browser
    #: refresh mid-call shows the true elapsed time instead of restarting from zero.
    call_answered_at: datetime | None = None


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
    #: DEMO: place the call even when the queue's schedule says it is shut. Rehearsals
    #: happen at 2 a.m. and judging happens at 10 a.m.; without this, half the queues are
    #: closed for one of them. It stands in for nothing in the real system — production
    #: has no such flag, and the closed-queue path (`D25`) is exercised by leaving it off.
    ignore_hours: bool = False


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


# --- the brief, as it crosses the wire ---------------------------------------------
#
# This DTO exists because `CaseBrief.model_dump()` LEAKED. The domain object embeds the
# whole frozen `ContextSnapshot`, so dumping it put the policy number, sum insured, every
# coverage figure and the customer's date of birth into the payload of a call sitting at
# `L1_PROBABLE` — while the same response said `may_disclose_policy_details: false`.
# `BriefBuilder` gates the rendered Thai lines, which is what it was asked to do; nothing
# gated the object graph hanging off the side of them.
#
# `D42` names this failure exactly: "Sending the full brief and hiding fields in React
# would put someone's coverage one devtools panel away." The gate has to be the *shape of
# what is serialised*, not a flag next to it — so these models simply have nowhere to put
# a policy number until the assurance level permits one.


class BriefIntentOut(ApiModel):
    code: str
    label_th: str
    confidence: float
    source: str


class BriefCustomerOut(ApiModel):
    """Requires `L1_PROBABLE`. A name is what lets the agent open the conversation."""

    display_name_th: str
    segment: str | None = None
    is_vulnerable: bool = False


class BriefCoverageOut(ApiModel):
    label_th: str
    limit_text: str | None = None


class BriefPolicyOut(ApiModel):
    """Requires `L2_STRONG` (`D20`). A borrowed phone must not surrender these."""

    policy_no: str
    product_th: str | None = None
    status: str
    line: str
    sum_insured: float | None = None
    next_due_date: date | None = None
    coverages: tuple[BriefCoverageOut, ...] = ()


class BriefProvenanceOut(ApiModel):
    field: str
    source: str
    stale: bool = False


class BriefOut(ApiModel):
    version: int
    kind: str
    urgency: str
    intent: BriefIntentOut | None = None
    summary_th: str | None = None
    suggested_opening_th: str | None = None
    #: Already filtered by `requires_assurance` — a step the agent may not take yet is
    #: absent, not greyed out.
    actions_th: tuple[str, ...] = ()
    customer: BriefCustomerOut | None = None
    relevant_policy: BriefPolicyOut | None = None
    other_policy_count: int = 0
    recent_claim_count: int = 0
    last_contact_th: str | None = None
    #: True whenever assurance is below `L2_STRONG`; the workstation says so rather than
    #: rendering an empty row that looks like missing data.
    disclosure_locked: bool = True
    degraded: str = "none"
    build_ms: float | None = None
    provenance: tuple[BriefProvenanceOut, ...] = ()
