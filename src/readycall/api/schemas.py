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
    #: The instant the wait is measured from, so the card can **tick** it (`B27`). The
    #: scalar above is the same number at snapshot time; this is what makes it move.
    #: Exactly the shape `acw_since` / `acw_seconds` already use, and for the same reason
    #: (`D68`, `B8`): a pre-computed duration can only change when a snapshot arrives, and
    #: snapshots arrive when something happens — which, for a caller who is waiting, is
    #: precisely never.
    waited_since: datetime | None = None
    assurance: str = "l0_anonymous"
    #: The one-line Thai rationale from the matcher: *why this agent* (`D22`).
    rationale_th: str | None = None
    #: Which time round the floor this caller is on (`D113`). 1 on virtually every call.
    #: Above 1 means every qualified agent has already turned them down and the exclusions
    #: were cleared — an agent seeing the same call twice with no explanation concludes the
    #: system is broken, and naming it is also the point: it is the sentence that makes
    #: somebody take it.
    offer_round: int = 1
    #: True when this agent is the **only** one who could take this call right now
    #: (`D113`). Deliberately a boolean rather than a count of available agents: "three
    #: others could take this" is a diffusion-of-responsibility prompt on a card whose
    #: other button is *decline*, and it is not actionable. "You are the only one" is
    #: both — it tells the agent the consequence of declining, which is the one thing
    #: they cannot otherwise know.
    sole_candidate: bool = False
    #: A **preview of the brief, gated exactly like the brief itself** (`D69`).
    #:
    #: The offer card used to carry only routing metadata — queue, urgency, wait, the
    #: rationale — so the agent pressed Accept knowing why the call had come to them and
    #: nothing about what it was *about*. That inverts the product: the pitch's whole claim
    #: is that the agent is ready before they speak, and the seconds spent reading the card
    #: are exactly the seconds `D21` set aside for preparing.
    #:
    #: These come from the same gated `BriefOut` the panel renders, so nothing here can
    #: disclose more than the assurance level permits — at `L1` there is no name and no
    #: policy, only the reason for the call (`D53`, `B5`).
    summary_th: str | None = None
    customer_name_th: str | None = None
    #: The first playbook step, so the card answers "what will I be doing" as well as
    #: "who is this". Below L2 that is the verify-identity step, which is the right first
    #: thing to see (`D56`).
    first_action_th: str | None = None


class DeclineOfferRequest(ApiModel):
    reason: str = Field(default="declined", max_length=64)
    #: Decline **and stop offering** (`D109`). Without it, declining leaves the agent
    #: `READY` and the very next tick can ring them with the next caller — which is right
    #: when they turned this *particular* call down, and wrong when what they meant was
    #: "not now". Letting the offer ring out already does this (RONA, `D33`); this is the
    #: same ending, chosen deliberately instead of by waiting twenty seconds in silence.
    #:
    #: A flag rather than a second request, because "declare not_ready" is not something
    #: an agent may do: `NOT_READY` is the one intent the **platform** writes (`D51`), so
    #: a client-side decline-then-declare would be refused - correctly - by
    #: `declarable_intents`.
    stop_offering: bool = False


class EndCallRequest(ApiModel):
    reason: str = Field(default="caller_hung_up", max_length=64)


