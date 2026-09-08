"""Handing the call to the company that has to decide it: `D124`, over HTTP.

`D117` split the duties from Krungsri's own slide — the insurer underwrites, rules on
coverage and pays claims; the broker analyses needs, selects the plan *and the company*,
services the policy and chases renewals. A claim call has arrived with a banner saying so
since `D117`, and until `D124` the banner was the whole feature.

The rules this file holds down:

* **The record beats the menu.** The carrier that wrote the policy is offerable whether or
  not anybody typed it into `insurers.yaml`, because the real extract arrives carrying
  carriers nobody has.
* **The client names WHICH, never WHAT IT IS CALLED.** A code, or the policy flag.
* **A reason that needs a policy is refused when we hold none** — by the server, not by
  the client's `disabled` attribute (`D121`).
* **A handoff does not invent a call state.** It ends the call the ordinary way and what
  makes it a handoff is the record (`D124`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, sweep_once
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.domain import events as ev
from tests.conftest import REPO_ROOT

NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def client(clock: ManualClock) -> Any:
    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


async def on_a_claim_call(client: Any, *, agent: str = "A006") -> str:
    """An agent on a live claim call — the shape `D117`'s banner is for."""
    client.post("/v1/agent/demo-login", json={"agent_id": agent})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = client.post(
        "/v1/demo/calls",
        json={
            "intent_code": "health.claim.notify",
            "intake_keys": ["2"],
            "ignore_hours": True,
            "caller_number": "0812345678",
        },
    ).json()
    await sweep_once(client.app.state.container)
    me = client.get("/v1/agent/me").json()
    client.post(f"/v1/agent/offers/{me['offer']['assignment_id']}/accept")
    return str(placed["call_session_id"])


# --- the options ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_customers_own_carrier_is_offered_first_and_is_never_missing(
    client: Any,
) -> None:
    """`D124`. `insurers.yaml` is a menu; `Policy.insurer` is a fact about this customer.

    On hackathon day the real extract arrives carrying carriers nobody has typed into
    config, and a broker who cannot hand a claim to the company that actually wrote the
    policy has no product. So the policy's carrier is its own row, separate from the
    market list.
    """
    call_id = await on_a_claim_call(client)
    options = client.get(f"/v1/agent/calls/{call_id}/handoff/options").json()

    assert options["policy_insurer"], "the demo customer holds real cover from a real carrier"
    assert options["handoff_expected"] is True, (
        "a claim is the insurer's to decide (`D117`), and the same flag that draws the "
        "banner is the one that says so here — two answers to one question is `B25`"
    )
    assert options["insurers"], "and there is a market menu for everything else"
    assert options["reasons"]


@pytest.mark.anyio
async def test_a_reason_that_needs_a_policy_says_so_when_there_is_none(client: Any) -> None:
    """Half the reasons are incoherent without cover: there is no claim to adjudicate and
    no wording to rule on. The client greys them; this asserts the SERVER's answer."""
    call_id = await on_a_claim_call(client)
    reasons = client.get(f"/v1/agent/calls/{call_id}/handoff/options").json()["reasons"]

    by_code = {r["code"]: r for r in reasons}
    assert by_code["claim_adjudication"]["requires_policy"] is True
    assert by_code["service_complaint"]["requires_policy"] is False
    # This caller holds a policy, so every reason is live for them.
    assert all(r["available"] for r in reasons)


# --- the handoff itself ---------------------------------------------------------------


