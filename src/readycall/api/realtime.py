"""The agent WebSocket hub: one socket per signed-in workstation.

Three properties matter more than the transport details.

**Every message is sequenced, per agent.** An offer that arrives out of order, or twice,
is a call ringing at a desk that already declined it. Sequence numbers make the client's
job trivial — apply anything newer than what you have, ignore the rest.

**Reconnect replays rather than resyncs.** Wi-Fi drops. A laptop sleeps. When the socket
comes back the client sends the last sequence it saw and gets everything after it, in
order, from a bounded outbox. The alternative — "reconnect, then re-fetch everything" —
loses precisely the events that happened during the gap, which is when the interesting
ones happen.

**The hub never decides anything.** It is a fan-out with a memory. Presence, offers and
briefs are decided by their services; if this module ever grows an `if` about call state,
that logic is in the wrong place.

Nothing here is call-state-bearing. If the socket is down when an offer is made, the
offer still exists, still times out, and still re-matches — the workstation is a *view*
of the system, never the system itself. That is also what makes `D32` safe: a browser is
allowed to be flaky.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from readycall.clock import Clock
from readycall.logging import get_logger

log = get_logger(__name__)

#: Per-agent replay buffer. Sized for a long reconnect, not for history — anything older
#: than this is recovered by re-fetching the workstation snapshot over REST.
OUTBOX_LIMIT = 200


class WebSocketLike(Protocol):
    """Only what the hub uses, so tests need no FastAPI test client."""

    async def send_json(self, data: dict[str, Any]) -> None: ...


@dataclass
class _Connection:
    socket: WebSocketLike
    #: Highest sequence the client has acknowledged. Everything after it is replayable.
    acked_seq: int = 0


@dataclass
class _AgentChannel:
    seq: int = 0
    outbox: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=OUTBOX_LIMIT))
    connections: list[_Connection] = field(default_factory=list)


class AgentHub:
    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._channels: dict[str, _AgentChannel] = {}
        self._lock = asyncio.Lock()

    # --- connection lifecycle -----------------------------------------------------------

    async def connect(
        self, agent_id: str, socket: WebSocketLike, *, since_seq: int = 0
    ) -> list[dict[str, Any]]:
        """Register a socket and return the backlog it missed, oldest first."""
        async with self._lock:
            channel = self._channels.setdefault(agent_id, _AgentChannel())
            channel.connections.append(_Connection(socket=socket, acked_seq=since_seq))
            backlog = [m for m in channel.outbox if m["seq"] > since_seq]
        if backlog:
            log.info("agent reconnected with a gap", agent_id=agent_id, replayed=len(backlog))
        return backlog

    async def disconnect(self, agent_id: str, socket: WebSocketLike) -> None:
        async with self._lock:
            channel = self._channels.get(agent_id)
            if channel is None:
                return
            channel.connections = [c for c in channel.connections if c.socket is not socket]

    async def reset(self, agent_id: str) -> None:
        """A new sign-in starts a new sequence (`B27`).

        The outbox exists so a **reconnect** can replay the gap (`D68`). A sign-in is not
        a reconnect: whatever is in there belongs to a previous session at this desk, and
        replaying it hands the new arrival somebody else's offers to re-read.

        It is also what makes the client's own reset safe. That side has to happen — the
        client's `lastSeq` is per tab while `seq` is per agent, so without it the second
        agent to use a tab silently drops every message below the first agent's high-water
        mark. Resetting both keeps the two counters describing the same thing.
        """
        async with self._lock:
            channel = self._channels.get(agent_id)
            if channel is None:
                return
            channel.seq = 0
            channel.outbox.clear()
            # Connections are deliberately left alone: the socket may already be open and
            # is about to be told, and closing it here would make a sign-in look like a
            # network failure to the client's backoff.
            for connection in channel.connections:
                connection.acked_seq = 0

    def is_connected(self, agent_id: str) -> bool:
        channel = self._channels.get(agent_id)
        return bool(channel and channel.connections)

    def connected_agents(self) -> list[str]:
        return [a for a, c in self._channels.items() if c.connections]

    async def ack(self, agent_id: str, socket: WebSocketLike, seq: int) -> None:
        async with self._lock:
            channel = self._channels.get(agent_id)
            if channel is None:
                return
            for connection in channel.connections:
                if connection.socket is socket:
                    connection.acked_seq = max(connection.acked_seq, seq)

    # --- sending -------------------------------------------------------------------------

    async def send(self, agent_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Queue a message for one agent and try to deliver it now.

        The message is recorded in the outbox **whether or not** delivery succeeds, which
        is the entire point: a message that failed to send is exactly the one a
        reconnecting client needs replayed.
        """
        async with self._lock:
            channel = self._channels.setdefault(agent_id, _AgentChannel())
            channel.seq += 1
            message = {
                "seq": channel.seq,
                "type": kind,
                "at": self._clock.now().isoformat(),
                "payload": payload,
            }
            channel.outbox.append(message)
            targets = list(channel.connections)

        dead: list[_Connection] = []
        for connection in targets:
            try:
                await connection.socket.send_json(message)
            except Exception as exc:
                # Not an error: a closed laptop, a walked-away agent, a proxy timeout. The
                # message stays in the outbox and the presence heartbeat sweep is what
                # decides the agent is gone (`D51`), not a failed write here.
                log.info(
                    "agent socket send failed",
                    agent_id=agent_id,
                    kind=kind,
                    error=type(exc).__name__,
                )
                dead.append(connection)

        if dead:
            async with self._lock:
                surviving = self._channels.get(agent_id)
                if surviving is not None:
                    surviving.connections = [c for c in surviving.connections if c not in dead]
        return message

    async def broadcast(self, kind: str, payload: dict[str, Any]) -> None:
        """To every agent with a channel — queue-strip updates, wallboard numbers.

        Only to agents who already have a channel: broadcasting would otherwise create an
        outbox for every agent in the roster who has never signed in, and a reconnect
        would replay a shift's worth of queue counters at them.
        """
        for agent_id in list(self._channels):
            await self.send(agent_id, kind, payload)

    def pending_for(self, agent_id: str) -> list[dict[str, Any]]:
        """Messages sent but not acknowledged by any live connection. For diagnostics."""
        channel = self._channels.get(agent_id)
        if channel is None:
            return []
        floor = max((c.acked_seq for c in channel.connections), default=0)
        return [m for m in channel.outbox if m["seq"] > floor]


__all__ = ["OUTBOX_LIMIT", "AgentHub", "WebSocketLike"]
