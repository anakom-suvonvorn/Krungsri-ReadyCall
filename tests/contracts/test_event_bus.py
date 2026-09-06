"""The EventBus contract (`D15`).

Two properties everything else leans on, so they are pinned here rather than assumed:

* **idempotent delivery** — a handler sees a given `event_id` exactly once, which is
  what makes at-least-once transport safe;
* **replayable** — the recorded log can be replayed per call, which is what lets the
  scenario runner drive the whole system without a telephone (`ARCHITECTURE.md` §17).

The Redis Streams and Kafka adapters will run this same suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.domain import events as ev
from readycall.domain.enums import CallState
from readycall.ports.event_bus import EventBus

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def state_changed(call_id: str = "call_1", *, to: CallState = CallState.QUEUED) -> ev.Event:
    return ev.CallStateChanged(
        call_session_id=call_id,
        occurred_at=NOW,
        from_state=CallState.IVR,
        to_state=to,
        reason="test",
    )


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


def test_satisfies_the_protocol(bus: InMemoryEventBus) -> None:
    assert isinstance(bus, EventBus)


async def test_publish_then_drain_delivers_to_the_named_topic(bus: InMemoryEventBus) -> None:
    seen: list[ev.Event] = []

    async def handler(event: ev.Event) -> None:
        seen.append(event)

    bus.subscribe("call.state.changed", handler)
    await bus.publish(state_changed())
    assert seen == [], "nothing should run before drain() - determinism depends on it"
    await bus.drain()
    assert len(seen) == 1


async def test_wildcard_subscriber_sees_everything(bus: InMemoryEventBus) -> None:
    seen: list[str] = []

    async def handler(event: ev.Event) -> None:
        seen.append(event.name)

    bus.subscribe("*", handler)
    await bus.publish(state_changed())
    await bus.publish(
        ev.CallQueued(call_session_id="call_1", occurred_at=NOW, queue_id="q_service")
    )
    await bus.drain()
    assert seen == ["call.state.changed", "call.queued"]


async def test_delivery_is_idempotent_per_event_id(bus: InMemoryEventBus) -> None:
    """Re-publishing the same event must not double-handle it."""
    count = 0

    async def handler(event: ev.Event) -> None:
        nonlocal count
        count += 1

    bus.subscribe("call.state.changed", handler)
    event = state_changed()
    await bus.publish(event)
    await bus.publish(event)  # same event_id, e.g. a transport retry
    await bus.drain()
    assert count == 1


async def test_a_handler_may_publish_and_the_cascade_settles(bus: InMemoryEventBus) -> None:
    """Real handlers react by publishing; drain() must run the whole cascade."""
    tail: list[str] = []

    async def on_queued(event: ev.Event) -> None:
        await bus.publish(
            ev.MatchingDecided(
                call_session_id=event.call_session_id,
                occurred_at=NOW,
                decision_id="match_1",
                kind=ev.MatchKind.ASSIGN,
                chosen_agent_id="A001",
            )
        )

    async def on_matched(event: ev.Event) -> None:
        tail.append(event.name)

    bus.subscribe("call.queued", on_queued)
    bus.subscribe("matching.decided", on_matched)
    await bus.publish(
        ev.CallQueued(call_session_id="call_1", occurred_at=NOW, queue_id="q_service")
    )
    await bus.drain()
    assert tail == ["matching.decided"]


async def test_history_replays_one_call_only(bus: InMemoryEventBus) -> None:
    await bus.publish(state_changed("call_a"))
    await bus.publish(state_changed("call_b"))
    await bus.publish(state_changed("call_a", to=CallState.MATCHED))
    await bus.drain()

    replayed = [e async for e in bus.history("call_a")]
    assert len(replayed) == 2
    assert {e.call_session_id for e in replayed} == {"call_a"}
    assert len([e async for e in bus.history()]) == 3


async def test_a_publish_loop_is_detected_rather_than_hanging(bus: InMemoryEventBus) -> None:
    """A handler that republishes forever should fail loudly, not freeze the process."""

    async def looper(event: ev.Event) -> None:
        await bus.publish(state_changed())  # a fresh event id every time

    bus.subscribe("call.state.changed", looper)
    await bus.publish(state_changed())
    with pytest.raises(RuntimeError, match="publish loop"):
        await bus.drain()


async def test_a_failing_handler_is_recorded(bus: InMemoryEventBus) -> None:
    lenient = InMemoryEventBus(strict_handlers=False)

    async def broken(event: ev.Event) -> None:
        raise ValueError("boom")

    lenient.subscribe("*", broken)
    await lenient.publish(state_changed())
    await lenient.drain()
    assert len(lenient.errors) == 1


class TestEventSchemas:
    def test_every_event_type_has_a_unique_name(self) -> None:
        names = [t.name for t in ev.EVENT_TYPES]
        assert len(names) == len(set(names))

    def test_decode_round_trips(self) -> None:
        original = state_changed()
        rebuilt = ev.decode(original.name, original.model_dump())
        assert rebuilt == original

    def test_decoding_an_unknown_event_is_a_hard_error(self) -> None:
        """Silently dropping an event we do not understand would break replay."""
        with pytest.raises(ValueError, match="unknown event name"):
            ev.decode("something.invented", {})
