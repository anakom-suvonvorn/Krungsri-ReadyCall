"""The agent WebSocket hub: sequencing, replay, and surviving a dead socket.

Driven with a fake socket rather than a test client, because what needs asserting is the
*buffer* behaviour — what a client gets after it was away — and a real socket makes that
harder to see, not easier.
"""

from __future__ import annotations

from typing import Any

import pytest

from readycall.api.realtime import OUTBOX_LIMIT, AgentHub
from readycall.clock import ManualClock


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


class BrokenSocket:
    """A closed laptop: the write raises and nothing else should care."""

    async def send_json(self, data: dict[str, Any]) -> None:
        raise ConnectionResetError("socket is gone")


@pytest.fixture
def hub(clock: ManualClock) -> AgentHub:
    return AgentHub(clock=clock)


async def test_messages_are_sequenced_per_agent(hub: AgentHub) -> None:
    socket = FakeSocket()
    await hub.connect("A001", socket)
    await hub.send("A001", "offer", {"n": 1})
    await hub.send("A001", "presence", {"n": 2})
    assert [m["seq"] for m in socket.sent] == [1, 2]

    # A second agent starts at 1 again: sequences are per channel, not global, so one
    # busy desk cannot push another's numbers into the future.
    other = FakeSocket()
    await hub.connect("A002", other)
    await hub.send("A002", "offer", {"n": 1})
    assert other.sent[0]["seq"] == 1


async def test_a_reconnect_replays_only_what_was_missed(hub: AgentHub) -> None:
    first = FakeSocket()
    await hub.connect("A001", first)
    await hub.send("A001", "offer", {"n": 1})
    await hub.send("A001", "presence", {"n": 2})
    await hub.disconnect("A001", first)

    # ...the laptop sleeps, and two things happen while it is away...
    await hub.send("A001", "offer_revoked", {"n": 3})
    await hub.send("A001", "presence", {"n": 4})

    second = FakeSocket()
    backlog = await hub.connect("A001", second, since_seq=2)
    assert [m["seq"] for m in backlog] == [3, 4], "exactly the gap, in order"
    assert [m["payload"]["n"] for m in backlog] == [3, 4]


async def test_a_message_sent_to_a_dead_socket_is_still_replayable(hub: AgentHub) -> None:
    """The whole reason the outbox is written before delivery is attempted."""
    await hub.connect("A001", BrokenSocket())
    await hub.send("A001", "offer", {"assignment_id": "asgn_1"})
    assert not hub.is_connected("A001"), "the broken socket was dropped"

    replacement = FakeSocket()
    backlog = await hub.connect("A001", replacement, since_seq=0)
    assert len(backlog) == 1
    assert backlog[0]["payload"]["assignment_id"] == "asgn_1"


async def test_the_outbox_is_bounded(hub: AgentHub) -> None:
    """A client away for a whole shift re-fetches the snapshot instead."""
    await hub.connect("A001", FakeSocket())
    for i in range(OUTBOX_LIMIT + 50):
        await hub.send("A001", "presence", {"n": i})

    backlog = await hub.connect("A001", FakeSocket(), since_seq=0)
    assert len(backlog) == OUTBOX_LIMIT
    assert backlog[-1]["seq"] == OUTBOX_LIMIT + 50


async def test_acking_marks_a_message_delivered(hub: AgentHub) -> None:
    socket = FakeSocket()
    await hub.connect("A001", socket)
    await hub.send("A001", "offer", {})
    await hub.send("A001", "presence", {})
    assert len(hub.pending_for("A001")) == 2

    await hub.ack("A001", socket, 1)
    assert [m["seq"] for m in hub.pending_for("A001")] == [2]


async def test_broadcast_reaches_only_agents_with_a_channel(hub: AgentHub) -> None:
    """Otherwise every agent in the roster grows an outbox they never asked for."""
    socket = FakeSocket()
    await hub.connect("A001", socket)
    await hub.broadcast("queues", {"waiting": 3})
    assert socket.sent[-1]["type"] == "queues"
    assert hub.pending_for("A099") == []


async def test_two_tabs_for_one_agent_both_receive(hub: AgentHub) -> None:
    """Agents do open a second tab. Neither should miss a call because of it."""
    first, second = FakeSocket(), FakeSocket()
    await hub.connect("A001", first)
    await hub.connect("A001", second)
    await hub.send("A001", "offer", {})
    assert len(first.sent) == 1
    assert len(second.sent) == 1
    assert first.sent[0]["seq"] == second.sent[0]["seq"], "one sequence, not one per tab"
