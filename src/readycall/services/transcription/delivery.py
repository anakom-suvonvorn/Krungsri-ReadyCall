"""Getting the transcript from the bus onto the agent's screen (`D106`).

Everything upstream of this file has existed for a while and none of it reached a browser.
The turns are produced by `services/transcription/`, handed to `IntakeService.on_turn`,
given to the intake strategy, and published by `PassiveRecordIntake` as `transcript.turn`.
`ARCHITECTURE` §14 says that event is consumed by *Analysis, Agent Delivery, call-progress*.
**Agent Delivery is this.**

### The part that is not plumbing

**During intake there is no agent to send to.** That is the whole point of the product: the
transcript is built *while the caller waits*, before anybody has accepted, so at the moment
a turn is published there is no `agent_id` for `AgentHub.send` to address. So this service
holds the call's turns and delivers them when an agent is actually assigned — the same
shape as `D69`'s gated brief preview, which also had to wait for somebody to send it to.

**The flush is on ACCEPT, not on OFFER**, and that is a deliberate line. An offer can be
declined or time out, and the call is then re-matched to somebody else (`D52`). An agent
who declines would have read the caller's words verbatim for a call they never took. The
offer card keeps the *summary* it already has (`D69`); the verbatim transcript waits for
the person who is actually going to be on the call.

### It sends the whole transcript every time

Not a delta. `D68` is the rule — if the server knows it, the server says it — and the
failure it is guarding against is a client that accumulates: a dropped message, a replay
that lands out of order, a reconnect past the 200-message outbox, and the tab is now
showing a transcript that is missing a sentence in the middle with nothing to indicate it.
A complete payload is idempotent, needs no gap handling, and makes the newest message the
only one that matters. A transcript is a few hundred bytes a turn; this is cheap.

### It is NOT gated on assurance

`D74` gates what the agent may **say and do**, not what they may see, and `D53`/`B5` are
about the customer's *record* — a policy number the caller never mentioned. This is the
caller's own words on the call the agent is about to take, at whatever assurance they have
reached, including L0. An anonymous cold caller explaining their problem while they wait is
precisely the case the product exists for; withholding it would delete the feature to
protect nothing. What must never appear here is anything *looked up*, and nothing here is.
"""

from __future__ import annotations

from typing import Any

from readycall.domain import events as ev
from readycall.domain.enums import TERMINAL_STATES, OfferOutcome
from readycall.logging import get_logger
from readycall.ports.event_bus import EventBus
from readycall.services.agents.dispatch import AgentNotifier

log = get_logger(__name__)

#: The socket message type. One kind, whether it is the flush on accept or a live turn
#: after it, because the payload is complete in both cases and the client's handling is
#: therefore identical.
TRANSCRIPT_MESSAGE = "transcript"

#: A backstop, not an expected limit: intake is bounded by `INTAKE_MAX_DURATION_S`, and a
#: live call is bounded by the call. 500 turns is something like 17 minutes of continuous
#: speech. Past it the OLDEST turns go, which is the opposite of `D100`'s choice for the
#: audio backlog and for the opposite reason — there, the oldest sentence was one nobody
#: had seen yet; here, it is one the agent has already read on screen.
MAX_TURNS_PER_CALL = 500


