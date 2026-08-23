"""The agent workstation over HTTP: the whole handshake, driven the way a browser does.

These are the tests that would have caught the wiring being wrong, which unit tests of
the services underneath cannot: an endpoint that reads `agent_id` from a body, a brief
rendered before the assurance gate, a capture that leaks its digits into a response.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app
from readycall.clock import ManualClock
from readycall.config import Settings
from tests.conftest import REPO_ROOT


@pytest.fixture
def clock() -> ManualClock:
    """A Monday morning in Bangkok — deliberately **not** `ManualClock()`'s default.

    That default is 09:00 UTC on 1 January = 16:00 Bangkok on New Year's Day, so every
    queue on the `business` schedule is closed and a placed call comes back
    `queue_closed:holiday` instead of an offer. This cost an hour once; see the note on
    `ManualClock`.
    """
    return ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC))  # 10:00 Asia/Bangkok


@pytest.fixture
def client(clock: ManualClock) -> Any:
    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
    )
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


def sign_in_agent(client: Any, agent_id: str = "A006") -> dict[str, Any]:
    response = client.post("/v1/agent/demo-login", json={"agent_id": agent_id})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def place_call(client: Any, **kwargs: Any) -> dict[str, Any]:
    """A caller arrives. Stands in for telephony until P5."""
    response = client.post("/v1/demo/calls", json=kwargs)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- session and presence ----------------------------------------------------------------


def test_an_agent_signs_in_not_ready(client: Any) -> None:
    """`D51`: the platform never asserts a state the person did not choose."""
    body = sign_in_agent(client)
    assert body["agent_intent"] == "not_ready"
    assert body["offerable"] is False
    assert body["display_name"], "the workstation shows a name, not an id"


def test_the_workstation_needs_an_agent_session(client: Any) -> None:
    assert client.get("/v1/agent/me").status_code == 401


def test_a_customer_session_is_not_an_agent_session(client: Any) -> None:
    """The reason staff auth is a separate store and a separate cookie."""
    client.post("/v1/demo/session", json={"customer_id": "C000001"})
    assert client.cookies.get("readycall_session"), "a customer session exists"
    assert client.get("/v1/agent/me").status_code == 401


def test_declaring_ready_makes_an_agent_offerable(client: Any) -> None:
    sign_in_agent(client)
    body = client.post("/v1/agent/state", json={"agent_intent": "ready"}).json()
    assert body["agent_intent"] == "ready"
    assert body["offerable"] is True


def test_an_agent_cannot_declare_not_ready(client: Any) -> None:
    sign_in_agent(client)
    response = client.post("/v1/agent/state", json={"agent_intent": "not_ready"})
    assert response.status_code == 400


def test_the_request_schema_has_no_agent_id_anywhere_it_matters() -> None:
    """The structural half of the rule, asserted the way `D4`'s customer test is.

    A workstation that could name its own agent id would let any signed-in agent accept
    someone else's offer or attest an identity in their name.
    """
    from readycall.api import schemas

    for name in (
        "DeclareStateRequest",
        "DeclineOfferRequest",
        "EndCallRequest",
        "WrapupRequest",
        "AttestIdentityRequest",
        "CaptureKeysRequest",
    ):
        model = getattr(schemas, name)
        assert "agent_id" not in model.model_fields, f"{name} must not accept an agent_id"


# --- a whole call ---------------------------------------------------------------------------


def test_a_call_is_offered_accepted_ended_and_wrapped(client: Any, clock: ManualClock) -> None:
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    placed = place_call(client, intent_code="health.ipd.preauth", caller_number="0812345678")
    assert placed["offered_to"] == "A006", placed

    snapshot = client.get("/v1/agent/me").json()
    offer = snapshot["offer"]
    assert offer is not None
    assert offer["call_session_id"] == placed["call_session_id"]
    assert offer["rationale_th"], "the offer card says WHY this agent (`D22`)"

    accepted = client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").json()
    assert accepted["presence"]["system_state"] == "on_call"
    assert accepted["active_call_session_id"] == placed["call_session_id"]

    clock.advance(240)
    ended = client.post(
        f"/v1/agent/calls/{placed['call_session_id']}/end", json={"reason": "caller_hung_up"}
    ).json()
    assert ended["presence"]["system_state"] == "after_call_work"
    assert ended["presence"]["acw_seconds"] == pytest.approx(0.0, abs=0.01)

    clock.advance(30)
    saved = client.post(
        f"/v1/agent/calls/{placed['call_session_id']}/wrapup",
        json={"disposition": "advice_given", "notes": "ห้องเดี่ยว 4,000/คืน"},
    ).json()
    # The call RECORD closed; the AGENT is still wrapping (`D45`).
    assert saved["presence"]["system_state"] == "after_call_work"
    assert saved["presence"]["acw_seconds"] == pytest.approx(30.0, abs=0.01)

    ready = client.post("/v1/agent/state", json={"agent_intent": "ready"}).json()
    assert ready["system_state"] == "available"
    assert ready["acw_seconds"] is None


def test_going_to_lunch_also_ends_after_call_work(client: Any, clock: ManualClock) -> None:
    """Not only Ready (`D45`) — and lunch leaves them un-offerable, with no special case."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = place_call(client, intent_code="health.ipd.preauth")
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    client.post(f"/v1/agent/calls/{placed['call_session_id']}/end", json={})

    clock.advance(20)
    body = client.post("/v1/agent/state", json={"agent_intent": "lunch"}).json()
    assert body["system_state"] == "available"
    assert body["agent_intent"] == "lunch"
    assert body["offerable"] is False
    assert body["acw_seconds"] is None


