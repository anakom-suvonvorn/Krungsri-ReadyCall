"""Where pending `CallIntent`s live.

An internal seam, not a port: this is our own store. The Protocol exists so P1 can run
without Postgres, and so the SQLAlchemy version drops in later untouched.

The lookups are exactly the two the assurance ladder needs (`D20`): by token hash (the app
path, L3) and by customer within a recency window (the "tapped Contact then dialled from
their own phone" path, L2).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from readycall.domain.models import CallIntent


@runtime_checkable
class CallIntentStore(Protocol):
    async def save(self, intent: CallIntent) -> None: ...

    async def get(self, intent_id: str) -> CallIntent | None: ...

    async def get_by_token_hash(self, token_hash: str) -> CallIntent | None: ...

    async def find_recent_for_customer(
        self, customer_id: str, *, since: datetime, within_s: float
    ) -> CallIntent | None: ...


class InMemoryCallIntentStore:
    """Dict-backed. Tests, the scenario runner, and P1."""

    def __init__(self) -> None:
        self._by_id: dict[str, CallIntent] = {}
        self._by_token: dict[str, str] = {}

    async def save(self, intent: CallIntent) -> None:
        self._by_id[intent.intent_id] = intent
        self._by_token[intent.correlation_token_hash] = intent.intent_id

    async def get(self, intent_id: str) -> CallIntent | None:
        return self._by_id.get(intent_id)

    async def get_by_token_hash(self, token_hash: str) -> CallIntent | None:
        intent_id = self._by_token.get(token_hash)
        return self._by_id.get(intent_id) if intent_id else None

    async def find_recent_for_customer(
        self, customer_id: str, *, since: datetime, within_s: float
    ) -> CallIntent | None:
        """Most recent unexpired intent for this customer inside the window."""
        cutoff = since - timedelta(seconds=within_s)
        candidates = [
            intent
            for intent in self._by_id.values()
            if intent.customer_id == customer_id
            and intent.created_at >= cutoff
            and not intent.is_expired(since)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda i: i.created_at)

    def all(self) -> list[CallIntent]:
        return list(self._by_id.values())
