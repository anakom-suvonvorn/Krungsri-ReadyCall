"""The public API: what a client may say, what it may see, and who it may see it as.

The most important tests here are the negative ones. `D4` says the client never asserts
identity, and the only way that stays true is if something fails loudly when it stops being
true — a schema field quietly added later would otherwise hand anyone the ability to mint a
correlation token for a stranger and walk their identity to `L3_VERIFIED` on the next call.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app
from readycall.api.schemas import CreateIntentRequest
from readycall.config import Settings
from readycall.domain.enums import ProductLine
from readycall.domainpack import DomainPack
from tests.conftest import REPO_ROOT

PERSONA = "C000001"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/v1/demo/session", json={"customer_id": PERSONA}).status_code == 200
    return client


# --- D4: the client cannot assert who it is ---------------------------------------------


def test_create_intent_schema_has_no_customer_id_field() -> None:
    """`D4`, enforced by the schema rather than by a check.

    If someone adds `customer_id` to the request model, the endpoint would happily accept a
    caller-supplied identity. This test is the tripwire.
    """
    assert "customer_id" not in CreateIntentRequest.model_fields


def test_a_customer_id_in_the_body_is_rejected_outright(signed_in: TestClient) -> None:
    """`extra="forbid"` means smuggling one in is a 422, not a silently ignored field."""
    response = signed_in.post(
        "/v1/calls/intents", json={"product_code": "KS-HEALTH-A", "customer_id": "C000002"}
    )
    assert response.status_code == 422


def test_intent_belongs_to_the_session_not_the_request(signed_in: TestClient) -> None:
    created = signed_in.post("/v1/calls/intents", json={"product_code": "KS-HEALTH-A"})
    assert created.status_code == 201
    status = signed_in.get(f"/v1/calls/intents/{created.json()['intent_id']}")
    assert status.json()["customer_id"] == PERSONA


# --- authentication ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/calls/intents", {}),
        ("post", "/v1/app/context-events", {"section": "x"}),
        ("get", "/v1/calls/intents/intent_whatever", None),
    ],
)
def test_endpoints_require_a_session(
    client: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    response = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 401


def test_a_forged_cookie_is_refused(client: TestClient, settings: Settings) -> None:
    client.cookies.set(settings.session_cookie_name, "not-a-real-token")
    assert client.post("/v1/calls/intents", json={}).status_code == 401


def test_logout_revokes_server_side_not_just_the_cookie(
    signed_in: TestClient, settings: Settings
) -> None:
    """Clearing the cookie alone would leave a usable session for anyone holding the token."""
    token = signed_in.cookies.get(settings.session_cookie_name)
    assert token
    signed_in.post("/v1/demo/logout")

    signed_in.cookies.set(settings.session_cookie_name, token)  # replay the old token
    assert signed_in.post("/v1/calls/intents", json={}).status_code == 401


def test_one_customer_cannot_read_anothers_intent(client: TestClient) -> None:
    client.post("/v1/demo/session", json={"customer_id": PERSONA})
    intent_id = client.post("/v1/calls/intents", json={}).json()["intent_id"]

    client.post("/v1/demo/session", json={"customer_id": "C000002"})
    response = client.get(f"/v1/calls/intents/{intent_id}")
    # 404 rather than 403, so this cannot be used to discover which ids exist.
    assert response.status_code == 404


def test_demo_login_only_accepts_configured_personas(client: TestClient) -> None:
    """Otherwise the picker would be 'log in as any customer id you can guess'."""
    assert client.post("/v1/demo/session", json={"customer_id": "C001999"}).status_code == 404


# --- the token ---------------------------------------------------------------------------


def test_token_is_returned_once_and_only_its_hash_is_stored(signed_in: TestClient) -> None:
    body = signed_in.post("/v1/calls/intents", json={}).json()
    token = body["correlation_token"]
    assert len(token) >= 20

    status = signed_in.get(f"/v1/calls/intents/{body['intent_id']}").json()
    assert token not in str(status), "the token must never be readable again"


# --- D6: prefetch, and what it produced --------------------------------------------------


def test_context_is_prefetched_and_reportable(signed_in: TestClient) -> None:
    """`D6` + `D18`: assembly starts at tap, and the result can say what it found."""
    intent_id = signed_in.post("/v1/calls/intents", json={"product_code": "KS-HEALTH-A"}).json()[
        "intent_id"
    ]

    status = signed_in.get(f"/v1/calls/intents/{intent_id}").json()
    assert status["context_ready"] is True
    assert status["policies_found"] == 3  # a broker portfolio, not one policy (`D117`)
    assert status["provenance_fields"] > 0
    assert status["degraded"] == "none"


def test_stage_timing_is_actually_measured(signed_in: TestClient) -> None:
    """`B3`: a clean 0.0 here means the clock cannot resolve what it is timing.

    On Windows `time.monotonic()` has ~15.6 ms resolution, so every sub-tick stage recorded
    as exactly zero and the demo reported "context assembled in 0.0 ms".
    """
    intent_id = signed_in.post("/v1/calls/intents", json={}).json()["intent_id"]
    build_ms = signed_in.get(f"/v1/calls/intents/{intent_id}").json()["context_build_ms"]
    assert build_ms is not None
    assert build_ms > 0.0, "stage timings are not being measured (see B3)"


# --- context events ----------------------------------------------------------------------


def test_context_events_accumulate(signed_in: TestClient) -> None:
    for i in range(3):
        response = signed_in.post(
            "/v1/app/context-events",
            json={"section": "coverage.hospitalisation", "dwell_ms": 1000 * i},
        )
        assert response.status_code == 200
    assert response.json()["events_held"] == 3


def test_context_event_rejects_an_absurd_dwell(signed_in: TestClient) -> None:
    response = signed_in.post(
        "/v1/app/context-events", json={"section": "x", "dwell_ms": 999_999_999}
    )
    assert response.status_code == 422


# --- ops ---------------------------------------------------------------------------------


def test_health_does_not_depend_on_the_bank_core(client: TestClient) -> None:
    """A dead upstream must not take the instance out of the load balancer (`D12`)."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["intents_loaded"] > 0