def test_declining_re_offers_to_somebody_else(client: Any) -> None:
    """`D52`: the caller must not watch the same desk not answer, forever."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    # A005 also holds health.claim; sign them in through a second cookie jar so both are
    # available at once. Same app, different session.
    second = client.__class__(client.app)
    second.post("/v1/agent/demo-login", json={"agent_id": "A005"})
    second.post("/v1/agent/state", json={"agent_intent": "ready"})

    placed = place_call(client, intent_code="health.claim.status")
    first_offer = client.get("/v1/agent/me").json()["offer"]
    if first_offer is None:
        first_offer = second.get("/v1/agent/me").json()["offer"]
        declining, waiting = second, client
    else:
        declining, waiting = client, second

    declining.post(
        f"/v1/agent/offers/{first_offer['assignment_id']}/decline",
        json={"reason": "wrong_language"},
    )

    # The other agent now has it, and the decliner does not.
    assert waiting.get("/v1/agent/me").json()["offer"] is not None
    assert declining.get("/v1/agent/me").json()["offer"] is None
    assert declining.get("/v1/agent/me").json()["presence"]["agent_intent"] == "ready", (
        "a decline is a person telling us something; they stay ready"
    )
    assert placed["call_session_id"]


# --- identity (D42) -----------------------------------------------------------------------------


def active_call(client: Any) -> str:
    return str(client.get("/v1/agent/me").json()["active_call_session_id"])


def take_a_call(client: Any, intent_code: str = "health.ipd.preauth") -> str:
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place_call(client, intent_code=intent_code, caller_number="0812345678")
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    return active_call(client)


def test_confirming_an_identity_requires_naming_the_challenge(client: Any) -> None:
    """An unfalsifiable audit row is exactly what `D42` exists to prevent."""
    call_id = take_a_call(client)
    response = client.post(f"/v1/agent/calls/{call_id}/identity", json={"outcome": "confirmed"})
    assert response.status_code == 400
    assert "challenge" in response.json()["detail"]


def test_a_third_party_does_not_unlock_disclosure(client: Any) -> None:
    """A daughter with her father's documents is not her father."""
    call_id = take_a_call(client)
    body = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "third_party", "relationship": "ลูกสาว"},
    ).json()
    assert body["identity"]["may_disclose_policy_details"] is False
    assert body["identity"]["authority_check_required"] is True


def test_rejecting_the_guess_drops_to_anonymous(client: Any) -> None:
    call_id = take_a_call(client)
    body = client.post(
        f"/v1/agent/calls/{call_id}/identity", json={"outcome": "not_this_person"}
    ).json()
    assert body["identity"]["assurance"] == "l0_anonymous"
    assert body["identity"]["customer_id"] is None


# --- keypad capture (D44) -------------------------------------------------------------


def test_a_capture_never_returns_its_digits(client: Any) -> None:
    """The inverted default: we do not know what these digits are, so they are sensitive."""
    call_id = take_a_call(client)
    capture = client.post(f"/v1/agent/calls/{call_id}/capture").json()
    for digit in "2025004512":
        body = client.post(
            f"/v1/agent/captures/{capture['capture_id']}/keys", json={"digits": digit}
        ).json()

    assert "digits" not in body
    assert body["masked"].endswith("12")
    assert body["masked"].count("•") == 8
    assert body["length"] == 10


def test_a_lookup_returns_evidence_and_changes_nothing(client: Any) -> None:
    """The whole discipline of `D44`, asserted rather than commented."""
    call_id = take_a_call(client)
    before = client.get("/v1/agent/me").json()["identity"]["assurance"]

    capture = client.post(f"/v1/agent/calls/{call_id}/capture").json()
    client.post(f"/v1/agent/captures/{capture['capture_id']}/keys", json={"digits": "999999"})
    body = client.post(
        f"/v1/agent/captures/{capture['capture_id']}/lookup", json={"kind": "policy_number"}
    ).json()

    assert body["lookups"][0]["matched"] is False
    after = client.get("/v1/agent/me").json()["identity"]["assurance"]
    assert after == before, "a lookup must never move assurance on its own"


