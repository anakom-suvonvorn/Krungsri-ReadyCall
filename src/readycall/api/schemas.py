"""Request and response DTOs for the public API.

These are deliberately *not* the domain models. Two reasons, and the first is the one that
matters here:

* **A request DTO defines what a client is allowed to say.** `CreateIntentRequest` has no
  `customer_id` field, so there is no way for a caller to assert an identity even by
  accident — the omission is the enforcement of `D4`, not a comment about it.
* A response DTO stops internal shape leaking into a public contract. `CallIntent` carries
  `correlation_token_hash`; the response carries the *token*, once, and never again.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- POST /v1/calls/intents -------------------------------------------------------------


class CreateIntentRequest(ApiModel):
    """What the app sends when the customer taps Contact.

    Note what is absent: `customer_id`. It comes from the session (`D4`).
    """

    product_code: str | None = Field(
        default=None, max_length=64, description="the plan they tapped Contact from"
    )
    plan_id: str | None = Field(default=None, max_length=64)
    entry_screen: str | None = Field(
        default=None,
        max_length=128,
        description="the screen the tap came from, e.g. 'coverage.hospitalisation'",
    )
    app_intent: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "intent hint derived from the screen (`D41`). A hint, weighted like a menu "
            "answer - speech may still override it later."
        ),
    )
    preferred_channel: str = Field(default="pstn", max_length=16)


class CreateIntentResponse(ApiModel):
    intent_id: str
    #: The bearer credential the call carries. Returned **once**; only its hash is stored.
    correlation_token: str
    dial_target: str
    expires_at: datetime


class IntentStatusResponse(ApiModel):
    """What the app (or the simulator) can see about an intent afterwards.

    This is what makes the pitch's central claim visible rather than asserted: the context
    was assembled *before the phone rang*, and here is how long it took and where every
    field came from.
    """

    intent_id: str
    customer_id: str
    expires_at: datetime
    expired: bool
    context_ready: bool
    context_build_ms: float | None = None
    snapshot_id: str | None = None
    policies_found: int = 0
    interactions_found: int = 0
    provenance_fields: int = 0
    degraded: str | None = None


# --- POST /v1/app/context-events ---------------------------------------------------------


class ContextEventRequest(ApiModel):
    """A screen the customer looked at, so *"viewed hospitalisation coverage"* is real."""

    product_code: str | None = Field(default=None, max_length=64)
    section: str = Field(max_length=128)
    dwell_ms: int = Field(default=0, ge=0, le=60 * 60 * 1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextEventResponse(ApiModel):
    accepted: bool
    events_held: int


# --- GET /v1/app/contact-reasons ---------------------------------------------------------


class ContactReason(ApiModel):
    """One reason a customer might be calling, for the in-app picker.

    These come from the **same `menus.yaml` the IVR reads** (`D28`). If the app offered a
    different list from the keypad, a customer would get different options depending on
    which door they came through, and the intent taxonomy would quietly fork in two.
    """

    intent_code: str
    label_th: str
    #: The keypad digit this reason corresponds to in the IVR. Shown so the app and the
    #: phone menu are visibly the same menu.
    key: str | None = None


class ContactReasonsResponse(ApiModel):
    product_line: str
    reasons: tuple[ContactReason, ...]


# --- demo only ---------------------------------------------------------------------------


class AppPlan(ApiModel):
    """A policy the customer actually holds, for the simulator's plan list.

    Demo scaffolding: the real Krungsri app already knows the customer's plans from its own
    screens and would never ask us for them. Kept under `/v1/demo/` so the public `/v1`
    surface stays exactly what a real client would call.
    """

    product_code: str
    product_line: str
    name_th: str
    policy_no_masked: str
    status: str


class DemoPersona(ApiModel):
    customer_id: str
    display_name: str
    summary: str
    product_code: str | None = None
    entry_screen: str | None = None
    app_intent: str | None = None


class DemoLoginRequest(ApiModel):
    customer_id: str = Field(max_length=64)


class DemoLoginResponse(ApiModel):
    customer_id: str
    display_name: str


# --- shared ------------------------------------------------------------------------------


class HealthResponse(ApiModel):
    status: str
    version: str
    core_data_provider: str
    session_resolver: str
    intents_loaded: int


class ErrorResponse(ApiModel):
    detail: str
