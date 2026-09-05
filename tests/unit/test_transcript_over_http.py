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


async def settle(client: Any, *, call_id: str | None = None, tries: int = 20) -> None:
    """Let the transcriber catch up, then run the bus.

    **Not a `sleep`, and the distinction matters.** `TranscriptionStream` never blocks
    ingestion on the model (`D12`), so `push()` queues a segment and a background worker
    transcribes it — on the *application's* event loop, which under `TestClient` only
    advances while a request is in flight. So this makes cheap requests until the turns
    appear rather than waiting on a clock: it is the same thing a real process does by
    simply continuing to run, and it fails fast instead of being slow when it passes.

    The first version of these tests had no such step and passed anyway, on scheduling
    luck. That is worse than failing — it is a green test asserting something the code did
    not guarantee.
    """
    delivery = container(client).transcript_delivery
    for _ in range(tries):
        me(client)  # gives the app loop a slice
        await pump_once(container(client))
        waiting_on = [call_id] if call_id else list(delivery.live_call_ids())
        if waiting_on and all(delivery.turns_for(one) for one in waiting_on):
            return
    await pump_once(container(client))


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

    await settle(client)
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
async def test_the_accept_response_already_carries_the_transcript(client: Any) -> None:
    """REST and the socket read the same list, so neither waits for the other (`D106`).

    Worth pinning down, because the two could easily have diverged. The snapshot builds
    `transcript` from the delivery service directly rather than from anything the socket
    did, so an agent whose socket is down still sees the transcript on a plain page load —
    which is what `D32` means by the tab being allowed to be flaky.

    The one thing that genuinely arrives late is the **tail**: `transcription.close()` runs
    inside this request and the turns it flushes are published to a bus that is drained by
    the pump, not on the request path (`D105`, and `D6`'s rule about not doing work there).
    They land within one `bus_drain_interval_s` — 0.05 s by default, against
    `ARCHITECTURE` §15's 1 s for match-to-screen.
    """
    sign_in(client)
    place_recorded_call(client)
    await settle(client)
    offer = me(client)["offer"]
    accepted = client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").json()
    assert accepted["transcript"], "a page load must not depend on a socket push"

    await pump_once(container(client))
    assert len(me(client)["transcript"]) >= len(accepted["transcript"])


@pytest.mark.asyncio
async def test_the_transcript_survives_a_refresh_and_the_hang_up(client: Any) -> None:
    """It is what the wrap-up is written from (`ARCHITECTURE` §12), and
    `active_call_session_id` goes null the moment the record is saved (`D68`)."""
    sign_in(client)
    placed = place_recorded_call(client)
    await settle(client)
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


@pytest.mark.asyncio
async def test_the_SECOND_call_of_a_demo_also_has_a_transcript(client: Any) -> None:
    """The one thing only a running server showed (`D107`).

    The container builds **one** STT engine for the process, which is right for a model
    and wrong for a script: `ScriptedSttEngine` carries a cursor through its lines, so the
    first demo call consumed all of them and the second — and every one after it — found
    the script exhausted and rendered an empty panel. On stage that is the stage-safe
    fallback failing in exactly the way it exists to prevent.

    **It passed every other test in this file**, because each of those places one call.
    Deliberately asserted against the delivery service rather than through an accept: what
    is being checked is that the audio path produces turns for the third caller, and
    dragging an agent through two lots of after-call work to find that out would make the
    test about something else.
    """
    sign_in(client)
    per_call: list[list[str]] = []
    for _ in range(3):
        placed = place_recorded_call(client)
        await settle(client, call_id=placed["call_session_id"])
        held = container(client).transcript_delivery.turns_for(placed["call_session_id"])
        per_call.append([t["text"] for t in held])

    assert all(per_call), f"a later call had no transcript at all: {per_call}"
    assert per_call[0] == per_call[1] == per_call[2], (
        "each recording must start at the top of the script"
    )


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
