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

from readycall.api.deps import ContainerDep
from readycall.api.schemas import DemoLoginRequest, DemoLoginResponse, DemoPersona
from readycall.api.security import DemoSessionStore
from readycall.errors import ConfigError
from readycall.logging import get_logger

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
