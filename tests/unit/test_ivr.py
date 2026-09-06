"""The keypad menu, which is the thing that actually routes the call (`D37`).

The bar these tests hold the system to is not "the AI works". It is **parity with an
ordinary call centre**: an unrecognised caller on the general hotline, with no app and no
consent, reaching the right specialist on keypresses alone. Every degradation rung in this
product sits on top of that floor, so if any of this breaks, the floor has moved.

They are written against the machine rather than a phone line because a timeout is then an
ordinary method call. `B7` is the reason: three services that fired *because time passed*
were all written, all correct, and all driven by nothing, and no test noticed.
"""

from __future__ import annotations

from datetime import date

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.adapters.telephony.simulated import SimulatedTelephonyProvider
from readycall.clock import ManualClock
from readycall.domain.enums import CallState, PolicyStatus, ProductLine
from readycall.domain.models import Claim, Customer360, Policy
from readycall.domainpack import DomainPack
from readycall.errors import ConfigError
from readycall.services.call_orchestrator import CallOrchestrator, InMemoryCallSessionRepository
from readycall.services.ivr.machine import IvrMachine, IvrOutcomeKind
from readycall.services.ivr.personalise import (
    PersonalisationInputs,
    promote,
    validate_signals,
)
from readycall.services.ivr.presentation import present
from readycall.services.ivr.service import IvrService, ScriptedChoices
from readycall.voiceprompts import PromptPack, PromptRole
from tests.conftest import REPO_ROOT

CONFIG = REPO_ROOT / "config"


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    return DomainPack.load(CONFIG)


@pytest.fixture(scope="module")
def prompts() -> PromptPack:
    return PromptPack.load(CONFIG / "voice_prompts.yaml")


@pytest.fixture
def machine(pack: DomainPack, prompts: PromptPack) -> IvrMachine:
    return IvrMachine(pack=pack, prompts=prompts)


def _walk(machine: IvrMachine, keys: list[str], **kwargs: object):
    run, step = machine.begin(call_session_id="call_1", **kwargs)  # type: ignore[arg-type]
    for key in keys:
        step = machine.on_digit(run, key)
    return run, step


