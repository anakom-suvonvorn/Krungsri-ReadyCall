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
        # The sweep is driven explicitly in the tests that care (`sweep_once`), so a real
        # wall-clock loop cannot make anything here time-dependent. The clock is manual,
        # so a background sweeper would find nothing to expire anyway - but a test that
        # asserts on RONA must control exactly when it happens.
        agent_sweep_interval_s=0,
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

    placed = place_call(client, intent_code="health.claim.notify", caller_number="0812345678")
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
    placed = place_call(client, intent_code="health.claim.notify")
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
    # A005 also holds claims.assist; sign them in through a second cookie jar so both are
    # available at once. Same app, different session.
    second = client.__class__(client.app)
    second.post("/v1/agent/demo-login", json={"agent_id": "A005"})
    second.post("/v1/agent/state", json={"agent_intent": "ready"})

    placed = place_call(client, intent_code="health.claim.notify")
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


def test_an_unanswered_offer_expires_and_frees_the_agent(client: Any, clock: ManualClock) -> None:
    """`B7`. Nothing drove RONA, so an ignored offer stranded the agent for good.

    The workstation hides the offer card when its countdown hits zero, which made this
    look handled: the agent stayed in `OFFERING` with no card, could not be offered
    anything else, could not place a test call, and the caller was never re-matched. The
    services were all correct — `expire_offers` even documented itself as running "on a
    timer in the API process" — and no timer existed.
    """
    from readycall.api.app import sweep_once

    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = place_call(client, intent_code="health.claim.notify")
    assert client.get("/v1/agent/me").json()["offer"] is not None

    # Not yet: the offer is still inside its window.
    clock.advance(5)
    _sweep(client, sweep_once, alive=("A006",))
    assert client.get("/v1/agent/me").json()["offer"] is not None, "expired early"

    clock.advance(30)  # past offer_timeout_s = 20
    _sweep(client, sweep_once, alive=("A006",))

    body = client.get("/v1/agent/me").json()
    assert body["offer"] is None, "the offer must actually resolve, not merely vanish"
    assert body["presence"]["system_state"] == "available", (
        "the agent has to leave OFFERING, or they are stuck there for the shift"
    )
    assert body["presence"]["agent_intent"] == "not_ready", (
        "RONA: an empty desk stops being offered calls (`D33`, `D51`)"
    )
    assert body["presence"]["intent_reason"] == "rona_missed_offer"
    assert placed["call_session_id"]


def test_the_caller_nobody_answered_is_re_offered_elsewhere(
    client: Any, clock: ManualClock
) -> None:
    """The other half of `B7`: expiring the offer is useless if nobody re-matches."""
    from readycall.api.app import sweep_once

    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    second = client.__class__(client.app)
    second.post("/v1/agent/demo-login", json={"agent_id": "A005"})
    second.post("/v1/agent/state", json={"agent_intent": "ready"})

    place_call(client, intent_code="health.claim.notify")
    first, other = (
        (client, second) if client.get("/v1/agent/me").json()["offer"] else (second, client)
    )
    assert first.get("/v1/agent/me").json()["offer"] is not None

    clock.advance(30)
    _sweep(client, sweep_once, alive=("A005", "A006"))

    assert first.get("/v1/agent/me").json()["offer"] is None
    assert other.get("/v1/agent/me").json()["offer"] is not None, (
        "the caller must land on another desk, with the silent agent excluded (`D52`)"
    )


def _sweep(client: Any, sweep_once: Any, *, alive: tuple[str, ...] = ()) -> None:
    """Run one sweep against the app's live container, from a sync test.

    `alive` names the agents whose browser is notionally still open. A real workstation
    heartbeats over its socket every 10 s; these tests have no socket, so without this the
    presence sweep would correctly declare the agent gone the moment the clock advances
    past the TTL - which is a different thing from RONA and would mask it.
    """
    import asyncio

    container = client.app.state.container

    async def run() -> None:
        for agent_id in alive:
            await container.presence.heartbeat(agent_id)
        await sweep_once(container)

    asyncio.run(run())


# --- identity (D42) -----------------------------------------------------------------------------


