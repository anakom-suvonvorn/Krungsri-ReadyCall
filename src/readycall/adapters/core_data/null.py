"""NullCoreDataProvider — everything unavailable.

Not a stub for convenience: it exists so the degradation ladder is *exercised* rather
than assumed (`ARCHITECTURE.md` §16). Set `CORE_DATA_PROVIDER=null` and the whole
system must still take a call and hand the agent an intent-only brief. If it cannot,
that is a bug worth finding before demo day rather than during it.
"""

from __future__ import annotations

from readycall.domain.models import (
    Claim,
    Customer,
    Holding,
    Interaction,
    LifeEvent,
    Policy,
    Product,
)


class NullCoreDataProvider:
    """Answers "I don't know" to everything, politely and quickly."""

    def __init__(self, *, healthy: bool = False) -> None:
        self._healthy = healthy

    @property
    def name(self) -> str:
        return "null"

    async def get_customer(self, customer_id: str) -> Customer | None:
        return None

    async def find_customer_by_phone(self, phone_e164: str) -> Customer | None:
        return None

    async def list_policies(self, customer_id: str, *, active_only: bool = True) -> list[Policy]:
        return []

    async def get_policy(self, policy_no: str) -> Policy | None:
        return None

    async def list_claims(self, policy_no: str) -> list[Claim]:
        return []

    async def list_interactions(self, customer_id: str, *, limit: int = 20) -> list[Interaction]:
        return []

    async def list_holdings(self, customer_id: str) -> list[Holding]:
        return []

    async def list_life_events(self, customer_id: str) -> list[LifeEvent]:
        return []

    async def get_product(self, product_code: str) -> Product | None:
        return None

    async def health_check(self) -> bool:
        return self._healthy
