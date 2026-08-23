"""The public app-facing API. Thin: validate, delegate, shape the response.

This is the *same* contract the real Krungsri app would call. The customer simulator in
`apps/customer_sim/` uses nothing else, which is the point — replacing the simulator with
the real app later changes nothing on this side.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from readycall.api.deps import ContainerDep, PrincipalDep
from readycall.api.schemas import (
    ContextEventRequest,
    ContextEventResponse,
    CreateIntentRequest,
    CreateIntentResponse,
    IntentStatusResponse,
)
from readycall.logging import call_context, get_logger
from readycall.services.context.store import AppContextEvent

log = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["app"])


@router.post(
    "/calls/intents",
    response_model=CreateIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tap Contact on a plan",
)
async def create_call_intent(
    body: CreateIntentRequest,
    principal: PrincipalDep,
    container: ContainerDep,
    background: BackgroundTasks,
) -> CreateIntentResponse:
    """Mint a correlation token and start assembling context.

    `principal.customer_id` comes from the session. `body` has no `customer_id` field to
    override it with, which is `D4` enforced by the schema rather than by a check.
    """
    intent, token = await container.intents.create(
        customer_id=principal.customer_id,
        product_code=body.product_code,
        plan_id=body.plan_id,
        entry_screen=body.entry_screen,
        app_intent=body.app_intent,
        app_context={"preferred_channel": body.preferred_channel},
    )

    # Drain AFTER the response is sent, so the six reads to the bank core are not on the
    # request path (`D6`). The app gets its dial target now; the context lands while the
    # customer is still lifting the phone to their ear.
    background.add_task(container.bus.drain)

    with call_context(call_session_id=intent.intent_id):
        log.info("intent issued to app", customer_id=principal.customer_id)

    return CreateIntentResponse(
        intent_id=intent.intent_id,
        correlation_token=token,  # returned once; only the hash is stored
        dial_target=container.intents.dial_target,
        expires_at=intent.expires_at,
    )


@router.get(
    "/calls/intents/{intent_id}",
    response_model=IntentStatusResponse,
    summary="What the prefetch produced",
)
async def get_call_intent(
    intent_id: str,
    principal: PrincipalDep,
    container: ContainerDep,
) -> IntentStatusResponse:
    """Read back an intent, including how the context assembly went.

    Exists so the claim *"the brief was ready before the phone rang"* is inspectable rather
    than asserted — the simulator shows the real build time and field count (`D18`).
    """
    intent = await container.intents.get(intent_id)
    if intent is None or intent.customer_id != principal.customer_id:
        # Same response for "does not exist" and "belongs to someone else", so this cannot
        # be used to discover which intent ids are real.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such intent")

    snapshot_id = container.snapshot_for_intent.get(intent_id)
    snapshot = await container.snapshots.get(snapshot_id) if snapshot_id else None
    now = container.clock.now()

    if snapshot is None:
        return IntentStatusResponse(
            intent_id=intent.intent_id,
            customer_id=intent.customer_id,
            expires_at=intent.expires_at,
            expired=intent.is_expired(now),
            context_ready=False,
        )

    return IntentStatusResponse(
        intent_id=intent.intent_id,
        customer_id=intent.customer_id,
        expires_at=intent.expires_at,
        expired=intent.is_expired(now),
        context_ready=True,
        context_build_ms=snapshot.build_ms,
        snapshot_id=snapshot.snapshot_id,
        policies_found=len(snapshot.payload.active_policies),
        interactions_found=len(snapshot.payload.recent_interactions),
        provenance_fields=len(snapshot.provenance),
        degraded=str(snapshot.degraded),
    )


@router.post(
    "/app/context-events",
    response_model=ContextEventResponse,
    summary="A screen the customer looked at",
)
async def record_context_event(
    body: ContextEventRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> ContextEventResponse:
    """What produces *"Recent activity: viewed hospitalisation coverage"* on the agent screen.

    Cheap and frequent, so it does no I/O beyond a ring-buffer append. Events are windowed
    on read — browsing from three weeks ago is not context, it is noise.
    """
    held = await container.record_app_event(
        AppContextEvent(
            customer_id=principal.customer_id,
            section=body.section,
            product_code=body.product_code,
            dwell_ms=body.dwell_ms,
            occurred_at=container.clock.now(),
            metadata=dict(body.metadata),
        )
    )
    return ContextEventResponse(accepted=True, events_held=held)