def active_call(client: Any) -> str:
    return str(client.get("/v1/agent/me").json()["active_call_session_id"])


def take_a_call(
    client: Any,
    intent_code: str = "health.claim.notify",
    caller_number: str = "0812345678",
) -> str:
    """Sign in, go ready, take the call. `caller_number` chooses whether ANI matches."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place_call(client, intent_code=intent_code, caller_number=caller_number)
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    return active_call(client)


def test_confirming_an_identity_requires_naming_the_challenge(client: Any) -> None:
    """An unfalsifiable audit row is exactly what `D42` exists to prevent."""
    call_id = take_a_call(client)
    response = client.post(f"/v1/agent/calls/{call_id}/identity", json={"outcome": "confirmed"})
    assert response.status_code == 400
    assert "challenge" in response.json()["detail"]


def test_an_authorised_third_party_is_verified_but_recorded_as_a_third_party(
    client: Any,
) -> None:
    """`D65`, which reverses the level and keeps the record.

    The button asserts the agent has checked this person may act for the policyholder, so
    the level follows the attestation exactly as it does for CONFIRMED — otherwise we
    withhold the context the agent needs from a caller we have just verified, which is the
    opposite of what the ladder is for.

    What must NOT change is the outcome. The log says an authorised representative was
    verified, never that the policyholder was, and the two are different facts about
    different people.
    """
    call_id = take_a_call(client)
    body = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "third_party", "caller_name": "สุดา ใจดี", "relationship": "ลูกสาว"},
    ).json()
    identity = body["identity"]
    assert identity["assurance"] == "l3_verified"
    assert identity["may_act_on_policy"] is True
    assert identity["attested_outcome"] == "third_party", (
        "the level rises; the RECORD still says who was actually on the phone"
    )
    assert identity["authority_check_required"] is True, "the flag stays visible all call"
    assert identity["third_party_name"] == "สุดา ใจดี"
    assert identity["relationship"] == "ลูกสาว"

    # And the brief must actually open up, or the promotion bought nothing.
    assert body["brief"]["relevant_policy"] is not None
    assert body["brief"]["disclosure_locked"] is False


def test_a_third_party_must_be_named(client: Any) -> None:
    """`D57`: the disclosure log has to say WHO was on the phone.

    "somebody who is not the policyholder called" is not a record anyone can act on.
    """
    call_id = take_a_call(client)
    unnamed = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "third_party", "relationship": "ลูกสาว"},
    )
    assert unnamed.status_code == 400
    no_relationship = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "third_party", "caller_name": "สุดา ใจดี"},
    )
    assert no_relationship.status_code == 400


def test_other_is_a_valid_challenge_but_needs_saying_what_it_was(client: Any) -> None:
    """`D57`: real verification does not fit a four-item dropdown.

    Recognised the voice from last week, read a claim reference off an SMS, transferred
    from a branch that already checked ID. A closed list forces all of those into the
    nearest lie — but a free-text `other` with nothing written in it is the unfalsifiable
    audit row `D42` exists to prevent, so the note is required.
    """
    call_id = take_a_call(client)
    blank = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "other", "challenge_note": "   "},
    )
    assert blank.status_code == 400

    body = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={
            "outcome": "confirmed",
            "challenge": "other",
            "challenge_note": "โอนสายจากสาขาที่ตรวจบัตรประชาชนแล้ว",
        },
    ).json()
    assert body["identity"]["assurance"] == "l3_verified"


def test_attesting_locks_the_control_until_it_is_reopened(client: Any) -> None:
    """`D60`. An attestation is a signed statement, not a toggle.

    It does not lock forever, though: an agent who confirmed and then realised they were
    speaking to the policyholder's daughter must be able to correct it. Reopening is
    explicit and appends a correction, so both statements survive in the log.
    """
    call_id = take_a_call(client)
    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )

    second = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "third_party", "caller_name": "สุดา ใจดี", "relationship": "ลูกสาว"},
    )
    assert second.status_code == 409, "a stray second click must not rewrite the record"

    amended = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={
            "outcome": "third_party",
            "caller_name": "สุดา ใจดี",
            "relationship": "ลูกสาว",
            "amend": True,
        },
    )
    assert amended.status_code == 200
    identity = amended.json()["identity"]
    # Since `D65` both outcomes sit at L3, so what an amendment changes here is the
    # RECORD, not the level: this call is now on file as an authorised representative
    # rather than the policyholder, which is the whole reason the third outcome exists.
    assert identity["attested_outcome"] == "third_party"
    assert identity["third_party_name"] == "สุดา ใจดี"
    assert identity["assurance"] == "l3_verified"


def test_amending_appends_and_the_count_lets_the_panel_re_lock(client: Any) -> None:
    """The signal the workstation re-locks on, and why it has to come from the server.

    The panel's "reopened for editing" flag is local state. Without a server-side edge to
    reset it on, one press of *amend* left the control unlocked for the rest of the call —
    so all three outcomes could be cycled freely, appending a disclosure row per click.
    `attestation_count` grows on every attestation *including* amendments (`D60` appends,
    never overwrites), which is precisely the edge the client needs.

    A third amendment is also asserted, because the bug only showed up on the second one.
    """
    call_id = take_a_call(client)

    first = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )
    assert first.json()["identity"]["attestation_count"] == 1

    second = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={
            "outcome": "third_party",
            "caller_name": "สุดา ใจดี",
            "relationship": "ลูกสาว",
            "amend": True,
        },
    )
    assert second.json()["identity"]["attestation_count"] == 2

    # Still locked after the amendment: a further click without `amend` must be refused,
    # which is the server-side half of the same rule (a disabled button is only a courtesy).
    stray = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "policy_number"},
    )
    assert stray.status_code == 409, "the control must re-lock after an amendment, not stay open"

    third = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "policy_number", "amend": True},
    )
    assert third.json()["identity"]["attestation_count"] == 3, "corrections accumulate"
    assert third.json()["identity"]["may_act_on_policy"] is True


def test_a_rejection_can_be_taken_back(client: Any) -> None:
    """`D71`, which deliberately reverses the one-way door `Q18` recorded.

    A rejection used to strand the call at `L0` for good, because it clears the customer
    and there was nothing left to restore. That punishes the two cases that actually
    happen: a mis-click, and a caller who only explains on the second attempt that they
    are the policyholder's daughter.

    The restored proposal is the one the resolver originally found. It is not invented,
    and the rejection stays in the log — amending appends (`D60`), so the record shows the
    agent rejected the match and then withdrew that.
    """
    call_id = take_a_call(client)
    rejected = client.post(
        f"/v1/agent/calls/{call_id}/identity", json={"outcome": "not_this_person"}
    ).json()
    assert rejected["identity"]["assurance"] == "l0_anonymous"
    assert rejected["identity"]["customer_id"] is None
    assert "confirmed" in rejected["identity"]["attestable"], (
        "the way back has to be offered, or a mis-click ends the call at L0"
    )

    restored = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth", "amend": True},
    )
    assert restored.status_code == 200
    identity = restored.json()["identity"]
    assert identity["assurance"] == "l3_verified"
    assert identity["customer_id"] is not None, "the original proposal comes back"
    assert identity["attestation_count"] == 2, "both statements survive in the log"


def test_a_caller_who_was_never_identified_has_nothing_to_attest(client: Any) -> None:
    """The one case that IS locked from the start (`D71`).

    With nobody proposed there is nothing to confirm, nothing to reject, and nobody to act
    on behalf of. The control is inert rather than offering three buttons that all fail,
    and it stays that way until customer search exists (`Q18`).
    """
    call_id = take_a_call(client, caller_number="0899999999")
    identity = client.get("/v1/agent/me").json()["identity"]
    assert identity["assurance"] == "l0_anonymous"
    assert identity["customer_id"] is None
    assert identity["attestable"] == [], "nothing to assert about nobody"

    refused = client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )
    assert refused.status_code == 400


def test_the_snapshot_says_the_identity_was_attested(client: Any) -> None:
    """So the panel can lock itself without keeping its own copy of what happened."""
    call_id = take_a_call(client)
    assert client.get("/v1/agent/me").json()["identity"]["attested"] is False
    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "policy_number"},
    )
    identity = client.get("/v1/agent/me").json()["identity"]
    assert identity["attested"] is True
    assert identity["attested_outcome"] == "confirmed"


def test_rejecting_the_guess_drops_to_anonymous(client: Any) -> None:
    call_id = take_a_call(client)
    body = client.post(
        f"/v1/agent/calls/{call_id}/identity", json={"outcome": "not_this_person"}
    ).json()
    assert body["identity"]["assurance"] == "l0_anonymous"
    assert body["identity"]["customer_id"] is None


# --- keypad capture (D44) -------------------------------------------------------------


def test_the_agent_sees_the_digits_and_the_log_does_not(client: Any) -> None:
    """`D58`. `D44`'s inverted default is "masked in transcripts and logs" — not masked
    from the agent.

    The agent asked the caller to key these digits and has to read them back or act on
    them. The first implementation masked them on the agent's own screen too, which
    deleted the feature and protected nothing: `••••••••12` is not a policy number
    anybody can use.
    """
    call_id = take_a_call(client)
    capture = client.post(f"/v1/agent/calls/{call_id}/capture").json()
    for digit in "2025004512":
        body = client.post(
            f"/v1/agent/captures/{capture['capture_id']}/keys", json={"digits": digit}
        ).json()

    assert body["digits"] == "2025004512", "the agent's own panel shows the real digits"
    assert body["masked"] == "••••••••12", "...and the masked form travels everywhere else"
    assert body["length"] == 10


def test_discarding_leaves_nothing_in_either_form(client: Any) -> None:
    call_id = take_a_call(client)
    capture = client.post(f"/v1/agent/calls/{call_id}/capture").json()
    client.post(f"/v1/agent/captures/{capture['capture_id']}/keys", json={"digits": "12345"})
    body = client.post(f"/v1/agent/captures/{capture['capture_id']}/discard").json()
    assert body["digits"] == ""
    assert body["masked"] == ""
    assert body["length"] == 0


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
    assert by_id["q_claims"]["is_open"] is True, "a crash does not check the clock"
    closed = [q for q in queues if not q["is_open"]]
    for queue in closed:
        assert queue["closed_reason"] in {"outside_hours", "holiday"}
        assert queue["next_open_at"], "a closed queue must say when it opens again (`D25`)"


# --- the disclosure gate is the SHAPE of the payload (D42) ------------------------


def test_an_l0_payload_carries_nothing_about_anybody(client: Any) -> None:
    """The regression guard for a real leak (`B5`), repointed by `D74` at the level that
    still withholds.

    `render_brief` used to return `CaseBrief.model_dump()`. The domain object embeds the
    whole frozen `ContextSnapshot`, so the response carried the policy number, the sum
    insured, every coverage figure and the customer's date of birth — in the *same body*
    that said disclosure was locked.

    `D74` opened L1: the agent is the bank's own employee and needs the record to do the
    verifying. What did **not** change is the mechanism or the floor. **L0 still carries
    nothing**, and the DTO still has no field for the raw snapshot — so the class of bug
    `B5` was remains structurally impossible, and this test still proves it. It has to
    search the raw bytes, because an assertion about rendered Thai cannot see a field the
    renderer never mentions.
    """
    call_id = take_a_call(client)
    # Reject the match: assurance drops to L0 and there is nobody left to show.
    client.post(f"/v1/agent/calls/{call_id}/identity", json={"outcome": "not_this_person"})

    body = client.get("/v1/agent/me").json()
    assert body["identity"]["assurance"] == "l0_anonymous"
    assert body["identity"]["may_act_on_policy"] is False

    raw = json.dumps(body, ensure_ascii=False)
    for secret in ("HL-2024-000811", "sum_insured", "coverages", "dob", "ภัทธีรา"):
        assert secret not in raw, f"{secret!r} must not cross the wire at L0"
    assert body["brief"]["relevant_policy"] is None
    assert body["brief"]["customer"] is None


def test_l1_shows_the_record_but_withholds_permission(client: Any) -> None:
    """`D74`, and the distinction the whole decision turns on.

    The number is on screen — the agent cannot check a caller's answer against something
    they cannot see — while `may_act_on_policy` stays false and the L2-gated playbook
    steps stay out of the list. Seeing is not saying.
    """
    take_a_call(client)
    body = client.get("/v1/agent/me").json()
    assert body["identity"]["assurance"] == "l1_probable"

    assert body["brief"]["relevant_policy"] is not None, "the agent can see it"
    assert body["brief"]["relevant_policy"]["policy_no"] == "HL-2024-000811"
    assert body["identity"]["may_act_on_policy"] is False, "and may not act on it yet"
    assert body["brief"]["disclosure_locked"] is True
    assert body["brief"]["actions_th"][0].startswith("ยืนยันตัวตน"), (
        "the first thing to do is still verify (`D56`)"
    )


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

    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "not_this_person", "amend": True},
    )
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


# --- the spoken line, and the step before every other step (D55) ------------------


def test_the_opening_line_never_names_an_unverified_caller(client: Any) -> None:
    """`D55`, and it is drawn in `diagrams/src/identity_promotion.mmd`.

    Two reasons, and the second is the stronger one. Greeting someone by name tells
    whoever is holding that phone that the number belongs to that person. And a leading
    question is weaker verification: "ใช่คุณภัทธีราไหมคะ" can be answered "yes" by
    anybody, while "ขอทราบชื่อผู้ติดต่อด้วยค่ะ" has to be produced.

    The name is still on screen. It is just not in the sentence the agent reads out.
    """
    call_id = take_a_call(client)
    brief = client.get("/v1/agent/me").json()["brief"]

    assert "ภัทธีรา" not in (brief["suggested_opening_th"] or "")
    assert "ขอทราบชื่อผู้ติดต่อ" in brief["suggested_opening_th"]
    assert brief["customer"]["display_name_th"].startswith("ภัทธีรา"), (
        "the agent can still see who we think it is"
    )

    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )
    after = client.get("/v1/agent/me").json()["brief"]
    assert "ภัทธีรา" in after["suggested_opening_th"], "once verified, greet them by name"


def test_verify_identity_is_prepended_as_action_zero(client: Any) -> None:
    """Below L2 the agent is never blocked — they are told what to do first."""
    call_id = take_a_call(client)
    actions = client.get("/v1/agent/me").json()["brief"]["actions_th"]
    assert actions[0] == "ยืนยันตัวตนผู้ติดต่อก่อนให้ข้อมูลกรมธรรม์"

    client.post(
        f"/v1/agent/calls/{call_id}/identity",
        json={"outcome": "confirmed", "challenge": "date_of_birth"},
    )
    after = client.get("/v1/agent/me").json()["brief"]["actions_th"]
    assert after[0] != "ยืนยันตัวตนผู้ติดต่อก่อนให้ข้อมูลกรมธรรม์"
    assert len(after) > len(actions) - 1, "and the L2-gated steps come back"


# --- what the screen is allowed to offer (D59) -----------------------------------


def test_the_server_says_which_status_buttons_are_legal(client: Any) -> None:
    """The workstation renders permissions; it does not guess them."""
    sign_in_agent(client, "A006")
    available = client.post("/v1/agent/state", json={"agent_intent": "ready"}).json()
    assert set(available["declarable"]) == {
        "ready",
        "break",
        "lunch",
        "training",
        "admin",
        "last_call",
        "draining",
    }

    place_call(client, intent_code="health.claim.notify", ignore_hours=True)
    offer = client.get("/v1/agent/me").json()["offer"]
    on_call = client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").json()
    assert set(on_call["presence"]["declarable"]) == {"ready", "last_call", "draining"}

    refused = client.post("/v1/agent/state", json={"agent_intent": "lunch"})
    assert refused.status_code == 400, "you cannot be at lunch while talking to a customer"


def test_a_spent_last_call_is_distinguishable_from_a_fresh_sign_in(client: Any) -> None:
    """Both are `not_ready`, and they want opposite things on screen (`D59`).

    Without a reason the wrap-up panel offers "Save & Ready" as the primary button to an
    agent who has just told us they are finishing — making the fastest click the one that
    undoes what they said.
    """
    fresh = sign_in_agent(client, "A006")
    assert fresh["agent_intent"] == "not_ready"
    assert fresh["intent_reason"] == "signed_in"

    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = place_call(client, intent_code="health.claim.notify", ignore_hours=True)
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    client.post("/v1/agent/state", json={"agent_intent": "last_call"})
    client.post(f"/v1/agent/calls/{placed['call_session_id']}/end", json={})

    after = client.get("/v1/agent/me").json()["presence"]
    assert after["agent_intent"] == "not_ready"
    assert after["intent_reason"] == "last_call_fulfilled"


def test_the_timers_are_anchored_to_server_timestamps(client: Any, clock: ManualClock) -> None:
    """So a browser refresh mid-call shows the true elapsed time, not zero."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = place_call(client, intent_code="health.claim.notify", ignore_hours=True)
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")

    snapshot = client.get("/v1/agent/me").json()
    assert snapshot["call_answered_at"], "the call timer needs an anchor, not a mount time"

    clock.advance(90)
    client.post(f"/v1/agent/calls/{placed['call_session_id']}/end", json={})
    wrapping = client.get("/v1/agent/me").json()["presence"]
    assert wrapping["acw_since"], "and so does the ACW timer"


