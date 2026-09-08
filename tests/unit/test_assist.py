"""The paired screen: `D120`, over HTTP, both ends.

The rule this file exists to hold down is the tier split. Tapping a link proves possession
of a phone, and `D42`'s whole argument is that possession is not identity — so a link-only
screen may be shown things true for anybody and nothing about a particular person. Getting
that backwards would put a stranger's policy on whoever is holding the handset.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, sweep_once
from readycall.clock import ManualClock
from readycall.config import Settings
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


async def on_a_call(client: Any) -> str:
    """Sign an agent in, place a call, accept it. Returns the call id."""
    client.post("/v1/agent/demo-login", json={"agent_id": "A006"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    placed = client.post(
        "/v1/demo/calls",
        json={
            "intent_code": "health.advice.compare",
            "intake_keys": ["2"],
            "ignore_hours": True,
            "caller_number": "0812345678",
        },
    ).json()
    await sweep_once(client.app.state.container)
    me = client.get("/v1/agent/me").json()
    client.post(f"/v1/agent/offers/{me['offer']['assignment_id']}/accept")
    return str(placed["call_session_id"])


def link_for(client: Any, call_id: str) -> dict[str, Any]:
    response = client.post(f"/v1/agent/calls/{call_id}/assist/link")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- pairing -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_broker_gets_a_link_and_the_customer_can_open_it(client: Any) -> None:
    call_id = await on_a_call(client)

    minted = link_for(client, call_id)
    assert minted["link"].endswith(minted["token"])
    assert minted["paired"] is False, "nobody has tapped it yet"

    screen = client.get(f"/v1/assist/{minted['token']}")
    assert screen.status_code == 200, screen.text
    assert screen.json()["tier"] == "guest"


@pytest.mark.asyncio
async def test_asking_twice_returns_the_same_pairing(client: Any) -> None:
    """The broker presses it again because the SMS was slow.

    Two live tokens for one call would send half the pushes to a screen nobody is looking
    at — `D110`'s `open_leg` lesson, in a new place.
    """
    call_id = await on_a_call(client)

    assert link_for(client, call_id)["token"] == link_for(client, call_id)["token"]


def test_an_unknown_token_is_a_404_and_says_nothing_else(client: Any) -> None:
    """Whether a token ever existed is not information a stranger with a stale link is
    owed."""
    assert client.get("/v1/assist/ast_totally_made_up").status_code == 404


# --- the tier split, which is the whole point ---------------------------------------


@pytest.mark.asyncio
async def test_a_link_only_screen_may_be_shown_things_true_for_anybody(client: Any) -> None:
    """No account needed to receive help. A customer comparing plans gets everything they
    need without registering, because none of it is about them."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.get(f"/v1/assist/{token}")

    pushed = client.post(
        f"/v1/agent/calls/{call_id}/assist/push",
        json={
            "tool_id": "compare.plans",
            "payload": {"columns_th": ["ความคุ้มครอง", "แผนปัจจุบัน"], "rows": []},
        },
    )

    assert pushed.status_code == 200, pushed.text
    assert client.get(f"/v1/assist/{token}").json()["items"][0]["kind"] == "comparison"


@pytest.mark.asyncio
async def test_a_personal_push_to_a_link_only_screen_is_refused(client: Any) -> None:
    """And the refusal is the feature (`D120`, `D74`).

    A prefilled form is about a particular person. Tapping a link proves somebody is
    holding that phone, which is not the same claim.
    """
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.get(f"/v1/assist/{token}")

    refused = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "form.claim_notify"}
    )

    assert refused.status_code == 400
    assert "sign in" in refused.json()["detail"].lower(), (
        "the broker must be told WHY, so they can ask the customer to sign in"
    )


@pytest.mark.asyncio
async def test_signing_in_unlocks_the_personal_pushes(client: Any) -> None:
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]

    signed = client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})
    assert signed.status_code == 200, signed.text
    assert signed.json()["tier"] == "verified"

    pushed = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "form.claim_notify"}
    )
    assert pushed.status_code == 200, pushed.text


