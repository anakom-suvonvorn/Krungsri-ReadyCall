"""CallOrchestrator — the single writer of call state.

Every transition is validated against the table, appended to the session's transition
log with a timestamp and a reason, and published as an event. That log is not
bookkeeping: it is what lets the demo *show* "context was ready 1.2 s before the phone
rang" instead of asserting it (`D18`), and what lets the Call Explorer answer "why did
the system do that".

This class does no AI work, talks to no vendor SDK, and makes no matching decisions.
It moves a call through its lifecycle and tells everyone else about it.
"""

from __future__ import annotations

from readycall import ids
from readycall.clock import Clock, Stopwatch
from readycall.domain import events as ev
from readycall.domain.enums import (
    CallState,
    ConsentScope,
    EntryChannel,
    ProductLine,
)
from readycall.domain.models import CallSession, Consent, StateTransition
from readycall.errors import PermanentError
from readycall.logging import call_context, get_logger
from readycall.ports.event_bus import EventBus
from readycall.services.call_orchestrator import machine
from readycall.services.call_orchestrator.repository import CallSessionRepository

log = get_logger(__name__)


class CallOrchestrator:
    def __init__(
        self,
        *,
        repository: CallSessionRepository,
        bus: EventBus,
        clock: Clock,
    ) -> None:
        self._repo = repository
        self._bus = bus
        self._clock = clock

    # --- creation --------------------------------------------------------------------

    async def start_from_intent(
        self,
        *,
        intent_id: str,
        customer_id: str,
        product_code: str | None = None,
        product_line: ProductLine = ProductLine.UNKNOWN,
        trace_id: str | None = None,
    ) -> CallSession:
        """The app path: the customer tapped Contact before dialling (`D6`)."""
        session = CallSession(
            call_session_id=ids.call_session_id(),
            entry_channel=EntryChannel.IN_APP,
            state=CallState.INTENT_CREATED,
            created_at=self._clock.now(),
            trace_id=trace_id or ids.trace_id(),
            intent_id=intent_id,
            customer_id=customer_id,
            product_code=product_code,
            product_line=product_line,
        )
        await self._record_start(session, reason="intent_created")
        await self._bus.publish(
            ev.IntentCreated(
                call_session_id=session.call_session_id,
                occurred_at=session.created_at,
                trace_id=session.trace_id,
                intent_id=intent_id,
                customer_id=customer_id,
                product_code=product_code,
            )
        )
        return session

    async def start_cold_call(
        self,
        *,
        entry_channel: EntryChannel = EntryChannel.HOTLINE,
        telephony_call_id: str | None = None,
        caller_number: str | None = None,
        dialled_did: str | None = None,
        product_line: ProductLine = ProductLine.UNKNOWN,
        provider: str | None = None,
        trace_id: str | None = None,
    ) -> CallSession:
        """The base case: a plain phone call, no app, maybe nobody we recognise (`D19`).

        Note there is no `customer_id` argument. Identity is resolved separately and
        arrives with an assurance level, because an ANI match is a guess (`D20`).
        """
        if entry_channel is EntryChannel.IN_APP:
            raise PermanentError("use start_from_intent() for the in-app channel")
        session = CallSession(
            call_session_id=ids.call_session_id(),
            entry_channel=entry_channel,
            state=CallState.CONNECTING,
            created_at=self._clock.now(),
            trace_id=trace_id or ids.trace_id(),
            telephony_call_id=telephony_call_id,
            caller_number=caller_number,
            dialled_did=dialled_did,
            product_line=product_line,
            provider=provider,
        )
        await self._record_start(session, reason=f"inbound:{entry_channel}")
        await self._bus.publish(
            ev.CallInitiated(
                call_session_id=session.call_session_id,
                occurred_at=session.created_at,
                trace_id=session.trace_id,
                entry_channel=str(entry_channel),
                telephony_call_id=telephony_call_id,
                caller_number=caller_number,
                dialled_did=dialled_did,
            )
        )
        return session

    async def _record_start(self, session: CallSession, *, reason: str) -> None:
        session.transitions = (
            StateTransition(
                from_state=None, to_state=session.state, at=session.created_at, reason=reason
            ),
        )
        await self._repo.save(session)
        log.info(
            "call started",
            call_session_id=session.call_session_id,
            entry_channel=str(session.entry_channel),
            state=str(session.state),
        )

    # --- the one mutator -------------------------------------------------------------

    async def transition(
        self,
        session: CallSession,
        to_state: CallState,
        *,
        reason: str,
        stage_timing: tuple[str, float] | None = None,
    ) -> CallSession:
        """Move a call. The only place `session.state` is ever assigned."""
        machine.assert_can(session.state, to_state)
        now = self._clock.now()
        from_state = session.state

        session.state = to_state
        session.transitions = (
            *session.transitions,
            StateTransition(from_state=from_state, to_state=to_state, at=now, reason=reason),
        )

        # Lifecycle timestamps that the metrics layer later depends on.
        if to_state is CallState.QUEUED and session.queued_at is None:
            session.queued_at = now
        elif to_state is CallState.IN_CALL and session.answered_at is None:
            session.answered_at = now
        elif machine.is_terminal(to_state):
            session.ended_at = now
            session.end_reason = reason

        if stage_timing is not None:
            session.record_timing(*stage_timing)

        await self._repo.save(session)

        with call_context(session.call_session_id, trace_id=session.trace_id):
            log.info(
                "call state changed",
                from_state=str(from_state),
                to_state=str(to_state),
                reason=reason,
            )

        await self._bus.publish(
            ev.CallStateChanged(
                call_session_id=session.call_session_id,
                occurred_at=now,
                trace_id=session.trace_id,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
            )
        )

        if machine.is_terminal(to_state):
            await self._bus.publish(
                ev.CallEnded(
                    call_session_id=session.call_session_id,
                    occurred_at=now,
                    trace_id=session.trace_id,
                    end_reason=reason,
                    duration_s=(now - session.created_at).total_seconds(),
                )
            )
        return session

    # --- convenience steps -----------------------------------------------------------

    async def enter_ivr(self, session: CallSession, *, reason: str = "ivr_started") -> CallSession:
        return await self.transition(session, CallState.IVR, reason=reason)

    async def enqueue(
        self,
        session: CallSession,
        *,
        queue_id: str,
        position: int | None = None,
        estimated_wait_s: float | None = None,
        reason: str = "queued",
    ) -> CallSession:
        session.queue_id = queue_id
        session = await self.transition(session, CallState.QUEUED, reason=reason)
        await self._bus.publish(
            ev.CallQueued(
                call_session_id=session.call_session_id,
                occurred_at=self._clock.now(),
                trace_id=session.trace_id,
                queue_id=queue_id,
                position=position,
                estimated_wait_s=estimated_wait_s,
            )
        )
        return session

    async def record_consent(
        self,
        session: CallSession,
        *,
        scope: ConsentScope,
        granted: bool,
        basis: str,
        channel: str = "ivr",
    ) -> CallSession:
        """No consent, no intake — and the call proceeds regardless (`D14`)."""
        now = self._clock.now()
        session.consents = (
            *session.consents,
            Consent(scope=scope, granted=granted, granted_at=now, basis=basis, channel=channel),
        )
        await self._repo.save(session)
        await self._bus.publish(
            ev.ConsentRecorded(
                call_session_id=session.call_session_id,
                occurred_at=now,
                trace_id=session.trace_id,
                scope=scope,
                granted=granted,
                basis=basis,
            )
        )
        return session

    async def abandon(self, session: CallSession, *, reason: str = "caller_hung_up") -> CallSession:
        return await self.transition(session, CallState.ABANDONED, reason=reason)

    async def fail(self, session: CallSession, *, reason: str) -> CallSession:
        return await self.transition(session, CallState.FAILED, reason=reason)

    # --- inspection ------------------------------------------------------------------

    def stopwatch(self) -> Stopwatch:
        return Stopwatch(self._clock)

    @staticmethod
    def timeline(session: CallSession) -> list[str]:
        """Human-readable state timeline. Printed by the scenario runner (`D18`)."""
        out: list[str] = []
        first = session.transitions[0].at if session.transitions else session.created_at
        for transition in session.transitions:
            offset = (transition.at - first).total_seconds()
            arrow = (
                f"{transition.from_state} -> {transition.to_state}"
                if transition.from_state
                else f"(start) {transition.to_state}"
            )
            out.append(f"+{offset:7.2f}s  {arrow:<44} {transition.reason}")
        return out
