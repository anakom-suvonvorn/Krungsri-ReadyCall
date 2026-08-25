"""Domain models — the vocabulary the whole system speaks.

Pure: no I/O, no ORM, no vendor types. **Adapters return these**, never raw rows or
vendor JSON (`CLAUDE.md`), which is exactly what makes the bank's data swappable on
hackathon morning (`DATA_MODEL.md` §4).

Pydantic v2 because these cross boundaries (events, API, DB payloads) and free
validation at the seam is worth more than the microseconds it costs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from readycall.domain.enums import (
    AgentIntent,
    AgentSystemState,
    AnalysisKind,
    AssuranceLevel,
    BriefKind,
    CallState,
    CefrLevel,
    ConsentScope,
    CustomerSegment,
    DegradationReason,
    EntryChannel,
    FinalizeReason,
    IdentityMethod,
    IntakeStrategyKind,
    Language,
    MatchKind,
    OfferOutcome,
    PolicyStatus,
    ProductLine,
    RatingSource,
    SpeakerRole,
    Urgency,
)


class DomainModel(BaseModel):
    """Base: immutable, strict, no silent extras."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------------------
# The bank's side (read-only, arrives via CoreDataProvider)
# --------------------------------------------------------------------------------------


class Product(DomainModel):
    product_code: str
    line: ProductLine
    name_th: str
    name_en: str | None = None
    short_desc: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class Coverage(DomainModel):
    """Coverage figures. **Always data, never model output** (`D16`).

    Deliberately a typed model rather than a free dict: if a coverage number can
    only arrive through a declared field, an LLM cannot invent one into the brief.
    """

    kind: str  # e.g. "ipd_room_board", "opd_limit", "deductible", "sum_insured"
    label_th: str
    amount: float | None = None
    currency: str = "THB"
    unit: str | None = None  # "per_day", "per_year", "per_visit"
    note: str | None = None


class Policy(DomainModel):
    policy_no: str
    customer_id: str
    product_code: str
    line: ProductLine
    status: PolicyStatus
    effective_date: date | None = None
    expiry_date: date | None = None
    sum_insured: float | None = None
    premium: float | None = None
    payment_frequency: str | None = None
    next_due_date: date | None = None
    coverages: tuple[Coverage, ...] = ()
    riders: tuple[str, ...] = ()
    agent_id_of_record: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status is PolicyStatus.ACTIVE


class Claim(DomainModel):
    claim_id: str
    policy_no: str
    kind: str
    status: str
    submitted_at: datetime | None = None
    incident_date: date | None = None
    amount_claimed: float | None = None
    amount_paid: float | None = None
    hospital_name: str | None = None
    documents_required: tuple[str, ...] = ()


class Interaction(DomainModel):
    interaction_id: str
    customer_id: str
    channel: str  # call / chat / branch / email / app
    direction: str  # inbound / outbound
    occurred_at: datetime
    topic: str | None = None
    summary: str | None = None
    agent_id: str | None = None
    outcome: str | None = None
    duration_s: float | None = None


class Holding(DomainModel):
    holding_id: str
    customer_id: str
    kind: str  # deposit / loan / card / fund
    opened_at: date | None = None
    balance_band: str | None = None
    status: str | None = None


class LifeEvent(DomainModel):
    event_id: str
    customer_id: str
    signal: str  # mortgage / new_child / job_change / relocation
    detected_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    source: str | None = None


class Customer(DomainModel):
    customer_id: str
    first_name_th: str
    last_name_th: str | None = None
    first_name_en: str | None = None
    last_name_en: str | None = None
    title_th: str | None = None
    dob: date | None = None
    segment: CustomerSegment = CustomerSegment.OTHER
    tier: str | None = None
    occupation: str | None = None
    income_band: str | None = None
    marital_status: str | None = None
    dependants: int | None = None
    preferred_language: str = "th"
    phones: tuple[str, ...] = ()
    email: str | None = None
    address_province: str | None = None
    kyc_status: str | None = None
    is_vulnerable: bool = False

    @property
    def display_name_th(self) -> str:
        parts = [p for p in (self.title_th, self.first_name_th, self.last_name_th) if p]
        return " ".join(parts)

    @property
    def polite_name_th(self) -> str:
        """How an agent would actually address them: 'คุณ<first name>'."""
        return f"คุณ{self.first_name_th}"


# --------------------------------------------------------------------------------------
# Our side
# --------------------------------------------------------------------------------------


