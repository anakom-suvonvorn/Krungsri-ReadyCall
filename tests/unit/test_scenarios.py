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
from readycall.domain.enums import CallState, ConsentScope
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
    assert any("context prefetch" in note for note in run.notes)


async def test_cold_call_resolves_identity_from_ani_only() -> None:
    """`D19`/`D20`: no app, no intent — identity is inferred from the caller's number."""
    scenario = Scenario.load(SCENARIOS_DIR / "roadside_motor_claim.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert session.intent_id is None, "a cold call must not fabricate an intent"
    assert session.customer_id == "C000002"  # matched from 0898887777
    assert session.dialled_did == "+6621234111"
    assert any("probable identity only" in note for note in run.notes)


async def test_the_thinnest_possible_call_still_completes() -> None:
    """The degradation ladder, end to end: no identity, no consent, no transcript.

    If this ever fails, the product has stopped working for the callers who get the
    least from it — which is exactly the wrong direction (`ARCHITECTURE.md` §16).
    """
    scenario = Scenario.load(SCENARIOS_DIR / "anonymous_declined.yaml")
    run = ScenarioRun(scenario, root=REPO_ROOT)
    session = await run.execute()

    assert session.customer_id is None
    assert session.consents == ()
    assert not session.may_run_intake
    assert run.transcript == []
    assert session.state is CallState.CLOSED

    # It skipped intake entirely and went straight from the queue to matching.
    visited = [t.to_state for t in session.transitions]
    assert CallState.INTAKE_ACTIVE not in visited
    assert CallState.IN_CALL in visited