@pytest.mark.asyncio
async def test_an_unknown_tool_is_refused_rather_than_rendered_blank(client: Any) -> None:
    """The catalogue is closed for the same reason the intent taxonomy is: an unknown tool
    is a blank panel on somebody's phone in the middle of a call."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})

    response = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "tool.hologram"}
    )

    assert response.status_code == 400
    assert "hologram" in response.json()["detail"]


# --- the round trip -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_form_the_customer_fills_reaches_the_broker(client: Any) -> None:
    """The one tool that has to work end to end.

    This is journey step 4 in a test: the broker stops describing a website and puts the
    form on the screen, and what the customer types comes back without anybody reading a
    field name down a phone line.
    """
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})

    item_id = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "form.claim_notify"}
    ).json()["item_id"]

    sent = client.post(
        f"/v1/assist/{token}/respond",
        json={
            "item_id": item_id,
            "response": {"detail": "ผ่าตัดนิ่วในถุงน้ำดี", "incident_date": "2026-09-08"},
        },
    )
    assert sent.status_code == 200, sent.text

    seen = client.get(f"/v1/agent/calls/{call_id}/assist").json()
    assert seen["items"][0]["responded"] is True
    assert seen["items"][0]["response"]["detail"] == "ผ่าตัดนิ่วในถุงน้ำดี"


@pytest.mark.asyncio
async def test_the_screen_says_whether_a_call_is_actually_live(client: Any) -> None:
    """So the page can show "you are talking to an agent" rather than a bare form with no
    context — and so a stale tab does not imply somebody is listening."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]

    assert client.get(f"/v1/assist/{token}").json()["call_active"] is True

    client.post(f"/v1/agent/calls/{call_id}/end", json={"reason": "caller_hung_up"})
    client.post(
        f"/v1/agent/calls/{call_id}/wrapup", json={"disposition": "advice_given", "notes": "-"}
    )

    assert client.get(f"/v1/assist/{token}").json()["call_active"] is False


@pytest.mark.asyncio
async def test_the_pairing_outlives_the_call_briefly(client: Any, clock: ManualClock) -> None:
    """A form somebody is halfway through when the broker hangs up should still submit.

    A token that lived for the rest of the day would be a standing key to a screen, so
    the grace is minutes rather than hours (`D14`).
    """
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    container = client.app.state.container

    container.assist.close(call_id)
    assert client.get(f"/v1/assist/{token}").status_code == 200, "still open right after"

    clock.advance(seconds=15 * 60)
    assert client.get(f"/v1/assist/{token}").status_code == 404, "and gone after the grace"


# --- `D121`: the gate is the tool's PURPOSE, not its shape ---------------------------


@pytest.mark.asyncio
async def test_a_blank_form_reaches_a_guest_screen_because_it_is_about_nobody(
    client: Any,
) -> None:
    """The correction `D121` makes, and the reason it is not cosmetic.

    `D120` gated on the KIND, so every form needed an account. But a blank "please quote
    me" form is true for anybody — a stranger holding the handset learns nothing about the
    customer from it — and requiring a sign-in to fill one in gates the single part of the
    journey with no privacy cost at all. That is the ordinary and wrong design.
    """
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.get(f"/v1/assist/{token}")

    pushed = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "form.quote_request"}
    )

    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["kind"] == "form", "still a form — it is the PURPOSE that differs"
    screen = client.get(f"/v1/assist/{token}").json()
    assert screen["tier"] == "guest"
    assert [f["name"] for f in screen["items"][0]["payload"]["fields"]] == [
        "product_line",
        "full_name",
        "phone",
        "note",
    ], "the field list comes from config, not from whatever the client sent"