class TranscriptDeliveryService:
    """Bus in, `AgentHub` out. Holds one list of turns per live call."""

    def __init__(self, *, notifier: AgentNotifier) -> None:
        self._notifier = notifier
        #: call_session_id -> the turns so far, oldest first, as wire payloads.
        #: The LIVE read path, and only that. Since `D114` the durable copy is
        #: `transcript_turns`, written per turn by `TranscriptRecorder` — a separate
        #: subscriber on purpose, so a storage failure cannot reach this screen (`D12`).
        #: A restart still loses *this* list, and no longer loses the transcript.
        self._turns: dict[str, list[dict[str, Any]]] = {}
        #: call_session_id -> the agent it was accepted by. Absent means nobody yet, which
        #: is the normal state for the whole of intake.
        self._agent_for_call: dict[str, str] = {}
        self._truncated: set[str] = set()

    # --- wiring -------------------------------------------------------------------------

    def subscribe(self, bus: EventBus) -> None:
        """Register the three handlers. Called once, by the container.

        The topics live here rather than in `deps.py` so that adding a fourth is a change
        to this file only — and so a reader of this service can see everything that drives
        it without going and looking somewhere else, which is the failure `B7` is about.
        """
        bus.subscribe(ev.TranscriptTurnAdded.name, self._on_turn)
        bus.subscribe(ev.OfferResolved.name, self._on_offer_resolved)
        bus.subscribe(ev.CallStateChanged.name, self._on_state_changed)

    # --- handlers -----------------------------------------------------------------------

    async def _on_turn(self, event: ev.Event) -> None:
        if not isinstance(event, ev.TranscriptTurnAdded):  # pragma: no cover - topic guard
            return
        call_session_id = event.call_session_id
        turns = self._turns.setdefault(call_session_id, [])
        turns.append(
            {
                "turn_id": event.turn_id,
                "seq": event.seq,
                "speaker_role": event.speaker_role,
                "text": event.text,
                "t_start_ms": event.t_start_ms,
                "t_end_ms": event.t_end_ms,
                "asr_confidence": event.asr_confidence,
            }
        )
        if len(turns) > MAX_TURNS_PER_CALL:
            del turns[: len(turns) - MAX_TURNS_PER_CALL]
            if call_session_id not in self._truncated:
                self._truncated.add(call_session_id)
                log.warning(
                    "transcript exceeded the per-call cap; dropping the oldest turns",
                    call_session_id=call_session_id,
                    cap=MAX_TURNS_PER_CALL,
                )

        # Live only once somebody owns the call. Before that there is nowhere to send it,
        # and that is the normal case for the whole of intake rather than an edge.
        agent_id = self._agent_for_call.get(call_session_id)
        if agent_id is not None:
            await self._push(agent_id, call_session_id)

    async def _on_offer_resolved(self, event: ev.Event) -> None:
        if not isinstance(event, ev.OfferResolved):  # pragma: no cover - topic guard
            return
        if event.outcome is not OfferOutcome.ACCEPTED:
            # Declined, timed out or cancelled. The call goes back to the pool and may
            # reach a different desk (`D52`); the turns stay held for whoever takes it.
            return
        self._agent_for_call[event.call_session_id] = event.agent_id
        await self._push(event.agent_id, event.call_session_id)

    async def _on_state_changed(self, event: ev.Event) -> None:
        if not isinstance(event, ev.CallStateChanged):  # pragma: no cover - topic guard
            return
        if event.to_state not in TERMINAL_STATES:
            return
        # `WRAP_UP` is deliberately NOT here: after-call work is when an agent writes the
        # summary, and the transcript is what they write it from (`ARCHITECTURE` §12).
        # Dropping it at hang-up would clear the panel exactly when it is most useful.
        self._forget(event.call_session_id)

    # --- sending ------------------------------------------------------------------------

    async def _push(self, agent_id: str, call_session_id: str) -> None:
        await self._notifier.send(
            agent_id,
            TRANSCRIPT_MESSAGE,
            {
                "call_session_id": call_session_id,
                # Complete, every time. See the module docstring.
                "turns": list(self._turns.get(call_session_id, ())),
            },
        )

    def _forget(self, call_session_id: str) -> None:
        self._turns.pop(call_session_id, None)
        self._agent_for_call.pop(call_session_id, None)
        self._truncated.discard(call_session_id)

    # --- read side, for the REST snapshot -----------------------------------------------

    def turns_for(self, call_session_id: str | None) -> tuple[dict[str, Any], ...]:
        """What the workstation snapshot carries, so a cold load is not blank.

        The socket is a *view* and is allowed to be flaky (`D32`); a tab that opens
        mid-call must not have to wait for the next utterance to show anything. Same
        content, same shape, one source.
        """
        if call_session_id is None:
            return ()
        return tuple(self._turns.get(call_session_id, ()))

    def live_call_ids(self) -> tuple[str, ...]:
        return tuple(self._turns)


__all__ = [
    "MAX_TURNS_PER_CALL",
    "TRANSCRIPT_MESSAGE",
    "TranscriptDeliveryService",
]
