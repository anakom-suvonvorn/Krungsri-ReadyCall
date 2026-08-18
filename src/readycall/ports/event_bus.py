"""EventBus port (`D15`).

Redis Streams in production, in-memory for tests, Kafka if scale demands it. Two
properties the implementations must preserve, because everything else leans on them:

* **idempotent delivery** — consumers dedupe on `event_id`, so at-least-once is fine;
* **replayable** — replaying a call's events must reproduce its final state.

That second property is what lets `scripts/run_scenario.py` drive the entire system
with no telephony at all (`ARCHITECTURE.md` §17).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, runtime_checkable

from readycall.domain.events import Event

Handler = Callable[[Event], Awaitable[None]]


@runtime_checkable
class EventBus(Protocol):
    async def publish(self, event: Event) -> None:
        """Append an event. Must not raise on a slow consumer."""
        ...

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register a handler. `topic` is an event name or `"*"` for everything."""
        ...

    async def drain(self) -> None:
        """Process everything currently pending, then return.

        Deterministic tests need a way to say "let the system settle" without
        sleeping. Production implementations may treat this as a flush.
        """
        ...

    def history(self, call_session_id: str | None = None) -> AsyncIterator[Event]:
        """Replay recorded events, optionally for one call."""
        ...

    async def close(self) -> None: ...