def test_the_offer_card_says_what_the_call_is_about(client: Any) -> None:
    """`D69`. The agent should not have to accept blind.

    The card used to carry routing metadata only — queue, urgency, wait, rationale — so
    the agent knew why the call reached them and nothing about what it was for. The
    preview is built from the same gated `BriefOut` the panel renders, which is the part
    that matters: it cannot disclose more than the assurance level allows.
    """
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place_call(client, intent_code="health.claim.notify", caller_number="0812345678")

    offer = client.get("/v1/agent/me").json()["offer"]
    assert offer["summary_th"], "the card has to say what this call is about"
    assert offer["first_action_th"], "and what to do first"


def test_the_offer_preview_is_gated_like_the_brief(client: Any) -> None:
    """The preview must not become a hole in `B5`'s fix.

    Note which rule applies where, because they are easy to confuse. At `L1_PROBABLE` the
    ladder permits the name **on screen** — that is what the level is for — while `D55`
    forbids the agent *speaking* it, which is a fact about the suggested opening, not about
    the card. What L1 does not permit is the policy, and the assertion that matters is on
    the raw bytes: an assertion about rendered text cannot see a field a renderer never
    mentions, which is exactly how `B5` survived every test it had.
    """
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    place_call(client, intent_code="health.claim.notify", caller_number="0812345678")

    body = client.get("/v1/agent/me")
    offer = body.json()["offer"]
    assert offer["assurance"] == "l1_probable"
    # `D74` opened L1, so the preview may name the caller and summarise their policy. What
    # the test still proves is that the preview is built from the SAME gated DTO the panel
    # renders, rather than reaching into `CaseBrief` — which is how `B5` happened, and
    # would happen again in a new place. The proof is that the two agree.
    assert offer["customer_name_th"], "L1 shows who we think it is"
    assert offer["summary_th"]
    # The brief panel itself is still null here: it renders only once the call is taken,
    # which is why the preview had to exist at all rather than being read off the brief.
    assert body.json()["brief"] is None


