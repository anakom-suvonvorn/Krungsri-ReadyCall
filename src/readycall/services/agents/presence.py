"""Agent presence: two axes, one writer each, and a log of every move.

The model is `D33`'s and the discipline is `D45`'s:

* **`system_state`** is the platform's axis — `OFFLINE`, `AVAILABLE`, `OFFERING`,
  `ON_CALL`, `AFTER_CALL_WORK`. It describes what the call platform has given this
  person to do. Only this service moves it, and only in response to a call event.
* **`agent_intent`** is the person's axis — what *they* say they are doing. The platform
  writes it in exactly two places, both marked `set_by="platform"` in the log (`D51`).

Two rules here are load-bearing and easy to "simplify" back into bugs:

1. **After-call work ends when the agent declares any next state** — Ready, Break, Lunch,
   Training, Admin — never when a form is saved and never on a timer (`D45`). An agent
   who ends a call and goes to lunch has finished their after-call work; they are simply
   not available. Ending ACW only on *Ready* would show them in wrap-up for an hour.
2. **The platform never auto-readies.** A timer here is a *visibility* device only: past
   a threshold it raises a long-ACW indicator. It writes nothing. Marking someone
   available while they are mid-task elsewhere costs a real caller a full offer timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from readycall.clock import Clock
from readycall.domain import events as ev
from readycall.domain.enums import AgentIntent, AgentSystemState
from readycall.domain.models import AgentPresence, AgentStateChange
from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.ports.event_bus import EventBus

log = get_logger(__name__)

#: Intents an agent may declare for themselves. `NOT_READY` is absent on purpose: a
#: person saying "I am not ready" is really saying break/lunch/admin, and collapsing
#: those into one value throws away the only thing workforce planning wants from it.
DECLARABLE: frozenset[AgentIntent] = frozenset(
    {
        AgentIntent.READY,
        AgentIntent.BREAK,
        AgentIntent.LUNCH,
        AgentIntent.TRAINING,
        AgentIntent.ADMIN,
        AgentIntent.LAST_CALL,
        AgentIntent.DRAINING,
    }
)

#: The subset that still means something **while a call is in progress** (`D59`).
#:
#: `agent_intent` is a **standing instruction**, not a momentary status — "keep sending me
#: calls", "this one, then stop", "no new ones". Read that way, only the forward-looking
#: three can change mid-call: an agent may well decide halfway through a conversation that
#: this is their last. `BREAK` / `LUNCH` / `TRAINING` / `ADMIN` describe what you are doing
#: *now*, and what you are doing now is talking to a customer.
DECLARABLE_ON_CALL: frozenset[AgentIntent] = frozenset(
    {AgentIntent.READY, AgentIntent.LAST_CALL, AgentIntent.DRAINING}
)

#: Display order for the status control. Kept beside the sets that filter it so the screen
#: and the rules cannot drift apart.
MENU_ORDER: tuple[AgentIntent, ...] = (
    AgentIntent.READY,
    AgentIntent.BREAK,
    AgentIntent.LUNCH,
    AgentIntent.TRAINING,
    AgentIntent.ADMIN,
    AgentIntent.LAST_CALL,
    AgentIntent.DRAINING,
)


@dataclass(frozen=True, slots=True)
class PresenceView:
    """What the workstation renders, and what matching reads."""

    presence: AgentPresence
    offerable: bool
    acw_seconds: float | None = None
    long_acw: bool = False
    #: When after-call work started. The client ticks its own timer from this anchor
    #: rather than waiting for a push, so the number moves every second instead of
    #: whenever something else happens to refresh the snapshot.
    acw_since: datetime | None = None
    #: Which intents may be declared **right now**. Computed here so the workstation
    #: renders permissions rather than guessing them — the same reason `offerable` is
    #: derived server-side and not recomputed in React.
    declarable: tuple[AgentIntent, ...] = ()
    #: True while in after-call work: the standing instruction is still whatever it was,
    #: but the agent owes us a declaration before they are available again (`D45`). The
    #: screen uses this to stop showing the old instruction as though it were current,
    #: which is what made the status control look broken after saving a wrap-up.
    awaiting_declaration: bool = False
    #: Why the current intent is what it is — the `reason` of the state-log row that set
    #: it. `NOT_READY` alone is ambiguous: it means "just signed in", "missed an offer"
    #: (RONA) and "your last call is finished" (`D59`), and those want three different
    #: things on screen. Without this the workstation cannot tell them apart.
    intent_reason: str = ""


class PresenceService:
    """Single writer of agent presence, mirroring `CallOrchestrator` for calls."""

    def __init__(
        self,
        *,
        clock: Clock,
        bus: EventBus,
        heartbeat_ttl_s: float = 30.0,
        long_acw_after_s: float = 45.0,
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._ttl_s = heartbeat_ttl_s
        self._long_acw_after_s = long_acw_after_s
        self._presence: dict[str, AgentPresence] = {}
        #: Durable in spirit; a list until the DB lands (`D39`). Append-only, on purpose.
        self._log: list[AgentStateChange] = []
        #: When the media disconnected, per agent. ACW is measured from here (`D45`),
        #: not from any form action.
        self._acw_since: dict[str, datetime] = {}

    # --- reading ----------------------------------------------------------------------

    def get(self, agent_id: str) -> AgentPresence | None:
        return self._presence.get(agent_id)

    def snapshot(self) -> dict[str, AgentPresence]:
        """What the matching engine consumes. A copy: matching must not mutate presence."""
        return dict(self._presence)

    def state_log(self, agent_id: str | None = None) -> list[AgentStateChange]:
        if agent_id is None:
            return list(self._log)
        return [row for row in self._log if row.agent_id == agent_id]

    def view(self, agent_id: str) -> PresenceView | None:
        presence = self._presence.get(agent_id)
        if presence is None:
            return None
        acw = self.acw_elapsed_s(agent_id)
        return PresenceView(
            presence=presence,
            offerable=(
                presence.system_state is AgentSystemState.AVAILABLE and presence.accepts_new_callers
            ),
            acw_seconds=acw,
            long_acw=acw is not None and acw >= self._long_acw_after_s,
            acw_since=self._acw_since.get(agent_id),
            declarable=self.declarable_intents(agent_id),
            awaiting_declaration=presence.in_after_call_work,
            intent_reason=self._intent_reason(agent_id),
        )

    def _intent_reason(self, agent_id: str) -> str:
        """The reason from the newest state-log row that moved this agent's intent."""
        current = self._presence.get(agent_id)
        if current is None:
            return ""
        for row in reversed(self._log):
            if row.agent_id == agent_id and row.agent_intent is current.agent_intent:
                return row.reason
        return ""

    def declarable_intents(self, agent_id: str) -> tuple[AgentIntent, ...]:
        """What this agent may declare at this moment, in menu order (`D59`)."""
        presence = self._presence.get(agent_id)
        if presence is None or presence.system_state is AgentSystemState.OFFLINE:
            return ()
        allowed = (
            DECLARABLE_ON_CALL
            if presence.system_state in {AgentSystemState.ON_CALL, AgentSystemState.OFFERING}
            else DECLARABLE
        )
        return tuple(intent for intent in MENU_ORDER if intent in allowed)

    def acw_elapsed_s(self, agent_id: str) -> float | None:
        started = self._acw_since.get(agent_id)
        if started is None:
            return None
        return (self._clock.now() - started).total_seconds()

    # --- the person's axis -------------------------------------------------------------

    async def sign_in(self, agent_id: str, *, session_id: str) -> AgentPresence:
        """Open the workstation. Signed in is **not** ready (`D51`).

        Starting an agent as READY would hand a call to someone who just opened the tab
        and is making coffee. The workstation shows a prominent Ready button instead;
        declaring is one click and is the person's own statement.
        """
        now = self._clock.now()
        presence = AgentPresence(
            agent_id=agent_id,
            system_state=AgentSystemState.AVAILABLE,
            agent_intent=AgentIntent.NOT_READY,
            since=now,
            current_load=0,
            session_id=session_id,
            heartbeat_at=now,
        )
        await self._commit(presence, set_by="platform", reason="signed_in")
        return presence

    async def sign_out(self, agent_id: str, *, reason: str = "signed_out") -> AgentPresence:
        presence = self._require(agent_id)
        if presence.system_state is AgentSystemState.ON_CALL:
            # Closing the tab mid-call does not end the call; the media leg is Asterisk's
            # business. Refusing here keeps presence from lying about a live conversation.
            raise PermanentError(f"agent {agent_id} cannot sign out while on a call")
        updated = presence.model_copy(
            update={
                "system_state": AgentSystemState.OFFLINE,
                "since": self._clock.now(),
                "session_id": None,
            }
        )
        self._acw_since.pop(agent_id, None)
        await self._commit(updated, set_by="agent", reason=reason)
        return updated

    async def declare(
        self,
        agent_id: str,
        intent: AgentIntent,
        *,
        reason: str = "agent_declared",
    ) -> AgentPresence:
        """The person says what they are doing next. This is also what ends ACW (`D45`)."""
        if intent not in DECLARABLE:
            raise PermanentError(f"{intent} is not an intent an agent may declare")
        presence = self._require(agent_id)
        if presence.system_state is AgentSystemState.OFFLINE:
            raise PermanentError(f"agent {agent_id} is offline; sign in first")
        if intent not in self.declarable_intents(agent_id):
            # Mid-call only the forward-looking instructions mean anything (`D59`).
            raise PermanentError(f"{intent} cannot be declared while {presence.system_state}")

        update: dict[str, object] = {"agent_intent": intent, "since": self._clock.now()}
        acw_seconds: float | None = None
        if presence.in_after_call_work:
            # ANY declaration ends after-call work, not only Ready (`D45`). Lunch ends
            # ACW *and* leaves them un-offerable - two axes, no special case.
            acw_seconds = self.acw_elapsed_s(agent_id)
            self._acw_since.pop(agent_id, None)
            update["system_state"] = AgentSystemState.AVAILABLE

        updated = presence.model_copy(update=update)
        await self._commit(updated, set_by="agent", reason=reason, acw_seconds=acw_seconds)
        return updated

    # --- the platform's axis ------------------------------------------------------------

    async def begin_offer(self, agent_id: str, *, call_session_id: str) -> AgentPresence:
        return await self._platform_move(
            agent_id,
            AgentSystemState.OFFERING,
            reason="offered",
            call_session_id=call_session_id,
        )

    async def begin_call(self, agent_id: str, *, call_session_id: str) -> AgentPresence:
        presence = self._require(agent_id)
        updated = presence.model_copy(
            update={
                "system_state": AgentSystemState.ON_CALL,
                "since": self._clock.now(),
                "current_load": presence.current_load + 1,
            }
        )
        await self._commit(
            updated, set_by="platform", reason="accepted", call_session_id=call_session_id
        )
        return updated

    async def begin_after_call_work(
        self, agent_id: str, *, call_session_id: str, disconnected_at: datetime | None = None
    ) -> AgentPresence:
        """Media disconnected. The ACW clock starts **here** (`D45`), not at a form."""
        presence = self._require(agent_id)
        self._acw_since[agent_id] = disconnected_at or self._clock.now()
        update: dict[str, object] = {
            "system_state": AgentSystemState.AFTER_CALL_WORK,
            "since": self._acw_since[agent_id],
            "current_load": max(presence.current_load - 1, 0),
        }
        fulfilled = presence.agent_intent is AgentIntent.LAST_CALL
        if fulfilled:
            # `LAST_CALL` is the one standing instruction with a built-in end condition:
            # "finish the current call, THEN stop taking new ones" (`D59`). That call has
            # just ended, so the instruction is spent. It becomes `NOT_READY` rather than
            # any concrete state, because the platform still must not assert what the
            # person is doing (`D45`) — only that they are no longer asking for calls.
            update["agent_intent"] = AgentIntent.NOT_READY
        updated = presence.model_copy(update=update)
        await self._commit(
            updated,
            set_by="platform",
            reason="last_call_fulfilled" if fulfilled else "media_disconnected",
            call_session_id=call_session_id,
        )
        return updated

    async def release_offer(self, agent_id: str, *, reason: str) -> AgentPresence:
        """The offer ended without a call — declined, timed out, or the caller hung up."""
        return await self._platform_move(agent_id, AgentSystemState.AVAILABLE, reason=reason)

    async def mark_not_responding(self, agent_id: str, *, call_session_id: str) -> AgentPresence:
        """RONA (`D33`): the offer timed out, so stop offering to this desk.

        The platform writes the *person's* axis here — the narrow exception in `D51`. It
        is logged as `set_by="platform"` so a supervisor can tell "they chose break" from
        "we stopped offering because nobody picked up", which are very different facts
        about the same person.
        """
        presence = self._require(agent_id)
        updated = presence.model_copy(
            update={
                "system_state": AgentSystemState.AVAILABLE,
                "agent_intent": AgentIntent.NOT_READY,
                "since": self._clock.now(),
            }
        )
        await self._commit(
            updated,
            set_by="platform",
            reason="rona_missed_offer",
            call_session_id=call_session_id,
        )
        return updated

    # --- heartbeat ----------------------------------------------------------------------

    async def heartbeat(self, agent_id: str) -> None:
        presence = self._presence.get(agent_id)
        if presence is None:
            return
        # Deliberately not logged and not published: a heartbeat is not a state change,
        # and at one per agent per few seconds it would drown the log it lives in.
        self._presence[agent_id] = presence.model_copy(update={"heartbeat_at": self._clock.now()})

    async def sweep(self) -> list[str]:
        """Drop agents whose heartbeat has gone stale. A closed laptop signs itself out.

        Returns the ids that went offline, so the caller can decide what to do about any
        call they were holding — this service will not guess.
        """
        now = self._clock.now()
        cutoff = now - timedelta(seconds=self._ttl_s)
        dropped: list[str] = []
        for agent_id, presence in list(self._presence.items()):
            if presence.system_state is AgentSystemState.OFFLINE:
                continue
            last = presence.heartbeat_at or presence.since
            if last < cutoff:
                updated = presence.model_copy(
                    update={
                        "system_state": AgentSystemState.OFFLINE,
                        "since": now,
                        "session_id": None,
                    }
                )
                self._acw_since.pop(agent_id, None)
                await self._commit(updated, set_by="platform", reason="heartbeat_expired")
                dropped.append(agent_id)
        return dropped

    # --- internals -----------------------------------------------------------------------

    def _require(self, agent_id: str) -> AgentPresence:
        presence = self._presence.get(agent_id)
        if presence is None:
            raise PermanentError(f"agent {agent_id} has no presence record; sign in first")
        return presence

    async def _platform_move(
        self,
        agent_id: str,
        state: AgentSystemState,
        *,
        reason: str,
        call_session_id: str | None = None,
    ) -> AgentPresence:
        presence = self._require(agent_id)
        updated = presence.model_copy(update={"system_state": state, "since": self._clock.now()})
        await self._commit(
            updated, set_by="platform", reason=reason, call_session_id=call_session_id
        )
        return updated

    async def _commit(
        self,
        presence: AgentPresence,
        *,
        set_by: str,
        reason: str,
        call_session_id: str | None = None,
        acw_seconds: float | None = None,
    ) -> None:
        self._presence[presence.agent_id] = presence
        self._log.append(
            AgentStateChange(
                agent_id=presence.agent_id,
                at=self._clock.now(),
                system_state=presence.system_state,
                agent_intent=presence.agent_intent,
                set_by=set_by,
                reason=reason,
                call_session_id=call_session_id,
                acw_seconds=acw_seconds,
            )
        )
        log.info(
            "agent presence",
            agent_id=presence.agent_id,
            system_state=str(presence.system_state),
            agent_intent=str(presence.agent_intent),
            set_by=set_by,
            reason=reason,
        )
        await self._bus.publish(
            ev.AgentPresenceChanged(
                agent_id=presence.agent_id,
                occurred_at=presence.since,
                system_state=str(presence.system_state),
                agent_intent=str(presence.agent_intent),
            )
        )


__all__ = ["DECLARABLE", "DECLARABLE_ON_CALL", "MENU_ORDER", "PresenceService", "PresenceView"]