class FieldProvenance(DomainModel):
    """Where one context field came from, and how old it is (`D18`).

    Every field on the workstation must be able to answer "says who, and when?".
    """

    field: str
    source: str  # e.g. "core:policies", "readycall:call_wrapups", "app:context_events"
    fetched_at: datetime
    provider: str  # the adapter that produced it
    stale: bool = False


class Customer360(DomainModel):
    """Everything we know, assembled for one call, at one moment."""

    customer: Customer | None = None
    selected_product: Product | None = None
    active_policies: tuple[Policy, ...] = ()
    relevant_policy: Policy | None = None
    recent_claims: tuple[Claim, ...] = ()
    recent_interactions: tuple[Interaction, ...] = ()
    holdings: tuple[Holding, ...] = ()
    life_events: tuple[LifeEvent, ...] = ()
    last_contact_at: datetime | None = None
    last_agent_id: str | None = None
    previous_inquiry: str | None = None


class ContextSnapshot(DomainModel):
    """A *frozen* Customer360: what the system knew when it decided (`D6`).

    Frozen so the workstation shows the state used for the decision, and so a
    scenario replay is reproducible even if upstream data moves underneath us.
    """

    snapshot_id: str
    customer_id: str | None
    built_at: datetime
    payload: Customer360
    provenance: tuple[FieldProvenance, ...] = ()
    provider_name: str = "unknown"
    build_ms: float | None = None
    degraded: DegradationReason = DegradationReason.NONE

    @property
    def is_stale(self) -> bool:
        return any(p.stale for p in self.provenance)


class IdentityResolution(DomainModel):
    """Who we think this is, how we decided, and how much we trust it (`D20`)."""

    method: IdentityMethod
    customer_id: str | None
    assurance: AssuranceLevel
    resolved_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def may_act_on_policy(self) -> bool:
        """Whether the agent may **do** things with the policy: read a number aloud,
        confirm a figure, change a detail, process a claim (`D74`).

        This is deliberately *not* about what the agent can see. Showing a bank employee
        the record they were routed to is internal processing, and it is how every real
        contact centre verifies a caller in the first place; the risk lives in what leaves
        the agent's mouth and what gets changed in the system, not on their screen.
        """
        return self.assurance.at_least(AssuranceLevel.L2_STRONG)

    @property
    def may_see_record(self) -> bool:
        """Whether there is an identified customer to show at all.

        `L0` is the only level that shows nothing, and not as a restriction — at `L0` the
        system genuinely has nobody to show.
        """
        return self.assurance.at_least(AssuranceLevel.L1_PROBABLE) and self.customer_id is not None

    @property
    def may_disclose_policy_details(self) -> bool:
        """Deprecated alias of `may_act_on_policy`, kept so nothing silently changes
        meaning while callers migrate. `D74` split one flag into two because it was being
        asked two different questions."""
        return self.may_act_on_policy


class CallIntent(DomainModel):
    """Created when the customer taps Contact in the app. Optional — a cold call
    has none (`D19`)."""

    intent_id: str
    customer_id: str
    product_code: str | None = None
    plan_id: str | None = None
    entry_screen: str | None = None
    app_context: dict[str, Any] = Field(default_factory=dict)
    correlation_token_hash: str
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class Consent(DomainModel):
    scope: ConsentScope
    granted: bool
    granted_at: datetime
    basis: str  # e.g. "ivr_keypress", "app_toggle"
    channel: str
    evidence_ref: str | None = None
    expires_at: datetime | None = None


class TranscriptTurn(DomainModel):
    """One utterance. Persisted incrementally, so a dropped call still leaves text."""

    turn_id: str
    call_session_id: str
    seq: int
    speaker_role: SpeakerRole
    text: str
    t_start_ms: int
    t_end_ms: int
    asr_confidence: float | None = None
    engine: str | None = None
    engine_version: str | None = None
    is_final: bool = True
    intake_id: str | None = None

    @property
    def duration_ms(self) -> int:
        return self.t_end_ms - self.t_start_ms


class ExtractedEntity(DomainModel):
    kind: str  # hospital, admission_date, plate_number, location, claim_id, amount...
    value: str
    normalized: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_turn_id: str | None = None


class IntakeResult(DomainModel):
    """What every `IntakeStrategy` produces, whatever it is (`D10`).

    Passive recording, TTS slot-filling, or a full conversational agent: downstream
    sees this same shape and cannot tell which ran. That is the whole point.
    """

    intake_id: str
    call_session_id: str
    strategy: IntakeStrategyKind
    started_at: datetime
    ended_at: datetime
    finalize_reason: FinalizeReason
    is_partial: bool
    turns: tuple[TranscriptTurn, ...] = ()
    slots: dict[str, Any] = Field(default_factory=dict)
    recording_ref: str | None = None
    degraded: DegradationReason = DegradationReason.NONE

    @property
    def transcript_text(self) -> str:
        return " ".join(t.text for t in self.turns if t.is_final)

    @property
    def spoke_at_all(self) -> bool:
        return bool(self.turns)


