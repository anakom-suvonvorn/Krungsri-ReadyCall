"""Where prefetched snapshots and in-app screen events live until something reads them.

Both are `Protocol` + in-memory implementation, same as everything else that will become a
table at P2 (`D39`). Deliberately small — the interesting behaviour is in the assembler;
these just hold what it produced.

The app-context store has one property worth naming: **screen events expire.** "Viewed
hospitalisation coverage" is useful evidence about why someone is calling *now*; the same
event from three weeks ago is noise, and showing it to an agent as if it were current would
be worse than showing nothing. So reads are always windowed.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from readycall.clock import Clock
from readycall.domain.models import ContextSnapshot
from readycall.logging import get_logger

log = get_logger(__name__)

#: How many screen events we keep per customer. A ring buffer, because the interesting
#: signal is "what were they just looking at", not their whole browsing history.
MAX_EVENTS_PER_CUSTOMER = 20


@dataclass(frozen=True, slots=True)
class AppContextEvent:
    customer_id: str
    section: str
    product_code: str | None
    dwell_ms: int
    occurred_at: datetime
    metadata: dict[str, Any]


class SnapshotStore(Protocol):
    async def save(self, snapshot: ContextSnapshot) -> None: ...
    async def get(self, snapshot_id: str) -> ContextSnapshot | None: ...


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ContextSnapshot] = {}

    async def save(self, snapshot: ContextSnapshot) -> None:
        self._by_id[snapshot.snapshot_id] = snapshot

    async def get(self, snapshot_id: str) -> ContextSnapshot | None:
        return self._by_id.get(snapshot_id)

    def __len__(self) -> int:
        return len(self._by_id)


class AppContextStore(Protocol):
    async def record(self, event: AppContextEvent) -> int: ...
    async def recent_for(
        self, customer_id: str, *, within: timedelta
    ) -> tuple[AppContextEvent, ...]: ...


class InMemoryAppContextStore:
    def __init__(self, *, clock: Clock, ttl: timedelta = timedelta(hours=24)) -> None:
        self._clock = clock
        self._ttl = ttl
        self._by_customer: dict[str, deque[AppContextEvent]] = defaultdict(
            lambda: deque(maxlen=MAX_EVENTS_PER_CUSTOMER)
        )

    async def record(self, event: AppContextEvent) -> int:
        bucket = self._by_customer[event.customer_id]
        bucket.append(event)
        return len(bucket)

    async def recent_for(
        self, customer_id: str, *, within: timedelta | None = None
    ) -> tuple[AppContextEvent, ...]:
        """Only events inside the window. Stale browsing is not context, it is noise."""
        horizon = self._clock.now() - (within or self._ttl)
        return tuple(e for e in self._by_customer.get(customer_id, ()) if e.occurred_at >= horizon)


__all__ = [
    "MAX_EVENTS_PER_CUSTOMER",
    "AppContextEvent",
    "AppContextStore",
    "InMemoryAppContextStore",
    "InMemorySnapshotStore",
    "SnapshotStore",
]
