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

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

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


#: The `other` code is special-cased in one place — the note requirement — so it is named
#: rather than string-literalled at the check. Everything else about the challenge list now
#: comes from `config/challenges.yaml` (`D72`), which is the single source of truth the
#: workstation also renders from.
OTHER_CHALLENGE = "other"

#: Fallback for callers that construct the service without a domain pack (tests of the
#: service in isolation). Production always passes the config-loaded list, so this is a
#: default, never a second copy to keep in step.
DEFAULT_CHALLENGES: frozenset[str] = frozenset(
    {"date_of_birth", "citizen_id_last4", "policy_number", "recent_claim_amount", OTHER_CHALLENGE}
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


class AttestationStore(Protocol):
    """The durable disclosure log (`D78`).

    **Append-only, and that is the entire design.** `D60` made an attestation a signed
    statement rather than a toggle; `D61` made correcting one an *append*. A call whose
    agent confirmed and then realised they were speaking to the policyholder's daughter
    keeps both rows, and the record shows the correction happening — which is more truthful
    than either version alone. There is deliberately no `update` and no `delete`.
    """

    async def append(self, attestation: Attestation) -> None: ...

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Attestation]: ...


class InMemoryAttestationStore:
    """The fake, held to the same contract suite as the real one (`D3`)."""

    def __init__(self) -> None:
        self._rows: list[Attestation] = []

    async def append(self, attestation: Attestation) -> None:
        self._rows.append(attestation)

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Attestation]:
        wanted = set(call_session_ids)
        return [a for a in self._rows if a.call_session_id in wanted]


@dataclass
class _CallIdentityState:
    attestations: list[Attestation] = field(default_factory=list)
    rejected_customer_ids: set[str] = field(default_factory=set)
    #: What the resolver proposed before any agent touched it. Kept so a **rejection can be
    #: taken back** (`D71`): *ไม่ใช่บุคคลนี้* clears the customer, and without this there is
    #: nothing left to restore, so a mis-click would strand the call at L0 for good. A
    #: mis-press is not rare, and neither is "I thought this was a stranger, then they
    #: explained they are the daughter".
    original: IdentityResolution | None = None