@pytest.mark.anyio
async def test_handing_over_ends_the_call_and_prefills_the_wrapup(client: Any) -> None:
    """The whole feature in one pass, and the ORDER is the part that matters.

    The wrap-up form renders the instant the state changes, so a prefill computed after
    the call ends is a prefill nobody ever sees.
    """
    call_id = await on_a_claim_call(client)
    carrier = client.get(f"/v1/agent/calls/{call_id}/handoff/options").json()["policy_insurer"]

    response = client.post(
        f"/v1/agent/calls/{call_id}/handoff",
        json={"reason_code": "claim_adjudication", "use_policy_insurer": True},
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()

    assert snapshot["presence"]["system_state"] == "after_call_work", (
        "the ACW clock starts at the handoff exactly as it does at วางสาย (`D45`)"
    )
    assert snapshot["wrapup_call_session_id"] == call_id
    handoff = snapshot["handoff"]
    assert handoff is not None, "and it is keyed on the call being WRAPPED, not the active one"
    assert handoff["insurer_name_th"] == carrier
    assert handoff["insurer_code"] is None, "absence says the record answered, not the list"
    assert carrier in handoff["disposition_th"]
    assert handoff["reason_label_th"] in handoff["disposition_th"]


@pytest.mark.anyio
async def test_a_handoff_does_not_invent_a_call_state(client: Any) -> None:
    """`D124`, and it is the decision worth protecting.

    `CallState.TRANSFERRED` exists, is terminal, and is entered by nothing. Using it here
    would mean a call that can never reach `WRAP_UP` — and after-call work on a handoff is
    real work. Making it non-terminal instead would leave TWO states both meaning "the
    media is over and the agent is filing", which is `B25`/`B26`'s shape exactly.

    So the call ends the ordinary way and the RECORD is what makes it a handoff.
    """
    call_id = await on_a_claim_call(client)
    client.post(
        f"/v1/agent/calls/{call_id}/handoff",
        json={"reason_code": "claim_adjudication", "use_policy_insurer": True},
    )
    container = client.app.state.container
    session = await container.calls.get(call_id)

    assert str(session.state) == "wrap_up", "the ordinary ending, not a state of its own"
    assert "transferred" not in [str(t.to_state) for t in session.transitions], (
        "`CallState.TRANSFERRED` stays entered by nothing — see the docstring, and `Q37`"
    )
    # The reason is what carries the fact into the transition log, so it must actually
    # say it: a test that only checks the state would pass on a plain hang-up.
    last = session.transitions[-1]
    assert last.reason.startswith("handed_to_insurer:"), last.reason


@pytest.mark.anyio
async def test_the_handoff_is_published_as_its_own_event(client: Any) -> None:
    """A business fact, not an operations one (`D124`).

    *"The media stopped"* and *"this went to Muang Thai because they have to rule on the
    coverage"* answer different questions, and counting handoffs per carrier is a question
    a broker actually has. Folding it into `CallEnded` would make that a string search.
    """
    seen: list[ev.CallHandedOff] = []

    async def collect(event: ev.Event) -> None:
        assert isinstance(event, ev.CallHandedOff)
        seen.append(event)

    container = client.app.state.container
    container.bus.subscribe(ev.CallHandedOff.name, collect)

    call_id = await on_a_claim_call(client)
    client.post(
        f"/v1/agent/calls/{call_id}/handoff",
        json={"reason_code": "coverage_ruling", "use_policy_insurer": True, "note": "รอเอกสาร"},
    )
    await container.bus.drain()

    assert len(seen) == 1
    assert seen[0].reason_code == "coverage_ruling"
    assert seen[0].call_session_id == call_id


@pytest.mark.anyio
async def test_the_customers_screen_stops_saying_they_are_talking_to_us(client: Any) -> None:
    """`B37`'s lesson applied to the other way a call can end.

    The customer has been passed on. Leaving their paired screen saying
    *"กำลังสนทนากับเจ้าหน้าที่"* through the whole of after-call work was a real bug once
    already, and a handoff is a second door into the same room.
    """
    call_id = await on_a_claim_call(client)
    link = client.post(f"/v1/agent/calls/{call_id}/assist/link").json()
    token = link["token"]
    client.get(f"/v1/assist/{token}")  # the customer taps it
    assert client.get(f"/v1/assist/{token}").json()["call_active"] is True

    client.post(
        f"/v1/agent/calls/{call_id}/handoff",
        json={"reason_code": "claim_adjudication", "use_policy_insurer": True},
    )
    assert client.get(f"/v1/assist/{token}").json()["call_active"] is False


# --- what the server refuses -----------------------------------------------------------


@pytest.mark.anyio
async def test_a_client_cannot_name_a_company_only_choose_one(client: Any) -> None:
    """A request able to supply a company NAME could file a handoff to a company that
    never wrote anything for this customer, and the wrap-up would record it as fact."""
    call_id = await on_a_claim_call(client)
    refused = client.post(
        f"/v1/agent/calls/{call_id}/handoff",
        json={
            "reason_code": "claim_adjudication",
            "insurer_code": "muang_thai",
            "insurer_name_th": "บริษัทที่ไม่มีอยู่จริง",
        },
    )
    assert refused.status_code == 422, "`extra=forbid` — the field does not exist to be sent"


@pytest.mark.anyio
async def test_an_unknown_insurer_or_reason_is_refused(client: Any) -> None:
    call_id = await on_a_claim_call(client)
    assert (
        client.post(
            f"/v1/agent/calls/{call_id}/handoff",
            json={"reason_code": "claim_adjudication", "insurer_code": "not-a-company"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/v1/agent/calls/{call_id}/handoff",
            json={"reason_code": "not-a-reason", "use_policy_insurer": True},
        ).status_code
        == 400
    )


@pytest.mark.anyio
async def test_only_the_agent_on_the_call_can_hand_it_over(client: Any) -> None:
    """`D4`'s staff-side twin: the agent comes from the cookie, and a call that is not
    theirs is not theirs to end — least of all by naming somebody else's company."""
    call_id = await on_a_claim_call(client)
    client.post("/v1/agent/logout")
    client.post("/v1/agent/demo-login", json={"agent_id": "A007"})

    refused = client.post(
        f"/v1/agent/calls/{call_id}/handoff",
        json={"reason_code": "claim_adjudication", "use_policy_insurer": True},
    )
    assert refused.status_code == 404


# --- compare & best-fit over HTTP (D126) ------------------------------------------------


@pytest.mark.anyio
async def test_the_comparison_is_ranked_and_composed_by_the_server(client: Any) -> None:
    """`D126`. The broker sees the table before pushing it, and neither the order nor the
    figures were decided in a browser."""
    call_id = await on_a_claim_call(client)
    view = client.get(f"/v1/agent/calls/{call_id}/comparison").json()

    assert view["available"] is True
    assert view["line"] == "health"
    assert view["held_policy_no"], "this caller holds health cover to compare against"
    assert 1 <= len(view["candidates"]) <= 3, "capped so a phone can render it"

    scores = [c["score"] for c in view["candidates"]]
    assert scores == sorted(scores, reverse=True), "ranked, not catalogue order"
    assert all(c["reason_th"] for c in view["candidates"])
    assert view["table"]["columns_th"][1] == "แผนปัจจุบันของคุณ", (
        "the gap is what the customer should see first"
    )
    assert "ไม่ใช่ใบเสนอราคา" in view["table"]["note_th"], (
        "pricing is out of scope and the table says so (`D115`)"
    )


@pytest.mark.anyio
async def test_a_pushed_comparison_is_built_here_never_taken_from_the_request(
    client: Any,
) -> None:
    """`D126`, and it is the rule worth protecting.

    Until this landed, `compare.plans` pushed whatever payload the client sent — so the
    coverage figures on a customer's phone would have been composed in a browser. `D16`
    says a coverage figure is data read from the record; "the client assembled it" is not
    that. A client that sends a table of its own gets the server's table instead.
    """
    call_id = await on_a_claim_call(client)
    link = client.post(f"/v1/agent/calls/{call_id}/assist/link").json()
    client.get(f"/v1/assist/{link['token']}")  # the customer taps it
    # Signed in, so the held column is in play too — otherwise this would be asserting
    # the guest table and would miss half of what the server composes (`D126`).
    client.post(f"/v1/assist/{link['token']}/sign-in", json={"customer_id": "C000001"})

    pushed = client.post(
        f"/v1/agent/calls/{call_id}/assist/push",
        json={
            "tool_id": "compare.plans",
            "payload": {
                "columns_th": ["ของปลอม"],
                "rows": [{"cells": ["คุ้มครอง 999,999,999 บาท"]}],
            },
        },
    )
    assert pushed.status_code == 200, pushed.text

    screen = client.get(f"/v1/assist/{link['token']}").json()
    item = screen["items"][-1]
    assert item["kind"] == "comparison"
    payload = item["payload"]
    assert payload["columns_th"] != ["ของปลอม"], "the client's table was discarded"
    assert "999,999,999" not in str(payload)
    assert payload["columns_th"][1] == "แผนปัจจุบันของคุณ"


@pytest.mark.anyio
async def test_comparing_a_line_with_no_catalogue_says_so_rather_than_failing(
    client: Any,
) -> None:
    """A broker with nothing to compare is an ordinary state, not an error (`D125`)."""
    call_id = await on_a_claim_call(client)
    view = client.get(f"/v1/agent/calls/{call_id}/comparison", params={"line": "savings"}).json()
    assert view["available"] is False
    assert view["candidates"] == []


@pytest.mark.anyio
async def test_a_guest_screen_gets_the_market_not_the_customers_own_cover(
    client: Any,
) -> None:
    """`D126`, and it was a live leak until it was looked at.

    `compare.plans` is `personal: false` and should be: comparing what the market offers
    is true for anybody, and gating it would wall off the one part of this journey with no
    privacy cost at all (`D120`). But the table gained a column headed
    *"แผนปัจจุบันของคุณ"* carrying real coverage figures — and that column is about ONE
    person, on a screen that has only proved somebody is holding a phone (`D42`).

    So the tool stays guest-safe and the COLUMN is what moves. This is `D121`'s
    correction — look at the purpose, not the shape — inside a single push.
    """
    call_id = await on_a_claim_call(client)
    link = client.post(f"/v1/agent/calls/{call_id}/assist/link").json()
    token = link["token"]
    client.get(f"/v1/assist/{token}")  # tapped, not signed in

    client.post(f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "compare.plans"})
    guest = client.get(f"/v1/assist/{token}").json()
    assert guest["tier"] == "guest"
    payload = guest["items"][-1]["payload"]

    assert "แผนปัจจุบันของคุณ" not in payload["columns_th"]
    assert payload["held_hidden"] is True, (
        "and it says so, so a guest does not conclude we simply do not hold their policy"
    )
    # The market comparison itself is still there — that is the half with no privacy cost.
    assert len(payload["columns_th"]) >= 2
    assert payload["rows"]

    # Signing in unlocks their own column, on the same tool with the same push.
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})
    client.post(f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "compare.plans"})
    verified = client.get(f"/v1/assist/{token}").json()
    assert verified["tier"] == "verified"
    unlocked = verified["items"][-1]["payload"]
    assert unlocked["columns_th"][1] == "แผนปัจจุบันของคุณ"
    assert unlocked["held_hidden"] is False