class IntentPrediction(DomainModel):
    """The one thing the LLM is allowed to decide (`D8`) — and only as a label."""

    intent_code: str
    label_th: str
    label_en: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: tuple[tuple[str, float], ...] = ()
    source: str = "unknown"  # did / dtmf / app / speech / blended

    @property
    def is_unknown(self) -> bool:
        return self.intent_code in {"unknown", ""}


class ConfidenceBreakdown(DomainModel):
    """Why the number on the screen is that number (`D13`).

    Shown, not just stored: an agent who can see *why* confidence is 62% can judge
    it. A bare percentage invites either blind trust or blanket dismissal.
    """

    classifier_prob: float = 0.0
    context_agreement: float = 0.0
    entity_completeness: float = 0.0
    asr_quality: float = 0.0
    calibrated: float = 0.0
    above_floor: bool = False


class RecommendedAction(DomainModel):
    """From a per-intent playbook, not model improvisation (`D16`)."""

    order: int
    text_th: str
    text_en: str | None = None
    requires_assurance: AssuranceLevel = AssuranceLevel.L0_ANONYMOUS


class CaseBrief(DomainModel):
    """What the agent reads. Versioned and immutable (`D7`)."""

    brief_id: str
    call_session_id: str
    version: int = Field(ge=1)
    kind: BriefKind
    built_at: datetime
    intent: IntentPrediction | None = None
    confidence: ConfidenceBreakdown | None = None
    summary_th: str | None = None
    entities: tuple[ExtractedEntity, ...] = ()
    recommended_actions: tuple[RecommendedAction, ...] = ()
    next_best_action: str | None = None
    suggested_opening_th: str | None = None
    urgency: Urgency = Urgency.NORMAL
    snapshot: ContextSnapshot | None = None
    identity: IdentityResolution | None = None
    degraded: DegradationReason = DegradationReason.NONE
    sources: dict[str, Any] = Field(default_factory=dict)
    build_ms: float | None = None

    @property
    def show_confidence_number(self) -> bool:
        """Below the floor we say 'intent unclear' instead of a number (`D13`)."""
        return bool(self.confidence and self.confidence.above_floor)

    @property
    def is_speech_informed(self) -> bool:
        return self.kind in {BriefKind.PARTIAL, BriefKind.FINAL}


class Analysis(DomainModel):
    """One AI call, fully accounted for: model, prompt version, latency, cost (`D18`)."""

    kind: AnalysisKind
    version: int
    output: dict[str, Any]
    model: str
    prompt_version: str | None = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    created_at: datetime
    degraded: bool = False


# --------------------------------------------------------------------------------------
# Agents and matching
# --------------------------------------------------------------------------------------


class AgentSkill(DomainModel):
    skill_code: str
    proficiency: float = Field(ge=0.0, le=1.0)
    certified_until: date | None = None


class AgentLanguage(DomainModel):
    """One language an agent speaks, and how well (`D38`)."""

    language: Language
    level: CefrLevel


class Agent(DomainModel):
    agent_id: str
    display_name: str
    team: str
    level: int = 1
    languages: tuple[AgentLanguage, ...] = (
        AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),
    )
    skills: tuple[AgentSkill, ...] = ()
    max_concurrent: int = 1
    is_active: bool = True

    def proficiency_for(self, skill_code: str) -> float:
        for skill in self.skills:
            if skill.skill_code == skill_code:
                return skill.proficiency
        return 0.0

    def has_skill(self, skill_code: str) -> bool:
        return self.proficiency_for(skill_code) > 0.0

    def level_in(self, language: Language) -> CefrLevel:
        for spoken in self.languages:
            if spoken.language is language:
                return spoken.level
        return CefrLevel.NONE

    def speaks(self, language: Language, *, at_least: CefrLevel = CefrLevel.B1) -> bool:
        """Language is a HARD filter in matching, never a soft score (`D38`).

        Past the wait ceiling the matcher drops fit entirely and connects the caller to
        anyone qualified - but "qualified" still has to include understanding them. A
        long wait is recoverable; a conversation neither party can hold is not.
        """
        return self.level_in(language).at_least(at_least)


