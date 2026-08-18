"""CoreDataProvider port — the bank's data, READ ONLY (`D5`).

This is the single most important seam in the system, because it is the one that
changes on the morning of the hackathon (`DATA_MODEL.md` §4). Rules:

* It returns **domain objects**, never rows, never vendor JSON. Each adapter owns its
  own mapping, so nothing upstream knows the shape of what we were handed.
* A missing record returns `None`. It is not an error for a caller to be unknown —
  that is the L0 path, and it must not throw (`D20`).
* There is **no write method, on purpose.** Everything ReadyCall produces goes to our
  own store. We cannot corrupt their data because we cannot reach it.
* Every adapter passes the same contract suite (`tests/contracts/`). An adapter written
  on hackathon day is "done" when that suite is green — that is the whole integration
  checklist, minutes rather than an afternoon.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from readycall.domain.models import (
    Claim,
    Customer,
    Holding,
    Interaction,
    LifeEvent,
    Policy,
    Product,
)


@runtime_checkable
class CoreDataProvider(Protocol):
    @property
    def name(self) -> str:
        """Adapter name, recorded in every `ContextSnapshot` provenance row."""
        ...

    async def get_customer(self, customer_id: str) -> Customer | None: ...

    async def find_customer_by_phone(self, phone_e164: str) -> Customer | None:
        """ANI lookup. Implementations must normalise `08x` <-> `+668x` themselves.

        A match here is *probable identity only* — never treat it as verified (`D20`).
        """
        ...

    async def list_policies(
        self, customer_id: str, *, active_only: bool = True
    ) -> list[Policy]: ...

    async def get_policy(self, policy_no: str) -> Policy | None: ...

    async def list_claims(self, policy_no: str) -> list[Claim]: ...

    async def list_interactions(self, customer_id: str, *, limit: int = 20) -> list[Interaction]:
        """Most recent first — the workstation shows 'last contact' from position 0."""
        ...

    async def list_holdings(self, customer_id: str) -> list[Holding]: ...

    async def list_life_events(self, customer_id: str) -> list[LifeEvent]: ...

    async def get_product(self, product_code: str) -> Product | None: ...

    async def health_check(self) -> bool:
        """Cheap liveness probe. Drives the degradation ladder, never a render path."""
        ...
