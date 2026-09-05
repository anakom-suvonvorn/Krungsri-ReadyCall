"""The transcript's last hop: off the bus, onto one agent's screen (`D106`).

Two things this suite is deliberately built to catch, because both are shapes that have
already shipped in this project:

* **a handler that is registered and never runs.** `InMemoryEventBus.publish()` only
  enqueues (`D15`), so every test here drains explicitly — and `test_the_api_process_runs
  _handlers_without_a_request` covers the live process, where until `D105` nothing did.
* **a client left to accumulate.** Every push carries the complete transcript, so a lost
  message cannot leave a gap. The assertions check the *payload*, not just that a send
  happened (`B20`'s lesson: "three turns arrived" is a much weaker claim than "turn three
  carried segment three's audio").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.clock import ManualClock
from readycall.domain import events as ev
from readycall.domain.enums import CallState, OfferOutcome, SpeakerRole
from readycall.services.transcription.delivery import (
    MAX_TURNS_PER_CALL,
    TRANSCRIPT_MESSAGE,
    TranscriptDeliveryService,
)

CALL = "cs_1"
AGENT = "A006"
NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


class SpyNotifier:
    """Stands in for `AgentHub`, keeping what it was asked to send."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def send(self, agent_id: str, kind: str, payload: dict[str, Any]) -> Any:
        self.sent.append((agent_id, kind, payload))
        return {"seq": len(self.sent)}

    async def broadcast(self, kind: str, payload: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError("the transcript is per agent, never broadcast")

    def transcripts(self) -> list[list[dict[str, Any]]]:
        return [p["turns"] for _, kind, p in self.sent if kind == TRANSCRIPT_MESSAGE]


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def notifier() -> SpyNotifier:
    return SpyNotifier()


@pytest.fixture(autouse=True)
def delivery(bus: InMemoryEventBus, notifier: SpyNotifier) -> TranscriptDeliveryService:
    """Autouse on purpose: a test that publishes to a bus nobody subscribed to passes by
    asserting nothing happened, which is exactly the failure this suite exists to catch."""
    service = TranscriptDeliveryService(notifier=notifier)
    service.subscribe(bus)
    return service


def turn(seq: int, text: str, *, call: str = CALL) -> ev.TranscriptTurnAdded:
    return ev.TranscriptTurnAdded(
        call_session_id=call,
        occurred_at=NOW + timedelta(seconds=seq),
        turn_id=f"tt_{seq}",
        seq=seq,
        speaker_role=str(SpeakerRole.CUSTOMER),
        text=text,
        t_start_ms=seq * 1000,
        t_end_ms=seq * 1000 + 900,
    )


def accepted(*, call: str = CALL, agent: str = AGENT) -> ev.OfferResolved:
    return ev.OfferResolved(
        call_session_id=call,
        occurred_at=NOW,
        assignment_id="as_1",
        agent_id=agent,
        outcome=OfferOutcome.ACCEPTED,
    )


# --- the part that is not plumbing ------------------------------------------------------


@pytest.mark.asyncio
async def test_turns_during_intake_are_held_because_nobody_owns_the_call_yet(
    bus: InMemoryEventBus, notifier: SpyNotifier, delivery: TranscriptDeliveryService
) -> None:
    """The whole point of the product: the transcript exists BEFORE an agent has it."""
    await bus.publish(turn(1, "ผมโทรมาเรื่องเคลมรถ"))
    await bus.publish(turn(2, "ชนเมื่อเช้านี้ครับ"))
    await bus.drain()

    assert notifier.sent == [], "there is no agent to send to during intake"
    assert len(delivery.turns_for(CALL)) == 2, "and the turns must not be lost"


@pytest.mark.asyncio
async def test_accepting_flushes_everything_said_so_far_in_order(
    bus: InMemoryEventBus, notifier: SpyNotifier
) -> None:
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(turn(2, "สอง"))
    await bus.publish(turn(3, "สาม"))
    await bus.publish(accepted())
    await bus.drain()

    assert len(notifier.sent) == 1, "one message carrying the whole transcript, not three"
    agent_id, kind, payload = notifier.sent[0]
    assert (agent_id, kind) == (AGENT, TRANSCRIPT_MESSAGE)
    assert payload["call_session_id"] == CALL
    assert [t["text"] for t in payload["turns"]] == ["หนึ่ง", "สอง", "สาม"]
    assert [t["seq"] for t in payload["turns"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_a_turn_after_the_accept_goes_live_to_that_agent(
    bus: InMemoryEventBus, notifier: SpyNotifier
) -> None:
    """P6 feeds this from the live call; `D21` can also land a last intake turn here."""
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(accepted())
    await bus.drain()
    await bus.publish(turn(2, "สอง"))
    await bus.drain()

    assert len(notifier.sent) == 2
    assert [t["text"] for t in notifier.transcripts()[-1]] == ["หนึ่ง", "สอง"]


@pytest.mark.asyncio
async def test_every_push_carries_the_complete_transcript_never_a_delta(
    bus: InMemoryEventBus, notifier: SpyNotifier
) -> None:
    """`D68`: a client that accumulates is a client that can silently lose a sentence."""
    await bus.publish(accepted())
    for seq in range(1, 5):
        await bus.publish(turn(seq, f"turn {seq}"))
    await bus.drain()

    # Five messages: the accept (empty so far), then one per turn, each carrying every
    # turn up to that point rather than only the new one.
    assert [[t["text"] for t in turns] for turns in notifier.transcripts()] == [
        [],
        ["turn 1"],
        ["turn 1", "turn 2"],
        ["turn 1", "turn 2", "turn 3"],
        ["turn 1", "turn 2", "turn 3", "turn 4"],
    ]


# --- who does NOT get it ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_declining_sends_nothing_and_keeps_the_turns_for_the_next_desk(
    bus: InMemoryEventBus, notifier: SpyNotifier, delivery: TranscriptDeliveryService
) -> None:
    """An agent who declines never took the call, so they never read it verbatim (`D52`)."""
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(
        ev.OfferResolved(
            call_session_id=CALL,
            occurred_at=NOW,
            assignment_id="as_1",
            agent_id=AGENT,
            outcome=OfferOutcome.DECLINED,
        )
    )
    await bus.drain()
    assert notifier.sent == []

    await bus.publish(accepted(agent="A007"))
    await bus.drain()
    assert notifier.sent[0][0] == "A007"
    assert [t["text"] for t in notifier.transcripts()[-1]] == ["หนึ่ง"]


@pytest.mark.asyncio
async def test_a_timed_out_offer_does_not_deliver_either(
    bus: InMemoryEventBus, notifier: SpyNotifier
) -> None:
    """RONA. Same reasoning as a decline, and the agent is not even at the desk."""
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(
        ev.OfferResolved(
            call_session_id=CALL,
            occurred_at=NOW,
            assignment_id="as_1",
            agent_id=AGENT,
            outcome=OfferOutcome.TIMEOUT,
        )
    )
    await bus.drain()
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_one_calls_turns_never_reach_another_calls_agent(
    bus: InMemoryEventBus, notifier: SpyNotifier
) -> None:
    await bus.publish(turn(1, "call one", call="cs_1"))
    await bus.publish(turn(1, "call two", call="cs_2"))
    await bus.publish(accepted(call="cs_1", agent="A006"))
    await bus.drain()

    agent_id, _, payload = notifier.sent[-1]
    assert agent_id == "A006"
    assert payload["call_session_id"] == "cs_1"
    assert [t["text"] for t in payload["turns"]] == ["call one"]


# --- lifecycle --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrap_up_keeps_the_transcript_because_that_is_what_it_is_written_from(
    bus: InMemoryEventBus, delivery: TranscriptDeliveryService
) -> None:
    """`ARCHITECTURE` §12: the wrap-up is drafted from the transcript. Clearing the panel
    at hang-up would empty it at the one moment the agent needs to read it."""
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(
        ev.CallStateChanged(
            call_session_id=CALL,
            occurred_at=NOW,
            from_state=CallState.IN_CALL,
            to_state=CallState.WRAP_UP,
            reason="caller_hung_up",
        )
    )
    await bus.drain()
    assert len(delivery.turns_for(CALL)) == 1


@pytest.mark.asyncio
async def test_a_closed_call_is_forgotten(
    bus: InMemoryEventBus, delivery: TranscriptDeliveryService
) -> None:
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(accepted())
    await bus.publish(
        ev.CallStateChanged(
            call_session_id=CALL,
            occurred_at=NOW,
            from_state=CallState.WRAP_UP,
            to_state=CallState.CLOSED,
            reason="wrapup_saved",
        )
    )
    await bus.drain()
    assert delivery.turns_for(CALL) == ()
    assert delivery.live_call_ids() == ()


@pytest.mark.asyncio
async def test_an_abandoned_call_is_forgotten_too(
    bus: InMemoryEventBus, delivery: TranscriptDeliveryService
) -> None:
    """A caller who gives up while waiting never reaches an agent, and the buffer must
    still go — otherwise every abandoned call leaks a transcript for the life of the
    process, and abandonment is a normal outcome, not an error."""
    await bus.publish(turn(1, "หนึ่ง"))
    await bus.publish(
        ev.CallStateChanged(
            call_session_id=CALL,
            occurred_at=NOW,
            from_state=CallState.INTAKE_ACTIVE,
            to_state=CallState.ABANDONED,
            reason="caller_hung_up",
        )
    )
    await bus.drain()
    assert delivery.live_call_ids() == ()


@pytest.mark.asyncio
async def test_the_per_call_cap_drops_the_oldest_and_says_so(
    bus: InMemoryEventBus, delivery: TranscriptDeliveryService
) -> None:
    for seq in range(1, MAX_TURNS_PER_CALL + 6):
        await bus.publish(turn(seq, f"turn {seq}"))
    await bus.drain()

    held = delivery.turns_for(CALL)
    assert len(held) == MAX_TURNS_PER_CALL
    # The NEWEST survive: on a screen the agent is reading, the recent sentence is the
    # one that matters. `D100` chose the opposite for the audio backlog, where the oldest
    # was the one nobody had seen.
    assert held[-1]["text"] == f"turn {MAX_TURNS_PER_CALL + 5}"


# --- the driver that makes any of this run ----------------------------------------------


@pytest.mark.asyncio
async def test_the_api_process_runs_handlers_without_a_request(tmp_path: Any) -> None:
    """`D105`. Publishing enqueues; something has to drain, and for a long time the only
    thing that did was a background task on `POST /v1/calls/intents`.

    This drives `pump_once` directly rather than sleeping on the real loop — the same
    reason `B7`'s tests drive `sweep_once`: a test that waits for a background task is a
    test that is slow when it passes and mysterious when it fails.
    """
    from readycall.api.app import pump_once, sweep_once
    from readycall.api.deps import Container
    from readycall.config import Settings
    from tests.conftest import REPO_ROOT

    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )
    container = Container(settings, clock=ManualClock(NOW))
    try:
        await container.bus.publish(turn(1, "หนึ่ง"))
        await container.bus.publish(accepted())

        await sweep_once(container)
        assert container.transcript_delivery.turns_for(CALL) == (), (
            "the sweep must not be what drains the bus - it runs once a second and the "
            "whole transcript budget is 1.5 s"
        )

        await pump_once(container)
        assert len(container.transcript_delivery.turns_for(CALL)) == 1
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_one_failing_handler_does_not_stop_the_pump_forever() -> None:
    """The bus is built `strict_handlers=True`, so a raising handler aborts the pass.

    `pump_once` swallowing it is what keeps the *next* pass running. Without that, one bad
    subscriber would stop every event in the process reaching every consumer, for good —
    and it would look like the system had simply gone quiet.
    """
    from readycall.api.app import pump_once

    bus = InMemoryEventBus()
    notifier = SpyNotifier()
    delivery = TranscriptDeliveryService(notifier=notifier)
    delivery.subscribe(bus)

    async def explodes(event: ev.Event) -> None:
        raise RuntimeError("this subscriber is broken")

    bus.subscribe(ev.TranscriptTurnAdded.name, explodes)

    class FakeContainer:
        def __init__(self, bus: InMemoryEventBus) -> None:
            self.bus = bus

    container = FakeContainer(bus)
    await bus.publish(turn(1, "หนึ่ง"))
    await pump_once(container)  # type: ignore[arg-type]

    await bus.publish(accepted())
    await pump_once(container)  # type: ignore[arg-type]
    assert notifier.sent, "the pump must survive a bad handler and keep delivering"