class AgentPresence(DomainModel):
    """System state combined with agent intent (`D33`). Availability is derived."""

    agent_id: str
    system_state: AgentSystemState
    agent_intent: AgentIntent
    since: datetime
    current_load: int = 0
    session_id: str | None = None
    heartbeat_at: datetime | None = None

    def is_available(self, agent: Agent, *, within_schedule: bool = True) -> bool:
        return (
            self.system_state is AgentSystemState.AVAILABLE
            and self.agent_intent is AgentIntent.READY
            and self.current_load < agent.max_concurrent
            and agent.is_active
            and within_schedule
        )

    @property
    def accepts_new_callers(self) -> bool:
        """LAST_CALL and DRAINING stay logged in but take nobody new (`D33`).

        Note `D45`'s supporting text says "intent in (ready, last_call)" — that is a slip
        in the prose, not the rule. `LAST_CALL` means *finish the one I am on*, so it must
        not be offered a new caller; amended in `DECISIONS.md` under `D45`.
        """
        return self.agent_intent is AgentIntent.READY

    @property
    def in_after_call_work(self) -> bool:
        return self.system_state is AgentSystemState.AFTER_CALL_WORK


class AgentStateChange(DomainModel):
    """One row of `agent_state_log` — who moved which axis, when, and who moved it.

    Both axes are recorded on every change even though only one moves at a time, because
    the useful question later is "what was true at 14:03", not "what changed at 14:03".
    """

    agent_id: str
    at: datetime
    system_state: AgentSystemState
    agent_intent: AgentIntent
    #: `"agent"` or `"platform"`. The platform writing to the intent axis is the narrow
    #: exception in `D51`, and this field is what makes it auditable rather than sneaky.
    set_by: str
    reason: str
    call_session_id: str | None = None
    #: Filled in only on the change that ends after-call work (`D45`): disconnect → here.
    acw_seconds: float | None = None


class FitBreakdown(DomainModel):
    """Every term that produced a fit score, kept for the rationale panel (`D22`)."""

    skill_match: float = 0.0
    continuity: float = 0.0
    historical_fit: float = 0.0
    load_penalty: float = 0.0
    total: float = 0.0
    hard_filter_failed: str | None = None

    @property
    def eligible(self) -> bool:
        return self.hard_filter_failed is None


class UrgencyBreakdown(DomainModel):
    wait_pressure: float = 0.0
    sla_risk: float = 0.0
    customer_priority: float = 0.0
    situational: float = 0.0
    total: float = 0.0


class MatchCandidate(DomainModel):
    agent_id: str
    fit: FitBreakdown
    score: float = 0.0


class MatchingDecision(DomainModel):
    """Why this agent — answerable months later, to a judge or an auditor (`D22`)."""

    decision_id: str
    call_session_id: str
    at: datetime
    kind: MatchKind
    candidates: tuple[MatchCandidate, ...] = ()
    urgency: UrgencyBreakdown | None = None
    chosen_agent_id: str | None = None
    total_score: float | None = None
    deferred_for_agent_id: str | None = None
    expected_free_in_s: float | None = None
    fit_gap: float | None = None
    rationale_th: str | None = None
    rationale_en: str | None = None
    weights_version: str = "unversioned"
    solver: str = "unknown"
    decide_ms: float | None = None


class Assignment(DomainModel):
    """The offer/accept handshake and after-call work, both measured (`D33`)."""

    assignment_id: str
    call_session_id: str
    agent_id: str
    offered_at: datetime
    accept_mode: str = "manual"
    outcome: OfferOutcome = OfferOutcome.PENDING
    accepted_at: datetime | None = None
    decline_reason: str | None = None
    bridged_at: datetime | None = None
    ended_at: datetime | None = None
    acw_started_at: datetime | None = None
    acw_ended_at: datetime | None = None
    #: The `AgentIntent` the agent declared to end after-call work — `ready`, `lunch`,
    #: `admin`, … Was `done_button | timer`, which `D45` reversed: a timer never ends
    #: ACW, and saving the form is not the same statement as being done with the call.
    acw_ended_by: str | None = None

    @property
    def time_to_accept_ms(self) -> float | None:
        if self.accepted_at is None:
            return None
        return (self.accepted_at - self.offered_at).total_seconds() * 1000.0

    @property
    def acw_seconds(self) -> float | None:
        """After-call work duration — the number the AI wrap-up should shrink."""
        if self.acw_started_at is None or self.acw_ended_at is None:
            return None
        return (self.acw_ended_at - self.acw_started_at).total_seconds()


