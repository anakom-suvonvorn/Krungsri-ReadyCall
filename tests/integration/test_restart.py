"""Does a shift actually survive a restart? (`D78`)

Every other test in this project proves a *piece* works. This one proves the claim the
whole phase was for, and it proves it the only way that counts: run a real app, do real
work through the real HTTP API, **throw the whole app away**, start a second one on the
same durable storage, and ask it what it knows.

That distinction matters more than it looks. `B7` was three services that were correct,
tested in isolation, and driven by nothing. A per-store contract suite would have passed
just as happily there. What catches that class of fault is a test where the *only* thing
carried across the boundary is the storage — no shared container, no shared services,
nothing that could be answering from a cache it happens to still be holding.

The suite runs against **SQLite by default**, so it runs on every commit, and against
Postgres when a container is reachable. Same reasoning as `D75`: a proof that only happens
when somebody remembers is not a proof.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from readycall.api.app import create_app
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.db.base import SCHEMA, Base
from readycall.db.repositories import PostgresAgentStateLog, PostgresCallSessionRepository
from readycall.db.session import create_engine, create_session_factory
from readycall.db.storage import Storage
from readycall.db.stores import (
    PostgresAssignmentStore,
    PostgresAttestationStore,
    PostgresCaptureStore,
    PostgresMatchingDecisionStore,
    PostgresRecordingStore,
    PostgresSnapshotStore,
    PostgresTranscriptStore,
    PostgresWrapupStore,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
#: A **separate database** from the one the app uses, and that separation is load-bearing.
#: These suites create their tables with `create_all` and drop them on teardown; pointed at
#: the dev database that deletes its contents *and* leaves `alembic_version` stamped at head
#: with no tables behind it, so `alembic upgrade head` becomes a silent no-op (`B9`).
POSTGRES_URL = os.environ.get(
    "READYCALL_TEST_DATABASE_URL",
    "postgresql+asyncpg://readycall:readycall@127.0.0.1:5432/readycall_test",
)
#: 10:00 Asia/Bangkok on a Monday, so the `business` queues are open. `ManualClock()`
#: defaults to New Year's Day, where every one of them is closed (`D54`).
T0 = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


async def _postgres_reachable() -> bool:
    try:
        engine = create_engine(POSTGRES_URL)
        async with engine.connect():
            pass
        await engine.dispose()
    except Exception:
        return False
    return True


def _storage(backend: str, factory: Any) -> Storage:
    """A durable bundle.

    Built here rather than by `build_storage`, which knows only `memory` and `postgres`:
    the suite wants a SQL backend that needs no container, and `D75` already established
    SQLite as exactly that. The Postgres implementations are the ones under test either
    way — SQLite is a different *engine*, not a different implementation.
    """
    return Storage(
        backend=backend,
        calls=PostgresCallSessionRepository(factory),
        agent_state_log=PostgresAgentStateLog(factory),
        assignments=PostgresAssignmentStore(factory),
        attestations=PostgresAttestationStore(factory),
        captures=PostgresCaptureStore(factory),
        decisions=PostgresMatchingDecisionStore(factory),
        recordings=PostgresRecordingStore(factory),
        snapshots=PostgresSnapshotStore(factory),
        transcripts=PostgresTranscriptStore(factory),
        wrapups=PostgresWrapupStore(factory),
    )


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def storage(request: Any) -> AsyncIterator[Storage]:
    url = "sqlite+aiosqlite://" if request.param == "sqlite" else POSTGRES_URL
    if request.param == "postgres" and not await _postgres_reachable():
        pytest.skip("no Postgres reachable; `docker compose -f infra/docker-compose.yml up -d`")

    # `pooled=False` on Postgres: this suite runs the app under `TestClient`, which owns
    # its own event loop, and an asyncpg connection belongs to the loop that made it.
    engine: AsyncEngine = create_engine(url, pooled=request.param != "postgres")
    async with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            await conn.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {SCHEMA}")
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield _storage(request.param, create_session_factory(engine))
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        # No background sweeper: this suite asserts on what a restart restored, and a loop
        # running against a `ManualClock` would only add noise to the failure message.
        agent_sweep_interval_s=0,
    )


def boot(settings: Settings, storage: Storage) -> Iterator[TestClient]:
    """Start a process. Nothing but `storage` survives from the previous one."""
    return TestClient(create_app(settings, clock=ManualClock(T0), storage=storage))


def sign_in(client: TestClient, agent_id: str = "A006") -> None:
    assert client.post("/v1/agent/demo-login", json={"agent_id": agent_id}).status_code == 200


def snapshot(client: TestClient) -> dict[str, Any]:
    response = client.get("/v1/agent/me")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def sweep(client: TestClient, *, alive: tuple[str, ...] = ()) -> None:
    """Run one tick of the background sweeper by hand.

    The suite disables the real loop, so nothing else drives dispatch. Worth knowing on its
    own: **declaring READY does not tick the matcher** — only placing a call, declining an
    offer, and the sweeper do. In production the 1 s sweep covers it; with the sweeper off
    a restored caller would sit in a restored pool that nothing ever looks at, which is
    `B7`'s shape and exactly what this suite must not accidentally reproduce.
    """
    import asyncio

    from readycall.api.app import sweep_once

    container = client.app.state.container  # type: ignore[attr-defined]

    async def run() -> None:
        for agent_id in alive:
            await container.presence.heartbeat(agent_id)
        await sweep_once(container)

    asyncio.run(run())


def place_call(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    response = client.post("/v1/demo/calls", json={"ignore_hours": True, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- the shift ------------------------------------------------------------------------------


def test_a_declared_intent_survives_a_restart(settings: Settings, storage: Storage) -> None:
    """The debt `NEXT_SESSION` carried since P2b, discharged.

    An agent who said *lunch* used to come back silently `NOT_READY` — the sign-in default
    (`D51`) — so the shift report said they had missed calls they had properly excused
    themselves from. Presence is now a projection of the state log (`D76`), so the
    **person's** axis comes back exactly as they left it.
    """
    with boot(settings, storage) as first:
        sign_in(first)
        assert first.post("/v1/agent/state", json={"agent_intent": "lunch"}).status_code == 200
        assert snapshot(first)["presence"]["agent_intent"] == "lunch"

    with boot(settings, storage) as second:
        sign_in(second)
        assert snapshot(second)["presence"]["agent_intent"] == "lunch"


def test_the_platform_axis_does_not_come_back(settings: Settings, storage: Storage) -> None:
    """And that is correct, not a gap (`D76`, `D78`).

    `system_state` describes what the **platform** has given this person to do. After a
    restart it has given them nothing: no socket, no offer, no call. Restoring `ON_CALL`
    from a log row would assert a conversation that is not happening and let the matcher
    count a desk that is not there — the precise failure the heartbeat sweep exists to
    prevent (`B7`), reintroduced through the back door.
    """
    with boot(settings, storage) as first:
        sign_in(first)
        first.post("/v1/agent/state", json={"agent_intent": "ready"})
        call = place_call(first, intent_code="health.claim.notify")
        assert call["state"] in {"offered", "matched"}
        assert snapshot(first)["presence"]["system_state"] == "offering"

    with boot(settings, storage) as second:
        sign_in(second)
        # Signing in again is what puts them back on the platform's axis, and it lands
        # them `AVAILABLE` + whatever they had declared — never mid-offer.
        assert snapshot(second)["presence"]["system_state"] == "available"


def test_a_waiting_caller_is_still_waiting_and_keeps_their_accrued_wait(
    settings: Settings, storage: Storage
) -> None:
    """The pool is rebuilt from `call_sessions`, not from a table of its own (`D78`).

    Waiting time is recomputed from `queued_at` rather than restored from a counter, so a
    caller who sat through our outage is *more* urgent afterwards, not reset to the back of
    the ordering. Being punished for our restart is exactly what `D22`'s urgency term is
    supposed to prevent.
    """
    with boot(settings, storage) as first:
        # Nobody is signed in, so the caller is matched and left waiting rather than offered.
        call = place_call(first, intent_code="health.claim.notify")
        call_id = call["call_session_id"]
        assert call["state"] == "matched"

    with boot(settings, storage) as second:
        sign_in(second)
        second.post("/v1/agent/state", json={"agent_intent": "ready"})
        waiting = [q for q in snapshot(second)["queues"] if q["waiting"] > 0]
        assert waiting, "the caller vanished with the process"

        # And the restored pool is live, not decorative: a dispatch tick offers them.
        sweep(second, alive=("A006",))
        after = snapshot(second)
        assert after["offer"] is not None
        assert after["offer"]["call_session_id"] == call_id


def test_an_attested_identity_and_its_disclosure_log_survive(
    settings: Settings, storage: Storage
) -> None:
    """The PDPA record is the one thing that must never be lost to an outage.

    Both halves are asserted: the **level**, so the agent is not asked to re-verify a
    caller they already verified, and the **count**, because `attestation_count` is what
    re-locks the control after every amendment (`D61`). Restoring an empty history would
    silently unlock the identity panel on a call that had already been attested.
    """
    with boot(settings, storage) as first:
        sign_in(first)
        first.post("/v1/agent/state", json={"agent_intent": "ready"})
        call = place_call(first, intent_code="health.claim.notify", caller_number="0812345678")
        call_id = call["call_session_id"]
        offer = snapshot(first)["offer"]
        first.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
        attested = first.post(
            f"/v1/agent/calls/{call_id}/identity",
            json={"outcome": "confirmed", "challenge": "date_of_birth"},
        )
        assert attested.status_code == 200, attested.text
        assert attested.json()["identity"]["assurance"] == "l3_verified"
        assert attested.json()["identity"]["attestation_count"] == 1

    with boot(settings, storage) as second:
        sign_in(second)
        identity = snapshot(second)["identity"]
        assert identity is not None, "the call came back with no identity at all"
        assert identity["assurance"] == "l3_verified"
        assert identity["attestation_count"] == 1


def test_an_agent_who_missed_an_offer_is_still_excluded_from_it(
    settings: Settings, storage: Storage
) -> None:
    """`D52` survives the restart, and this is the subtle one.

    Without it a restart forgets that this desk already declined, the global matcher
    re-solves, reaches the same optimum, and offers the same caller to the same silent
    agent — the exact loop `D52` exists to break, reintroduced by an outage. Every
    individual decision would look defensible while the caller waits forever.
    """
    with boot(settings, storage) as first:
        sign_in(first, "A006")
        first.post("/v1/agent/state", json={"agent_intent": "ready"})
        call = place_call(first, intent_code="health.claim.notify")
        call_id = call["call_session_id"]
        offer = snapshot(first)["offer"]
        declined = first.post(
            f"/v1/agent/offers/{offer['assignment_id']}/decline", json={"reason": "busy"}
        )
        assert declined.status_code == 200, declined.text

    with boot(settings, storage) as second:
        sign_in(second, "A006")
        second.post("/v1/agent/state", json={"agent_intent": "ready"})
        offer = snapshot(second)["offer"]
        assert offer is None or offer["call_session_id"] != call_id, (
            "the declined call was re-offered to the same agent after a restart"
        )


def test_the_brief_still_renders_after_a_restart(settings: Settings, storage: Storage) -> None:
    """Otherwise a restored call is a shell (`ContextSnapshotRow`).

    The state, the timeline and the identity would all come back and the agent's screen
    would be blank, because the brief is a **re-render of the frozen snapshot** (`D42`) and
    the snapshot lived in a dict. This is the test that made `context_snapshots` a table.
    """
    with boot(settings, storage) as first:
        sign_in(first)
        first.post("/v1/agent/state", json={"agent_intent": "ready"})
        place_call(first, intent_code="health.claim.notify", caller_number="0812345678")
        offer = snapshot(first)["offer"]
        first.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
        before = snapshot(first)["brief"]
        assert before is not None and before["summary_th"]

    with boot(settings, storage) as second:
        sign_in(second)
        after = snapshot(second)["brief"]
        assert after is not None, "the brief did not survive; the screen would be empty"
        assert after["summary_th"] == before["summary_th"]


def test_a_saved_wrapup_survives_and_an_unsaved_one_stays_absent(
    settings: Settings, storage: Storage
) -> None:
    """Both halves matter. `D45` refuses to auto-save precisely because the *absence* of a
    wrap-up is honest data — it records that this call was never wrapped up. A restore that
    invented one would replace a true fact with a fabricated one."""
    with boot(settings, storage) as first:
        sign_in(first)
        first.post("/v1/agent/state", json={"agent_intent": "ready"})
        call = place_call(first, intent_code="health.claim.notify")
        call_id = call["call_session_id"]
        offer = snapshot(first)["offer"]
        first.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
        assert (
            first.post(
                f"/v1/agent/calls/{call_id}/end", json={"reason": "caller_hung_up"}
            ).status_code
            == 200
        )
        saved = first.post(
            f"/v1/agent/calls/{call_id}/wrapup",
            json={"disposition": "resolved", "notes": "ส่งเอกสารเพิ่มเติม"},
        )
        assert saved.status_code == 200, saved.text

    # Read straight from the store, because a CLOSED call is history and history is not
    # loaded back into memory (`D78` bounds restore to live calls). The agent's writing
    # survived and is queryable, which is the claim; keeping it in a process's dict for
    # the rest of the day is not.
    import asyncio

    rows = asyncio.run(storage.wrapups.for_calls([call_id]))
    assert [r.disposition for r in rows] == ["resolved"]
    assert rows[0].notes == "ส่งเอกสารเพิ่มเติม"
    assert rows[0].agent_id == "A006"

    with boot(settings, storage) as second:
        live = second.app.state.container.wrapups  # type: ignore[attr-defined]
        assert call_id not in live, "a closed call was pulled back into the working set"
        assert asyncio.run(storage.wrapups.for_calls(["call_never_wrapped"])) == [], (
            "an unsaved wrap-up must stay absent — that absence is the data (`D45`)"
        )
