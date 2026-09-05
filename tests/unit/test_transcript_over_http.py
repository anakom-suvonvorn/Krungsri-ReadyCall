"""The whole path, driven the way a browser and a phone line drive it (`D106`, `D107`).

`test_transcript_delivery.py` proves the service. This proves the **wiring**, which is the
half that keeps being wrong in this project: `B7`, `B9` and `B12` were all correct code
that nothing called, or called with the wrong thing, and no unit test could see any of it.

The chain under test, in one line: a caller presses 1 → a recording opens → a WAV is fed
down the leg → the endpointer cuts it → the engine transcribes → the strategy publishes
`transcript.turn` → the bus is pumped → the delivery service holds it → an agent accepts →
it is on their screen.

Every link is the production one. The only stand-ins are the trigger (`POST /v1/demo/calls`
instead of Asterisk) and the engine (`scripted` instead of Typhoon), and both are the
stand-ins the system is designed around rather than test scaffolding.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, pump_once
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.media.sources import write_wav
from tests.conftest import REPO_ROOT


def _speech(*, seconds: float, rate: int = 16000, amplitude: float = 0.25) -> list[float]:
    """Loud and harmonically structured — enough for `EnergyVad`, which is what CI runs."""
    n = int(rate * seconds)
    return [
        amplitude
        * (math.sin(2 * math.pi * 190 * i / rate) + 0.5 * math.sin(2 * math.pi * 700 * i / rate))
        / 1.5
        for i in range(n)
    ]


@pytest.fixture
def clock() -> ManualClock:
    """A Monday morning in Bangkok — `ManualClock()`'s default is a public holiday and
    every `business` queue is shut, which costs an hour every time somebody forgets."""
    return ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC))


@pytest.fixture
def audio_dir(tmp_path: Any) -> Any:
    """Three utterances of three seconds each, with silence between them.

    Synthesised rather than committed because `*.wav` is gitignored — and long enough on
    purpose: `D98`'s rate guard refuses more than ~15 characters per second of audio, and
    the shipped `config/demo_transcript.yaml` lines are real Thai sentences of 25-35
    characters. On the one-second utterances of a smaller fixture every line would be
    silently dropped, and the test would fail for a reason that had nothing to do with the
    wiring it is testing. That trap is worth keeping visible: the fallback script and the
    audio it plays over have to be sized for each other.
    """
    samples: list[float] = []
    for _ in range(3):
        samples += _speech(seconds=3.0)
        samples += [0.0] * int(16000 * 0.6)
    write_wav(tmp_path / "intake.wav", samples, sample_rate=16000)
    return tmp_path


@pytest.fixture
def settings(audio_dir: Any) -> Settings:
    return Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        demo_audio_dir=audio_dir,
        demo_transcript_file=REPO_ROOT / "config" / "demo_transcript.yaml",
        # Both drivers are stepped by hand. A test that waits on a background loop is slow
        # when it passes and unreadable when it fails (`B7`'s suite is written the same way).
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )


@pytest.fixture
def client(settings: Settings, clock: ManualClock) -> Any:
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


def sign_in(client: Any, agent_id: str = "A002") -> None:
    assert client.post("/v1/agent/demo-login", json={"agent_id": agent_id}).status_code == 200
    assert client.post("/v1/agent/state", json={"agent_intent": "ready"}).status_code == 200, (
        "an agent who is not ready is never offered anything"
    )


def place_recorded_call(client: Any, **kwargs: Any) -> dict[str, Any]:
    body = {
        "intent_code": "motor.claim.accident",
        "caller_number": "0812345678",
        # `1` accepts the recording offer. `2` would decline it and there would be no
        # transcript at all, which is a different test.
        "intake_keys": ["1"],
        "audio": "intake.wav",
        "ignore_hours": True,
        **kwargs,
    }
    response = client.post("/v1/demo/calls", json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def container(client: Any) -> Any:
    return client.app.state.container


def me(client: Any) -> dict[str, Any]:
    response = client.get("/v1/agent/me")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- the chain --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_recorded_call_puts_the_callers_words_on_the_agents_screen(
    client: Any,
) -> None:
    sign_in(client)
    placed = place_recorded_call(client)
    call_id = placed["call_session_id"]
    assert placed["intake"]["recording"] is True
    # `turn_count` is deliberately 0 here and that is not a bug: `run_offer` returns while
    # the intake is still LIVE (`D88`, `D21`), so it has no `IntakeResult` to count yet.
    # What proves the audio path ran is the delivery service holding turns.
    assert placed["intake"]["turn_count"] == 0

    await pump_once(container(client))
    held = container(client).transcript_delivery.turns_for(call_id)
    assert held, (
        "the audio path produced no turns - nothing opened the recording, the detector "
        "found no speech, or the scripted lines were refused by `D98`'s rate guard for "
        "being too long for the audio they arrived on (`D107`)"
    )

    # And none of it is on a screen: during intake the call belongs to nobody.
    assert me(client)["transcript"] == [], "there is no agent to send a transcript to yet"

    offer = me(client)["offer"]
    assert offer is not None, "the caller should have been offered to the ready agent"
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    await pump_once(container(client))

    transcript = me(client)["transcript"]
    assert transcript, "accepting must flush everything the caller said while waiting"
    assert [t["seq"] for t in transcript] == list(range(1, len(transcript) + 1))
    assert all(t["speaker_role"] == "customer" for t in transcript)
    assert all(t["text"].strip() for t in transcript)
    # The timings come from the AUDIO, not from the script (`D107`) — a scripted line
    # inherits the segment it arrived on, which is what makes the fallback look like a
    # transcription rather than a slideshow.
    assert transcript[0]["t_end_ms"] > transcript[0]["t_start_ms"]
    assert transcript[-1]["t_start_ms"] >= transcript[0]["t_start_ms"]


@pytest.mark.asyncio
async def test_the_accept_response_is_early_and_the_socket_catches_up(client: Any) -> None:
    """A known, deliberate consequence of `D105` rather than a defect.

    The bus is drained by the pump, not on the request path — putting a `drain()` inside
    the accept handler would run every subscriber in the process before the agent's screen
    could paint, which is what `D6` moved OFF the request path in the first place. So the
    accept response can carry an empty transcript and the `transcript` push fills it
    within one `bus_drain_interval_s` (0.05 s by default), well inside `ARCHITECTURE`
    §15's 1 s for match-to-screen. Asserted here so that if it ever changes, it changes on
    purpose.
    """
    sign_in(client)
    place_recorded_call(client)
    offer = me(client)["offer"]
    accepted = client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").json()
    assert accepted["transcript"] == []

    await pump_once(container(client))
    assert me(client)["transcript"], "one pump later it is there"


@pytest.mark.asyncio
async def test_the_transcript_survives_a_refresh_and_the_hang_up(client: Any) -> None:
    """It is what the wrap-up is written from (`ARCHITECTURE` §12), and
    `active_call_session_id` goes null the moment the record is saved (`D68`)."""
    sign_in(client)
    placed = place_recorded_call(client)
    offer = me(client)["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    await pump_once(container(client))

    during_call = me(client)["transcript"]
    assert during_call, "a cold page load mid-call must not be blank"

    client.post(f"/v1/agent/calls/{placed['call_session_id']}/end")
    await pump_once(container(client))
    in_wrap_up = me(client)
    assert in_wrap_up["active_call_session_id"] is None or in_wrap_up["transcript"]
    assert in_wrap_up["transcript"] == during_call, (
        "after-call work is when the agent reads it; clearing it at hang-up empties the "
        "panel at the one moment it is most useful"
    )


@pytest.mark.asyncio
async def test_a_declined_recording_leaves_no_transcript(client: Any) -> None:
    """Pressing 2 is a recorded refusal, not a thin transcript (`D88`, `D14`)."""
    sign_in(client)
    placed = place_recorded_call(client, intake_keys=["2"])
    assert placed["intake"]["consented"] is False
    assert placed["intake"]["turn_count"] == 0

    offer = me(client)["offer"]
    accepted = client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").json()
    assert accepted["transcript"] == []


# --- the demo audio door ----------------------------------------------------------------


def test_a_path_is_not_a_filename(client: Any) -> None:
    """`audio` is a bare filename inside `demo_audio_dir` and nothing else (`D107`).

    A path in a request body is a file-read primitive. The endpoint being demo-gated is
    the *second* line of defence, not the first — a demo flag left on is exactly the
    configuration this would be exploited through.
    """
    sign_in(client)
    for attempt in ("../config/demo_personas.yaml", "thai_calls/anything.wav", "..", "/etc/passwd"):
        response = client.post(
            "/v1/demo/calls",
            json={
                "intent_code": "motor.claim.accident",
                "intake_keys": ["1"],
                "audio": attempt,
                "ignore_hours": True,
            },
        )
        assert response.status_code in {400, 404}, f"{attempt} was accepted: {response.text}"


def test_an_unknown_file_is_a_404_not_a_crash(client: Any) -> None:
    sign_in(client)
    response = client.post(
        "/v1/demo/calls",
        json={
            "intent_code": "motor.claim.accident",
            "intake_keys": ["1"],
            "audio": "nothing_here.wav",
            "ignore_hours": True,
        },
    )
    assert response.status_code == 404


def test_no_audio_still_opens_the_recording(client: Any) -> None:
    """A caller who consents and then says nothing is a real outcome, not an error. The
    recording opens, the silence timeout ends it, and the brief says why it is thin."""
    sign_in(client)
    placed = place_recorded_call(client, audio=None)
    assert placed["intake"]["recording"] is True
    assert placed["intake"]["turn_count"] == 0
    assert container(placed and client).transcription.live_call_ids() == (
        placed["call_session_id"],
    )
