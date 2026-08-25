"""Engine and session factory.

One engine per process, built from `Settings` like everything else (`D3`), so switching
between the in-memory store and Postgres is configuration rather than a code change.

**Why `expire_on_commit=False`:** the repositories return **domain models**, not ORM rows
(`D77`), so nothing outside this package ever holds an attached instance. Leaving the
default on would cost a re-SELECT on attribute access for objects we have already finished
with, purely to refresh rows nobody is looking at.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from readycall.logging import get_logger

log = get_logger(__name__)


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """An async engine for Postgres or SQLite.

    SQLite is not a deployment target — it is what lets the whole suite run with no
    container, which is the difference between the database path being tested on every
    commit and being tested when somebody remembers. `B7` is what happens to code that is
    only exercised when somebody remembers.
    """
    if url.startswith("sqlite"):
        # A shared in-memory SQLite database needs one connection for every session, or
        # each session gets its own empty database and the tests pass while proving
        # nothing.
        from sqlalchemy import event
        from sqlalchemy.pool import StaticPool

        engine = create_async_engine(
            url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            """SQLite ignores foreign keys unless asked, and a fast test path that
            enforces less than production is a fast test path that lies.

            Found by the contract suite: an `agent_state_log` row pointing at a call that
            did not exist was refused by Postgres and accepted by SQLite, so the same test
            passed on one backend and failed on the other. Divergence like that is worse
            than having no SQLite path at all, because it converts a real constraint into
            one that only appears in production.
            """
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on anything at all.

    Deliberately not a per-request dependency. A call's lifecycle spans requests, socket
    pushes and background sweeps, so the transaction boundary is the *operation*, not the
    HTTP request that happened to trigger it.
    """
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = ["create_engine", "create_session_factory", "session_scope"]