@pytest.mark.asyncio
async def test_a_personal_form_arrives_prefilled_from_the_frozen_snapshot(
    client: Any,
) -> None:
    """Prefilling is what makes a form a statement about one customer, which is exactly
    why it is only reachable past the sign-in."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})

    client.post(f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "form.claim_notify"})

    prefill = client.get(f"/v1/assist/{token}").json()["items"][0]["payload"]["prefill"]
    assert prefill["policy_no"], "the policy number should be filled in for them"
    assert prefill["insurer"], "and which carrier it is with (`D117`)"


@pytest.mark.asyncio
async def test_the_client_cannot_talk_its_way_past_the_gate(client: Any) -> None:
    """The gate reads `personal` from `assist_tools.yaml`, never from the request.

    A client able to declare its own push non-personal would be this gate's own bypass,
    so the request has nowhere to say it — extra keys are simply not part of the contract.
    """
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.get(f"/v1/assist/{token}")

    refused = client.post(
        f"/v1/agent/calls/{call_id}/assist/push",
        json={"tool_id": "form.claim_notify", "personal": False, "kind": "info"},
    )

    # 422, not 400: `ApiModel` forbids unknown fields, so the attempt is rejected as a
    # malformed request rather than accepted-and-ignored. That is the stronger of the two
    # outcomes — an ignored field is a bypass that merely happens not to work today.
    assert refused.status_code == 422, "the tool's own spec decides, not the caller"
    assert not client.get(f"/v1/assist/{token}").json()["items"], "and nothing was pushed"


@pytest.mark.asyncio
async def test_a_stubbed_tool_says_so_on_the_customers_screen(client: Any) -> None:
    """`D115` allows honest stubs and requires them to be labelled. A stub the customer
    cannot tell from the real thing is how a demo becomes a claim nobody meant to make."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})

    client.post(f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "doc.esign"})

    assert client.get(f"/v1/assist/{token}").json()["items"][0]["stub"] is True


@pytest.mark.asyncio
async def test_the_rail_is_served_from_config_rather_than_built_by_the_client(
    client: Any,
) -> None:
    """`D72`'s rule: a client that renders its own list will eventually offer a tool the
    server would refuse, and the client is the copy that is wrong."""
    await on_a_call(client)

    catalogue = client.get("/v1/agent/assist/tools").json()

    tools = {t["tool_id"]: t for g in catalogue["groups"] for t in g["tools"]}
    assert tools["form.quote_request"]["personal"] is False
    assert tools["form.claim_notify"]["personal"] is True
    assert [g["group_id"] for g in catalogue["groups"]] == [
        "compare",
        "forms",
        "documents",
        "links",
    ], "groups come back in their configured order, so the rail is not alphabetical"


# --- `B37`: the customer's call ends when the media does, not when the paperwork does ---


@pytest.mark.asyncio
async def test_ending_the_call_ends_it_on_the_customers_screen(client: Any) -> None:
    """`B37`. `D45` separates "the media stopped" from "the agent finished their notes",
    and the customer is on the far side of that line — they have hung up.

    `WRAP_UP` counted as live, so the customer's screen went on saying
    *"กำลังสนทนากับเจ้าหน้าที่"* for the whole of after-call work, which can run minutes.
    """
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.get(f"/v1/assist/{token}")
    assert client.get(f"/v1/assist/{token}").json()["call_active"] is True

    ended = client.post(f"/v1/agent/calls/{call_id}/end", json={"reason": "caller_hung_up"})
    assert ended.status_code == 200, ended.text

    assert client.get(f"/v1/assist/{token}").json()["call_active"] is False, (
        "after-call work is the agent's paperwork, not a conversation"
    )