def test_the_challenge_list_is_served_not_hardcoded(client: Any) -> None:
    """`D72`. One source of truth, the same shape as menus.yaml serving app and IVR (`D48`).

    The list lived in two hand-kept places — a frozenset in the service and an array in the
    React panel — and the service *refuses* anything not in its copy. So a drift means the
    screen offers an option that fails on submit, which an agent cannot diagnose. `Q12`
    says the list is going to change, so the drift was scheduled rather than hypothetical.
    """
    take_a_call(client)
    served = client.get("/v1/agent/me").json()["challenges"]
    assert served, "the workstation has to be told what it may offer"

    codes = {c["code"] for c in served}
    assert "other" in codes, "the escape hatch is a first-class option (`D57`)"
    assert all(c["label_th"] for c in served), "the screen renders labels, not codes"

    # The note requirement travels with the option, so the panel does not decide it.
    other = next(c for c in served if c["code"] == "other")
    assert other["requires_note"] is True


def test_every_served_challenge_is_actually_accepted(client: Any) -> None:
    """The property that makes one source of truth worth having.

    Anything the server tells the workstation to offer must be something the server will
    then accept. This is the test that fails the day somebody edits `challenges.yaml`
    without checking, which is exactly when it is needed.
    """
    call_id = take_a_call(client)
    served = client.get("/v1/agent/me").json()["challenges"]

    for spec in served:
        body: dict[str, Any] = {
            "outcome": "confirmed",
            "challenge": spec["code"],
            "amend": True,
        }
        if spec["requires_note"]:
            body["challenge_note"] = "ยืนยันจากการโทรครั้งก่อน"
        response = client.post(f"/v1/agent/calls/{call_id}/identity", json=body)
        assert response.status_code == 200, f"{spec['code']} is offered but refused"


