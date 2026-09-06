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
from readycall.config import Settings
from readycall.services.call_orchestrator import (
    CallOrchestrator,
    InMemoryCallSessionRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "mock" / "bank_core" / "fixtures"
SCENARIOS_DIR = REPO_ROOT / "tests" / "scenarios"


@pytest.fixture(autouse=True, scope="session")
def ignore_the_developers_dotenv() -> Iterator[None]:
    """The suite must never read `.env`, and until 2026-09-06 it did.

    `Settings.model_config` names `env_file=".env"`, so every test that builds
    `Settings(...)` without pinning a field inherited whatever this laptop happened to
    have in an **untracked, gitignored** file. A developer who set `STT_ENGINE=typhoon`
    to try the real engine sent five transcript tests to the GPU, where synthetic test
    tones produce no turns — five failures with nothing wrong in the repository, and a
    green suite for the next person who cloned it.

    That is `B9` and `B15`'s family: every other check in this project describes the
    working tree, and this one quietly described one machine's private configuration.
    The docstring at the top of this file has always promised the suite touches no GPU;
    this is what makes the promise true.

    Environment *variables* are deliberately left alone — `READYCALL_TEST_MINIO=1` and
    friends are how the opt-in rows are selected, and `STT_ENGINE=scripted uv run pytest`
    has to keep working. It is the file that nobody expects to change a test result.
    """
    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    yield
    Settings.model_config["env_file"] = original


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
