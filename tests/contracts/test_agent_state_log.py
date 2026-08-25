"""One suite, every agent-state-log store (`D3`, `D75`, `D76`).

This is the table that fixes the debt `NEXT_SESSION` has carried since P2b: *"presence,
assignments and `agent_state_log` are in memory, so a restart loses a shift."* The claim
being tested is not "rows persist" but **"a restarted process can rebuild what every agent
had declared"**, which is the thing an agent notices.
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
from readycall.db.repositories import InMemoryAgentStateLog, PostgresAgentStateLog
from readycall.db.session import create_engine, create_session_factory
from readycall.domain.enums import AgentIntent, AgentSystemState
from readycall.domain.models import AgentStateChange

POSTGRES_URL = os.environ.get(
    "READYCALL_TEST_DATABASE_URL",
    "postgresql+asyncpg://readycall:readycall@127.0.0.1:5432/readycall",
)
T0 = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)


async def _prepare(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            await conn.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {SCHEMA}")
        await conn.run_sync(Base.metadata.create_all)


async def _postgres_reachable() -> bool:
    try:
        engine = create_engine(POSTGRES_URL)
        async with engine.connect():
            pass
        await engine.dispose()
    except Exception:
        return False
    return True


@pytest_asyncio.fixture(params=["memory", "sqlite", "postgres"])
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryAgentStateLog()
        return

    url = "sqlite+aiosqlite://" if request.param == "sqlite" else POSTGRES_URL
    if request.param == "postgres" and not await _postgres_reachable():
        pytest.skip("no Postgres reachable; `docker compose -f infra/docker-compose.yml up -d`")

    engine = create_engine(url)
    await _prepare(engine)
    try:
        yield PostgresAgentStateLog(create_session_factory(engine))
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def change(agent_id: str, intent: AgentIntent, *, at: datetime, **kw: Any) -> AgentStateChange:
    return AgentStateChange(
        agent_id=agent_id,
        at=at,
        system_state=kw.pop("system_state", AgentSystemState.AVAILABLE),
        agent_intent=intent,
        set_by=kw.pop("set_by", "agent"),
        reason=kw.pop("reason", "agent_declared"),
        **kw,
    )


@pytest.mark.asyncio
async def test_a_shift_survives_a_restart(store: Any) -> None:
    """`D76`, and the whole reason this table exists.

    Three agents declare different things. A restarted process asks for the latest row per
    agent and gets back exactly what each of them had said — not a default, and not
    everyone silently `NOT_READY`, which is what an in-memory store gives you.
    """
    await store.append(change("A001", AgentIntent.READY, at=T0))
    await store.append(change("A002", AgentIntent.LUNCH, at=T0 + timedelta(minutes=1)))
    await store.append(change("A001", AgentIntent.BREAK, at=T0 + timedelta(minutes=2)))
    await store.append(change("A003", AgentIntent.DRAINING, at=T0 + timedelta(minutes=3)))

    latest = await store.latest_per_agent()
    assert {a: c.agent_intent for a, c in latest.items()} == {
        "A001": AgentIntent.BREAK,  # the LATER of A001's two rows
        "A002": AgentIntent.LUNCH,
        "A003": AgentIntent.DRAINING,
    }


@pytest.mark.asyncio
async def test_who_set_it_and_why_survive_too(store: Any) -> None:
    """`D51`: "they chose break" and "nobody picked up" are different facts.

    A shift report that cannot tell them apart is worse than none, so both columns are
    part of the contract rather than incidental.
    """
    await store.append(
        change(
            "A001",
            AgentIntent.NOT_READY,
            at=T0,
            set_by="platform",
            reason="rona_missed_offer",
        )
    )
    latest = await store.latest_per_agent()
    row = latest["A001"]
    assert row.set_by == "platform"
    assert row.reason == "rona_missed_offer"


@pytest.mark.asyncio
async def test_the_log_is_append_only_and_ordered(store: Any) -> None:
    """History, newest first — the substrate for every ACW and utilisation metric."""
    for minute, intent in enumerate(
        [AgentIntent.READY, AgentIntent.LAST_CALL, AgentIntent.NOT_READY]
    ):
        await store.append(change("A001", intent, at=T0 + timedelta(minutes=minute)))

    rows = await store.for_agent("A001")
    assert [r.agent_intent for r in rows] == [
        AgentIntent.NOT_READY,
        AgentIntent.LAST_CALL,
        AgentIntent.READY,
    ]
    assert await store.for_agent("A999") == []


@pytest.mark.asyncio
async def test_acw_seconds_are_kept_on_the_row_that_ends_it(store: Any) -> None:
    """`D45` measures ACW from disconnect to declaration, and this is where it lands.

    It is the number the product's headline claim rests on — the AI-drafted wrap-up should
    shrink it — so it has to be stored, not recomputed from timestamps later.

    No `call_session_id` here on purpose: the column is a foreign key to `call_sessions`,
    and a store-level test has no call to point at. That the FK **is** enforced is asserted
    separately below, on the backends that have one.
    """
    await store.append(
        change(
            "A001",
            AgentIntent.READY,
            at=T0,
            system_state=AgentSystemState.AVAILABLE,
            acw_seconds=42.5,
        )
    )
    row = (await store.for_agent("A001"))[0]
    assert row.acw_seconds == 42.5


@pytest.mark.asyncio
async def test_a_log_row_cannot_point_at_a_call_that_does_not_exist(store: Any) -> None:
    """The foreign key is real, and it is real on **every** SQL backend.

    Worth its own test because it was briefly true on only one: SQLite ignores foreign
    keys unless asked, so this row was refused by Postgres and accepted by SQLite. A fast
    test path that enforces less than production is worse than no fast path — it turns a
    constraint into something that only shows up in production.
    """
    if isinstance(store, InMemoryAgentStateLog):
        pytest.skip("a list has no referential integrity to enforce")

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await store.append(
            change("A001", AgentIntent.READY, at=T0, call_session_id="call_does_not_exist")
        )


@pytest.mark.asyncio
async def test_an_empty_log_rebuilds_to_nothing(store: Any) -> None:
    """A cold start is not an error: nobody has declared anything yet."""
    assert await store.latest_per_agent() == {}