@pytest.mark.asyncio
async def test_a_half_finished_form_still_submits_after_the_call_ends(client: Any) -> None:
    """The other half of `B37`, and the reason `close()` shortens the pairing rather than
    deleting it: `PAIRING_GRACE` exists so hanging up does not blank a form under the
    customer's fingers."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})
    item_id = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"tool_id": "form.claim_notify"}
    ).json()["item_id"]

    client.post(f"/v1/agent/calls/{call_id}/end", json={"reason": "caller_hung_up"})

    sent = client.post(
        f"/v1/assist/{token}/respond",
        json={"item_id": item_id, "response": {"detail": "พิมพ์ค้างไว้ตอนวางสาย"}},
    )
    assert sent.status_code == 200, "the grace window is the whole point of close()"
    assert sent.json()["items"][0]["responded"] is True


# --- `B38`: hanging up has to actually remove the caller ------------------------------


@pytest.mark.asyncio
async def test_a_caller_who_hangs_up_leaves_the_queue(client: Any) -> None:
    """`B38`. `dispatch.release` had exactly ONE caller — the accept path — so a caller
    who gave up stayed in the waiting pool: state `ABANDONED`, and the matcher still
    routing them to desks. `D78`'s hazard exactly, a projection nothing updates."""
    client.post("/v1/demo/session", json={"customer_id": "C000001"})
    intent = client.post("/v1/calls/intents", json={"app_intent": "health.claim.notify"}).json()
    client.post(
        "/v1/demo/calls",
        json={
            "correlation_token": intent["correlation_token"],
            "intent_code": "health.claim.notify",
            "caller_number": "0812345678",
            "intake_keys": ["2"],
            "ignore_hours": True,
        },
    )
    container = client.app.state.container
    assert len(container.dispatch.waiting()) == 1

    assert client.post("/v1/app/call/hangup").status_code == 200

    assert container.dispatch.waiting() == [], "an abandoned caller must not still be routable"


@pytest.mark.asyncio
async def test_hanging_up_while_a_desk_is_ringing_frees_the_agent(client: Any) -> None:
    """The edge case the user named: nobody has accepted yet.

    Abandoning the call alone would leave the agent stuck holding an offer for somebody
    who is no longer there — so this goes through `AssignmentService.cancel`, which is
    what frees their presence and resolves the assignment.
    """
    client.post("/v1/agent/demo-login", json={"agent_id": "A006"})
    client.post("/v1/agent/state", json={"agent_intent": "ready"})
    client.post("/v1/demo/session", json={"customer_id": "C000001"})
    intent = client.post("/v1/calls/intents", json={"app_intent": "health.advice.compare"}).json()
    client.post(
        "/v1/demo/calls",
        json={
            "correlation_token": intent["correlation_token"],
            "intent_code": "health.advice.compare",
            "caller_number": "0812345678",
            "intake_keys": ["2"],
            "ignore_hours": True,
        },
    )
    await sweep_once(client.app.state.container)
    assert client.get("/v1/agent/me").json()["offer"] is not None, "a desk is ringing"

    assert client.post("/v1/app/call/hangup").status_code == 200

    me = client.get("/v1/agent/me").json()
    assert me["offer"] is None, "the offer card must clear"
    assert me["presence"]["system_state"] != "offering", "and the agent must be free again"


@pytest.mark.asyncio
async def test_hanging_up_mid_conversation_ends_the_call_and_starts_acw(client: Any) -> None:
    """`B39`, and the ORDINARY case — which is the one I did not test.

    `B38` was fixed only for a caller still waiting, because the user mentioned that as an
    edge case to remember and I treated it as the whole case. During an actual
    conversation, hanging up returned **409**: `IN_CALL` cannot reach `ABANDONED` at all,
    and the code fell through to `orchestrator.abandon`.

    That transition is correctly forbidden — a conversation that happened is not an
    abandoned call — so the ending is the same one the agent's own วางสาย uses:
    `end_call`, which moves the call to `WRAP_UP` and starts after-call work. The agent
    still owes a wrap-up for a call that happened, whoever put the phone down.
    """
    call_id = await on_a_call(client)
    client.post("/v1/demo/session", json={"customer_id": "C000001"})

    hung_up = client.post("/v1/app/call/hangup")

    assert hung_up.status_code == 200, hung_up.text
    assert hung_up.json()["call_session_id"] == call_id
    me = client.get("/v1/agent/me").json()
    assert me["presence"]["system_state"] == "after_call_work", (
        "the agent is owed their ACW even though the customer rang off"
    )
    assert me["wrapup_call_session_id"] == call_id