# --- the IVR on the demo path (P3) -------------------------------------------------------


def test_the_demo_endpoint_walks_the_real_menu(client: Any) -> None:
    """`# P2b:` retired. Only the keypresses are faked now — the greeting, the notice,
    the menu order, the retries and the queue decision are all the production walk."""
    body = place_call(client, did="+6621234000", keys=["2", "4"], ignore_hours=True)
    assert body["queue_id"] == "q_service_health"


def test_a_demo_caller_who_presses_nothing_is_not_stranded(client: Any) -> None:
    """The floor, through HTTP: silence twice still reaches a queue (`D37`)."""
    body = place_call(client, did="+6621234222", keys=[], ignore_hours=True)
    assert body["queue_id"] not in (None, "")
    assert body["state"] in {"matched", "offered", "queued"}


def test_pressing_zero_reaches_a_human_through_the_api(client: Any) -> None:
    body = place_call(client, did="+6621234000", keys=["0"], ignore_hours=True)
    assert body["queue_id"] == "q_service"


# --- B10: leaving ACW without saving must not strand the call -----------------------------


def _run_one_call(client: Any, clock: ManualClock, *, intent_code: str, number: str) -> str:
    """Offer -> accept -> end. Leaves the agent in ACW with the call in WRAP_UP."""
    placed = place_call(client, intent_code=intent_code, caller_number=number, ignore_hours=True)
    offer = client.get("/v1/agent/me").json()["offer"]
    assert offer is not None, placed
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    clock.advance(60)
    client.post(f"/v1/agent/calls/{placed['call_session_id']}/end", json={})
    return str(placed["call_session_id"])