class TestTheFloor:
    """`D37`'s exit criterion, stated as tests."""

    def test_the_general_hotline_routes_to_a_specialist_on_keypresses_alone(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        """No app, no identity, no consent, no speech. This is the whole argument."""
        did = pack.did("+6621234000")
        _, step = _walk(machine, ["2", "4"], did=did)

        assert step.outcome is not None
        assert step.outcome.kind is IvrOutcomeKind.ROUTED
        assert step.outcome.product_line is ProductLine.HEALTH
        assert step.outcome.intent_code == "health.service.policy"
        assert step.outcome.path == ("2", "4")

    def test_the_recording_notice_comes_before_any_menu(
        self, machine: IvrMachine, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """It has to be first. Not a preference — a legal ordering (`D14`)."""
        _, step = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        played = [line.prompt_id for line in step.lines]
        notice = prompts.id_for(PromptRole.RECORDING_NOTICE)
        assert played.index(notice) < played.index(pack.menus["product_line"].prompt)

    def test_a_product_did_does_not_ask_a_question_it_knows_the_answer_to(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        """`D37`: asking is bad service, not thoroughness. One keypress, not two."""
        did = pack.did("+6621234111")  # motor claims, printed in the car
        _, step = _walk(machine, ["2"], did=did)

        assert step.outcome is not None
        assert step.outcome.intent_code == "motor.roadside_assist"
        assert step.outcome.path == ("2",), "the product menu should have been skipped"

    def test_the_app_path_asks_nothing_at_all(self, machine: IvrMachine) -> None:
        """`D48`: tapping Contact answers both questions before the phone rings."""
        _, step = machine.begin(call_session_id="c", known_intent="health.claim.notify", did=None)
        assert step.outcome is not None
        assert step.outcome.kind is IvrOutcomeKind.SKIPPED
        assert step.outcome.product_line is ProductLine.HEALTH
        assert step.expects_input is False


class TestNeverADeadEnd:
    def test_the_way_out_is_a_spoken_option_not_a_reserved_key(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        """`D86`: there is no operator key. Every menu ends in its own catch-all, which is
        read aloud like any other option — so the escape is part of the menu rather than a
        convention the caller has to already know."""
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        machine.on_digit(run, "5")  # เรื่องอื่นๆ at step 1
        step = machine.on_digit(run, "7")  # เรื่องอื่นๆ at step 2

        assert step.outcome is not None
        assert step.outcome.kind is IvrOutcomeKind.ROUTED
        assert step.outcome.intent_code == "general.other"

    def test_zero_now_repeats_the_menu_instead_of_apologising(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        """`D90`. `D86` removed the operator meaning and admitted one cost: a caller who
        pressed `0` out of habit heard an apology. Moving repeat onto the freed key
        deletes that cost — they now hear the options again, which is the closest thing
        to help this menu can give them."""
        assert pack.menu_settings.repeat_key == "0"
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        step = machine.on_digit(run, "0")

        assert step.expects_input
        assert not run.finished
        assert run.attempts == 0, "repeating is not a mistake"
        assert step.lines[0].prompt_id != pack.menu_settings.invalid_prompt
        assert step.lines[0].prompt_id == pack.menus["product_line"].prompt

    def test_every_reason_menu_still_ends_in_a_catch_all(self, pack: DomainPack) -> None:
        """With the operator key gone this is the ONLY way out of a reason menu, so the
        rule stops being a nicety and becomes load-bearing."""
        for menu_id, menu in pack.menus.items():
            if not menu_id.endswith("_reason"):
                continue
            assert any(
                option.intent and pack.intents[option.intent].is_catch_all
                for option in menu.options
                if option.intent
            ), menu_id

    def test_a_wrong_key_is_never_a_strike(self, machine: IvrMachine, pack: DomainPack) -> None:
        """`D82`. There is no attempt limit. Ending the menu after N mistakes traded the
        good answer we were about to get for a guaranteed mediocre one — and a caller
        pressing buttons is a caller who is present and trying."""
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        for _ in range(12):
            step = machine.on_digit(run, "8")
            assert step.expects_input, "the caller is still being offered the menu"
            assert not run.finished

        # And the right key still works afterwards, with a clean path.
        machine.on_digit(run, "2")
        step = machine.on_digit(run, "4")
        assert step.outcome is not None
        assert step.outcome.kind is IvrOutcomeKind.ROUTED
        assert step.outcome.path == ("2", "4")
        assert step.outcome.pressed.count("8") == 12, "the mistakes are kept as a UX signal"

    def test_a_stuck_sender_is_still_bounded(self, machine: IvrMachine, pack: DomainPack) -> None:
        """The guard is for a MACHINE, not a person: a DTMF sender repeating one digit
        for ever. It sits far past anything a human would do, and even then the caller
        reaches a queue rather than a dial tone."""
        guard = pack.menu_settings.runaway_press_guard
        assert guard >= 20, "a guard a human could hit is an attempt limit by another name"

        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        for _ in range(guard):
            step = machine.on_digit(run, "8")
        assert step.outcome is not None
        assert step.outcome.kind is IvrOutcomeKind.EXHAUSTED
        assert step.outcome.reason.startswith("runaway_input")

    def test_the_apology_names_the_repeat_key(self, prompts: PromptPack, pack: DomainPack) -> None:
        """With no attempt limit, hearing the options again has to be offered — otherwise
        "press again" is the only advice a lost caller ever gets.

        Asserted on the RENDERED line, not the template. The template used to spell the
        digit out (`"กด 9 …"`) while `menus.yaml` owned the real value, so the two could
        disagree silently and the apology could name a key the IVR no longer honoured.
        It is a declared slot now (`D90`), and this is the test that keeps it one.
        """
        spec = prompts.spec(pack.menu_settings.invalid_prompt)
        assert "repeat_key" in spec.slots, "the key must come from menus.yaml, not the text"

        line = prompts.render(
            pack.menu_settings.invalid_prompt, repeat_key=pack.menu_settings.repeat_key
        )
        assert pack.menu_settings.repeat_key in line.text

    def test_a_wrong_press_replays_the_menu_with_an_apology_first(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        step = machine.on_digit(run, "8")
        assert step.expects_input
        assert step.lines[0].prompt_id == pack.menu_settings.invalid_prompt
        assert step.lines[1].prompt_id == pack.menus["product_line"].prompt

    def test_repeating_is_not_a_mistake(self, machine: IvrMachine, pack: DomainPack) -> None:
        """The repeat key must not spend an attempt. Someone who did not catch the options
        the first time is being careful, not failing."""
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        for _ in range(5):
            step = machine.on_digit(run, pack.menu_settings.repeat_key)
            assert step.expects_input
        assert run.attempts == 0
        assert not run.finished

        step = machine.on_digit(run, "2")
        assert run.product_line is ProductLine.HEALTH

    def test_silence_re_prompts_once_then_routes(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        """Only time passes here — the rung `B7` says has to be tested on its own."""
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))

        first = machine.on_timeout(run)
        assert first.expects_input, "a caller who says nothing once deserves a second go"

        second = machine.on_timeout(run)
        assert second.outcome is not None
        assert second.outcome.kind is IvrOutcomeKind.EXHAUSTED
        assert second.outcome.reason == "silence_x2"

    def test_a_press_forgives_an_earlier_silence(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        machine.on_timeout(run)
        machine.on_digit(run, "2")
        assert run.timeouts == 0

    def test_hanging_up_is_recorded_as_abandonment_not_as_a_route(
        self, machine: IvrMachine, pack: DomainPack
    ) -> None:
        run, _ = machine.begin(call_session_id="c", did=pack.did("+6621234000"))
        step = machine.on_hangup(run)
        assert step.outcome is not None
        assert step.outcome.kind is IvrOutcomeKind.ABANDONED


class TestPersonalisation:
    """`D37`'s bonus: cheap, invisible, and it must not disclose anything."""

    @staticmethod
    def _customer(*lines: ProductLine, claim_on: ProductLine | None = None) -> Customer360:
        policies = tuple(
            Policy(
                policy_no=f"P-{line.value}",
                customer_id="C1",
                product_code=line.value.upper(),
                line=line,
                status=PolicyStatus.ACTIVE,
            )
            for line in lines
        )
        claims = (
            (
                Claim(
                    claim_id="C-1",
                    policy_no=f"P-{claim_on.value}",
                    kind="own_damage",
                    status="pending_documents",
                ),
            )
            if claim_on is not None
            else ()
        )
        return Customer360(active_policies=policies, recent_claims=claims)

    def test_an_open_claim_outranks_merely_holding_a_policy(self, pack: DomainPack) -> None:
        """The strongest single predictor of why somebody is calling."""
        assert pack.personalisation is not None
        inputs = PersonalisationInputs.from_snapshot(
            self._customer(ProductLine.HEALTH, ProductLine.MOTOR, claim_on=ProductLine.MOTOR),
            today=date(2026, 8, 25),
        )
        promotions = promote(pack.menus["product_line"], inputs, pack.personalisation)

        assert [p.line for p in promotions] == [ProductLine.MOTOR, ProductLine.HEALTH]
        assert any("open claim" in reason for reason in promotions[0].reasons)

    def test_an_anonymous_caller_hears_the_menu_exactly_as_configured(
        self, pack: DomainPack, prompts: PromptPack
    ) -> None:
        """No snapshot, no promotion, no renumbering — and the built prompt pack is
        therefore complete for the common case."""
        menu = pack.menus["product_line"]
        presentation = present(menu, prompts=prompts, settings=pack.menu_settings)
        assert not presentation.reordered
        assert [p.spoken_key for p in presentation.options] == [o.key for o in menu.options]

    def test_promoted_options_take_the_low_numbers_and_stay_in_order(
        self, pack: DomainPack, prompts: PromptPack
    ) -> None:
        """Reading numbers out of sequence ("กด 3 … กด 1 …") is worse than not
        personalising, so the menu is renumbered rather than merely shuffled (`D81`)."""
        menu = pack.menus["product_line"]
        presentation = present(
            menu, prompts=prompts, settings=pack.menu_settings, promoted=["2", "4"]
        )
        spoken = [p.spoken_key for p in presentation.options]
        assert spoken == ["1", "2", "3", "4", "5"]
        assert presentation.options[0].option.product_line is ProductLine.HEALTH
        assert presentation.options[1].option.product_line is ProductLine.LIFE
        assert presentation.reordered

    def test_the_pressed_key_resolves_back_to_the_canonical_option(
        self, pack: DomainPack, prompts: PromptPack
    ) -> None:
        """The landmine this whole design exists to defuse: with health promoted, `1`
        means health *on this call only*. Every stored path is canonical (`D81`)."""
        presentation = present(
            pack.menus["product_line"], prompts=prompts, settings=pack.menu_settings, promoted=["2"]
        )
        assert presentation.canonical_key("1") == "2"
        assert presentation.resolve("1") is not None
        assert presentation.resolve("1").product_line is ProductLine.HEALTH  # type: ignore[union-attr]

    def test_a_menu_option_never_speaks_customer_detail(
        self, pack: DomainPack, prompts: PromptPack
    ) -> None:
        """`D37`, stated as bytes: the spoken line is the number and the plain label, and
        nothing else. Hearing your own policy number recited by a machine is unsettling
        and makes every option longer."""
        presentation = present(
            pack.menus["product_line"], prompts=prompts, settings=pack.menu_settings, promoted=["2"]
        )
        for presented, line in zip(presentation.options, presentation.lines[1:], strict=False):
            assert line.text == f"กด {presented.spoken_key} {presented.option.label_th}"

    def test_a_reason_menu_is_never_reordered(self, pack: DomainPack) -> None:
        """It is already inside one product line; reordering it would mean guessing the
        reason, which is the AI intake's job, not the keypad's."""
        assert pack.personalisation is not None
        inputs = PersonalisationInputs.from_snapshot(
            self._customer(ProductLine.HEALTH, claim_on=ProductLine.HEALTH),
            today=date(2026, 8, 25),
        )
        assert promote(pack.menus["health_reason"], inputs, pack.personalisation) == ()

    def test_a_signal_nothing_computes_fails_at_startup(self, pack: DomainPack) -> None:
        """Config that claims to do something it does not is worse than config that says
        nothing — the weight looks tuned and changes no behaviour at all."""
        assert pack.personalisation is not None
        broken = type(pack.personalisation)(
            enabled=True,
            max_promoted=2,
            renumber=True,
            weights={"phase_of_the_moon": 1.0},
            open_claim_statuses=frozenset(),
            renewal_window_days=30,
            app_view_window_hours=24,
        )
        with pytest.raises(ConfigError, match="phase_of_the_moon"):
            validate_signals(broken)


class TestTheServiceEndToEnd:
    @pytest.fixture
    def service(self, pack: DomainPack, prompts: PromptPack) -> IvrService:
        clock = ManualClock()
        return IvrService(
            pack=pack,
            prompts=prompts,
            telephony=SimulatedTelephonyProvider(clock=clock),
            orchestrator=CallOrchestrator(
                repository=InMemoryCallSessionRepository(),
                bus=InMemoryEventBus(),
                clock=clock,
            ),
            clock=clock,
        )

    async def test_a_walk_moves_the_call_into_ivr_and_writes_what_it_learned(
        self, service: IvrService, pack: DomainPack, orchestrator: CallOrchestrator
    ) -> None:
        session = await orchestrator.start_cold_call(dialled_did="+6621234000")
        result = await service.run(
            session, caller=ScriptedChoices(["2", "4"]), did=pack.did("+6621234000")
        )

        assert session.state is CallState.IVR
        assert session.menu_path == ("2", "4")
        assert session.menu_intent_code == "health.service.policy"
        assert session.product_line is ProductLine.HEALTH
        assert result.queue_id == "q_service_health"
        assert result.played[0].startswith("greeting.")

    async def test_a_scripted_caller_presses_what_they_meant_not_what_was_configured(
        self, service: IvrService, pack: DomainPack, orchestrator: CallOrchestrator
    ) -> None:
        """A scenario file says which option the caller wanted. With health promoted the
        key under it changes, and the file must keep meaning what it says (`D81`)."""
        session = await orchestrator.start_cold_call(dialled_did="+6621234000")
        caller = ScriptedChoices(["2", "4"])
        # A travel policy with a claim open on it, plus a life policy. Both are promoted,
        # so health - canonically key `2` - is pushed down to the fourth slot.
        inputs = PersonalisationInputs.from_snapshot(
            TestPersonalisation._customer(
                ProductLine.TRAVEL, ProductLine.LIFE, claim_on=ProductLine.TRAVEL
            ),
            today=date(2026, 8, 25),
        )
        result = await service.run(
            session, caller=caller, did=pack.did("+6621234000"), inputs=inputs
        )

        assert result.personalised
        assert result.promoted_because
        assert caller.sent[0] == "4", "health moved down, so the caller pressed 4 to get it"
        assert result.outcome.pressed[0] == "4"
        assert result.outcome.path == ("2", "4"), "the stored path is canonical either way"
        assert result.outcome.intent_code == "health.service.policy"
        assert session.menu_path == ("2", "4")

    async def test_the_catch_all_route_reaches_the_lines_generalist(
        self, service: IvrService, pack: DomainPack, orchestrator: CallOrchestrator
    ) -> None:
        """`D86`: the way out of a menu is its own catch-all, and it keeps the product
        line — which is the whole reason the operator key was not worth keeping."""
        session = await orchestrator.start_cold_call(dialled_did="+6621234222")
        did = pack.did("+6621234222")
        result = await service.run(session, caller=ScriptedChoices(["6"]), did=did)

        assert result.outcome.kind is IvrOutcomeKind.ROUTED
        assert result.outcome.intent_code == "health.other"
        assert result.queue_id == pack.queue_for_intent("health.other")

    async def test_a_caller_who_says_nothing_still_reaches_their_lines_queue(
        self, service: IvrService, pack: DomainPack, orchestrator: CallOrchestrator
    ) -> None:
        """`D40`: knowing only the product line must still beat the general queue."""
        session = await orchestrator.start_cold_call(dialled_did="+6621234222")
        result = await service.run(session, caller=ScriptedChoices([]), did=pack.did("+6621234222"))

        assert result.outcome.kind is IvrOutcomeKind.EXHAUSTED
        assert result.outcome.product_line is ProductLine.HEALTH
        assert result.queue_id == pack.queue_for_intent("health.other")
        assert result.queue_id != "q_service"

    async def test_a_did_may_assume_an_intent_but_never_claims_it_was_pressed(
        self, service: IvrService, pack: DomainPack, orchestrator: CallOrchestrator
    ) -> None:
        """Someone dialling the number in their glovebox is probably beside a damaged
        car (`D19`) — real evidence, weaker than a keypress. So the queue uses it and the
        record still says nobody pressed anything."""
        session = await orchestrator.start_cold_call(dialled_did="+6621234111")
        result = await service.run(session, caller=ScriptedChoices([]), did=pack.did("+6621234111"))

        assert result.queue_id == "q_claims"
        assert result.intent_code == "motor.claim.notify"
        assert result.intent_source == "did"
        assert result.outcome.intent_code is None, "nothing was pressed, so nothing is claimed"
        assert result.outcome.path == ()
        assert session.menu_intent_code is None

    async def test_every_line_played_is_a_clip_that_the_build_produced(
        self, service: IvrService, pack: DomainPack, orchestrator: CallOrchestrator, prompts
    ) -> None:
        """The join between the two halves of `D24`: if the IVR asks for a line the build
        never rendered, the caller hears silence."""
        session = await orchestrator.start_cold_call(dialled_did="+6621234000")
        result = await service.run(
            session, caller=ScriptedChoices(["2", "4"]), did=pack.did("+6621234000")
        )
        for prompt_id in result.played:
            assert prompt_id in prompts.prompts
