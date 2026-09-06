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
            "kind": "comparison",
            "title_th": "เปรียบเทียบแผนสุขภาพ",
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
        f"/v1/agent/calls/{call_id}/assist/push",
        json={"kind": "form", "title_th": "แบบฟอร์มเคลม", "payload": {"fields": []}},
    )

    assert refused.status_code == 400
    assert "sign" in refused.json()["detail"].lower(), (
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
        f"/v1/agent/calls/{call_id}/assist/push",
        json={"kind": "form", "title_th": "แบบฟอร์มเคลม", "payload": {"fields": []}},
    )
    assert pushed.status_code == 200, pushed.text


@pytest.mark.asyncio
async def test_an_unknown_push_kind_is_refused_rather_than_rendered_blank(client: Any) -> None:
    """The set is closed for the same reason the intent taxonomy is: an unknown kind is a
    blank panel on somebody's phone in the middle of a call."""
    call_id = await on_a_call(client)
    token = link_for(client, call_id)["token"]
    client.post(f"/v1/assist/{token}/sign-in", json={"customer_id": "C000001"})

    response = client.post(
        f"/v1/agent/calls/{call_id}/assist/push", json={"kind": "hologram", "title_th": "x"}
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
        f"/v1/agent/calls/{call_id}/assist/push",
        json={
            "kind": "form",
            "title_th": "แจ้งข้อมูลการเข้ารักษา",
            "payload": {
                "fields": [
                    {"name": "hospital", "label_th": "โรงพยาบาล"},
                    {"name": "admit_date", "label_th": "วันที่เข้ารักษา", "type": "date"},
                ]
            },
        },
    ).json()["item_id"]

    sent = client.post(
        f"/v1/assist/{token}/respond",
        json={
            "item_id": item_id,
            "response": {"hospital": "โรงพยาบาลกรุงเทพ", "admit_date": "2026-09-08"},
        },
    )
    assert sent.status_code == 200, sent.text

    seen = client.get(f"/v1/agent/calls/{call_id}/assist").json()
    assert seen["items"][0]["responded"] is True
    assert seen["items"][0]["response"]["hospital"] == "โรงพยาบาลกรุงเทพ"


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