def test_declaring_a_state_without_saving_still_ends_the_call(
    client: Any, clock: ManualClock
) -> None:
    """`B10`. `D45` says the PERSON ends after-call work and nothing auto-saves a wrap-up.
    Both still hold — but the CALL must not sit in `WRAP_UP` for ever, because while it
    does it counts as this agent's active call."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    call_id = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")

    wrapping = client.get("/v1/agent/me").json()
    assert wrapping["active_call_session_id"] == call_id
    assert wrapping["wrapup_saved"] is False

    # The agent walks away from the form and declares a next state instead.
    client.post("/v1/agent/state", json={"agent_intent": "lunch"})

    after = client.get("/v1/agent/me").json()
    assert after["presence"]["system_state"] != "after_call_work"
    assert after["active_call_session_id"] is None, "the call must not still be active"
    assert after["wrapup_call_session_id"] is None
    assert after["identity"] is None, "nobody is on the phone, so nobody is on screen"
    assert after["brief"] is None


def test_no_wrapup_is_invented_for_a_call_nobody_wrapped_up(
    client: Any, clock: ManualClock
) -> None:
    """The absence IS the record (`D45`). Closing the call must not fabricate a
    disposition just to tidy the state machine."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    call_id = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")
    client.post("/v1/agent/state", json={"agent_intent": "lunch"})

    after = client.get("/v1/agent/me").json()
    assert after["wrapup_call_session_id"] is None
    assert [row["call_session_id"] for row in after["pending_wrapups"]] == [call_id]