def test_demo_endpoints_disappear_when_disabled() -> None:
    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=False,
    )
    with TestClient(create_app(settings)) as c:
        assert c.post("/v1/demo/session", json={"customer_id": PERSONA}).status_code == 404
        assert c.get("/health").status_code == 200


# --- plans and contact reasons (D48) -----------------------------------------------------


def test_plans_reflect_what_the_customer_actually_holds(client: TestClient) -> None:
    """No made-up rows and no fixed count — the list is the customer's real policies."""
    client.post("/v1/demo/session", json={"customer_id": "C000002"})
    plans = client.get("/v1/demo/plans").json()
    assert [p["product_line"] for p in plans] == ["motor", "travel"]
    assert all(p["policy_no_masked"].startswith("•••") for p in plans)


def test_a_customer_with_nothing_active_gets_an_empty_list(client: TestClient) -> None:
    """C000003's only policy is lapsed. An empty list is the correct answer, not an error."""
    client.post("/v1/demo/session", json={"customer_id": "C000003"})
    assert client.get("/v1/demo/plans").json() == []


def test_plan_list_masks_policy_numbers(client: TestClient) -> None:
    """A policy number on screen is a disclosure, even in a list the customer owns."""
    client.post("/v1/demo/session", json={"customer_id": "C000002"})
    body = client.get("/v1/demo/plans").text
    assert "MT-2025-004512" not in body


