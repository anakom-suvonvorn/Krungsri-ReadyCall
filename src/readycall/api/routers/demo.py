"""DEMO ONLY: the persona picker that stands in for the bank's login (`D47`).

Everything here is a stand-in, gated behind `demo_login_enabled`. It exists because the
customer simulator needs *some* way to become an authenticated customer, and building a
real identity provider is both out of scope for the competition and beside the point — we
are a context layer on top of Krungsri's systems, not an auth vendor.

What is **not** a stand-in is the shape: a token issued server-side, a server-held mapping
to a customer, and an HttpOnly cookie. `/v1/calls/intents` cannot tell a demo session from
a real one, so replacing this with the bank's OIDC changes this file and nothing else.

Note what this module does *not* do: add a `list_customers` method to `CoreDataProvider`.
A browse endpoint is not something the real system needs, and putting it on the port would
oblige every future adapter — including one written under time pressure on hackathon
morning — to implement it. Instead the persona *ids* come from `config/demo_personas.yaml`
and everything displayed is read through port methods that already exist, so the picker
shows exactly the data an agent would see.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request, Response, status

from readycall.api.deps import ContainerDep, PrincipalDep
from readycall.api.schemas import (
    AppPlan,
    DemoLoginRequest,
    DemoLoginResponse,
    DemoPersona,
    PlaceCallRequest,
    PlaceCallResponse,
)
from readycall.api.security import DemoSessionStore
from readycall.domain.enums import CallState, ProductLine, Urgency
from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.services.identity.resolver import hash_token
from readycall.services.matching.scoring import WaitingCall

log = get_logger(__name__)
router = APIRouter(prefix="/v1/demo", tags=["demo"])


@dataclass(frozen=True, slots=True)
class PersonaSpec:
    customer_id: str
    entry_screen: str | None
    app_intent: str | None
    product_code: str | None
    blurb_en: str
    blurb_th: str


def load_personas(config_dir: Path) -> tuple[PersonaSpec, ...]:
    path = Path(config_dir) / "demo_personas.yaml"
    if not path.exists():
        return ()
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    out: list[PersonaSpec] = []
    for entry in raw.get("personas") or []:
        try:
            out.append(
                PersonaSpec(
                    customer_id=entry["customer_id"],
                    entry_screen=entry.get("entry_screen"),
                    app_intent=entry.get("app_intent"),
                    product_code=entry.get("product_code"),
                    blurb_en=entry.get("blurb_en", ""),
                    blurb_th=entry.get("blurb_th", ""),
                )
            )
        except KeyError as exc:
            raise ConfigError(f"demo persona is malformed: missing {exc}") from exc
    return tuple(out)


def _require_demo(container: ContainerDep) -> None:
    if not container.settings.demo_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="demo endpoints are disabled")


@router.get("/personas", response_model=list[DemoPersona], summary="Who you can log in as")
async def list_personas(container: ContainerDep) -> list[DemoPersona]:
    _require_demo(container)

    personas: list[DemoPersona] = []
    for spec in load_personas(container.settings.config_dir):
        customer = await container.core.get_customer(spec.customer_id)
        if customer is None:
            # A persona pointing at a customer the core does not have is a config error,
            # but not a reason to break the picker - skip it and say so.
            log.warning("demo persona has no customer", customer_id=spec.customer_id)
            continue
        policies = await container.core.list_policies(spec.customer_id, active_only=True)
        held = ", ".join(str(p.line) for p in policies) or "no active policies"
        personas.append(
            DemoPersona(
                customer_id=customer.customer_id,
                display_name=customer.polite_name_th,
                summary=f"{held} · {spec.blurb_th or spec.blurb_en}",
                product_code=spec.product_code,
                entry_screen=spec.entry_screen,
                app_intent=spec.app_intent,
            )
        )
    return personas


@router.get("/plans", response_model=list[AppPlan], summary="The signed-in customer's plans")
async def list_plans(principal: PrincipalDep, container: ContainerDep) -> list[AppPlan]:
    """Demo scaffolding. The real app already knows the customer's plans.

    Kept under `/v1/demo/` rather than `/v1/app/` precisely because a real client would
    never call it — the public surface stays exactly what the real Krungsri app would use.
    """
    _require_demo(container)

    plans: list[AppPlan] = []
    for policy in await container.core.list_policies(principal.customer_id, active_only=True):
        product = await container.core.get_product(policy.product_code)
        plans.append(
            AppPlan(
                product_code=policy.product_code,
                product_line=str(policy.line),
                name_th=product.name_th if product else policy.product_code,
                # Masked even here: this is a list view, and a policy number on screen is
                # a disclosure. The agent side gates it on assurance for the same reason.
                policy_no_masked="•••" + policy.policy_no[-4:],
                status=str(policy.status),
            )
        )
    return plans


@router.post("/session", response_model=DemoLoginResponse, summary="Log in as a persona")
async def demo_login(
    body: DemoLoginRequest, response: Response, container: ContainerDep
) -> DemoLoginResponse:
    _require_demo(container)

    allowed = {p.customer_id for p in load_personas(container.settings.config_dir)}
    if body.customer_id not in allowed:
        # Only the configured personas, so this never becomes "log in as any customer id".
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such persona")

    customer = await container.core.get_customer(body.customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such persona")

    sessions = container.sessions
    if not isinstance(sessions, DemoSessionStore):  # pragma: no cover - config guard
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not running a demo session store")

    token = sessions.issue(customer.customer_id)
    response.set_cookie(
        key=container.settings.session_cookie_name,
        value=token,
        httponly=True,  # the page's own JS must never be able to read a session credential
        samesite="lax",
        max_age=int(container.settings.session_ttl_s),
    )
    log.info("demo login", customer_id=customer.customer_id)
    return DemoLoginResponse(customer_id=customer.customer_id, display_name=customer.polite_name_th)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Drop the session")
async def demo_logout(request: Request, response: Response, container: ContainerDep) -> None:
    _require_demo(container)
    # Revoke server-side as well as clearing the cookie. Deleting only the cookie leaves a
    # perfectly valid session sitting in the store for anyone who kept a copy of the token.
    token = request.cookies.get(container.settings.session_cookie_name)
    sessions = container.sessions
    if isinstance(sessions, DemoSessionStore):
        sessions.revoke(token)
    response.delete_cookie(container.settings.session_cookie_name)


# --- DEMO: a caller arriving, standing in for telephony -------------------------------
#
# Real calls arrive through `TelephonyProvider` at P5 (Asterisk/ARI). Until then the
# workstation needs *some* way to have a caller on the line, so this walks one call
# through the real lifecycle: identity resolution, IVR, queue, match, offer. Every step
# below is the production service - only the trigger is fake.
#
# `# P2b:` marks the steps a real IVR will drive. `grep -rn "# P2b:" src/` lists them.


@router.post("/calls", response_model=PlaceCallResponse, summary="DEMO: place a call")
async def place_call(
    body: PlaceCallRequest, container: ContainerDep, request: Request
) -> PlaceCallResponse:
    _require_demo(container)

    # `D4` again, on the demo path: the correlation token is the only thing that binds a
    # call to an identity. A body field naming the customer would undo the whole ladder.
    resolution = await container.identity.resolve(
        correlation_token=body.correlation_token,
        caller_number=body.caller_number,
    )

    intent = None
    if body.correlation_token:
        intent = await container.intent_store.get_by_token_hash(hash_token(body.correlation_token))

    if intent is not None and resolution.customer_id:
        session = await container.orchestrator.start_from_intent(
            intent_id=intent.intent_id,
            customer_id=resolution.customer_id,
            product_code=intent.product_code,
            product_line=(
                container.pack.intent(body.intent_code).line
                if body.intent_code
                else ProductLine.UNKNOWN
            ),
        )
    else:
        session = await container.orchestrator.start_cold_call(
            caller_number=body.caller_number,
            dialled_did=body.did,
        )

    container.identity_for_call[session.call_session_id] = resolution

    # The context snapshot: reuse the one the app path already built (`D6`), or build one
    # now for a cold call. Reuse is the point - by the time the phone rings, the six reads
    # to the bank core have already happened.
    snapshot_id = None
    if intent is not None:
        snapshot_id = container.snapshot_for_intent.get(intent.intent_id)
    if snapshot_id is None and resolution.customer_id:
        snapshot = await container.assembler.build(customer_id=resolution.customer_id)
        await container.snapshots.save(snapshot)
        snapshot_id = snapshot.snapshot_id
    if snapshot_id:
        container.snapshot_for_call[session.call_session_id] = snapshot_id

    # P2b: the IVR walk is a real menu walk at P3; here the caller's choices arrive in
    # the request body because there is no audio yet.
    intent_code = body.intent_code
    if body.keys:
        walk = container.pack.walk_menu(list(body.keys))
        intent_code = walk.intent_code or intent_code
    queue_id = container.pack.queue_for_intent(intent_code) if intent_code else "q_general"
    spec = container.pack.queues[queue_id]

    await container.orchestrator.enter_ivr(session)
    session.menu_path = tuple(body.keys)
    session.menu_intent_code = intent_code
    session.snapshot_id = snapshot_id
    await container.orchestrator.enqueue(session, queue_id=queue_id)

    # A closed queue is a routing outcome, not an error (`D25`). Say so plainly rather
    # than letting the caller sit in a queue nobody is staffing.
    hours = container.hours.state(spec.hours, container.clock.now())
    if not hours.is_open:
        await container.orchestrator.transition(
            session, CallState.VOICEMAIL, reason=f"queue_closed:{hours.closed_reason}"
        )
        return PlaceCallResponse(
            call_session_id=session.call_session_id,
            state=str(session.state),
            queue_id=queue_id,
            queue_open=False,
            closed_reason=hours.closed_reason,
            next_open_at=hours.next_open_at,
            assurance=str(resolution.assurance),
        )

    await container.orchestrator.transition(session, CallState.MATCHED, reason="ready_for_matching")
    container.dispatch.admit(
        session,
        WaitingCall(
            call_session_id=session.call_session_id,
            queue_id=queue_id,
            required_skill=spec.required_skill,
            intent_code=intent_code or "unknown",
            intent_urgency=(
                container.pack.intent(intent_code).default_urgency
                if intent_code
                else Urgency.NORMAL
            ),
            waiting_s=body.waited_s,
            sla_seconds=spec.sla_seconds,
        ),
    )
    result = await container.dispatch.tick()

    return PlaceCallResponse(
        call_session_id=session.call_session_id,
        state=str(session.state),
        queue_id=queue_id,
        queue_open=True,
        assurance=str(resolution.assurance),
        offered_to=result.offered[0] if result.offered else None,
        unplaced_reason=result.unplaced.get(session.call_session_id),
    )
