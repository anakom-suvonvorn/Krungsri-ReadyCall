"""Coming back after the platform dropped a silent desk (`D139`, `B42`).

⚠️ **The session and the presence expire independently, and only one of them expired.**
`presence.sweep` moves a desk that stopped heartbeating to `OFFLINE` and clears its
`session_id` — but the agent's **cookie is untouched**. So `GET /me` keeps answering `200`
with an offline presence, a browser reload fetches exactly the same dead state, and the
only way back was to sign out and sign in again. The 401 path the client already had
cannot see this, because there is no 401.

The tests below are written from the *user's* report — "it stays in a weird state and
reload does not fix it" — rather than from the code, so the first one asserts the stuck
state exists before the second asserts the way out of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app
from readycall.clock import ManualClock
from readycall.config import Settings
from tests.conftest import REPO_ROOT

AGENT = "A003"


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC))


@pytest.fixture
def client(clock: ManualClock) -> Iterator[TestClient]:
    settings = Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        prompt_dir=REPO_ROOT / "prompts" / "th",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
        llm_warmup_enabled=False,
    )
    app = create_app(settings, clock=clock)
    with TestClient(app) as c:
        c.post("/v1/agent/demo-login", json={"agent_id": AGENT})
        yield c


def _go_silent(client: TestClient, clock: ManualClock) -> None:
    """Let the heartbeat lapse and run the sweep, exactly as a real shift would."""
    container = client.app.state.container  # type: ignore[attr-defined]
    clock.advance(container.settings.agent_presence_ttl_s + 5)
    dropped = client.portal.call(container.presence.sweep)  # type: ignore[attr-defined]
    assert AGENT in dropped, "the sweep did not drop a silent desk; the setup is wrong"


def test_a_dropped_desk_is_still_authenticated_which_is_why_reload_never_fixed_it(
    client: TestClient, clock: ManualClock
) -> None:
    """⚠️ The bug, stated as an assertion. This is `B42` and it must keep being true —
    the fix is a new transition, NOT expiring the session, because expiring it would sign
    somebody out mid-shift for looking at another window."""
    _go_silent(client, clock)

    me = client.get("/v1/agent/me")

    assert me.status_code == 200, "a 401 here would mean the client's existing path handled it"
    assert me.json()["presence"]["system_state"] == "offline"


def test_resume_puts_the_desk_back_without_signing_out(
    client: TestClient, clock: ManualClock
) -> None:
    """The way out that did not exist. One button, no sign-out, same session."""
    _go_silent(client, clock)

    body = client.post("/v1/agent/resume").json()

    assert body["presence"]["system_state"] == "available"
    assert client.get("/v1/agent/me").json()["presence"]["system_state"] == "available"


def test_resume_does_NOT_make_the_desk_ready(client: TestClient, clock: ManualClock) -> None:
    """`D51`. Coming back is not the same as being at the desk — the platform has no idea
    whether they are, so the person presses พร้อมรับสาย themselves. Resuming straight into
    `READY` would hand a call to an empty chair, which is the whole reason a drop happened."""
    _go_silent(client, clock)

    presence = client.post("/v1/agent/resume").json()["presence"]

    assert presence["agent_intent"] == "not_ready"
    assert presence["offerable"] is False


def test_resume_keeps_the_same_session_cookie(client: TestClient, clock: ManualClock) -> None:
    """⚠️ Deliberately not a re-login. Re-issuing the cookie would rotate the session and
    reset the per-agent push sequence (`B27`) for somebody who never actually left."""
    before = client.cookies.get("readycall_agent")
    _go_silent(client, clock)

    client.post("/v1/agent/resume")

    assert client.cookies.get("readycall_agent") == before


def test_resuming_a_desk_that_is_already_live_is_not_an_error(client: TestClient) -> None:
    """Two tabs, or a click after the socket already recovered. Returning the snapshot is
    the honest answer to "put me back" when they are already back."""
    response = client.post("/v1/agent/resume")

    assert response.status_code == 200
    assert response.json()["presence"]["system_state"] == "available"


def test_resume_requires_a_session(client: TestClient, clock: ManualClock) -> None:
    """It is a way back for somebody already authenticated, never a way in."""
    _go_silent(client, clock)
    client.cookies.clear()

    assert client.post("/v1/agent/resume").status_code == 401


# --- D144: สายสุดท้าย actually gets a last call -------------------------------------------


def _place(client: TestClient, intent: str = "general.renewal") -> str:
    body = client.post(
        "/v1/demo/calls",
        json={
            "intent_code": intent,
            "caller_number": "+66898887777",
            "intake_keys": ["2"],
            "ignore_hours": True,
        },
    ).json()
    return str(body["call_session_id"])


def test_last_call_is_offered_the_waiting_caller(client: TestClient) -> None:
    """`D144`, and it is the user's report as an assertion: *"if there's someone in the
    queue it won't do anything"*. It does now — a desk declaring สายสุดท้าย while idle is
    asking for **one more call**, and the caller already waiting is that call."""
    client.post("/v1/agent/state", json={"agent_intent": "last_call"})
    _place(client)

    offer = client.get("/v1/agent/me").json()["offer"]

    assert offer is not None, "a waiting caller never reached a desk on สายสุดท้าย"


def test_draining_is_NOT_offered_the_waiting_caller(client: TestClient) -> None:
    """The other half. If both options behaved the same the reversal would have moved the
    duplication rather than removed it."""
    client.post("/v1/agent/state", json={"agent_intent": "draining"})
    _place(client)

    assert client.get("/v1/agent/me").json()["offer"] is None


def test_the_last_call_spends_the_instruction_when_it_ends(client: TestClient) -> None:
    """The end condition that could never fire before, because the call that spends it was
    never offered. Take the last call, end it, and the desk stops asking for work."""
    client.post("/v1/agent/state", json={"agent_intent": "last_call"})
    call_id = _place(client)
    offer = client.get("/v1/agent/me").json()["offer"]
    assert offer is not None
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")

    client.post(f"/v1/agent/calls/{call_id}/end", json={"reason": "caller_hung_up"})

    presence = client.get("/v1/agent/me").json()["presence"]
    assert presence["agent_intent"] == "not_ready"
    assert presence["intent_reason"] == "last_call_fulfilled"


def test_a_desk_on_last_call_is_not_handed_a_SECOND_caller(client: TestClient) -> None:
    """"Last" has to mean last. Once the call is in flight the desk is no longer
    `AVAILABLE`, which is where that half of the old rule actually lives."""
    client.post("/v1/agent/state", json={"agent_intent": "last_call"})
    _place(client)
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")

    _place(client)

    assert client.get("/v1/agent/me").json()["offer"] is None, "a second caller was offered"
