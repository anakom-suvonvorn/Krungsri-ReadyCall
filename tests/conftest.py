"""Shared fixtures.

Two habits worth keeping: ids and time are deterministic in every test (so failures
are reproducible rather than "it passed last time"), and nothing here touches a network,
a GPU, or a database.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from readycall import ids
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.clock import ManualClock
from readycall.services.call_orchestrator import (
    CallOrchestrator,
    InMemoryCallSessionRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "mock" / "bank_core" / "fixtures"
SCENARIOS_DIR = REPO_ROOT / "tests" / "scenarios"


@pytest.fixture(autouse=True)
def deterministic_ids() -> Iterator[None]:
    ids.install(ids.DeterministicIds())
    yield
    ids.reset()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def repo() -> InMemoryCallSessionRepository:
    return InMemoryCallSessionRepository()


@pytest.fixture
def orchestrator(
    repo: InMemoryCallSessionRepository, bus: InMemoryEventBus, clock: ManualClock
) -> CallOrchestrator:
    return CallOrchestrator(repository=repo, bus=bus, clock=clock)


@pytest.fixture
def core_fixtures() -> FixtureFileProvider:
    return FixtureFileProvider(FIXTURES_DIR)