class AttestationService:
    """Applies an agent's attestation to a call's identity, and records why."""

    def __init__(
        self,
        *,
        clock: Clock,
        challenges: frozenset[str] | None = None,
        store: AttestationStore | None = None,
    ) -> None:
        self._clock = clock
        self._store = store
        self._by_call: dict[str, _CallIdentityState] = {}
        #: From `config/challenges.yaml` (`D72`). The service refuses anything not in it,
        #: which is precisely why the workstation must render the same list rather than
        #: keep its own — an option the screen offers and the server refuses is a dead end
        #: the agent cannot diagnose.
        self._challenges = challenges if challenges is not None else DEFAULT_CHALLENGES

    async def restore(
        self,
        call_session_ids: Sequence[str],
        *,
        resolutions: dict[str, IdentityResolution] | None = None,
    ) -> int:
        """Reload the disclosure log for live calls. Returns the number of rows.

        Three things have to come back, and only the first is obvious:

        1. **The history**, because `attestation_count` is what re-locks the control after
           every amendment (`D61`). Restoring an empty history would unlock the identity
           panel on a call that had already been attested, and a lock that opens by itself
           is worse than no lock — the screen would say the question was settled while
           letting anyone re-answer it.
        2. **The rejected customer ids**, so nothing re-proposes a match the agent has
           already thrown out (`D42`).
        3. **The original resolution**, without which a rejection stops being reversible
           (`D71`). It is taken from the live session's identity when that still names a
           customer, and otherwise from the rejected id on the log row — the same match the
           system originally made, offered again. Nothing is invented either way.
        """
        if self._store is None:
            return 0
        rows = await self._store.for_calls(call_session_ids)
        for record in sorted(rows, key=lambda r: r.at):
            state = self._by_call.setdefault(record.call_session_id, _CallIdentityState())
            state.attestations.append(record)
            if record.rejected_customer_id:
                state.rejected_customer_ids.add(record.rejected_customer_id)
        for call_session_id, state in self._by_call.items():
            if state.original is not None:
                continue
            live = (resolutions or {}).get(call_session_id)
            if live is not None and live.customer_id is not None:
                state.original = live
                continue
            rejected = next(
                (a.rejected_customer_id for a in state.attestations if a.rejected_customer_id),
                None,
            )
            if rejected is not None and live is not None:
                state.original = live.model_copy(update={"customer_id": rejected})
        if rows:
            log.info("attestations restored", attestations=len(rows), calls=len(self._by_call))
        return len(rows)

    def history(self, call_session_id: str) -> tuple[Attestation, ...]:
        state = self._by_call.get(call_session_id)
        return tuple(state.attestations) if state else ()

    def rejected_customer_ids(self, call_session_id: str) -> frozenset[str]:
        """Never re-propose someone the agent has explicitly rejected on this call."""
        state = self._by_call.get(call_session_id)
        return frozenset(state.rejected_customer_ids) if state else frozenset()

    def attestable(
        self, call_session_id: str, current: IdentityResolution
    ) -> tuple[AttestationOutcome, ...]:
        """Which outcomes this call may be given **right now** (`D71`).

        The server decides, and the screen renders the answer, for the same reason
        `declarable` works that way (`D59`): a client that computes it would need its own
        copy of the rules and would eventually disagree.

        Three cases, and the middle one is the interesting one:

        * **Nobody was ever proposed** — an unrecognised number, `L0` from the first
          second. There is nothing to confirm, nothing to reject, and nobody to act on
          behalf of, so the whole control is inert until customer search exists (`Q18`).
        * **The system verified them itself** — the call came in on an app token, so
          authentication already happened and the agent adds nothing by re-asserting it.
          The one question still open is whether the person holding the phone is the
          account holder or somebody helping them, so *third party* stays live and is the
          only thing offered.
        * **Everything else**, including after a rejection: all three. A rejection is
          explicitly **not** a one-way door — a mis-click, or a caller who only explains on
          the second try that they are the daughter, must be able to come back.

        Note what does *not* qualify as system-verified: `IVR_VERIFY`. Keying the last four
        of a citizen id is a knowledge check, and a family member standing in the same room
        knows those digits. App auth is possession of an authenticated session; the two are
        not the same strength and must not be collapsed.
        """
        state = self._by_call.get(call_session_id)
        original = state.original if state and state.original is not None else current

        if original.customer_id is None and current.customer_id is None:
            return ()
        if original.method is IdentityMethod.APP_TOKEN:
            return (AttestationOutcome.THIRD_PARTY,)
        return (
            AttestationOutcome.CONFIRMED,
            AttestationOutcome.THIRD_PARTY,
            AttestationOutcome.NOT_THIS_PERSON,
        )

    def system_verified(self, call_session_id: str, current: IdentityResolution) -> bool:
        """True when authentication happened before the agent ever saw the call."""
        state = self._by_call.get(call_session_id)
        original = state.original if state and state.original is not None else current
        return original.method is IdentityMethod.APP_TOKEN

    async def attest(
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
        if state.original is None:
            state.original = current
        now = self._clock.now()

        # Taking a rejection back (`D71`). *ไม่ใช่บุคคลนี้* clears the customer, so the
        # resolution the agent is amending FROM has nobody in it — and a mis-click, or a
        # caller who only explained on the second attempt that they are the daughter,
        # would otherwise be stuck at L0 for the rest of the call with no way home. The
        # proposal is restored from what the resolver originally found; it is not invented.
        if (
            outcome is not AttestationOutcome.NOT_THIS_PERSON
            and current.customer_id is None
            and state.original.customer_id is not None
        ):
            current = state.original
            state.rejected_customer_ids.discard(str(current.customer_id))
            log.info(
                "identity rejection withdrawn",
                call_session_id=call_session_id,
                agent_id=agent_id,
                customer_id=current.customer_id,
            )

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
            if challenge not in self._challenges:
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
                        # Amending third-party -> confirmed has to CLEAR the third-party
                        # facts, or the panel keeps saying "acting on behalf of the
                        # policyholder" about a call the agent has just confirmed IS the
                        # policyholder. Evidence is carried forward wholesale, so anything
                        # an earlier outcome set has to be explicitly retired here.
                        "authority_check_required": False,
                        "third_party_name": None,
                        "relationship": None,
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
        if self._store is not None:
            # Durable before the caller is told it worked. An attestation the agent saw
            # accepted, and the log never received, is the one failure this table exists
            # to make impossible.
            await self._store.append(record)
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
    "DEFAULT_CHALLENGES",
    "OTHER_CHALLENGE",
    "Attestation",
    "AttestationOutcome",
    "AttestationService",
    "AttestationStore",
    "InMemoryAttestationStore",
]
