"""Liveness and readiness.

Deliberately does no I/O to the bank core. A health endpoint that fails when an upstream is
down turns *"our data source is slow"* into *"take this instance out of the load balancer"*,
which is precisely backwards — the whole point of the caching and circuit-breaking layer is
that the system keeps serving, degraded, when the core is unreachable (`D12`).
"""

from __future__ import annotations

from fastapi import APIRouter

from readycall import __version__
from readycall.api.deps import ContainerDep
from readycall.api.schemas import HealthResponse

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=HealthResponse, summary="Is the process alive")
async def health(container: ContainerDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        core_data_provider=container.core.name,
        session_resolver=container.sessions.name,
        intents_loaded=len(container.pack.intents),
    )
