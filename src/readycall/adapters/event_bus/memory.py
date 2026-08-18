"""In-memory EventBus: the test and scenario-runner backbone (`D15`).

Deterministic on purpose. `publish()` only enqueues; nothing runs until `drain()`,
and `drain()` keeps going until the queue is empty so cascades (a handler publishing
another event) settle fully. That means a scenario replay produces the *same* ordering
every run, which is what makes golden-output comparison possible at all.

It also keeps the full history, so `history(call_session_id)` can replay one call —
the property `ARCHITECTURE.md` §14 requires of every bus implementation.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator

from readycall.domain.events import Event
from readycall.logging import get_logger
from readycall.ports.event_bus import Handler

log = get_logger(__name__)

WILDCARD = "*"


class InMemoryEventBus:
    """Single-process bus with explicit draining and a replayable log."""

    def __init__(self, *, strict_handlers: bool = True) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._pending: deque[Event] = deque()
        self._log: list[Event] = []
        self._seen: set[tuple[int, str]] = set()
        self._strict = strict_handlers
        self._errors: list[tuple[Event, Exception]] = []

    @property
    def name(self) -> str:
        return "memory"

    async def publish(self, event: Event) -> None:
        self._log.append(event)
        self._pending.append(event)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    async def drain(self) -> None:
        """Run every pending event, including anything handlers publish in turn."""
        guard = 0
        while self._pending:
            guard += 1
            if guard > 10_000:
                raise RuntimeError(
                    "event bus drain exceeded 10000 iterations - probable publish loop"
                )
            event = self._pending.popleft()
            for handler in [*self._handlers.get(event.name, []), *self._handlers.get(WILDCARD, [])]:
                # Idempotent delivery: at-least-once is fine, double-handling is not.
                key = (id(handler), event.event_id)
                if key in self._seen:
                    continue
                self._seen.add(key)
                try:
                    await handler(event)
                except Exception as exc:
                    self._errors.append((event, exc))
                    log.error(
                        "event handler failed",
                        # NOT `event=` - structlog reserves that kwarg for the message
                        # itself and raises TypeError on collision (`B2`).
                        event_name=event.name,
                        event_id=event.event_id,
                        call_session_id=event.call_session_id,
                        error=repr(exc),
                    )
                    if self._strict:
                        raise

    async def history(self, call_session_id: str | None = None) -> AsyncIterator[Event]:
        for event in self._log:
            if call_session_id is None or event.call_session_id == call_session_id:
                yield event

    def recorded(self, call_session_id: str | None = None) -> list[Event]:
        """Synchronous view of the log. Assertions in tests, not production code."""
        return [
            e for e in self._log if call_session_id is None or e.call_session_id == call_session_id
        ]

    def names(self, call_session_id: str | None = None) -> list[str]:
        return [e.name for e in self.recorded(call_session_id)]

    @property
    def errors(self) -> list[tuple[Event, Exception]]:
        return list(self._errors)

    async def close(self) -> None:
        self._pending.clear()
