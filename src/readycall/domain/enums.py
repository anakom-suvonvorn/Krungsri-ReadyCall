"""Domain enumerations. No I/O, no dependencies beyond the standard library.

`StrEnum` throughout so values serialise to readable strings in JSON, events and DB
columns — a transition log full of `CallState.QUEUED` reads better than `3`.
"""

from __future__ import annotations

from enum import StrEnum


class CallState(StrEnum):
    """The call lifecycle (`ARCHITECTURE.md` §4). Single writer: the orchestrator."""

    INTENT_CREATED = "intent_created"  # app path only; a cold call starts at CONNECTING
    CONNECTING = "connecting"
    IVR = "ivr"  # identify -> product menu -> consent
    QUEUED = "queued"
    INTAKE_ACTIVE = "intake_active"
    INTAKE_COMPLETE = "intake_complete"
    MATCHED = "matched"
    OFFERED = "offered"  # offer card + ringtone in the agent's BROWSER (D32)
    IN_CALL = "in_call"
    WRAP_UP = "wrap_up"
    # NOTE: there is deliberately no RATING state (`D46`). A rating is a fact received
    # about a call, not a phase the call passes through - the customer rates in the IVR
    # seconds after hanging up, while the agent may still be writing the wrap-up.
    CLOSED = "closed"
    # terminal / exceptional
    ABANDONED = "abandoned"  # caller hung up while waiting
    VOICEMAIL = "voicemail"  # after hours -> briefed callback task (D25)
    TRANSFERRED = "transferred"
    FAILED = "failed"


TERMINAL_STATES = frozenset(
    {
        CallState.CLOSED,
        CallState.ABANDONED,
        CallState.VOICEMAIL,
        CallState.TRANSFERRED,
        CallState.FAILED,
    }
)


class EntryChannel(StrEnum):
    """How the call arrived (`D19`). A plain phone call is the base case."""

    IN_APP = "in_app"  # tap Contact; carries a correlation token
    PRODUCT_DID = "product_did"  # printed number per product line = free intent signal
    HOTLINE = "hotline"  # the general number
    CALLBACK = "callback"  # we placed the call
    TRANSFER = "transfer"  # another agent handed it over


class AssuranceLevel(StrEnum):
    """How sure we are who this is (`D20`). Gates what the workstation may display."""

    L0_ANONYMOUS = "l0_anonymous"
    L1_PROBABLE = "l1_probable"  # ANI match; NOT verified
    L2_STRONG = "l2_strong"  # ANI + a pending app intent
    L3_VERIFIED = "l3_verified"  # app token, or IVR verification

    @property
    def rank(self) -> int:
        return {"l0_anonymous": 0, "l1_probable": 1, "l2_strong": 2, "l3_verified": 3}[self.value]

    def at_least(self, other: AssuranceLevel) -> bool:
        return self.rank >= other.rank


class IdentityMethod(StrEnum):
    APP_TOKEN = "app_token"
    ANI = "ani"
    PENDING_INTENT = "pending_intent"
    IVR_VERIFY = "ivr_verify"
    MANUAL = "manual"
    NONE = "none"


class ConsentScope(StrEnum):
    """Separate scopes, separately granted. Health data is its own scope (`D14`)."""

    RECORDING = "recording"
    AI_PROCESSING = "ai_processing"
    HEALTH_DATA = "health_data"
    CROSS_ORG = "cross_org"


class IntakeStrategyKind(StrEnum):
    """Which pre-call experience ran (`D10`). Downstream must not care."""

    PASSIVE = "passive"
    GUIDED = "guided"
    CONVERSATIONAL = "conversational"


class FinalizeReason(StrEnum):
    """Why intake stopped."""

    CUSTOMER_DONE = "customer_done"  # pressed 1 again, or said so
    SILENCE_TIMEOUT = "silence_timeout"
    MAX_DURATION = "max_duration"
    OFFER_ACCEPTED = "offer_accepted"  # agent answered -> finalise as partial (D21)
    DECLINED = "declined"  # caller pressed 2; never offered
    NO_CONSENT = "no_consent"
    ERROR = "error"
    CALL_ENDED = "call_ended"


