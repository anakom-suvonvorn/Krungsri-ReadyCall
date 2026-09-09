"""The customer TYPES what they wanted to say, instead of speaking it (`D133`).

`D88` put the intake seam at **turns rather than frames** so a strategy is a pure function
of what was said, however it was said. Typed text is a turn that never needed a model — so
this is a first-class path, not a stand-in, and everything downstream works with no audio,
no microphone and no GPU anywhere.

That property is worth a test file of its own, because it is what lets the whole AI story
be demonstrated on a laptop with nothing plugged into it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, pump_once
from readycall.clock import ManualClock
from readycall.config import Settings
from tests.conftest import REPO_ROOT


@pytest.fixture
def clock() -> ManualClock:
    """A Monday morning in Bangkok — the default `ManualClock()` is a public holiday and
    every `business` queue is shut."""
    return ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        prompt_dir=REPO_ROOT / "prompts" / "th",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )


@pytest.fixture
def client(settings: Settings, clock: ManualClock) -> Any:
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


def container(client: Any) -> Any:
    return client.app.state.container


def sign_in_customer(client: Any, customer_id: str = "C000001") -> None:
    assert client.post("/v1/demo/session", json={"customer_id": customer_id}).status_code == 200


def place(client: Any, *, keys: list[str]) -> str:
    """A call that answered the intake offer the way the app does — before dialling.

    ⚠️ `intake_keys` carries the customer's choice from the app's own sheet (`D133`).
    `["1"]` consents and opens the intake; `["2"]` declines, and the refusal is recorded
    as a refusal rather than as nothing (`D88`).
    """
    response = client.post(
        "/v1/demo/calls",
        json={
            "intent_code": "health.claim.notify",
            "caller_number": "0812345678",
            "intake_keys": keys,
            "ignore_hours": True,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["call_session_id"])


# --- the path ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_reaches_the_transcript_with_no_audio_anywhere(client: Any) -> None:
    sign_in_customer(client)
    call_id = place(client, keys=["1"])

    for text in ("พรุ่งนี้ผมต้องเข้าโรงพยาบาลกรุงเทพครับ", "ต้องเตรียมเอกสารอะไรบ้าง"):
        assert client.post("/v1/app/intake/note", json={"text": text}).status_code == 200

    # ⚠️ `publish()` only ENQUEUES (`D105`). Nothing reaches a subscriber until the bus is
    # drained, and this app is built with `bus_drain_interval_s=0` so the test drives it.
    # Forgetting this is `B24`: a correct, tested subscriber that nothing ever reached.
    await pump_once(container(client))
    held = container(client).transcript_delivery.turns_for(call_id)
    assert [t["text"] for t in held] == [
        "พรุ่งนี้ผมต้องเข้าโรงพยาบาลกรุงเทพครับ",
        "ต้องเตรียมเอกสารอะไรบ้าง",
    ]
    assert [t["seq"] for t in held] == [1, 2], "order is the record; it must not be a set"


@pytest.mark.asyncio
async def test_a_typed_turn_is_never_labelled_as_speech(client: Any) -> None:
    """`engine` says `typed`, and `asr_confidence` stays empty.

    Recording a keyboard as though it were a transcriber would put a confidence score on
    something that was never uncertain, and would make the two indistinguishable in
    `transcript_turns` afterwards — which is the table P4's analysis and P6's wrap-up read.
    """
    sign_in_customer(client)
    call_id = place(client, keys=["1"])
    client.post("/v1/app/intake/note", json={"text": "รถชนเมื่อเช้านี้ครับ"})

    published: list[Any] = []

    async def capture(event: Any) -> None:
        published.append(event)

    container(client).bus.subscribe("transcript.turn", capture)
    client.post("/v1/app/intake/note", json={"text": "ไม่มีใครบาดเจ็บ"})
    await pump_once(container(client))

    assert published, "a typed turn must go on the bus like any other (`D114`)"
    event = published[-1]
    assert getattr(event, "engine", None) == "typed"
    assert getattr(event, "asr_confidence", None) is None
    assert call_id  # the call is what all of this hangs off


def test_typing_is_refused_when_the_customer_declined_the_offer(client: Any) -> None:
    """Pressing 2 means no intake was opened, so there is nothing to write into.

    A 409 rather than a silent drop: the app has a box on screen and the customer pressed
    send, and "it went nowhere and nobody said so" is the failure `B37`'s grace-window
    half already taught once.
    """
    sign_in_customer(client)
    place(client, keys=["2"])
    response = client.post("/v1/app/intake/note", json={"text": "อยากเล่าเพิ่ม"})
    assert response.status_code == 409


def test_typing_is_refused_when_no_call_is_live(client: Any) -> None:
    sign_in_customer(client)
    assert client.post("/v1/app/intake/note", json={"text": "สวัสดี"}).status_code == 409


def test_an_empty_note_is_refused_rather_than_stored(client: Any) -> None:
    sign_in_customer(client)
    place(client, keys=["1"])
    assert client.post("/v1/app/intake/note", json={"text": "   "}).status_code == 400


@pytest.mark.asyncio
async def test_the_app_can_see_whether_the_intake_is_still_open(client: Any) -> None:
    """What the typing box is drawn from. It reports state; it never changes it."""
    sign_in_customer(client)
    call_id = place(client, keys=["1"])

    state = client.get("/v1/app/assist").json()
    assert state["intake"]["open"] is True
    assert state["intake"]["consented"] is True
    assert state["intake"]["turns"] == 0

    client.post("/v1/app/intake/note", json={"text": "รถชนเมื่อเช้านี้ครับ"})
    await pump_once(container(client))
    assert client.get("/v1/app/assist").json()["intake"]["turns"] == 1
    assert container(client).transcript_delivery.turns_for(call_id)


def test_a_declined_offer_says_so_rather_than_looking_unanswered(client: Any) -> None:
    """`D88`: `declined` and `ignored` are different facts, and the app must not render a
    refusal as though the question were still open."""
    sign_in_customer(client)
    place(client, keys=["2"])
    intake = client.get("/v1/app/assist").json()["intake"]
    assert intake["open"] is False
    assert intake["consented"] is False


@pytest.mark.asyncio
async def test_a_long_note_is_not_rate_guarded_the_way_speech_is(client: Any) -> None:
    """⚠️ `D98`'s guard refuses more characters than a human could have SPOKEN in the
    seconds of audio a turn arrived on. A keyboard has no such limit, and applying it here
    would police a customer rather than catch a hallucinating model."""
    sign_in_customer(client)
    call_id = place(client, keys=["1"])
    long_note = "รถชนเมื่อเช้านี้ที่ถนนรัชดา " * 20  # far more than 15 chars/second
    assert client.post("/v1/app/intake/note", json={"text": long_note}).status_code == 200
    await pump_once(container(client))
    assert container(client).transcript_delivery.turns_for(call_id), "it must be kept"
