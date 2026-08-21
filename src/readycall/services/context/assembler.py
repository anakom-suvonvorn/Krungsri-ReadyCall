"""Builds `Customer360` and freezes it into a `ContextSnapshot`.

This is the piece that makes the pitch's central claim true: context assembly starts at
*tap* (or at the moment caller ID resolves), not at *answer* (`D6`). By the time anyone
picks up, the non-speech half of the brief already exists.

Two properties matter as much as the data itself:

* **Provenance.** Every field records where it came from, which adapter produced it, when
  it was fetched, and whether it is stale. If the workstation cannot answer "says who, and
  how old is this?", the agent has no way to judge it (`D18`).
* **Frozen.** The snapshot is immutable once built, so the screen shows *what the system
  knew when it decided*, and a scenario replay reproduces exactly the same brief even if
  upstream data moves underneath us.

It never raises. A dead upstream produces a thinner snapshot with a `DegradationReason`,
because the call must proceed regardless (`D12`).
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from readycall import ids
from readycall.clock import Clock, Stopwatch
from readycall.domain.enums import DegradationReason, ProductLine
from readycall.domain.models import (
    Claim,
    ContextSnapshot,
    Customer,
    Customer360,
    FieldProvenance,
    Holding,
    Interaction,
    LifeEvent,
    Policy,
    Product,
)
from readycall.logging import get_logger
from readycall.ports.core_data import CoreDataProvider

log = get_logger(__name__)

T = TypeVar("T")

#: How many past interactions the workstation's "Recent Context" panel can use.
RECENT_INTERACTION_LIMIT = 10


class ContextAssembler:
    def __init__(self, *, core: CoreDataProvider, clock: Clock) -> None:
        self._core = core
        self._clock = clock

    async def build(
        self,
        *,
        customer_id: str | None,
        product_code: str | None = None,
        product_line: ProductLine = ProductLine.UNKNOWN,
    ) -> ContextSnapshot:
        """Assemble everything we know about this customer, right now.

        `customer_id` may be `None` — an anonymous caller still gets a snapshot, just an
        empty one. That keeps the caller of this method free of special cases.
        """
        watch = Stopwatch(self._clock)
        now = self._clock.now()
        provenance: list[FieldProvenance] = []
        degraded = DegradationReason.NONE

        if customer_id is None:
            return ContextSnapshot(
                snapshot_id=ids.snapshot_id(),
                customer_id=None,
                built_at=now,
                payload=Customer360(),
                provenance=(),
                provider_name=self._core.name,
                build_ms=watch.elapsed_ms(),
                degraded=DegradationReason.LOW_ASSURANCE,
            )

        # Fan out. These are independent reads, so they go in parallel — the difference
        # between one round trip and seven is the whole tap-to-snapshot budget.
        gathered: tuple[Any, ...] = await asyncio.gather(
            self._safe(self._core.get_customer(customer_id), None, "customer"),
            self._safe(self._core.list_policies(customer_id, active_only=True), [], "policies"),
            self._safe(
                self._core.list_interactions(customer_id, limit=RECENT_INTERACTION_LIMIT),
                [],
                "interactions",
            ),
            self._safe(self._core.list_holdings(customer_id), [], "holdings"),
            self._safe(self._core.list_life_events(customer_id), [], "life_events"),
            self._safe(
                self._core.get_product(product_code) if product_code else _none(), None, "product"
            ),
        )
        customer: Customer | None = gathered[0]
        policies: list[Policy] = gathered[1]
        interactions: list[Interaction] = gathered[2]
        holdings: list[Holding] = gathered[3]
        life_events: list[LifeEvent] = gathered[4]
        product: Product | None = gathered[5]

        if customer is None:
            # We were told a customer id and the core does not have it. That is a real
            # degradation, not an anonymous call.
            degraded = DegradationReason.CORE_DATA_UNAVAILABLE
            log.warning("context assembly found no customer", customer_id=customer_id)

        relevant = self._pick_relevant_policy(policies, product_code, product_line)
        claims: list[Claim] = await self._safe(
            self._core.list_claims(relevant.policy_no) if relevant else _empty(), [], "claims"
        )

        last_contact = interactions[0] if interactions else None
        stale = getattr(self._core, "served_stale", False)
        if stale:
            degraded = DegradationReason.CORE_DATA_STALE

        for field_name, source, present in (
            ("customer", "core:customers", customer is not None),
            ("active_policies", "core:policies", bool(policies)),
            ("relevant_policy", "core:policies", relevant is not None),
            ("recent_claims", "core:claims", bool(claims)),
            ("recent_interactions", "core:interactions", bool(interactions)),
            ("holdings", "core:financial_products", bool(holdings)),
            ("life_events", "core:life_events", bool(life_events)),
            ("selected_product", "core:products", product is not None),
        ):
            if present:
                provenance.append(
                    FieldProvenance(
                        field=field_name,
                        source=source,
                        fetched_at=now,
                        provider=self._core.name,
                        stale=bool(stale),
                    )
                )

        payload = Customer360(
            customer=customer,
            selected_product=product,
            active_policies=tuple(policies),
            relevant_policy=relevant,
            recent_claims=tuple(claims),
            recent_interactions=tuple(interactions),
            holdings=tuple(holdings),
            life_events=tuple(life_events),
            last_contact_at=last_contact.occurred_at if last_contact else None,
            last_agent_id=self._last_agent(interactions),
            previous_inquiry=last_contact.topic if last_contact else None,
        )

        snapshot = ContextSnapshot(
            snapshot_id=ids.snapshot_id(),
            customer_id=customer_id,
            built_at=now,
            payload=payload,
            provenance=tuple(provenance),
            provider_name=self._core.name,
            build_ms=watch.elapsed_ms(),
            degraded=degraded,
        )
        log.info(
            "context snapshot built",
            customer_id=customer_id,
            policies=len(policies),
            interactions=len(interactions),
            build_ms=round(snapshot.build_ms or 0.0, 2),
            degraded=str(degraded),
        )
        return snapshot

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    def _pick_relevant_policy(
        policies: list[Policy], product_code: str | None, product_line: ProductLine
    ) -> Policy | None:
        """Which policy is this call *about*?

        Most specific evidence first: the exact plan they tapped, then the product line
        the DID or menu gave us, then — only if they hold exactly one — that one. With two
        unrelated policies and no signal we deliberately pick nothing rather than guess,
        because a confidently wrong policy on screen is worse than an empty panel.
        """
        if not policies:
            return None
        if product_code:
            for policy in policies:
                if policy.product_code == product_code:
                    return policy
        if product_line is not ProductLine.UNKNOWN:
            in_line = [p for p in policies if p.line is product_line]
            if len(in_line) == 1:
                return in_line[0]
            if in_line:
                # Several in the same line: prefer the one expiring soonest, which is the
                # likeliest subject of a renewal or coverage question.
                return min(in_line, key=lambda p: (p.expiry_date is None, p.expiry_date))
        if len(policies) == 1:
            return policies[0]
        return None

    @staticmethod
    def _last_agent(interactions: list[Interaction]) -> str | None:
        for interaction in interactions:
            if interaction.agent_id:
                return interaction.agent_id
        return None

    @staticmethod
    async def _safe(awaitable: object, default: T, field: str) -> T:
        """Never let one failed read sink the whole snapshot."""
        try:
            return await awaitable  # type: ignore[misc, no-any-return]
        except Exception as exc:
            log.warning("context field unavailable", field=field, error=repr(exc))
            return default


async def _none() -> Product | None:
    return None


async def _empty() -> list[Claim]:
    return []


__all__ = ["RECENT_INTERACTION_LIMIT", "ContextAssembler", "Customer"]