class SpeakerRole(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    AI = "ai"  # future conversational intake
    UNKNOWN = "unknown"


class Language(StrEnum):
    """Spoken languages the service supports.

    Thai-only is implemented today; English is modelled so nothing has to be retrofitted
    when it lands (`D38`).
    """

    TH = "th"
    EN = "en"


class CefrLevel(StrEnum):
    """How well an agent speaks a language, on the CEFR scale.

    A plain "speaks English: yes/no" flag is not enough to route on. An agent with A1
    English cannot handle a complex claim conversation in English, and pretending
    otherwise produces a worse call than a longer wait. So proficiency is graded and the
    required level is per-intent (`D38`).
    """

    NONE = "none"
    A1 = "a1"
    A2 = "a2"
    B1 = "b1"
    B2 = "b2"
    C1 = "c1"
    C2 = "c2"
    NATIVE = "native"

    @property
    def rank(self) -> int:
        return {
            "none": 0,
            "a1": 1,
            "a2": 2,
            "b1": 3,
            "b2": 4,
            "c1": 5,
            "c2": 6,
            "native": 7,
        }[self.value]

    def at_least(self, other: CefrLevel) -> bool:
        return self.rank >= other.rank


class ProductLine(StrEnum):
    """Insurance means every line, not just health."""

    MOTOR = "motor"
    HEALTH = "health"
    LIFE = "life"
    TRAVEL = "travel"
    PA = "pa"  # personal accident
    SAVINGS = "savings"
    UNKNOWN = "unknown"


class PolicyStatus(StrEnum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    PENDING = "pending"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"  # at an accident scene, medical emergency

    @property
    def weight(self) -> float:
        return {"low": 0.0, "normal": 0.25, "high": 0.6, "critical": 1.0}[self.value]


class CustomerSegment(StrEnum):
    """The brief's three personas."""

    SALARIED = "salaried"
    SME_OWNER = "sme_owner"
    FREELANCER = "freelancer"
    OTHER = "other"


class AgentSystemState(StrEnum):
    """Set by the platform, not the person (`D33`)."""

    OFFLINE = "offline"
    AVAILABLE = "available"
    OFFERING = "offering"
    ON_CALL = "on_call"
    AFTER_CALL_WORK = "after_call_work"


class AgentIntent(StrEnum):
    """Set by the person (`D33`)."""

    READY = "ready"
    BREAK = "break"
    LUNCH = "lunch"
    TRAINING = "training"
    ADMIN = "admin"
    LAST_CALL = "last_call"  # finish the current call, then stop taking new ones
    DRAINING = "draining"  # no new callers, but honour what is already committed


class OfferOutcome(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TIMEOUT = "timeout"  # RONA: re-match, and flip the agent out of READY
    CANCELLED = "cancelled"  # caller hung up while the offer was open


class MatchKind(StrEnum):
    """What the matcher decided, including what it decided *not* to do (`D22`)."""

    ASSIGN = "assign"
    DEFER = "defer"
    DEFER_REJECTED = "defer_rejected"
    FALLBACK = "fallback"  # past the wait ceiling: any qualified agent
    # The two ways a call goes unplaced are OPPOSITE messages to a supervisor, so they are
    # two kinds rather than one (`D50`). Nobody qualified is a ROSTER problem - waiting
    # cannot help, someone with the skill has to come online. All qualified busy is a
    # CAPACITY problem - this caller is next as soon as one of them frees up.
    NO_QUALIFIED_AGENT = "no_qualified_agent"
    ALL_QUALIFIED_BUSY = "all_qualified_busy"


class BriefKind(StrEnum):
    CONTEXT_ONLY = "context_only"  # no consent / declined / STT unavailable
    PARTIAL = "partial"  # speech-informed, intake still running or cut short
    FINAL = "final"


class AnalysisKind(StrEnum):
    INTENT = "intent"
    ENTITIES = "entities"
    SENTIMENT = "sentiment"
    SUMMARY = "summary"
    NEXT_BEST_ACTION = "next_best_action"
    SUGGESTED_OPENING = "suggested_opening"
    PII = "pii"
    WRAPUP = "wrapup"
    CALL_PROGRESS = "call_progress"


class RatingSource(StrEnum):
    CUSTOMER_IVR = "customer_ivr"
    CUSTOMER_APP = "customer_app"
    AGENT = "agent"


class DegradationReason(StrEnum):
    """Why a brief is lesser than it could have been (`ARCHITECTURE.md` §16).

    Shown on the workstation: an agent seeing a thin brief deserves to know why.
    """

    NONE = "none"
    NO_CONSENT = "no_consent"
    INTAKE_DECLINED = "intake_declined"
    STT_UNAVAILABLE = "stt_unavailable"
    LLM_UNAVAILABLE = "llm_unavailable"
    CORE_DATA_UNAVAILABLE = "core_data_unavailable"
    CORE_DATA_STALE = "core_data_stale"
    LOW_ASSURANCE = "low_assurance"
    MEDIA_FORK_FAILED = "media_fork_failed"
    TIMED_OUT = "timed_out"
