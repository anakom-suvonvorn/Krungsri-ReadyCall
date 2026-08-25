"""Postgres implementations of the store seams, behind the interfaces P0 already had.

**These return domain models, never ORM rows** (`D77`). The same rule adapters follow at
the port boundary: everything above this package deals in `CallSession` and
`AgentStateChange`, so swapping the store changes one factory line and nothing else. An
ORM row escaping into a service would drag a session lifetime and a lazy-load along with
it, and the first place that breaks is a background sweep whose session has closed.

The mapping is written by hand rather than generated. It is the one place where the
database shape and the domain shape are allowed to differ, so it is worth being able to
read it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from readycall.db.models import AgentStateLogRow, CallSessionRow, CallStateTransitionRow
from readycall.db.session import session_scope
from readycall.domain.enums import (
    AgentIntent,
    AgentSystemState,
    CallState,
    EntryChannel,
    Language,
    ProductLine,
)
from readycall.domain.models import (
    AgentStateChange,
    CallSession,
    Consent,
    IdentityResolution,
    StateTransition,
)
from readycall.logging import get_logger
from readycall.services.agents.presence import InMemoryAgentStateLog

log = get_logger(__name__)


def _session_to_row(session: CallSession, row: CallSessionRow) -> CallSessionRow:
    row.call_session_id = session.call_session_id
    row.entry_channel = str(session.entry_channel)
    row.state = str(session.state)
    row.created_at = session.created_at
    row.trace_id = session.trace_id
    row.intent_id = session.intent_id
    row.customer_id = session.customer_id
    row.caller_number = session.caller_number
    row.dialled_did = session.dialled_did
    row.product_line = str(session.product_line)
    row.product_code = session.product_code
    row.telephony_call_id = session.telephony_call_id
    row.provider = session.provider
    row.preferred_language = str(session.preferred_language)
    row.acceptable_languages = [str(x) for x in session.acceptable_languages]
    row.menu_path = list(session.menu_path)
    row.menu_intent_code = session.menu_intent_code
    row.queue_id = session.queue_id
    row.priority = session.priority
    row.queued_at = session.queued_at
    row.answered_at = session.answered_at
    row.ended_at = session.ended_at
    row.end_reason = session.end_reason
    row.snapshot_id = session.snapshot_id
    row.intake_id = session.intake_id
    row.brief_version = session.brief_version
    row.assigned_agent_id = session.assigned_agent_id
    row.identity = session.identity.model_dump(mode="json") if session.identity else None
    row.consents = [c.model_dump(mode="json") for c in session.consents]
    row.stage_timings_ms = dict(session.stage_timings_ms)
    return row


def _row_to_session(row: CallSessionRow) -> CallSession:
    return CallSession(
        call_session_id=row.call_session_id,
        entry_channel=EntryChannel(row.entry_channel),
        state=CallState(row.state),
        created_at=row.created_at,
        trace_id=row.trace_id,
        intent_id=row.intent_id,
        customer_id=row.customer_id,
        identity=IdentityResolution.model_validate(row.identity) if row.identity else None,
        caller_number=row.caller_number,
        dialled_did=row.dialled_did,
        product_line=ProductLine(row.product_line),
        product_code=row.product_code,
        telephony_call_id=row.telephony_call_id,
        provider=row.provider,
        preferred_language=Language(row.preferred_language),
        acceptable_languages=tuple(Language(x) for x in row.acceptable_languages),
        menu_path=tuple(row.menu_path),
        menu_intent_code=row.menu_intent_code,
        queue_id=row.queue_id,
        priority=row.priority,
        queued_at=row.queued_at,
        answered_at=row.answered_at,
        ended_at=row.ended_at,
        end_reason=row.end_reason,
        snapshot_id=row.snapshot_id,
        intake_id=row.intake_id,
        brief_version=row.brief_version,
        assigned_agent_id=row.assigned_agent_id,
        consents=tuple(Consent.model_validate(c) for c in row.consents),
        transitions=tuple(
            StateTransition(
                from_state=CallState(t.from_state) if t.from_state else None,
                to_state=CallState(t.to_state),
                at=t.at,
                reason=t.reason,
            )
            for t in row.transitions
        ),
        stage_timings_ms=dict(row.stage_timings_ms),
    )


class PostgresCallSessionRepository:
    """The same interface `InMemoryCallSessionRepository` implements (`CallSessionRepository`).

    `save()` is an upsert because the orchestrator mutates one `CallSession` object through
    a whole call and saves it repeatedly — it does not distinguish create from update, and
    making it do so would push transaction awareness into the state machine, which is the
    one place that has to stay easy to read.
    """

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def save(self, session: CallSession) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(CallSessionRow, session.call_session_id)
            if row is None:
                row = CallSessionRow()
                db.add(_session_to_row(session, row))
            else:
                _session_to_row(session, row)

            # Transitions are append-only, so only the ones this save has not seen are
            # written. Replacing them wholesale would churn ids and break any foreign key
            # pointing at a transition, and re-inserting all of them would duplicate the
            # timeline on every save - the orchestrator saves the same object many times
            # per call.
            existing = len(row.transitions)
            for transition in session.transitions[existing:]:
                row.transitions.append(
                    CallStateTransitionRow(
                        from_state=str(transition.from_state) if transition.from_state else None,
                        to_state=str(transition.to_state),
                        at=transition.at,
                        reason=transition.reason,
                    )
                )

    async def get(self, call_session_id: str) -> CallSession | None:
        async with self._factory() as db:
            row = await db.get(CallSessionRow, call_session_id)
            return _row_to_session(row) if row else None

    async def find_by_telephony_id(self, telephony_call_id: str) -> CallSession | None:
        async with self._factory() as db:
            found = await db.scalars(
                select(CallSessionRow).where(CallSessionRow.telephony_call_id == telephony_call_id)
            )
            row = found.first()
            return _row_to_session(row) if row else None

    async def list_in_states(self, *states: object) -> list[CallSession]:
        wanted = [str(s) for s in states]
        async with self._factory() as db:
            found = await db.scalars(select(CallSessionRow).where(CallSessionRow.state.in_(wanted)))
            return [_row_to_session(row) for row in found.all()]


class PostgresAgentStateLog:
    """Append-only. **Current presence is a projection of this** (`D76`).

    There is no `agent_presence` table on purpose: a stored "current state" beside a log
    is two facts that can disagree, and the log is the one that answers the question a
    supervisor actually asks — *what was true at 14:03*.
    """

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def append(self, change: AgentStateChange) -> None:
        async with session_scope(self._factory) as db:
            db.add(
                AgentStateLogRow(
                    agent_id=change.agent_id,
                    at=change.at,
                    system_state=str(change.system_state),
                    agent_intent=str(change.agent_intent),
                    set_by=change.set_by,
                    reason=change.reason,
                    call_session_id=change.call_session_id,
                    acw_seconds=change.acw_seconds,
                )
            )

    async def for_agent(self, agent_id: str, *, limit: int = 500) -> list[AgentStateChange]:
        async with self._factory() as db:
            found = await db.scalars(
                select(AgentStateLogRow)
                .where(AgentStateLogRow.agent_id == agent_id)
                .order_by(AgentStateLogRow.at.desc(), AgentStateLogRow.id.desc())
                .limit(limit)
            )
            return [_row_to_change(row) for row in found.all()]

    async def latest_per_agent(self) -> dict[str, AgentStateChange]:
        """Rebuild every agent's standing state after a restart (`D76`).

        This is the method that turns "a restart loses a shift" into "a restart costs a
        reconnect". Deliberately a full scan ordered by time rather than a window function:
        the table is a shift's worth of rows, the query runs once at startup, and a plain
        SELECT is the same on every backend including the SQLite the tests use.
        """
        async with self._factory() as db:
            found = await db.scalars(
                select(AgentStateLogRow).order_by(AgentStateLogRow.at, AgentStateLogRow.id)
            )
            latest: dict[str, AgentStateChange] = {}
            for row in found.all():
                latest[row.agent_id] = _row_to_change(row)
            return latest


def _row_to_change(row: AgentStateLogRow) -> AgentStateChange:
    return AgentStateChange(
        agent_id=row.agent_id,
        at=row.at,
        system_state=AgentSystemState(row.system_state),
        agent_intent=AgentIntent(row.agent_intent),
        set_by=row.set_by,
        reason=row.reason,
        call_session_id=row.call_session_id,
        acw_seconds=row.acw_seconds,
    )


# `InMemoryAgentStateLog` lives beside the Protocol in `services/agents/presence.py`, with
# every other in-memory store. It is re-exported here because the contract suite and the
# storage factory both reach for it by this name.


__all__ = [
    "InMemoryAgentStateLog",
    "PostgresAgentStateLog",
    "PostgresCallSessionRepository",
]
