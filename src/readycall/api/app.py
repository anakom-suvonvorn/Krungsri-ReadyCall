"""The FastAPI application factory.

`create_app()` takes settings rather than reading them, so tests build an app with a
`ManualClock` and fixture data without touching the environment — the same reason the clock
and id generator are injected everywhere else (`D35`).

The customer simulator is mounted from here as a static file. It is a plain HTML page with
no build step (`D47`), so `uv run python -m readycall.entrypoints.api` is the whole setup:
no npm install, no bundler, nothing that can fail five minutes before a demo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from readycall import __version__
from readycall.api.deps import Container
from readycall.api.routers import agent, demo, health, mobile
from readycall.api.security import AuthenticationRequired
from readycall.clock import Clock
from readycall.config import Settings, get_settings
from readycall.console import enable_utf8
from readycall.logging import configure, get_logger

log = get_logger(__name__)

SIM_DIR = Path(__file__).resolve().parents[3] / "apps" / "customer_sim"


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    enable_utf8()  # Thai on a cp1252 console kills the process (`B1`)
    settings = settings or get_settings()
    configure(log_format=settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container(settings, clock=clock)
        app.state.container = container
        log.info(
            "api ready",
            core_data_provider=container.core.name,
            sessions=container.sessions.name,
            demo_login=settings.demo_login_enabled,
            intents=len(container.pack.intents),
        )
        yield
        # Drain anything still queued so a shutdown mid-request does not silently drop a
        # context prefetch that was already promised to a caller.
        await container.bus.drain()

    app = FastAPI(
        title="Krungsri ReadyCall",
        version=__version__,
        summary="An AI context layer for insurance service calls",
        lifespan=lifespan,
    )

    @app.exception_handler(AuthenticationRequired)
    async def _no_session(request: Request, exc: AuthenticationRequired) -> JSONResponse:
        # 401 with no detail about *why*: "unknown session" and "expired session" are the
        # same answer to anyone who is not already holding a valid one.
        return JSONResponse(status_code=401, content={"detail": "authentication required"})

    app.include_router(health.router)
    app.include_router(mobile.router)
    app.include_router(agent.router)
    if settings.demo_login_enabled:
        app.include_router(demo.router)

    if SIM_DIR.is_dir():
        app.mount("/sim/static", StaticFiles(directory=SIM_DIR), name="sim-static")

        @app.get("/sim", include_in_schema=False)
        async def customer_simulator() -> FileResponse:
            return FileResponse(SIM_DIR / "index.html")

        @app.get("/", include_in_schema=False)
        async def root() -> FileResponse:
            return FileResponse(SIM_DIR / "index.html")

    return app
