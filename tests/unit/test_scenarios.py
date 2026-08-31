"""Every scenario file must run clean, and produce identical output twice.

This is the P0 exit criterion turned into a test, so it cannot quietly rot: the whole
system is driven from intent (or a cold call) through to `closed` on fakes alone.

The determinism assertion matters as much as the pass/fail one — golden-output
comparison of briefs and matching decisions in later phases depends on two runs of the
same scenario being byte-identical.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from readycall import ids
from readycall.domain.enums import (
    AssuranceLevel,
    CallState,
    ConsentScope,
    DegradationReason,
    IdentityMethod,
    Urgency,
)
from tests.conftest import REPO_ROOT, SCENARIOS_DIR

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from run_scenario import Scenario, ScenarioRun, render

SCENARIO_FILES = sorted(SCENARIOS_DIR.glob("*.yaml"))


def test_scenario_files_exist() -> None:
    assert SCENARIO_FILES, "no scenario files found"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
async def test_scenario_reaches_its_expected_final_state(path: Path) -> None:
    scenario = Scenario.load(path)
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert str(session.state) == scenario.expect_state
    assert session.is_terminal
    assert session.ended_at is not None
    assert not run.bus.errors
    # The transition log is the demonstrable artifact; an empty one means nothing ran.
    assert len(session.transitions) >= 5


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
async def test_scenario_output_is_deterministic(path: Path) -> None:
    scenario = Scenario.load(path)

    outputs: list[str] = []
    for _ in range(2):
        ids.install(ids.DeterministicIds())
        run = ScenarioRun(scenario, root=REPO_ROOT)
        session = await run.execute()
        outputs.append(render(run, session))

    assert outputs[0] == outputs[1], "two runs of the same scenario diverged"


async def test_in_app_scenario_binds_identity_from_the_session() -> None:
    """`D4`: the app path knows exactly who it is, before anyone speaks."""
    scenario = Scenario.load(SCENARIOS_DIR / "pattheera_ipd.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert session.intent_id is not None
    assert session.customer_id == "C000001"
    assert session.has_consent(ConsentScope.HEALTH_DATA)
    assert len(run.transcript) == 3

    identity = session.identity
    assert identity is not None
    assert identity.assurance is AssuranceLevel.L3_VERIFIED
    assert identity.method is IdentityMethod.APP_TOKEN
    assert identity.may_disclose_policy_details

    # Verified, so the policy number IS shown and no verify-first step is prepended.
    assert run.brief is not None
    assert "HL-2024-000811" in (run.brief.summary_th or "")
    assert run.brief.recommended_actions[0].order == 1


async def test_context_is_prefetched_with_provenance() -> None:
    """`D6`/`D18`: assembled before the phone is answered, and every field can say
    where it came from."""
    scenario = Scenario.load(SCENARIOS_DIR / "pattheera_ipd.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    snapshot = run.snapshot
    assert snapshot is not None
    assert snapshot.customer_id == "C000001"
    assert snapshot.payload.relevant_policy is not None
    assert snapshot.payload.relevant_policy.policy_no == "HL-2024-000811"
    assert snapshot.payload.last_contact_at is not None
    assert snapshot.provenance, "a populated snapshot must carry provenance"
    for field in snapshot.provenance:
        assert field.source.startswith("core:")
        assert field.provider
        assert not field.stale
    assert "context_build_ms" in session.stage_timings_ms


async def test_the_menu_routes_the_call_with_no_identity_and_no_ai() -> None:
    """`D37`, end to end and at its hardest: an unrecognised caller who declines the
    recording still reaches the right specialist queue, on keypresses alone."""
    scenario = Scenario.load(SCENARIOS_DIR / "anonymous_declined.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert session.customer_id is None
    assert not session.may_run_intake
    assert run.transcript == []

    assert session.menu_path == ("2", "4")
    assert session.queue_id == "q_health_policy"
    assert run.brief is not None
    assert run.brief.intent is not None
    assert run.brief.intent.intent_code == "health.coverage.query"
    assert run.brief.intent.source == "dtmf"


async def test_a_product_did_raises_the_urgency_floor() -> None:
    """`D19`: dialling the motor-claims number implies something before anyone speaks."""
    scenario = Scenario.load(SCENARIOS_DIR / "roadside_motor_claim.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    await run.execute()

    assert run.brief is not None
    assert run.brief.urgency is Urgency.CRITICAL


async def test_cold_call_resolves_identity_from_ani_only() -> None:
    """`D19`/`D20`: no app, no intent — identity is inferred from the caller's number,
    and inference is explicitly not verification."""
    scenario = Scenario.load(SCENARIOS_DIR / "roadside_motor_claim.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert session.intent_id is None, "a cold call must not fabricate an intent"
    assert session.customer_id == "C000002"  # matched from 0898887777
    assert session.dialled_did == "+6621234111"

    identity = session.identity
    assert identity is not None
    assert identity.assurance is AssuranceLevel.L1_PROBABLE
    assert identity.method is IdentityMethod.ANI
    assert not identity.may_act_on_policy, "inference is not verification (`D20`)"
    assert identity.may_see_record, "but the agent still sees who we think it is (`D74`)"

    # The brief therefore SHOWS the policy - the agent needs it to check what the caller
    # tells them - and withholds permission to use it, plus a verify step at position 0
    # (`D74`). The number being on screen and the number being usable are different
    # things, and only the second one is what L1 restricts.
    assert run.brief is not None
    assert "MT-2025-004512" in (run.brief.summary_th or "")
    assert "ยังไม่ยืนยันตัวตน" in (run.brief.summary_th or "")
    assert run.brief.recommended_actions[0].order == 0
    assert run.brief.degraded is DegradationReason.LOW_ASSURANCE


async def test_the_thinnest_possible_call_still_completes() -> None:
    """The degradation ladder, end to end: no identity, no consent, no transcript.

    If this ever fails, the product has stopped working for the callers who get the
    least from it — which is exactly the wrong direction (`ARCHITECTURE.md` §16).
    """
    scenario = Scenario.load(SCENARIOS_DIR / "anonymous_declined.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert session.customer_id is None
    assert not session.may_run_intake
    assert run.transcript == []
    assert session.state is CallState.CLOSED

    # Nothing was GRANTED - but the refusal itself is on the record (`D88`). "They said
    # no" and "we never asked" are different facts, and a call whose consent list is
    # simply empty cannot tell you which of the two happened.
    assert [(c.scope, c.granted) for c in session.consents] == [(ConsentScope.AI_PROCESSING, False)]
    assert session.consents[0].basis == "ivr_keypress_2"

    # It skipped intake entirely and went straight from the queue to matching.
    visited = [t.to_state for t in session.transitions]
    assert CallState.INTAKE_ACTIVE not in visited
    assert CallState.IN_CALL in visited