class AssistPushRequest(ApiModel):
    """One thing the broker puts on the customer's screen (`D120`).

    `kind` is validated against a CLOSED set in the router, for the same reason the intent
    taxonomy is closed: the customer's page renders known shapes, and an unknown kind is a
    blank panel on somebody's phone mid-call.
    """

    kind: str = Field(max_length=32)
    title_th: str = Field(max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


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
    #: Whether the agent may **act** on the policy: read a number aloud, confirm a figure,
    #: change a detail, process a claim (`D74`). Renamed from `may_disclose_policy_details`
    #: because it was being asked two different questions — what may be *shown* to the
    #: agent, and what may be *done* with it. Only the second is gated above L1.
    may_act_on_policy: bool
    #: Whether there is an identified customer to render at all. False only at L0, and not
    #: as a restriction: at L0 the system genuinely has nobody to show.
    may_see_record: bool = False
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
    #: Which of the three outcomes may be pressed right now (`D71`). Server-decided, same
    #: reasoning as `declarable` (`D59`) — a client computing this would need its own copy
    #: of the rules and would eventually disagree. Empty when nobody was ever proposed
    #: (`L0` from the first second: nothing to confirm, reject, or act on behalf of);
    #: `third_party` alone when the call arrived on an app token, because authentication
    #: already happened and the only open question is who is holding the phone.
    attestable: tuple[str, ...] = ()
    #: True when the caller authenticated before the agent saw the call. Changes the
    #: third-party wording: not "I checked their authority" but "somebody is operating
    #: this account on the holder's behalf".
    system_verified: bool = False


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
    #: The anchor for the longest wait — the earliest-queued caller still in this queue.
    #: Same reason as `OfferOut.waited_since` (`B27`): a duration sent as a number can
    #: only change when a snapshot arrives.
    longest_wait_since: datetime | None = None
    #: Whether the signed-in agent holds this queue's required skill, i.e. whether any of
    #: these callers could actually reach them (`D70`). The strip listed all nine queues
    #: identically, so a health agent watched motor and life fill up with no way to tell
    #: which numbers were theirs to act on. Computed here because the client would
    #: otherwise need its own copy of the skill-to-queue mapping, and a second copy is a
    #: second thing that can disagree with the matcher.
    mine: bool = False


class ChallengeOut(ApiModel):
    """One verification method, served from `config/challenges.yaml` (`D72`).

    The workstation renders this list rather than keeping its own, so it can never offer an
    option the server would refuse — which is what two hand-kept copies eventually produce,
    and `Q12` says this list is going to change.
    """

    code: str
    label_th: str
    requires_note: bool = False


class TranscriptTurnOut(ApiModel):
    """One utterance, on its way to the transcript panel (`D106`).

    A wire DTO rather than the domain `TranscriptTurn`, for the reason `D53` gives and
    `B5` proved: serialising a domain model across a boundary ships whatever fields it
    grows later. The three this deliberately omits are `engine`, `engine_version` and
    `intake_id` — provenance the agent's screen has no use for and which would put the
    model's name in front of them as if it meant something.

    Not gated on assurance. This is the caller's own speech on the call being taken, not
    anything looked up about them, and the whole product is that an agent knows why
    somebody is calling before they answer — including an anonymous caller at L0, which is
    exactly the case with no other source of context.
    """

    turn_id: str
    seq: int
    #: `customer` / `agent` / `ai` — from the leg the audio was forked from, never from a
    #: diarisation model (`D26`).
    speaker_role: str
    text: str
    #: Milliseconds from the start of the recording, so the panel can show when in the
    #: call something was said without needing a wall-clock timestamp per turn.
    t_start_ms: int
    t_end_ms: int
    #: The engine's own confidence where it reports one. Rendered as a hint, never as a
    #: filter — a low-confidence turn is still what the caller said, and hiding it would
    #: leave a silent gap that reads as the caller having said nothing (`D16`).
    asr_confidence: float | None = None


class PendingWrapupOut(ApiModel):
    """A call this agent handled and never filed a wrap-up for (`D87`).

    Derived, not stored (`D78`): an assignment whose ACW has ended with no `call_wrapups`
    row *is* the backlog entry. Nothing marks a call as owing one.
    """

    call_session_id: str
    #: When the conversation itself ended, so the list can be ordered oldest-first and the
    #: agent can see which one has been waiting.
    ended_at: datetime | None = None
    #: How long the agent was in after-call work before they left it. Useful context when
    #: coming back cold: a 4-second ACW means they left immediately.
    acw_seconds: float | None = None
    intent_code: str | None = None
    intent_label_th: str | None = None
    #: Only when the identity of THAT call permits it (`D74`). A backlog row is still a
    #: disclosure surface, and it renders long after the call — so it is gated like any
    #: other, from the resolution rather than from whatever the snapshot happens to hold.
    customer_name_th: str | None = None
    assurance: str = "l0_anonymous"


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
    #: The verification methods this deployment allows (`D72`). Static per process, sent
    #: with the snapshot so the panel needs no second round trip to render its dropdown.
    challenges: tuple[ChallengeOut, ...] = ()
    #: The server's own clock, sent so the client can correct for a browser clock that
    #: disagrees. Every timer is `now - server_timestamp`, which silently assumed the two
    #: agreed; on a laptop whose clock has drifted the call timer is simply wrong, and
    #: nobody would suspect the clock. This field existed for exactly that and was read by
    #: nothing until `D68`.
    server_time: datetime | None = None
    #: When the current call was answered. The call timer is drawn from this, so a browser
    #: refresh mid-call shows the true elapsed time instead of restarting from zero.
    call_answered_at: datetime | None = None
    #: Whether the wrap-up record for the *current* call has been saved. The client used to
    #: track this in a local `Set`, which lost it on refresh and — worse — keyed it on a
    #: call id that becomes `null` the moment saving closes the record, so the confirmation
    #: never appeared at all. The server has always known this; it just never said (`D68`).
    wrapup_saved: bool = False
    #: The call the agent is wrapping up, which survives the record closing. `active_call_
    #: session_id` deliberately covers only IN_CALL and WRAP_UP, so it drops to `null` on
    #: save — correct for "which call can I still act on", useless for "what am I wrapping".
    wrapup_call_session_id: str | None = None
    #: Calls this agent left after-call work on without filing anything (`D87`). `D45`
    #: says the person decides when ACW ends, so they are free to walk away mid-form —
    #: but the record still has to be fileable afterwards, or "free to leave" quietly
    #: means "the note is lost". Oldest first.
    pending_wrapups: tuple[PendingWrapupOut, ...] = ()
    #: The live transcript of the active call, oldest first (`D106`). Also pushed on the
    #: socket as `transcript`, and it is the **same list** from the same service rather
    #: than a second answer to the question — the socket is a view and is allowed to be
    #: flaky (`D32`), so a tab opening mid-call must not have to wait for the caller to
    #: say something else before it shows anything.
    transcript: tuple[TranscriptTurnOut, ...] = ()


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
    #: Keypresses for the intake offer, which happens *after* the queue is decided and is
    #: therefore a separate script: `("1",)` records, `("2",)` holds, `()` says nothing and
    #: falls through to hold. Separate from `keys` so a demo cannot accidentally spend a
    #: menu press on the offer, or the reverse.
    intake_keys: tuple[str, ...] = ()
    #: Seconds already waited, so a demo can show a caller near their SLA without waiting.
    waited_s: float = Field(default=0.0, ge=0.0, le=3600.0)
    #: DEMO: place the call even when the queue's schedule says it is shut. Rehearsals
    #: happen at 2 a.m. and judging happens at 10 a.m.; without this, half the queues are
    #: closed for one of them. It stands in for nothing in the real system — production
    #: has no such flag, and the closed-queue path (`D25`) is exercised by leaving it off.
    ignore_hours: bool = False
    #: DEMO: a WAV file to play down the line as the caller's own voice, so the audio path
    #: runs end to end with no telephony (`D107`). A **bare filename** inside
    #: `Settings.demo_audio_dir` — never a path — because a path in a request body is a
    #: file-read primitive, and "it is only the demo endpoint" is how that argument always
    #: starts. Ignored unless the caller consented, since a recording nobody agreed to is
    #: the one thing this system must not make (`D14`).
    audio: str | None = Field(default=None, max_length=128)
    #: DEMO: pace the file at wall-clock speed rather than feeding it in one burst. Off by
    #: default because a request that blocks for the length of a phone call is a bad demo
    #: affordance; **on** is what makes the latency real, and it is what a phone does.
    audio_realtime: bool = False


class IntakeOut(ApiModel):
    """How the pre-call intake went — including, deliberately, when it did not happen.

    `declined` and `ignored` are outcomes, not errors (`D19`): the menu already routed the
    call, so the agent still gets a brief. What they must not get is a thin brief with no
    explanation, which is why `degraded` travels with it (`D14`).
    """

    outcome: str | None = None
    consented: bool | None = None
    recording: bool = False
    offers_made: int = 0
    intake_id: str | None = None
    turn_count: int = 0
    is_partial: bool = False
    degraded: str = "none"
    #: Prompt ids, in order — the spoken half of "show, do not claim" (`D18`).
    played: tuple[str, ...] = ()
    pressed: tuple[str, ...] = ()


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
    #: What the hold produced (`D88`). `null` when the queue was shut and nobody held.
    intake: IntakeOut | None = None


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
    #: Which carrier underwrote it (`D117`). The first thing a broker needs and the one
    #: field an insurer's own screen would never have: it decides who a claim is handed
    #: to and whose terms a comparison is against.
    insurer: str | None = None
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
    #: Whether this call is expected to END by handing the customer to the insurer
    #: (`D117`). A broker takes the notification and gathers what the insurer will ask
    #: for; it does not adjudicate. The screen says so before the agent starts talking,
    #: because "I will check and call you back" and "I am passing you to the insurer now"
    #: are different promises and only one of them is ours to make.
    handoff_to_insurer: bool = False
    degraded: str = "none"
    build_ms: float | None = None
    provenance: tuple[BriefProvenanceOut, ...] = ()
