"""One suite, every call-session store (`D3`, `D75`).

The project's standing rule for ports is that every implementation passes the same
contract tests. The stores are an internal seam rather than a port, but the argument is
identical and stronger here: the in-memory one is what every other test runs against, so
if the two ever disagree the whole suite is green while production is broken.

**Both backends run by default.** SQLite covers it everywhere; the Postgres cases run
whenever a database is reachable and skip when it is not, so `docker compose up` is the
difference between "tested" and "tested harder" rather than between "tested" and "not".
That matters because `B7` was code that was only ever exercised when somebody remembered.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from readycall.db.base import SCHEMA, Base
from readycall.db.repositories import PostgresCallSessionRepository
from readycall.db.session import create_engine, create_session_factory
from readycall.domain.enums import CallState, EntryChannel, ProductLine
from readycall.domain.models import CallSession, StateTransition
from readycall.services.call_orchestrator.repository import (
    CallSessionRepository,
    InMemoryCallSessionRepository,
)
from tests.conftest import postgres_reachable

#: A **separate database** from the one the app uses, and that separation is load-bearing.
#: These suites create their tables with `create_all` and drop them on teardown; pointed at
#: the dev database that deletes its contents *and* leaves `alembic_version` stamped at head
#: with no tables behind it, so `alembic upgrade head` becomes a silent no-op (`B9`).
POSTGRES_URL = os.environ.get(
    "READYCALL_TEST_DATABASE_URL",
    "postgresql+asyncpg://readycall:readycall@127.0.0.1:5432/readycall_test",
)


async def _prepare(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            # SQLite has no schemas. Attaching one under the same name is what lets the
            # models carry `schema="readycall"` unconditionally, so the table definitions
            # the tests exercise are byte-for-byte the ones Postgres gets.
            await conn.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {SCHEMA}")
        await conn.run_sync(Base.metadata.create_all)


async def _postgres_reachable() -> bool:
    # One probe per session, with a deadline: see `postgres_reachable` (`B44`).
    return await postgres_reachable(POSTGRES_URL)


@pytest_asyncio.fixture(params=["memory", "sqlite", "postgres"])
async def repo(request: Any) -> AsyncIterator[CallSessionRepository]:
    if request.param == "memory":
        yield InMemoryCallSessionRepository()
        return

    url = "sqlite+aiosqlite://" if request.param == "sqlite" else POSTGRES_URL
    if request.param == "postgres" and not await _postgres_reachable():
        pytest.skip("no Postgres reachable; `docker compose -f infra/docker-compose.yml up -d`")

    engine = create_engine(url)
    await _prepare(engine)
    try:
        yield PostgresCallSessionRepository(create_session_factory(engine))
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def a_call(call_id: str = "call_1", **overrides: Any) -> CallSession:
    base: dict[str, Any] = {
        "call_session_id": call_id,
        "entry_channel": EntryChannel.HOTLINE,
        "state": CallState.CONNECTING,
        "created_at": datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
        "trace_id": "trace_1",
        "caller_number": "0812345678",
        "product_line": ProductLine.HEALTH,
    }
    base.update(overrides)
    return CallSession(**base)


@pytest.mark.asyncio
async def test_a_saved_call_comes_back_identical(repo: CallSessionRepository) -> None:
    """The whole point of a store, and the assertion is on the DOMAIN object.

    Comparing field by field would let a column quietly stop being written and still pass,
    which is how `B5` survived every test it had.
    """
    original = a_call()
    await repo.save(original)

    loaded = await repo.get("call_1")
    assert loaded is not None
    assert loaded == original


@pytest.mark.asyncio
async def test_an_unknown_call_is_none_not_an_error(repo: CallSessionRepository) -> None:
    assert await repo.get("call_nope") is None


@pytest.mark.asyncio
async def test_saving_twice_updates_rather_than_duplicating(
    repo: CallSessionRepository,
) -> None:
    """The orchestrator saves the same object many times per call."""
    call = a_call()
    await repo.save(call)
    call.state = CallState.QUEUED
    call.queue_id = "q_claims"
    await repo.save(call)

    loaded = await repo.get("call_1")
    assert loaded is not None
    assert loaded.state is CallState.QUEUED
    assert loaded.queue_id == "q_claims"
    assert len(await repo.list_in_states(CallState.QUEUED)) == 1


@pytest.mark.asyncio
async def test_transitions_accumulate_and_never_duplicate(
    repo: CallSessionRepository,
) -> None:
    """The timeline is append-only, and it is saved repeatedly (`D18`).

    Re-inserting the whole list on every save would multiply the timeline by the number of
    saves — and a scenario replay would still *look* right, because the last transition
    would be correct and nobody reads the middle of a timeline.
    """
    call = a_call()
    start = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)
    call.transitions = (
        StateTransition(from_state=None, to_state=CallState.CONNECTING, at=start, reason="start"),
    )
    await repo.save(call)
    await repo.save(call)  # the same object again, unchanged

    loaded = await repo.get("call_1")
    assert loaded is not None
    assert len(loaded.transitions) == 1, "saving twice must not duplicate the timeline"

    call.transitions = (
        *call.transitions,
        StateTransition(
            from_state=CallState.CONNECTING,
            to_state=CallState.IVR,
            at=start + timedelta(seconds=1),
            reason="ivr_started",
        ),
    )
    await repo.save(call)

    loaded = await repo.get("call_1")
    assert loaded is not None
    assert [t.to_state for t in loaded.transitions] == [CallState.CONNECTING, CallState.IVR]
    assert loaded.transitions[1].reason == "ivr_started"


@pytest.mark.asyncio
async def test_timestamps_come_back_timezone_aware(repo: CallSessionRepository) -> None:
    """SQLite returns naive datetimes, and comparing naive to aware raises.

    Everything in this system is UTC by construction (`D35`), so the store asserts that
    rather than hoping — the failure would otherwise land in the matcher, mid-call.
    """
    await repo.save(a_call(queued_at=datetime(2026, 8, 25, 3, 5, tzinfo=UTC)))
    loaded = await repo.get("call_1")
    assert loaded is not None
    assert loaded.created_at.tzinfo is not None
    assert loaded.queued_at is not None and loaded.queued_at.tzinfo is not None
    # The real assertion: arithmetic against an aware "now" does not explode.
    assert (datetime(2026, 8, 25, 4, 0, tzinfo=UTC) - loaded.created_at).total_seconds() == 3600


@pytest.mark.asyncio
async def test_lookup_by_telephony_id(repo: CallSessionRepository) -> None:
    """How an inbound webhook finds the call it belongs to."""
    await repo.save(a_call(telephony_call_id="PJSIP/abc-0001"))
    found = await repo.find_by_telephony_id("PJSIP/abc-0001")
    assert found is not None and found.call_session_id == "call_1"
    assert await repo.find_by_telephony_id("PJSIP/nope") is None


@pytest.mark.asyncio
async def test_listing_by_state_is_what_the_matcher_asks(
    repo: CallSessionRepository,
) -> None:
    await repo.save(a_call("call_1", state=CallState.QUEUED))
    await repo.save(a_call("call_2", state=CallState.QUEUED))
    await repo.save(a_call("call_3", state=CallState.IN_CALL))

    queued = await repo.list_in_states(CallState.QUEUED)
    assert {c.call_session_id for c in queued} == {"call_1", "call_2"}

    both = await repo.list_in_states(CallState.QUEUED, CallState.IN_CALL)
    assert len(both) == 3
    assert await repo.list_in_states(CallState.CLOSED) == []
