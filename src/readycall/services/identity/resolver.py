"""Working out who is calling, and how much we should believe it (`D20`).

Identity here is **not a boolean**. A phone can be borrowed, shared, or spoofed, so an
ANI match is a *guess* — a useful one, but not a licence to read someone's policy number
aloud. The resolver returns a customer id **plus an assurance level**, and the level is
what gates disclosure downstream.

The ladder:

| Level | Reached by | What it means |
|---|---|---|
| `L0_ANONYMOUS` | nothing matched | we have no idea, and that is fine |
| `L1_PROBABLE`  | caller ID matches a customer | probably them; do not disclose details |
| `L2_STRONG`    | caller ID **and** a pending app intent | almost certainly them |
| `L3_VERIFIED`  | app token, or IVR verification | it is them |

Note what this class does **not** do: it never refuses to resolve. An unrecognised caller
is the L0 path, not an error — the call continues and the agent verifies the old-fashioned
way (`D19`).
"""

from __future__ import annotations

import hashlib

from readycall.clock import Clock
from readycall.domain.enums import AssuranceLevel, IdentityMethod
from readycall.domain.models import IdentityResolution
from readycall.logging import get_logger
from readycall.ports.core_data import CoreDataProvider
from readycall.services.identity.store import CallIntentStore

log = get_logger(__name__)


def hash_token(token: str) -> str:
    """Correlation tokens are bearer credentials: stored hashed, never in the clear."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class IdentityResolver:
    def __init__(
        self,
        *,
        core: CoreDataProvider,
        intents: CallIntentStore,
        clock: Clock,
        pending_intent_window_s: float = 900.0,
    ) -> None:
        self._core = core
        self._intents = intents
        self._clock = clock
        self._window_s = pending_intent_window_s

    async def resolve(
        self,
        *,
        correlation_token: str | None = None,
        caller_number: str | None = None,
        ivr_verified_customer_id: str | None = None,
    ) -> IdentityResolution:
        now = self._clock.now()

        # --- L3: the app token. Identity is bound to the media session (`D4`). --------
        if correlation_token:
            intent = await self._intents.get_by_token_hash(hash_token(correlation_token))
            if intent is not None and not intent.is_expired(now):
                return IdentityResolution(
                    method=IdentityMethod.APP_TOKEN,
                    customer_id=intent.customer_id,
                    assurance=AssuranceLevel.L3_VERIFIED,
                    resolved_at=now,
                    evidence={"intent_id": intent.intent_id},
                )
            # An expired or unknown token is not an error — it just earns nothing. Fall
            # through to the weaker rungs rather than failing the call.
            log.info(
                "correlation token did not resolve",
                expired=intent is not None,
                has_caller_number=caller_number is not None,
            )

        # --- L3: IVR verification (DOB / last 4 of citizen id / policy number) --------
        #
        # **The live IVR never supplies this** (`D84`). Asking a caller to key their
        # citizen id before anyone says hello would promote them to L3 with no human in
        # the loop, which is precisely what `D44` refuses — a lookup returns evidence,
        # and only the agent attests. The agent's own keypad capture does the same job
        # during the call, with judgement attached.
        #
        # The rung is kept rather than deleted because `D25`'s after-hours voicemail path
        # has **no agent at all**, and is the one place a self-service check would have to
        # stand on its own. Anything wiring this up owes a decision entry saying why the
        # missing human is acceptable there.
        if ivr_verified_customer_id:
            return IdentityResolution(
                method=IdentityMethod.IVR_VERIFY,
                customer_id=ivr_verified_customer_id,
                assurance=AssuranceLevel.L3_VERIFIED,
                resolved_at=now,
                evidence={"channel": "ivr"},
            )

        # --- L1/L2: caller ID ---------------------------------------------------------
        if caller_number:
            customer = await self._core.find_customer_by_phone(caller_number)
            if customer is not None:
                recent = await self._intents.find_recent_for_customer(
                    customer.customer_id, since=now, within_s=self._window_s
                )
                if recent is not None:
                    # They tapped Contact in the app minutes ago and are now on the phone
                    # from their own number. Not proof, but a great deal more than ANI.
                    return IdentityResolution(
                        method=IdentityMethod.PENDING_INTENT,
                        customer_id=customer.customer_id,
                        assurance=AssuranceLevel.L2_STRONG,
                        resolved_at=now,
                        evidence={
                            "caller_number": caller_number,
                            "intent_id": recent.intent_id,
                            "window_s": self._window_s,
                        },
                    )
                return IdentityResolution(
                    method=IdentityMethod.ANI,
                    customer_id=customer.customer_id,
                    assurance=AssuranceLevel.L1_PROBABLE,
                    resolved_at=now,
                    evidence={"caller_number": caller_number},
                )

        # --- L0: we do not know, and the call proceeds anyway --------------------------
        return IdentityResolution(
            method=IdentityMethod.NONE,
            customer_id=None,
            assurance=AssuranceLevel.L0_ANONYMOUS,
            resolved_at=now,
            evidence={"caller_number": caller_number} if caller_number else {},
        )