def test_discarding_a_capture_removes_the_digits(client: Any) -> None:
    call_id = take_a_call(client)
    capture = client.post(f"/v1/agent/calls/{call_id}/capture").json()
    client.post(f"/v1/agent/captures/{capture['capture_id']}/keys", json={"digits": "1234"})
    body = client.post(f"/v1/agent/captures/{capture['capture_id']}/discard").json()
    assert body["state"] == "discarded"
    assert body["length"] == 0
    assert body["masked"] == ""


def test_control_keys_are_not_data(client: Any) -> None:
    """`*` and `#` are how a caller corrects themselves, not part of the value."""
    call_id = take_a_call(client)
    capture = client.post(f"/v1/agent/calls/{call_id}/capture").json()
    response = client.post(
        f"/v1/agent/captures/{capture['capture_id']}/keys", json={"digits": "12#"}
    )
    assert response.status_code == 422, "rejected by the schema, before any service sees it"


# --- queues --------------------------------------------------------------------------------------


def test_the_queue_strip_says_when_a_closed_queue_reopens(client: Any) -> None:
    sign_in_agent(client)
    queues = client.get("/v1/agent/queues").json()
    by_id = {q["queue_id"]: q for q in queues}
    assert by_id["q_motor_claim"]["is_open"] is True, "a crash does not check the clock"
    closed = [q for q in queues if not q["is_open"]]
    for queue in closed:
        assert queue["closed_reason"] in {"outside_hours", "holiday"}
        assert queue["next_open_at"], "a closed queue must say when it opens again (`D25`)"


# --- the disclosure gate is the SHAPE of the payload (D42) ------------------------


def test_the_l1_payload_does_not_contain_what_l1_may_not_see(client: Any) -> None:
    """The regression guard for a real leak.

    `render_brief` used to return `CaseBrief.model_dump()`. The domain object embeds the
    whole frozen `ContextSnapshot`, so the response carried the policy number, the sum
    insured, every coverage figure and the customer's date of birth — in the *same body*
    that said `may_disclose_policy_details: false`.

    Asserting on rendered Thai lines would not have caught it, because those were
    correctly gated. Only searching the raw bytes does.
    """
    call_id = take_a_call(client)
    body = client.get("/v1/agent/me").json()
    assert body["identity"]["assurance"] == "l1_probable"
    assert body["identity"]["may_disclose_policy_details"] is False

    raw = json.dumps(body, ensure_ascii=False)
    for secret in ("HL-2024-000811", "policy_no", "sum_insured", "coverages", "dob"):
        assert secret not in raw, f"{secret!r} must not cross the wire at L1"
    assert body["brief"]["disclosure_locked"] is True
    assert body["brief"]["relevant_policy"] is None
    assert call_id


def test_promotion_is_a_re_render_that_reveals_the_policy(client: Any) -> None:
    """The other half: once the agent attests, the same data is allowed through."""
    call_id = take_a_call(client)
    before = client.get("/v1/agent/me").json()["brief"]["version"]

    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )
    after = client.get("/v1/agent/me").json()

    assert after["identity"]["assurance"] == "l3_verified"
    assert after["brief"]["relevant_policy"] is not None
    assert after["brief"]["relevant_policy"]["policy_no"] == "HL-2024-000811"
    assert after["brief"]["disclosure_locked"] is False
    # `D7`: a new version, so the record shows what the agent saw before and after.
    assert after["brief"]["version"] > before


def test_a_rejected_identity_takes_the_policy_back_off_the_screen(client: Any) -> None:
    """Assurance moves DOWN as well as up, and the payload has to follow it."""
    call_id = take_a_call(client)
    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "policy_number"},
    )
    assert client.get("/v1/agent/me").json()["brief"]["relevant_policy"] is not None

    client.post(f"/v1/agent/calls/{call_id}/identity", json={"outcome": "not_this_person"})
    body = client.get("/v1/agent/me").json()
    raw = json.dumps(body, ensure_ascii=False)
    assert "HL-2024-000811" not in raw
    assert body["brief"] is None or body["brief"]["relevant_policy"] is None


def test_actions_needing_assurance_are_absent_not_disabled(client: Any) -> None:
    """A step the agent may not take yet is advice that does not apply, not a grey button."""
    call_id = take_a_call(client)
    at_l1 = client.get("/v1/agent/me").json()["brief"]["actions_th"]
    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )
    at_l3 = client.get("/v1/agent/me").json()["brief"]["actions_th"]
    assert len(at_l3) >= len(at_l1)
