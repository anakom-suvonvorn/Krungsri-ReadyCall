"""The agent's identity control: three outcomes, and only a person may use it (`D42`).

`D20` treated assurance as a one-shot decision taken when the call arrived. But identity
is exactly the thing that gets resolved *during* the conversation — the agent asks, the
caller answers. Without a way up, an ANI-matched caller stayed at `L1_PROBABLE` for the
whole call and the agent could never unlock the details they had just verbally verified.

Three outcomes, not two:

* **Confirmed** — verified by challenge. The agent records *which* challenge. → `L3`.
* **Not this person** — the ANI guess was wrong. → `L0`, and the rejected `customer_id`
  is suppressed for the rest of the call so nothing re-proposes them.
* **Third party with authority to act** — a daughter calling about her father's claim,
  whose authority the agent has checked. → `L3`, since `D65`.

The third button is the whole point, and what makes it work is the **outcome**, not the
level. Forced into a binary, that daughter gets recorded as a verified *policyholder*, and
the disclosure log — the only reason to keep one under PDPA — becomes a record of something
that did not happen. Recording her as `third_party`, named and related, keeps the log true
while still letting the agent do their job.

`D65` corrected the first implementation, which held third parties at `L1` and so withheld
the very context the agent needed to help a caller they had just verified. There was also
no second control to complete an authority check with, so the level was stuck for good.

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
#: are a list rather than a hardcoded set for exactly that reason.
NAMED_CHALLENGES: frozenset[str] = frozenset(
    {"date_of_birth", "citizen_id_last4", "policy_number", "recent_claim_amount"}
)

#: The escape hatch, and the more important half (`D44`, `D57`). Real verification does not
#: fit a closed list: the caller was recognised by voice from last week's call, produced a
#: claim reference from an SMS, was verified at a branch and transferred in, answered a
#: question about a recent transaction. A four-item dropdown forces every one of those into
#: the nearest lie. With `other`, the agent types what they actually did — and `D44` argued
#: this should have been the *first* mode built, not the last.
OTHER_CHALLENGE = "other"
CHALLENGES: frozenset[str] = NAMED_CHALLENGES | {OTHER_CHALLENGE}


@dataclass(frozen=True, slots=True)
class Attestation:
    """One row of the disclosure log. What the *agent* said, and on what basis."""

    call_session_id: str
    agent_id: str
    outcome: AttestationOutcome
    at: datetime
    #: Only for CONFIRMED. Which challenge was asked — never the answer itself.
    challenge: str | None = None
    #: Required when `challenge == "other"`: what the agent actually did, in their words.
    #: An `other` with no explanation is an unfalsifiable audit row, which is the thing
    #: `D42` exists to prevent, so the service refuses it.
    challenge_note: str | None = None
    #: Only for NOT_THIS_PERSON: who we had wrongly proposed.
    rejected_customer_id: str | None = None
    #: Only for THIRD_PARTY. **Both are required** (`D57`): the log has to say who was on
    #: the phone, not merely that somebody-not-the-policyholder was.
    caller_name: str | None = None
    #: Free text, e.g. "ลูกสาว". Not a closed list on purpose — the relationships that
    #: turn up are not enumerable in advance.
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
        challenge_note: str | None = None,
        caller_name: str | None = None,
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
            if challenge not in CHALLENGES:
                raise PermanentError(f"unknown challenge {challenge!r}")
            if challenge == OTHER_CHALLENGE and not (challenge_note or "").strip():
                raise PermanentError(
                    "challenge 'other' requires a note saying how identity was established"
                )
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
                        # liability we have no reason to take on. The `other` note is the
                        # agent's own description of what they did, so it is kept as-is.
                        "challenge": challenge,
                        "challenge_note": challenge_note,
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
            if not (caller_name or "").strip():
                raise PermanentError("a third party must be named — who is on the phone?")
            if not (relationship or "").strip():
                raise PermanentError("a third party must state their relationship to the customer")
            resolution = current.model_copy(
                update={
                    # PROMOTED, since `D65`. The button says the agent has checked that
                    # this person is authorised to act for the policyholder, so the level
                    # follows the attestation — the same rule as CONFIRMED. What keeps the
                    # log honest is not a lower number but the OUTCOME, which stays
                    # `third_party` forever: the record says "an authorised representative
                    # was verified", never "the policyholder was verified".
                    "assurance": AssuranceLevel.L3_VERIFIED,
                    "method": IdentityMethod.MANUAL,
                    "resolved_at": now,
                    "evidence": {
                        **current.evidence,
                        "third_party_declared_by": agent_id,
                        "third_party_name": caller_name,
                        "relationship": relationship,
                        # Kept, and it is now a *record* rather than a pending task: the
                        # agent asserts they made this check by pressing the button. The
                        # workstation still shows the reminder, because acting for someone
                        # else is worth flagging for the whole call.
                        "authority_check_required": True,
                        "authority_attested_by": agent_id,
                    },
                }
            )

        record = Attestation(
            call_session_id=call_session_id,
            agent_id=agent_id,
            outcome=outcome,
            at=now,
            challenge=challenge,
            challenge_note=challenge_note,
            rejected_customer_id=(
                current.customer_id if outcome is AttestationOutcome.NOT_THIS_PERSON else None
            ),
            caller_name=caller_name,
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


__all__ = [
    "CHALLENGES",
    "NAMED_CHALLENGES",
    "OTHER_CHALLENGE",
    "Attestation",
    "AttestationOutcome",
    "AttestationService",
]