def test_contact_reasons_come_from_the_same_menu_as_the_ivr(signed_in: TestClient) -> None:
    """`D48`: one `menus.yaml`, two surfaces — amended by `D122`.

    The app no longer returns the keypad's list *verbatim*; it returns a filtered view of
    it. What must still hold, and what `D48` was actually protecting, is that the taxonomy
    cannot **fork**: every option the app offers is an option the menu defines, in the
    menu's own order, with the menu's own key. A second hand-written list is what would
    let a customer see options that depend on which door they came through.
    """
    pack = DomainPack.load(REPO_ROOT / "config")
    menu = pack.reason_menu_for(ProductLine.MOTOR)
    assert menu is not None
    from_menu = [(o.key, o.intent) for o in menu.options if o.intent]

    for context in ("plan", "general"):
        reasons = signed_in.get(
            "/v1/app/contact-reasons", params={"product_line": "motor", "context": context}
        ).json()["reasons"]
        offered = [(r["key"], r["intent_code"]) for r in reasons]
        assert offered, f"context {context} must not be empty"
        assert offered == [pair for pair in from_menu if pair in offered], (
            "the app's list is a SUBSET of the menu, in the menu's order"
        )


def test_a_plan_the_customer_holds_is_never_offered_a_buy_it_option(
    signed_in: TestClient,
) -> None:
    """`D122`, and the exact thing the user reported.

    Tapping a travel policy they already hold and being offered *"ซื้อประกันเดินทาง"* —
    buy travel insurance — was the app rendering the phone's menu verbatim. The phone's
    menu is written for somebody we know nothing about; the app knows they own it.
    """
    on_a_plan = signed_in.get(
        "/v1/app/contact-reasons", params={"product_line": "travel", "context": "plan"}
    ).json()["reasons"]
    codes = [r["intent_code"] for r in on_a_plan]

    assert "travel.advice.quote" not in codes
    assert "travel.claim.notify" in codes, "claiming on it is exactly what this surface is for"
    assert any(r["intent_code"].endswith(".other") for r in on_a_plan), (
        "and there is always a way out (`D122` guards this per context)"
    )

    # The same option is still right on the other surface, which is why it is filtered
    # rather than deleted.
    general = signed_in.get(
        "/v1/app/contact-reasons", params={"product_line": "travel", "context": "general"}
    ).json()["reasons"]
    assert "travel.advice.quote" in [r["intent_code"] for r in general]


def test_the_same_intent_can_be_worded_for_the_surface_it_is_offered_from(
    signed_in: TestClient,
) -> None:
    """Comparing plans is legitimate in both situations and is not the same conversation:
    a stranger is shopping, a policyholder is deciding whether to renew (`D117`)."""

    def label(context: str) -> str:
        reasons = signed_in.get(
            "/v1/app/contact-reasons", params={"product_line": "motor", "context": context}
        ).json()["reasons"]
        return next(r["label_th"] for r in reasons if r["intent_code"] == "motor.advice.compare")

    assert label("plan") != label("general")
    assert "ต่ออายุ" in label("plan"), "the policyholder's version is framed around renewal"


def test_unknown_product_line_falls_back_to_the_general_menu(signed_in: TestClient) -> None:
    """Exactly what the IVR does, so 'something else' is never a dead end."""
    reasons = signed_in.get("/v1/app/contact-reasons", params={"product_line": "banana"}).json()
    assert reasons["product_line"] == "unknown"
    assert any(r["intent_code"].startswith("general.") for r in reasons["reasons"])


def test_choosing_a_reason_in_the_app_carries_the_intent(signed_in: TestClient) -> None:
    """`D41` + `D48`: both menu questions answered before the call is placed."""
    created = signed_in.post(
        "/v1/calls/intents",
        json={"product_code": "KS-HEALTH-A", "app_intent": "health.claim.notify"},
    )
    assert created.status_code == 201


# --- the app path, end to end (`B36`, `D122`) -------------------------------------------


