"""The public app-facing API. Thin: validate, delegate, shape the response.

This is the *same* contract the real Krungsri app would call. The customer simulator in
`apps/customer_sim/` uses nothing else, which is the point — replacing the simulator with
the real app later changes nothing on this side.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from readycall.api.deps import ContainerDep, PrincipalDep
from readycall.api.schemas import (
    AssistRespondFromApp,
    ContactLine,
    ContactLinesResponse,
    ContactReason,
    ContactReasonsResponse,
    ContextEventRequest,
    ContextEventResponse,
    CreateIntentRequest,
    CreateIntentResponse,
    IntentStatusResponse,
)
from readycall.domain.enums import CallState, ProductLine
from readycall.domainpack import MENU_CONTEXTS
from readycall.errors import IllegalTransition, PermanentError
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


@router.get(
    "/app/contact-reasons",
    response_model=ContactReasonsResponse,
    summary="Why might you be calling about this line",
)
async def contact_reasons(
    principal: PrincipalDep,
    container: ContainerDep,
    product_line: str = "unknown",
    context: str = "general",
) -> ContactReasonsResponse:
    """The reasons the app shows after the customer taps Contact (`D48`, `D122`).

    Read from the **same `menus.yaml` the IVR reads**. One taxonomy, two surfaces, so the
    options cannot fork — and answering here is strictly better UX than answering on the
    phone, because reading five options takes a second on a screen and thirty in an
    earpiece (`D41`).

    **But not the same LIST** (`D122`). `context` says which situation the customer is in:

    * `plan` — they tapped a policy they hold. The line is known *and so is the fact that
      they own cover on it*, so options that only make sense for somebody without it are
      filtered out. This is the surface that was offering *"ซื้อประกันเดินทาง"* — buy
      travel insurance — to a customer holding a travel policy.
    * `general` — "something else": account-level admin, or cover they do not have yet.

    The IVR does not pass a context and is not filtered: a keypad caller has told us
    nothing about what they hold, so there is nothing to filter on.
    """
    if context not in MENU_CONTEXTS:
        raise HTTPException(status_code=400, detail=f"unknown context {context!r}")
    try:
        line = ProductLine(product_line)
    except ValueError:
        line = ProductLine.UNKNOWN

    menu = container.pack.reason_menu_for(line)
    if menu is None:
        # No line, or a line with no reason menu: fall back to the general menu, which is
        # exactly what the IVR does.
        menu = container.pack.menus.get("general_reason")

    reasons: list[ContactReason] = []
    for option in menu.options if menu else ():
        if not option.intent or not option.shown_in(context):
            continue
        # The OPTION's label, not the intent's: the same intent is a different
        # conversation depending on where it is offered from, which is what
        # `label_plan_th` exists for (`D122`).
        reasons.append(
            ContactReason(
                intent_code=option.intent,
                label_th=option.label_for(context),
                key=option.key,
            )
        )
    return ContactReasonsResponse(product_line=str(line), reasons=tuple(reasons))


@router.get(
    "/app/contact-lines",
    response_model=ContactLinesResponse,
    summary="Which kind of cover is this about (the 'something else' branch)",
)
async def contact_lines(principal: PrincipalDep, container: ContainerDep) -> ContactLinesResponse:
    """The step-1 menu, for a customer asking about cover they do not hold (`D123`).

    `D122` filtered the *"about this plan"* list correctly and left the other branch —
    *"something else"* — showing only `general_reason`, which is account admin. So the app
    could ask about a policy the customer holds and about their own details, and could not
    ask about **cover they do not have yet**. That is journey step 3, the brief's biggest
    leak (`D115`), reachable from the keypad and from nothing else.

    The fix is not a new menu. It is the one the keypad already asks first: *which kind of
    cover?* An app customer who has not tapped a policy has told us exactly as little as a
    caller who has just dialled, so this list is **not filtered** — `contexts` describes
    reasons, and the situation this question establishes is the thing a context names. The
    per-line reason menu that follows is then fetched at `context=general`, where the
    options that only make sense for somebody *without* the cover finally become reachable
    (`travel.advice.quote` — *"ซื้อประกันเดินทาง"* — is the one the user found missing).

    `product_line: unknown` is one of the rows rather than a special case: it is the
    keypad's own *"เรื่องอื่นๆ"*, and following it lands on `general_reason`, which is
    exactly where this branch used to start. Nothing was taken away; a step was put in
    front of it.
    """
    root = container.pack.menus.get("product_line")
    lines = tuple(
        ContactLine(
            key=option.key,
            label_th=option.label_th,
            product_line=str(option.product_line or ProductLine.UNKNOWN),
        )
        for option in (root.options if root else ())
        if option.next_menu
    )
    return ContactLinesResponse(lines=lines)


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


# --- the in-app call, and the screen the broker can fill (D122, D120's third path) --------

#: A call the CUSTOMER is on — which starts the moment they dial, not when somebody
#: answers. Deliberately wider than the token-paired screen's set, and the difference is
#: the point: that screen exists so a BROKER can push, which needs somebody to have
#: accepted; this one exists so the APP can say "you are on a call", and a customer
#: holding for ninety seconds is very much on a call. They are two different questions,
#: so they get two sets rather than one stretched to cover both (`D50`'s argument).
#:
#: ⚠️ `WRAP_UP` is NOT here (`B37`): after-call work is the agent's paperwork, and the
#: customer hung up when the media stopped.
_LIVE_CALL_STATES = (
    CallState.CONNECTING,
    CallState.IVR,
    CallState.QUEUED,
    CallState.MATCHED,
    CallState.OFFERED,
    CallState.IN_CALL,
)

#: The subset where a human is actually on the line, so the app can say which is happening
#: instead of claiming a conversation that has not started.
_CONNECTED_STATES = (CallState.IN_CALL,)


def _assist_item(i: Any) -> dict[str, Any]:
    return {
        "item_id": i.item_id,
        "kind": str(i.kind),
        "title_th": i.title_th,
        "payload": i.payload,
        "responded": i.response is not None,
        "response": i.response,
        "stub": i.stub,
    }


@router.get("/app/assist", summary="Is a call live for me, and what has the broker sent?")
async def app_assist(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    """The in-app half of the paired screen (`D120`'s first and third rows).

    `D120` described three ways a call and a screen become bound and shipped one: the
    link. This is the other two, and they are the same code path — the app **is** the
    paired screen, so there is no token to tap and nothing to send.

    **The tier is `VERIFIED` without a sign-in step, and that is not a shortcut.** The
    customer is already authenticated to the app; `D4`'s session is a far stronger claim
    about who they are than tapping a link ever was. Asking them to sign in *again*, on
    the device they are already signed in on, is the "log in to be helped" pattern the
    link page just stopped doing.

    Returns `call_active: false` when nothing is live, which is what lets the app **offer**
    help rather than show a permanently visible button nobody can find — the reasoning
    `AssistService.live_for` was written for and, until now, had no caller at all.
    """
    live = [
        call
        for call in await container.calls.list_in_states(*_LIVE_CALL_STATES)
        if getattr(call, "customer_id", None) == principal.customer_id
    ]
    if not live:
        # The call is over, but the pairing outlives it briefly (`D120`'s grace). Keep
        # showing what is on screen so a half-finished form can still be sent — and say
        # plainly that the call has ended, rather than pretending it has not (`B37`).
        recent = container.assist.latest_for_customer(principal.customer_id)
        if recent is None or not recent.items:
            return {"call_active": False, "ended": False, "tier": None, "items": []}
        return {
            "call_active": False,
            "ended": True,
            "tier": str(recent.tier),
            "items": [_assist_item(i) for i in recent.items],
        }

    # Newest first: a customer who somehow has two live calls is looking at the one they
    # just started, not the one that is being wrapped up.
    call = max(live, key=lambda c: getattr(c, "started_at", None) or container.clock.now())
    pairing = container.assist.for_call(call.call_session_id)
    if pairing is None:
        pairing = container.assist.open_for_call(
            call.call_session_id, customer_id=principal.customer_id
        )
    session = container.assist.sign_in(pairing.token, customer_id=principal.customer_id)
    return {
        "call_active": True,
        "call_session_id": call.call_session_id,
        "connected": call.state in _CONNECTED_STATES,
        "ended": False,
        "tier": str(session.tier),
        "items": [_assist_item(i) for i in session.items],
    }


@router.post("/app/call/hangup", summary="The customer hangs up")
async def app_hangup(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    """The caller rings off. Until now, nobody could.

    This is not cosmetic. `D113` ships `max_offer_rounds: 0` — a caller the whole floor
    declines circles **forever** — and the argument for that default is explicitly *"a
    caller still holding can hang up whenever they choose"*. That was true of a real
    telephone and false of this system, which had no path for a customer to end a call at
    all. A queue nobody can leave is a different product from the one that decision
    describes.

    A caller can ring off at any point, and there are **three** different endings
    depending on what the rest of the system is doing (`B38`, `B39`):

    | when | what ending it is |
    |---|---|
    | waiting, nobody rung yet | `orchestrator.abandon` |
    | a desk is ringing | `assignments.cancel` — frees that agent, resolves the offer |
    | **an agent is talking to them** | `assignments.end_call` — media disconnect, ACW starts |

    The third is the ordinary case and the one that was missing. `IN_CALL` cannot go to
    `ABANDONED` at all — the only ways out are `WRAP_UP`, `TRANSFERRED` and `FAILED` —
    because a conversation that happened is not an abandoned call, and the agent is owed
    their after-call work either way. Hanging up mid-conversation is *exactly* what the
    agent's own **วางสาย** does; the difference is only which end of the line pressed it.
    """
    live = [
        call
        for call in await container.calls.list_in_states(*_LIVE_CALL_STATES)
        if getattr(call, "customer_id", None) == principal.customer_id
    ]
    if not live:
        raise HTTPException(status_code=404, detail="no live call")
    call = max(live, key=lambda c: getattr(c, "created_at", None) or container.clock.now())

    # If a desk is RINGING for this caller, cancelling the offer is the whole ending, not
    # a step before it (`B38`). `AssignmentService.cancel` — docstring: *"The caller gave
    # up while it was ringing. Nobody did anything wrong."* — moves the call to
    # `ABANDONED`, frees the agent's presence and publishes the resolution. It was written
    # for exactly this and had no caller at all.
    offer = container.assignments.open_offer_for(call.call_session_id)
    accepted = container.assignments.accepted_for_call(call.call_session_id)
    try:
        if accepted is not None:
            # The ordinary case: they are mid-conversation and they hang up. Same ending
            # as the agent pressing วางสาย — the media stops, the call moves to `WRAP_UP`
            # and the agent's after-call work begins (`D45`). They still owe a wrap-up for
            # a call that happened.
            await container.assignments.end_call(
                call, assignment_id=accepted.assignment_id, reason="caller_hung_up"
            )
        elif offer is not None:
            await container.assignments.cancel(
                call, assignment_id=offer.assignment_id, reason="caller_hung_up"
            )
        else:
            await container.orchestrator.abandon(call, reason="caller_hung_up")
    except (IllegalTransition, PermanentError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # AND TAKE THEM OUT OF THE QUEUE. `dispatch.release` had exactly one caller — the
    # ACCEPT path — so a caller who hung up stayed in the waiting pool and went on being
    # offered to desks. The state said `ABANDONED` while the matcher went on routing them,
    # which is `D78`'s hazard exactly: a projection that nothing updates.
    container.dispatch.release(call.call_session_id)
    container.assist.close(call.call_session_id)
    log.info(
        "caller hung up",
        call_session_id=call.call_session_id,
        ending="in_call" if accepted else ("ringing" if offer else "waiting"),
    )
    return {"ok": True, "call_session_id": call.call_session_id}


@router.post("/app/assist/respond", summary="Send a filled form back to the broker")
async def app_assist_respond(
    body: AssistRespondFromApp, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    """The app's own respond path. Same service, no token in the URL.

    The token stays server-side deliberately: on this surface it is not a credential the
    customer holds, it is an internal handle, and putting it in a request the app has to
    remember would invite it being treated as one.
    """
    live = [
        call
        for call in await container.calls.list_in_states(*_LIVE_CALL_STATES)
        if getattr(call, "customer_id", None) == principal.customer_id
    ]
    # The grace window has to cover the SEND, not just the display (`B37`). Looking only
    # at live calls meant the form survived the broker ringing off, sat there fully typed,
    # and then 404'd on submit — which is worse than clearing it, because the customer is
    # told nothing and believes it went.
    pairing = (
        container.assist.for_call(live[0].call_session_id)
        if live
        else container.assist.latest_for_customer(principal.customer_id)
    )
    if pairing is None:
        raise HTTPException(status_code=404, detail="no paired screen")
    try:
        container.assist.respond(pairing.token, item_id=body.item_id, response=body.response)
    except PermanentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
