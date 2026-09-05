"""Fit, urgency, and the hard filters — the three stages that produce one number.

The order matters more than the arithmetic:

1. **Hard filters exclude.** Skill and language are pass/fail. A failed filter is *not* a
   low score — it is not a candidate. Scoring them softly is exactly how a caller ends up
   with an agent who cannot help them, purely because everything else scored well.
2. **Fit** answers *how good is this agent for this call*.
3. **Urgency** answers a different question — *how badly does this call need someone* — and
   **multiplies** rather than adds.

That last choice is the whole answer to starvation (`D22`). Under pure best-fit, a caller
nobody is a great match for waits while better-matched callers overtake them, forever.
Multiplying means waiting eventually wins on its own merit, and a hard wait ceiling then
drops to any-qualified-agent.

Every term is kept, not just the total (`FitBreakdown`, `UrgencyBreakdown`), because
"why did I get this call?" has to be answerable (`D18`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from readycall.domain.enums import AgentSystemState, CefrLevel, Language, Urgency
from readycall.domain.models import (
    Agent,
    AgentPresence,
    FitBreakdown,
    UrgencyBreakdown,
)
from readycall.services.matching.weights import MatchingWeights


@dataclass(frozen=True, slots=True)
class WaitingCall:
    """What the matcher needs to know about a caller. Deliberately not `CallSession`.

    The engine should not be able to reach into a live session and mutate it, and keeping
    this small makes the matching simulator trivial to drive with synthetic load.
    """

    call_session_id: str
    queue_id: str
    required_skill: str
    intent_code: str
    intent_urgency: Urgency
    waiting_s: float
    sla_seconds: int
    acceptable_languages: tuple[Language, ...] = (Language.TH,)
    customer_id: str | None = None
    last_agent_id: str | None = None
    last_contact_at: datetime | None = None
    last_outcome_good: bool = True
    is_vulnerable: bool = False
    #: Survives a re-match; never reset by a fit change (`QueueEntry.waiting_credit_s`).
    waiting_credit_s: float = 0.0
    #: The instant this caller's wait is measured **from**, credit included — so a screen
    #: can tick it once a second instead of waiting for the next snapshot (`B27`).
    #: `total_wait_s` is the same quantity as a number at snapshot time; this is the
    #: anchor. Both are set together in `DispatchService._live()`, deliberately, so the
    #: number and the thing a client counts from cannot drift apart.
    waiting_since: datetime | None = None
    #: Agents who already declined or missed this call (`D33`). A hard filter, not a
    #: penalty: without it the global solver re-picks the same best agent on the very
    #: next tick and the caller watches one desk not answer, indefinitely.
    excluded_agent_ids: tuple[str, ...] = ()

    @property
    def total_wait_s(self) -> float:
        return self.waiting_s + self.waiting_credit_s


#: Hard-filter names that mean "this person could take this call, but not right now"
#: (`D108`). Distinct from the capability filters (`skill`, `language`) and from
#: `already_offered`, which is about this caller rather than about the agent.
AVAILABILITY_FILTERS = frozenset({"offline", "not_ready", "busy", "at_capacity"})


def hard_filter(
    call: WaitingCall,
    agent: Agent,
    presence: AgentPresence,
    weights: MatchingWeights,
) -> str | None:
    """Return the name of the first filter that fails, or `None` if the agent qualifies."""
    if agent.agent_id in call.excluded_agent_ids:
        # Checked first: "they already turned this call down" is a better explanation than
        # any of the others, and it is the one a supervisor asks about.
        return "already_offered"

    if weights.require_skill and agent.proficiency_for(call.required_skill) <= 0.0:
        return "skill"

    if weights.require_language:
        floor = CefrLevel(weights.min_language_level)
        if not any(agent.speaks(lang, at_least=floor) for lang in call.acceptable_languages):
            # `D38`: a graded check, not a yes/no flag. An agent with A1 English cannot
            # handle a complex claim in English, and pretending otherwise produces a worse
            # call than a longer wait.
            return "language"

    # --- can this person take a call RIGHT NOW (`D108`, `B25`) ------------------------
    #
    # None of this was checked until 2026-09-05, and `AgentPresence.is_available()` - the
    # method that says exactly this - was called by nothing. The matcher would hand a
    # caller to an agent who had never pressed "ready", who was on lunch, or who had
    # signed out and closed the tab. A signed-out agent then held the offer for its full
    # RONA timeout, so on a small floor every caller waited 20 seconds per ghost before
    # reaching anybody real.
    #
    # Three separate reasons rather than one, for `D50`'s reason: "they are offline",
    # "they have not asked for calls" and "they are mid-something" are different
    # conversations for whoever is looking at the queue.
    if presence.system_state is AgentSystemState.OFFLINE:
        return "offline"

    if not presence.accepts_new_callers:
        # NOT_READY, BREAK, LUNCH, TRAINING, ADMIN - and also LAST_CALL and DRAINING,
        # which mean "finish what I have, give me nothing new" (`D33`, `D59`).
        return "not_ready"

    if presence.system_state is not AgentSystemState.AVAILABLE:
        # ON_CALL, AFTER_CALL_WORK, or OFFERING - already holding an offer. That last one
        # matters more than it looks: without it one desk collects every waiting caller
        # in a single tick and none of them reaches anybody else until each times out.
        return "busy"

    if weights.respect_max_concurrent and presence.current_load >= agent.max_concurrent:
        return "at_capacity"

    if not agent.is_active:
        return "inactive"

    return None


def score_fit(
    call: WaitingCall,
    agent: Agent,
    presence: AgentPresence,
    weights: MatchingWeights,
    *,
    now: datetime,
    historical: float = 0.5,
) -> FitBreakdown:
    """How good is this agent *for this call*. Every term kept for the rationale panel."""
    skill = agent.proficiency_for(call.required_skill)

    continuity = 0.0
    if call.last_agent_id == agent.agent_id and call.last_agent_id is not None:
        fresh = True
        if call.last_contact_at is not None:
            age = now - call.last_contact_at
            fresh = age <= timedelta(days=weights.continuity_max_age_days)
        good = call.last_outcome_good or not weights.continuity_requires_good_outcome
        # Continuity is valuable but must not become a trap: if the last call went badly,
        # or was months ago, sending them back to the same agent is a rut, not continuity.
        continuity = 1.0 if (fresh and good) else 0.0

    load = presence.current_load / max(agent.max_concurrent, 1)

    total = (
        weights.fit_skill_match * skill
        + weights.fit_continuity * continuity
        + weights.fit_historical * historical
        - weights.fit_load_penalty * load
    )
    return FitBreakdown(
        skill_match=skill,
        continuity=continuity,
        historical_fit=historical,
        load_penalty=load,
        total=max(total, 0.0),
    )


def score_urgency(call: WaitingCall, weights: MatchingWeights) -> UrgencyBreakdown:
    """How badly does *this call* need someone, as a multiplier on fit."""
    # Wait pressure is relative to the queue's own SLA: 40 s is nothing on a 120 s policy
    # question and nearly a breach on a 45 s pre-authorisation.
    wait_pressure = min(call.total_wait_s / max(call.sla_seconds, 1), 2.0)
    sla_risk = 1.0 if call.total_wait_s >= call.sla_seconds else 0.0
    priority = 1.0 if call.is_vulnerable else 0.0
    situational = call.intent_urgency.weight

    raw = (
        weights.urgency_wait_pressure * wait_pressure
        + weights.urgency_sla_risk * sla_risk
        + weights.urgency_customer_priority * priority
        + weights.urgency_situational * situational
    )
    # Clamped so urgency can dominate but never make skill irrelevant: a caller at a crash
    # scene still must not be routed to someone who cannot handle a motor claim.
    total = max(weights.urgency_min, min(weights.urgency_min + raw, weights.urgency_max))
    return UrgencyBreakdown(
        wait_pressure=wait_pressure,
        sla_risk=sla_risk,
        customer_priority=priority,
        situational=situational,
        total=total,
    )


def rationale_th(call: WaitingCall, agent: Agent, fit: FitBreakdown, urg: UrgencyBreakdown) -> str:
    """One Thai sentence an agent (or a supervisor) can actually read.

    A matching decision nobody can explain is one nobody will trust enough to leave
    switched on, so the rationale is built here rather than reconstructed later (`D18`).
    """
    parts = [f"ทักษะ {call.required_skill} {fit.skill_match:.0%}"]
    if fit.continuity > 0:
        parts.append("เคยดูแลลูกค้ารายนี้")
    if urg.sla_risk > 0:
        parts.append(f"รอเกิน SLA ({call.total_wait_s:.0f}s / {call.sla_seconds}s)")
    if urg.situational >= Urgency.HIGH.weight:
        parts.append("เรื่องเร่งด่วน")
    if fit.load_penalty > 0:
        parts.append(f"โหลดปัจจุบัน {fit.load_penalty:.0%}")
    return " · ".join(parts)


__all__ = ["WaitingCall", "hard_filter", "rationale_th", "score_fit", "score_urgency"]
