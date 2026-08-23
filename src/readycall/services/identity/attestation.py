"""The agent's identity control: three outcomes, and only a person may use it (`D42`).

`D20` treated assurance as a one-shot decision taken when the call arrived. But identity
is exactly the thing that gets resolved *during* the conversation — the agent asks, the
caller answers. Without a way up, an ANI-matched caller stayed at `L1_PROBABLE` for the
whole call and the agent could never unlock the details they had just verbally verified.

Three outcomes, not two:

* **Confirmed** — verified by challenge. The agent records *which* challenge. → `L3`.
* **Not this person** — the ANI guess was wrong. → `L0`, and the rejected `customer_id`
  is suppressed for the rest of the call so nothing re-proposes them.
* **Third party acting for them** — a daughter calling about her father's claim. The case
  context stays; **disclosure stays locked**; a playbook step appears to check authority.

The third button is the whole point. Forced into a binary, that daughter gets recorded as
a verified policyholder, and the disclosure log — the only reason to keep one under PDPA —
becomes a record of something that did not happen.

**Nothing here is automatic.** A keypad lookup may say "these digits match policy
MT-2025-004512"; that is evidence, and evidence is not an attestation (`D44`). Only the
agent attests, and the record keeps the two facts separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from readycall.clock import Clock
from readycall.domain.enums import AssuranceLevel, IdentityMethod
from readycall.domain.models import IdentityResolution
from readycall.errors import PermanentError
from readycall.logging import get_logger

log = get_logger(__name__)


class AttestationOutcome(StrEnum):
    CONFIRMED = "confirmed"
    NOT_THIS_PERSON = "not_this_person"
    THIRD_PARTY = "third_party"


#: Challenges that count for promotion to L3. `Q12` — confirm these with Krungsri; they
#: are a config list rather than a hardcoded set for exactly that reason.
KNOWN_CHALLENGES: frozenset[str] = frozenset(
    {"date_of_birth", "citizen_id_last4", "policy_number", "recent_claim_amount"}
)


@dataclass(frozen=True, slots=True)
class Attestation:
    """One row of the disclosure log. What the *agent* said, and on what basis."""

    call_session_id: str
    agent_id: str
    outcome: AttestationOutcome
    at: datetime
    #: Only for CONFIRMED. Which challenge was asked — never the answer itself.
    challenge: str | None = None
    #: Only for NOT_THIS_PERSON: who we had wrongly proposed.
    rejected_customer_id: str | None = None
    #: Only for THIRD_PARTY: free text, e.g. "daughter". Not a closed list on purpose —
    #: the relationships that turn up are not enumerable in advance.
    relationship: str | None = None
    note: str | None = None


@dataclass
class _CallIdentityState:
    attestations: list[Attestation] = field(default_factory=list)
    rejected_customer_ids: set[str] = field(default_factory=set)


class AttestationService:
    """Applies an agent's attestation to a call's identity, and records why."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._by_call: dict[str, _CallIdentityState] = {}

    def history(self, call_session_id: str) -> tuple[Attestation, ...]:
        state = self._by_call.get(call_session_id)
        return tuple(state.attestations) if state else ()

    def rejected_customer_ids(self, call_session_id: str) -> frozenset[str]:
        """Never re-propose someone the agent has explicitly rejected on this call."""
        state = self._by_call.get(call_session_id)
        return frozenset(state.rejected_customer_ids) if state else frozenset()

    def attest(
        self,
        *,
        call_session_id: str,
        agent_id: str,
        current: IdentityResolution,
        outcome: AttestationOutcome,
        challenge: str | None = None,
        relationship: str | None = None,
        note: str | None = None,
    ) -> tuple[IdentityResolution, Attestation]:
        """Return the new resolution **and** the log row. Both, always, together."""
        state = self._by_call.setdefault(call_session_id, _CallIdentityState())
        now = self._clock.now()

        if outcome is AttestationOutcome.CONFIRMED:
            if current.customer_id is None:
                raise PermanentError(
                    "cannot confirm an identity we never proposed; "
                    "resolve or search for a customer first"
                )
            if not challenge:
                # The challenge is the evidence. "Confirmed" with no basis recorded is
                # exactly the unfalsifiable audit row `D42` exists to prevent.
                raise PermanentError("confirming an identity requires naming the challenge used")
            if challenge not in KNOWN_CHALLENGES:
                raise PermanentError(f"unknown challenge {challenge!r}")
            resolution = current.model_copy(
                update={
                    "assurance": AssuranceLevel.L3_VERIFIED,
                    "method": IdentityMethod.MANUAL,
                    "resolved_at": now,
                    "evidence": {
                        **current.evidence,
                        "agent_confirmed_by": agent_id,
                        # The challenge NAME, never the answer. Knowing we asked for a
                        # date of birth is auditable; storing the date of birth is a
                        # liability we have no reason to take on.
                        "challenge": challenge,
                    },
                }
            )

        elif outcome is AttestationOutcome.NOT_THIS_PERSON:
            if current.customer_id is not None:
                state.rejected_customer_ids.add(current.customer_id)
            resolution = IdentityResolution(
                method=IdentityMethod.NONE,
                customer_id=None,
                assurance=AssuranceLevel.L0_ANONYMOUS,
                resolved_at=now,
                evidence={
                    **current.evidence,
                    "rejected_by": agent_id,
                    "rejected_customer_id": current.customer_id,
                    # Rejection is information, not the absence of it: a wrong ANI match
                    # usually means a recycled mobile sitting stale in the core data, and
                    # that is a data-quality signal worth keeping (`D42`).
                    "rejection_is_a_data_signal": True,
                },
            )

        else:  # THIRD_PARTY
            if current.customer_id is None:
                raise PermanentError("a third party must be acting for *someone*")
            resolution = current.model_copy(
                update={
                    # Deliberately NOT promoted. The case context attaches so the agent
                    # can see which policy this is about, and disclosure stays locked.
                    "assurance": min(
                        current.assurance,
                        AssuranceLevel.L1_PROBABLE,
                        key=lambda level: level.rank,
                    ),
                    "method": IdentityMethod.MANUAL,
                    "resolved_at": now,
                    "evidence": {
                        **current.evidence,
                        "third_party_declared_by": agent_id,
                        "relationship": relationship or "unspecified",
                        "authority_check_required": True,
                    },
                }
            )

        record = Attestation(
            call_session_id=call_session_id,
            agent_id=agent_id,
            outcome=outcome,
            at=now,
            challenge=challenge,
            rejected_customer_id=(
                current.customer_id if outcome is AttestationOutcome.NOT_THIS_PERSON else None
            ),
            relationship=relationship,
            note=note,
        )
        state.attestations.append(record)
        log.info(
            "identity attested",
            call_session_id=call_session_id,
            agent_id=agent_id,
            outcome=str(outcome),
            assurance=str(resolution.assurance),
            challenge=challenge,
        )
        return resolution, record


__all__ = ["KNOWN_CHALLENGES", "Attestation", "AttestationOutcome", "AttestationService"]