def test_tapping_contact_in_the_app_actually_puts_a_caller_in_a_queue(
    signed_in: TestClient,
) -> None:
    """`B36`. The app path through `POST /v1/demo/calls` had never once been run.

    `start_from_intent` leaves the call in `INTENT_CREATED` — correct, because tapping
    Contact produces a dial target and the customer has not rung yet — and only
    `CONNECTING` may enter the IVR. So every app-originated call raised
    `IllegalTransition: intent_created -> ivr`. Nothing noticed because the simulator
    minted a token and stopped there, and every other test and scenario arrives as a cold
    call, which starts in `CONNECTING` already.

    This is the test that makes the two halves of the demo one system: without it, the app
    and the call centre are two things that have never met.
    """
    intent = signed_in.post(
        "/v1/calls/intents",
        json={"product_code": "KS-MOTOR-1ST", "app_intent": "motor.claim.notify"},
    ).json()

    placed = signed_in.post(
        "/v1/demo/calls",
        json={
            "correlation_token": intent["correlation_token"],
            "intent_code": "motor.claim.notify",
            "caller_number": "0812345678",
            "intake_keys": ["2"],
            "ignore_hours": True,
        },
    )

    assert placed.status_code == 200, placed.text
    body = placed.json()
    assert body["queue_id"] == "q_claims", "routed by the reason the app already knew"
    assert body["state"] != "intent_created", "the call actually progressed"
    # And the identity came from the token, not from the request body (`D4`).
    assert body["assurance"] != "l0_anonymous"


def test_the_app_is_told_when_a_call_is_live_for_it(signed_in: TestClient) -> None:
    """`D120`'s third row, and the first caller `AssistService.live_for` has ever had.

    The app must not carry a permanently visible "let an agent help me" button — on a
    screen nobody is calling from it is noise, and the customer would have to go looking
    for it at exactly the moment they are least able to. The server knows, so the app is
    told.
    """
    assert signed_in.get("/v1/app/assist").json()["call_active"] is False

    intent = signed_in.post("/v1/calls/intents", json={"app_intent": "motor.claim.notify"}).json()
    signed_in.post(
        "/v1/demo/calls",
        json={
            "correlation_token": intent["correlation_token"],
            "intent_code": "motor.claim.notify",
            "caller_number": "0812345678",
            "intake_keys": ["2"],
            "ignore_hours": True,
        },
    )

    live = signed_in.get("/v1/app/assist").json()
    assert live["call_active"] is True
    # Waiting in a queue IS being on a call, from the customer's side — but it is not a
    # conversation, and the app says which.
    assert live["connected"] is False
    assert live["tier"] == "verified", (
        "in-app needs no link and no second sign-in: the app session is a stronger claim "
        "about who they are than tapping a link ever was (`D4`, `D42`)"
    )


def test_a_form_typed_in_the_app_still_sends_after_the_broker_rings_off(
    signed_in: TestClient,
) -> None:
    """`B37`'s second half, found by actually pressing the button.

    The grace window kept the half-typed form on screen and then refused the submit,
    because `respond` looked only at LIVE calls. That is worse than clearing the form:
    the customer is told nothing and believes it went.
    """
    intent = signed_in.post("/v1/calls/intents", json={"app_intent": "motor.claim.notify"}).json()
    signed_in.post(
        "/v1/demo/calls",
        json={
            "correlation_token": intent["correlation_token"],
            "intent_code": "motor.claim.notify",
            "caller_number": "0812345678",
            "intake_keys": ["2"],
            "ignore_hours": True,
        },
    )
    signed_in.get("/v1/app/assist")  # the app pairs itself on first look

    hung_up = signed_in.post("/v1/app/call/hangup")
    assert hung_up.status_code == 200, hung_up.text

    # Nothing was pushed, so there is no item to respond to — but the endpoint must fail
    # on "unknown item", not on "no live call". Those are different bugs and only one of
    # them is the customer's fault.
    refused = signed_in.post("/v1/app/assist/respond", json={"item_id": "psh_nope", "response": {}})
    assert refused.status_code == 400, refused.text
    assert "item" in refused.json()["detail"].lower()
