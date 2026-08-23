"""Everything the API needs, built once and handed to routes by dependency injection.

The container is assembled from `Settings`, so switching the bank-data adapter or the event
bus is an env var (`D3`). Routes never construct anything themselves — they ask for what
they need, which is what keeps them thin enough to read in one screen.

The one piece of behaviour that lives here rather than in a service is the **prefetch
handler**: on `intent.created`, build the context snapshot. It sits here because it is
wiring — deciding *that* the assembler runs on that event — rather than logic. Doing it
this way is what puts assembly off the request path (`D6`): the endpoint publishes and
returns a dial target immediately, and the six reads to the bank core happen afterwards,
while the customer is still lifting the phone to their ear.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request

from readycall.adapters.core_data.caching import CachingCoreDataProvider
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.api.security import DemoSessionStore, Principal, SessionResolver
from readycall.clock import Clock, SystemClock
from readycall.config import CoreDataProviderName, Settings
from readycall.domain import events as ev
from readycall.domainpack import DomainPack
from readycall.logging import get_logger
from readycall.ports.core_data import CoreDataProvider
from readycall.services.context.assembler import ContextAssembler
from readycall.services.context.store import (
    AppContextEvent,
    InMemoryAppContextStore,
    InMemorySnapshotStore,
)
from readycall.services.identity.intents import IntentService
from readycall.services.identity.store import InMemoryCallIntentStore

log = get_logger(__name__)


def build_core_data(settings: Settings, clock: Clock) -> CoreDataProvider:
    """Pick the bank-data adapter by config, then wrap it so a slow core degrades cheaply."""
    inner: CoreDataProvider
    if settings.core_data_provider is CoreDataProviderName.FIXTURES:
        inner = FixtureFileProvider(settings.core_fixtures_dir)
    else:
        # P1b: mock_postgres and http_api land with the DB layer at P2 (`D39`).
        log.warning(
            "core data provider not implemented yet, using null",
            requested=str(settings.core_data_provider),
        )
        inner = NullCoreDataProvider()
    return CachingCoreDataProvider(inner, clock=clock)


class Container:
    """Every long-lived object the API uses. One per process."""

    def __init__(self, settings: Settings, *, clock: Clock | None = None) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self.pack = DomainPack.load(settings.config_dir)
        self.bus = InMemoryEventBus()
        self.core = build_core_data(settings, self.clock)

        self.intent_store = InMemoryCallIntentStore()
        self.snapshots = InMemorySnapshotStore()
        self.app_context = InMemoryAppContextStore(clock=self.clock)

        self.assembler = ContextAssembler(core=self.core, clock=self.clock)
        self.intents = IntentService(
            store=self.intent_store,
            bus=self.bus,
            clock=self.clock,
            ttl_s=settings.intent_ttl_s,
            dial_target=settings.default_dial_target,
        )

        self.sessions: SessionResolver = DemoSessionStore(
            clock=self.clock, ttl_s=settings.session_ttl_s
        )

        #: intent_id -> snapshot_id, so an intent can report what the prefetch produced.
        self.snapshot_for_intent: dict[str, str] = {}
        self.bus.subscribe(ev.IntentCreated.name, self._prefetch_context)

    async def _prefetch_context(self, event: ev.Event) -> None:
        """`D6`: assembly starts at *tap*, not at *answer*."""
        if not isinstance(event, ev.IntentCreated):
            return
        snapshot = await self.assembler.build(
            customer_id=event.customer_id, product_code=event.product_code
        )
        await self.snapshots.save(snapshot)
        self.snapshot_for_intent[event.intent_id] = snapshot.snapshot_id
        log.info(
            "context prefetched for intent",
            intent_id=event.intent_id,
            snapshot_id=snapshot.snapshot_id,
            build_ms=round(snapshot.build_ms or 0.0, 2),
        )

    async def record_app_event(self, event: AppContextEvent) -> int:
        return await self.app_context.record(event)

    async def recent_app_events(self, customer_id: str) -> tuple[AppContextEvent, ...]:
        return await self.app_context.recent_for(customer_id, within=timedelta(hours=24))


# --- FastAPI dependencies ----------------------------------------------------------------


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_principal(request: Request, container: ContainerDep) -> Principal:
    """Resolve the caller from their session cookie — never from the request body (`D4`)."""
    token = request.cookies.get(container.settings.session_cookie_name)
    return await container.sessions.resolve(token)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


__all__ = [
    "Container",
    "ContainerDep",
    "PrincipalDep",
    "build_core_data",
    "get_container",
    "get_principal",
]
