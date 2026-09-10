"""The runtime model switch, and the false report it was built to fix (`D138`, `B41`).

Two things are proved here, and the first is a bug that had been true on the dev laptop for
days:

* **`B41` — a machine can be calling a hosted model while every screen says it is not.**
  `build_fast_llm` builds a REAL client whenever `LLM_FAST_MODEL` is set, whatever
  `LLM_PROVIDER` says, and the preview summary, the customer-context panel and the
  comparison's reason sentence all run on `fast_llm or llm`. The test-call dialog computed
  *"is the AI on?"* from `LLM_PROVIDER` alone and reported **"no model configured — calls
  no model"** on exactly that setup.
* **`D138` — the provider can be changed without a restart**, so a pitch can show the same
  call with and without the model. Rebuilt through `build_llm`, never persisted, and the
  request names a provider and nothing else.

Nothing here calls a model: the switch is asserted on which client got built, and
`rulebased` is a real adapter that needs no key.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app
from readycall.clock import ManualClock
from readycall.config import Settings
from tests.conftest import REPO_ROOT


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        prompt_dir=REPO_ROOT / "prompts" / "th",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
        # ⚠️ A REAL provider call otherwise, on a fake key, from a unit test (`B41`).
        llm_warmup_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, clock=ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC)))
    with TestClient(app) as client:
        client.post("/v1/agent/demo-login", json={"agent_id": "A003"})
        yield client


@pytest.fixture
def plain() -> Iterator[TestClient]:
    """No model anywhere — the shipped default (`D119`)."""
    yield from _client(_settings())


@pytest.fixture
def fast_only() -> Iterator[TestClient]:
    """⚠️ `B41`'s exact shape, and it is what the dev laptop's `.env` actually said:
    the main provider is rule-based and a fast model IS configured, so a real client is
    built and three of the four AI features use it."""
    yield from _client(
        _settings(
            llm_fast_provider="openai_compatible",
            llm_fast_base_url="https://api.openai.com/v1",
            llm_fast_model="gpt-5.4-mini",
            openai_api_key="sk-test-not-a-real-key",
        )
    )


# --- B41: the dialog must not say "no model" while one is being called --------------------


def test_the_dialog_reports_a_model_when_only_the_FAST_one_is_configured(
    fast_only: TestClient,
) -> None:
    """`B41`. `LLM_PROVIDER=rulebased` + `LLM_FAST_MODEL=gpt-5.4-mini` really does call a
    hosted model for the preview, the context panel and the comparison sentence. Saying
    *"ไม่เรียกโมเดลใดๆ"* there is a false statement about what the system is doing — and it
    is spending money while making it."""
    options = fast_only.get("/v1/demo/call-options").json()

    assert options["llm"]["real"] is True
    assert options["llm"]["fast_model"] == "gpt-5.4-mini"
    assert "ไม่เรียกโมเดลใดๆ" not in options["llm"]["note_th"]
    assert "gpt-5.4-mini" in options["llm"]["note_th"]


def test_the_dialog_still_says_no_model_when_there_really_is_none(plain: TestClient) -> None:
    """The other half. Loosening `B41`'s fix into "always claim a model" would be the same
    bug pointing the other way."""
    options = plain.get("/v1/demo/call-options").json()

    assert options["llm"]["real"] is False
    assert options["llm"]["model"] is None and options["llm"]["fast_model"] is None
    assert "ไม่เรียกโมเดลใดๆ" in options["llm"]["note_th"]


def test_the_note_names_the_two_stages_apart(fast_only: TestClient) -> None:
    """They genuinely differ here — rule-based after Accept, a real model before it — and
    collapsing that into one sentence is what made `B41` invisible."""
    note = fast_only.get("/v1/demo/call-options").json()["llm"]["note_th"]

    assert "แบบกฎ" in note, "the rule-based half of this configuration is not mentioned"
    assert "gpt-5.4-mini" in note, "the real half is not mentioned"


# --- D138: the switch --------------------------------------------------------------------


def test_the_panel_reports_what_is_available_without_any_key_in_the_payload(
    fast_only: TestClient,
) -> None:
    """⚠️ Availability is a boolean the server computed. A settings screen that echoed a
    key back would put a live credential in a browser, a log and a screenshot."""
    body = fast_only.get("/v1/agent/settings").json()["llm"]
    raw = fast_only.get("/v1/agent/settings").text

    by_provider = {o["provider"]: o for o in body["options"]}
    assert by_provider["rulebased"]["available"] is True
    assert by_provider["openai_compatible"]["available"] is True
    assert by_provider["anthropic"]["available"] is False, "no ANTHROPIC_API_KEY was set"
    assert "sk-test" not in raw, "a key reached the settings payload"


def test_switching_to_rulebased_turns_the_FAST_client_off_too(fast_only: TestClient) -> None:
    """⚠️ The half that makes "off" mean off. Leaving `fast_llm` alive would keep the offer
    card calling a hosted model while the panel reports the AI as disabled — which is
    `B41` again, created by the control that exists to fix it."""
    after = fast_only.post("/v1/agent/settings/llm", json={"provider": "rulebased"}).json()

    assert after["llm"]["enabled"] is False
    assert after["llm"]["model"] is None
    assert after["llm"]["fast_model"] is None


def test_switching_back_on_rebuilds_a_real_client(fast_only: TestClient) -> None:
    fast_only.post("/v1/agent/settings/llm", json={"provider": "rulebased"})
    after = fast_only.post("/v1/agent/settings/llm", json={"provider": "openai_compatible"}).json()

    assert after["llm"]["enabled"] is True
    assert after["llm"]["provider"] == "openai_compatible"


def test_a_provider_with_no_key_is_refused_by_the_SERVER(fast_only: TestClient) -> None:
    """`D121`, `B5`. The client greys it out; the gate is here. A panel that could be
    persuaded to select an unusable provider would leave every summary failing with a key
    error nobody could see from the workstation."""
    response = fast_only.post("/v1/agent/settings/llm", json={"provider": "anthropic"})

    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_an_unknown_provider_is_refused(fast_only: TestClient) -> None:
    assert fast_only.post("/v1/agent/settings/llm", json={"provider": "nope"}).status_code == 400


def test_the_request_cannot_name_a_base_url_or_a_key(fast_only: TestClient) -> None:
    """⚠️ The rule that matters most here. A request able to name its own endpoint would
    let a signed-in agent point this system's summariser at a server of their choosing and
    post every caller's words to it (`D4`, `D121`'s reasoning)."""
    response = fast_only.post(
        "/v1/agent/settings/llm",
        json={
            "provider": "openai_compatible",
            "llm_base_url": "https://evil.example.com/v1",
            "api_key": "sk-mine",
        },
    )

    assert response.status_code == 422, "an extra field was accepted rather than refused"


def test_the_switch_is_absent_on_a_non_demo_instance() -> None:
    """Gated on `DEMO_AGENT_LOGIN_ENABLED` (`D138`): an endpoint that can swap the AI
    provider over HTTP belongs behind the same switch as the one that lets anybody sign in
    as any agent."""
    settings = _settings(demo_agent_login_enabled=False)
    app = create_app(settings, clock=ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC)))
    with TestClient(app) as client:
        assert client.get("/v1/agent/settings").status_code in (401, 404)


def test_switching_clears_text_the_other_model_wrote(fast_only: TestClient) -> None:
    """⚠️ Both caches hold sentences a DIFFERENT model produced (`D135`, `D137`). Keeping
    them would show the broker one model's prose on a screen reporting the other — the
    panel lying about the one thing it exists to report."""
    box = fast_only.app.state.container  # type: ignore[attr-defined]
    box.comparison_reason_cache[("call_x", "health")] = {"P1": "เขียนโดยโมเดลเก่า"}
    box.context_summaries["call_x"] = object()

    fast_only.post("/v1/agent/settings/llm", json={"provider": "rulebased"})

    assert box.comparison_reason_cache == {}
    assert box.context_summaries == {}


# --- D142: each stage set independently ---------------------------------------------------


def test_the_four_stages_are_reported_separately(fast_only: TestClient) -> None:
    """`D142`, and it is `B41`'s fix generalised: four jobs, four answers. A panel that
    reported one number for all of them was wrong about three."""
    stages = fast_only.get("/v1/agent/settings").json()["llm"]["stages"]

    assert [s["stage"] for s in stages] == ["preview", "context", "comparison", "final"]
    # This fixture is `B41`'s shape: a fast model configured, the main one rule-based.
    by_stage = {s["stage"]: s for s in stages}
    assert by_stage["final"]["model"] is None
    assert by_stage["preview"]["model"] == "gpt-5.4-mini"
    assert by_stage["context"]["model"] == "gpt-5.4-mini"
    assert by_stage["comparison"]["model"] == "gpt-5.4-mini"


def test_one_stage_can_be_changed_without_touching_the_others(fast_only: TestClient) -> None:
    """The point of `D142`. Turning the comparison's model off must not silently take the
    offer-card preview with it."""
    body = fast_only.post(
        "/v1/agent/settings/llm", json={"provider": "rulebased", "stage": "comparison"}
    ).json()

    by_stage = {s["stage"]: s for s in body["llm"]["stages"]}
    assert by_stage["comparison"]["model"] is None
    assert by_stage["preview"]["model"] == "gpt-5.4-mini", "an unrelated stage was changed"
    assert by_stage["context"]["model"] == "gpt-5.4-mini"


def test_the_overall_provider_reads_mixed_rather_than_picking_one(fast_only: TestClient) -> None:
    """⚠️ `B41`'s lesson as a rule: when the four disagree, say so. Reporting one of them
    is a true statement about that stage and a false one about the rest."""
    fast_only.post("/v1/agent/settings/llm", json={"provider": "rulebased", "stage": "context"})

    assert fast_only.get("/v1/agent/settings").json()["llm"]["provider"] == "mixed"


def test_setting_every_stage_at_once_still_works(fast_only: TestClient) -> None:
    """The no-stage form is what the panel opens with and what a pitch uses."""
    body = fast_only.post("/v1/agent/settings/llm", json={"provider": "rulebased"}).json()["llm"]

    assert body["provider"] == "rulebased"
    assert all(s["model"] is None for s in body["stages"])
    assert body["enabled"] is False


def test_an_unknown_stage_is_refused(fast_only: TestClient) -> None:
    response = fast_only.post(
        "/v1/agent/settings/llm", json={"provider": "rulebased", "stage": "nope"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown stage"


def test_a_stage_still_cannot_reach_an_unavailable_provider(fast_only: TestClient) -> None:
    """The key check is per-request, not per-stage: naming a stage must not become a way
    round the gate (`D121`, `B5`)."""
    response = fast_only.post(
        "/v1/agent/settings/llm", json={"provider": "anthropic", "stage": "final"}
    )

    assert response.status_code == 400
