"""The FastAPI application factory.

`create_app()` takes settings rather than reading them, so tests build an app with a
`ManualClock` and fixture data without touching the environment — the same reason the clock
and id generator are injected everywhere else (`D35`).

The customer simulator is mounted from here as a static file. It is a plain HTML page with
no build step (`D47`), so `uv run python -m readycall.entrypoints.api` is the whole setup:
no npm install, no bundler, nothing that can fail five minutes before a demo.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from readycall.db.storage import Storage
from readycall.logging import configure, get_logger

log = get_logger(__name__)

SIM_DIR = Path(__file__).resolve().parents[3] / "apps" / "customer_sim"
WORKSTATION_DIST = Path(__file__).resolve().parents[3] / "apps" / "workstation" / "dist"


async def sweep_once(container: Container) -> None:
    """One pass of the things that must happen *because time passed*, not because a
    request arrived (`B7`).

    Three services were written expecting a periodic driver and never given one:

    * `DispatchService.expire_offers()` — RONA. Its own docstring said "runs on a timer in
      the API process", and no timer existed, so an unanswered offer never resolved: the
      agent sat in `OFFERING` forever, could not be offered anything else, and the caller
      was never re-matched. The workstation hid the offer card at zero, which made it look
      like the offer had been dealt with.
    * `DispatchService.tick()` — re-matching. Once an offer expires the call is `MATCHED`
      again with no open offer, and the very next tick re-offers it to somebody else, with
      the agent who missed it excluded (`D52`). Without a driver the caller simply waited.
    * `PresenceService.sweep()` — heartbeat expiry. A closed laptop is supposed to fall out
      of presence on a TTL; instead it stayed `AVAILABLE` and kept being chosen.

    `TranscriptionService.check_timeouts()` is the newest member and joined for exactly the
    same reason (`D96`): a recording ends when the caller goes quiet for
    `INTAKE_SILENCE_TIMEOUT_S` or hits `INTAKE_MAX_DURATION_S`, and on both of those the
    only thing that has happened is that time passed. There is no request in flight to
    notice.

    `IntakeService.reoffer_due()` joined them rather than being scheduled by whichever
    request queued the call. It is the same shape of trap: the re-offer at
    `INTAKE_REOFFER_AFTER_S` fires *because the wait got long*, and nothing else about the
    call happens at that moment to carry it.

    Exceptions are logged and swallowed: this loop must survive a bad tick, because the
    thing it drives is the thing that recovers from bad ticks.
    """
    try:
        expired = await container.dispatch.expire_offers()
        dropped = await container.presence.sweep()
        reoffered = await container.intake.reoffer_due()
        recordings = await container.transcription.check_timeouts()
        result = await container.dispatch.tick()
        if expired or dropped or reoffered or recordings or result.offered:
            log.info(
                "sweep",
                offers_expired=len(expired),
                agents_dropped=len(dropped),
                calls_offered=len(result.offered),
                intake_reoffers=len(reoffered),
                recordings_timed_out=len(recordings),
            )
    except Exception:
        log.exception("sweep failed")


async def _sweep_forever(container: Container, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        await sweep_once(container)


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    enable_utf8()  # Thai on a cp1252 console kills the process (`B1`)
    settings = settings or get_settings()
    configure(log_format=settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container(settings, clock=clock, storage=storage)
        app.state.container = container
        # **Before the sweeper, and before the first request.** A tick that ran against an
        # empty working set would re-match callers who already have an offer out and would
        # see an empty floor, so it would do real damage in the half-second before restore
        # finished. Restoring first is not tidiness; it is the ordering the sweeper assumes.
        restored = await container.restore()
        sweeper = (
            asyncio.create_task(_sweep_forever(container, settings.agent_sweep_interval_s))
            if settings.agent_sweep_interval_s > 0
            else None
        )
        log.info(
            "api ready",
            core_data_provider=container.core.name,
            sessions=container.sessions.name,
            demo_login=settings.demo_login_enabled,
            intents=len(container.pack.intents),
            sweep_every_s=settings.agent_sweep_interval_s,
            storage=container.storage.backend,
            restored=restored,
        )
        yield
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        # Drain anything still queued so a shutdown mid-request does not silently drop a
        # context prefetch that was already promised to a caller.
        await container.bus.drain()
        await container.aclose()

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

    # The workstation is a built React bundle (`D32`), mounted only when it exists. That
    # keeps `uv run python -m readycall.entrypoints.api` working with no node installed —
    # you get the API and the customer simulator, and the workstation appears once
    # somebody has run `npm run build` in `apps/workstation`. The demo therefore needs
    # node ONCE, never at run time (the same worry that made the simulator build-free).
    if WORKSTATION_DIST.is_dir():
        app.mount(
            "/workstation/assets",
            StaticFiles(directory=WORKSTATION_DIST / "assets"),
            name="workstation-assets",
        )

        @app.get("/workstation", include_in_schema=False)
        async def workstation() -> FileResponse:
            return FileResponse(WORKSTATION_DIST / "index.html")

    if SIM_DIR.is_dir():
        app.mount("/sim/static", StaticFiles(directory=SIM_DIR), name="sim-static")

        @app.get("/sim", include_in_schema=False)
        async def customer_simulator() -> FileResponse:
            return FileResponse(SIM_DIR / "index.html")

        @app.get("/", include_in_schema=False)
        async def root() -> FileResponse:
            return FileResponse(SIM_DIR / "index.html")

    return app