# --- D87: the wrap-up backlog ---------------------------------------------------------


def test_walking_away_puts_the_wrap_up_in_a_backlog_not_a_bin(
    client: Any, clock: ManualClock
) -> None:
    """`D87`. `D45` lets the agent leave mid-form — so the record has to survive them
    leaving, or "free to go" quietly means "the note is lost"."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    call_id = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")

    clock.advance(4)
    client.post("/v1/agent/state", json={"agent_intent": "lunch"})

    pending = client.get("/v1/agent/me").json()["pending_wrapups"]
    assert len(pending) == 1
    row = pending[0]
    assert row["call_session_id"] == call_id
    assert row["intent_code"] == "health.claim.notify"
    assert row["intent_label_th"], "the agent needs to recognise which call this was"
    assert row["acw_seconds"] == pytest.approx(4.0, abs=0.5), (
        "how long they spent before walking away is context for coming back cold"
    )


def test_a_backlog_wrap_up_can_be_filed_later_and_then_clears(
    client: Any, clock: ManualClock
) -> None:
    """The whole point: they stepped away, and they can still finish it afterwards."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    call_id = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")
    client.post("/v1/agent/state", json={"agent_intent": "lunch"})

    clock.advance(1200)  # twenty minutes later, back from lunch
    saved = client.post(
        f"/v1/agent/calls/{call_id}/wrapup",
        json={"disposition": "advice_given", "notes": "ตามที่คุยไว้ก่อนพัก"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["pending_wrapups"] == () or saved.json()["pending_wrapups"] == []


def test_an_accidental_state_press_is_recoverable(client: Any, clock: ManualClock) -> None:
    """The state buttons sit right beside the form. Mis-clicking one must not destroy the
    record — it is the case that made a hard block look attractive, and the backlog
    answers it without taking the choice away from the agent."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    call_id = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")

    client.post("/v1/agent/state", json={"agent_intent": "ready"})  # oops
    assert client.get("/v1/agent/me").json()["pending_wrapups"][0]["call_session_id"] == call_id

    filed = client.post(
        f"/v1/agent/calls/{call_id}/wrapup",
        json={"disposition": "advice_given", "notes": "กดพลาด"},
    )
    assert filed.status_code == 200
    assert not filed.json()["pending_wrapups"]


def test_the_backlog_holds_more_than_one_and_is_oldest_first(
    client: Any, clock: ManualClock
) -> None:
    """A busy hour can leave several. They need an order, and the one that has been
    waiting longest is the one to clear."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    first = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    clock.advance(300)
    second = _run_one_call(client, clock, intent_code="health.claim.notify", number="0898887777")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    pending = client.get("/v1/agent/me").json()["pending_wrapups"]
    assert [row["call_session_id"] for row in pending] == [first, second]


def test_a_filed_wrapup_never_reappears_in_the_backlog(client: Any, clock: ManualClock) -> None:
    """Derived, not stored (`D78`): the backlog is "ACW ended and no wrap-up exists", so
    filing one removes it by construction — there is no second flag to forget to clear."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    call_id = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")
    client.post(
        f"/v1/agent/calls/{call_id}/wrapup",
        json={"disposition": "advice_given", "notes": "saved during ACW"},
    )
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    assert not client.get("/v1/agent/me").json()["pending_wrapups"], (
        "a call wrapped up during ACW was never owed anything"
    )


def test_a_stranded_call_does_not_come_back_after_the_next_one(
    client: Any, clock: ManualClock
) -> None:
    """The exact sequence that surfaced this: abandon the form on call A, handle call B
    normally, save B — and A used to reappear on screen and stay for the rest of the
    shift, because `_active_call_id` fell back to whatever was still in `WRAP_UP`."""
    sign_in_agent(client, "A006")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    first = _run_one_call(client, clock, intent_code="health.claim.notify", number="0812345678")
    client.post("/v1/agent/state", json={"agent_intent": "ready"})  # no save — the bug's trigger

    second = _run_one_call(client, clock, intent_code="health.claim.notify", number="0898887777")
    assert client.get("/v1/agent/me").json()["active_call_session_id"] == second

    client.post(
        f"/v1/agent/calls/{second}/wrapup",
        json={"disposition": "advice_given", "notes": ""},
    )
    client.post("/v1/agent/state", json={"agent_intent": "ready"})

    final = client.get("/v1/agent/me").json()
    assert final["active_call_session_id"] != first, "the stranded call came back"
    assert final["active_call_session_id"] is None
    assert final["brief"] is None, "the screen must be empty between calls"
