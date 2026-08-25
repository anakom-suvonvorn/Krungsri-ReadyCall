"""One factory that turns `STORAGE_BACKEND` into a set of stores.

This module is the answer to the question `NEXT_SESSION` carried for two sessions: *"the
repositories exist and pass their contract suite, but nothing constructs them."* Code with
no driver is `B7`, and a persistence layer nothing calls is the same bug with a bigger
blast radius — it looks finished and loses a shift.

**Both branches build the same set.** The `memory` backend is not "no stores"; it is the
in-memory implementations, so the write-through path in every service runs on the default
configuration and in every test. That is `D75`'s argument for keeping SQLite in the suite,
applied one level up: a code path exercised only when somebody starts a container is a code
path nobody runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from readycall.config import Settings, StorageBackend
from readycall.logging import get_logger
from readycall.services.agents.assignment import AssignmentStore, InMemoryAssignmentStore
from readycall.services.agents.dispatch import (
    InMemoryMatchingDecisionStore,
    MatchingDecisionStore,
)
from readycall.services.agents.presence import AgentStateLog, InMemoryAgentStateLog
from readycall.services.call_orchestrator.repository import (
    CallSessionRepository,
    InMemoryCallSessionRepository,
)
from readycall.services.capture.keypad import CaptureStore, InMemoryCaptureStore
from readycall.services.context.store import InMemorySnapshotStore, SnapshotStore
from readycall.services.identity.attestation import (
    AttestationStore,
    InMemoryAttestationStore,
)
from readycall.services.wrapup.store import InMemoryWrapupStore, WrapupStore

log = get_logger(__name__)


class Disposable(Protocol):
    """Anything holding connections that shutdown must release.

    A Protocol rather than `AsyncEngine` so this module stays free of SQLAlchemy at import
    time — the in-memory system has to start on a stage with no database driver installed.
    """

    async def dispose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Storage:
    """Everything durable about a call, in one bundle.

    Held as a group rather than injected one by one because **restore has to happen in
    order**: the live calls come first, and every other store is then loaded *for those
    calls*. Passing seven independent stores around would leave that ordering as folklore.
    """

    backend: str
    calls: CallSessionRepository
    agent_state_log: AgentStateLog
    assignments: AssignmentStore
    attestations: AttestationStore
    captures: CaptureStore
    decisions: MatchingDecisionStore
    snapshots: SnapshotStore
    wrapups: WrapupStore
    #: Closed on shutdown. `None` for the in-memory backend, which owns no connections.
    engine: Disposable | None = None


def build_storage(settings: Settings) -> Storage:
    """Pick the backend. **This is the one line `D75` was written for.**"""
    if settings.storage_backend is StorageBackend.MEMORY:
        return Storage(
            backend="memory",
            calls=InMemoryCallSessionRepository(),
            agent_state_log=InMemoryAgentStateLog(),
            assignments=InMemoryAssignmentStore(),
            attestations=InMemoryAttestationStore(),
            captures=InMemoryCaptureStore(),
            decisions=InMemoryMatchingDecisionStore(),
            snapshots=InMemorySnapshotStore(),
            wrapups=InMemoryWrapupStore(),
        )

    # Imported here rather than at module scope so the default configuration never pays
    # for SQLAlchemy's import cost, and so a broken driver install cannot stop the
    # in-memory system — which is the one that has to work on a stage with no container.
    from readycall.db.repositories import (
        PostgresAgentStateLog,
        PostgresCallSessionRepository,
    )
    from readycall.db.session import create_engine, create_session_factory
    from readycall.db.stores import (
        PostgresAssignmentStore,
        PostgresAttestationStore,
        PostgresCaptureStore,
        PostgresMatchingDecisionStore,
        PostgresSnapshotStore,
        PostgresWrapupStore,
    )

    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    log.info("storage backend", backend=str(settings.storage_backend))
    return Storage(
        backend=str(settings.storage_backend),
        calls=PostgresCallSessionRepository(factory),
        agent_state_log=PostgresAgentStateLog(factory),
        assignments=PostgresAssignmentStore(factory),
        attestations=PostgresAttestationStore(factory),
        captures=PostgresCaptureStore(factory),
        decisions=PostgresMatchingDecisionStore(factory),
        snapshots=PostgresSnapshotStore(factory),
        wrapups=PostgresWrapupStore(factory),
        engine=engine,
    )


__all__ = ["Storage", "build_storage"]