class CallWrapup(DomainModel):
    """What the agent wrote when the call was over — **never written by anything else**.

    Saving this closes the *call record*; it does not end after-call work, which runs
    until the person declares a next state (`D45`). The two are different statements and
    either may happen without the other.

    Was a bare dict on the API container until it needed to survive a restart. A dict was
    fine while nothing read it but the screen that had just written it; a row in someone's
    file deserves a shape, and this is the model the disclosure and quality records are
    both derived from.
    """

    call_session_id: str
    agent_id: str
    saved_at: datetime
    disposition: str
    notes: str | None = None
    follow_up_required: bool = False
    #: Did the agent change the AI's draft? The honest input to *"did the draft help"* —
    #: the evaluation signal behind the ACW claim (`D27`).
    was_edited: bool = True


class QueueEntry(DomainModel):
    queue_id: str
    call_session_id: str
    enqueued_at: datetime
    priority: int = 0
    waiting_credit_s: float = 0.0  # survives a re-match; never reset by a fit change

    def wait_seconds(self, now: datetime) -> float:
        return (now - self.enqueued_at).total_seconds() + self.waiting_credit_s


class Rating(DomainModel):
    """A score somebody gave a call.

    Deliberately *not* a call state (`D46`). Ratings arrive on their own schedule: the
    customer rates in the IVR within seconds of hanging up, the agent rates during or
    after wrap-up, and either may never rate at all. So this attaches to a call record by
    `call_session_id` whenever it turns up - including after the call has closed.

    Both sides rate, which is how we learn whether the brief was any good (`D27`).
    """

    rating_id: str
    call_session_id: str
    source: RatingSource
    received_at: datetime
    csat: int | None = Field(default=None, ge=1, le=5)
    nps: int | None = Field(default=None, ge=0, le=10)
    comment: str | None = None


class StateTransition(DomainModel):
    from_state: CallState | None
    to_state: CallState
    at: datetime
    reason: str


class CallSession(DomainModel):
    """The spine. Everything about one call hangs off `call_session_id`.

    `intent_id` is optional on purpose: a cold call from a windscreen sticker has no
    app intent, and that is the base case, not an edge case (`D19`).
    """

    model_config = ConfigDict(frozen=False, extra="forbid")  # the orchestrator mutates it

    call_session_id: str
    entry_channel: EntryChannel
    state: CallState
    created_at: datetime
    trace_id: str | None = None

    intent_id: str | None = None
    customer_id: str | None = None
    identity: IdentityResolution | None = None
    caller_number: str | None = None
    dialled_did: str | None = None
    product_line: ProductLine = ProductLine.UNKNOWN
    product_code: str | None = None

    telephony_call_id: str | None = None
    provider: str | None = None

    # Language (D38). `preferred` is what we speak to them; `acceptable` is the hard
    # filter for matching. They differ because a keypress tells us what someone PREFERS,
    # not what they can understand - a bilingual caller who picks English is still
    # perfectly routable to a Thai speaker.
    preferred_language: Language = Language.TH
    acceptable_languages: tuple[Language, ...] = (Language.TH,)

    # What the caller actually pressed, in order, e.g. ("1", "2") -> motor, roadside.
    # Kept because it is the most reliable intent evidence we have (D37) and because a
    # confused menu path is a UX signal worth seeing.
    menu_path: tuple[str, ...] = ()
    menu_intent_code: str | None = None

    queue_id: str | None = None
    priority: int = 0
    queued_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None

    snapshot_id: str | None = None
    intake_id: str | None = None
    brief_version: int = 0
    assigned_agent_id: str | None = None

    consents: tuple[Consent, ...] = ()
    transitions: tuple[StateTransition, ...] = ()
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _app_channel_implies_intent(self) -> Self:
        if self.entry_channel is EntryChannel.IN_APP and self.intent_id is None:
            raise ValueError("an IN_APP call must carry an intent_id")
        return self

    # --- consent (D14) ---

    def has_consent(self, scope: ConsentScope) -> bool:
        return any(c.scope is scope and c.granted for c in self.consents)

    @property
    def may_run_intake(self) -> bool:
        """No consent, no intake — and the call proceeds anyway (`D14`)."""
        return self.has_consent(ConsentScope.RECORDING) and self.has_consent(
            ConsentScope.AI_PROCESSING
        )

    # --- derived ---

    @property
    def is_terminal(self) -> bool:
        from readycall.domain.enums import TERMINAL_STATES

        return self.state in TERMINAL_STATES

    def wait_seconds(self, now: datetime) -> float:
        if self.queued_at is None:
            return 0.0
        end = self.answered_at or now
        return (end - self.queued_at).total_seconds()

    def record_timing(self, stage: str, ms: float) -> None:
        self.stage_timings_ms[stage] = ms
