"""The hold: the queue position, the intake offer, and the recording that outlives it.

Everything here sits **below** the line `ARCHITECTURE.md` §6 draws across the flow — the
queue is already decided, so nothing in this file can change where the call goes. That is
the property most worth protecting, and the first test asserts it directly: a caller who
refuses the offer outright reaches the same agent with the same queue as one who talks for
three minutes. If that ever stops being true, the AI has started making routing decisions
and `D37` has quietly been reversed.

Written against the machine rather than a phone line, for `B7`'s reason: a silence, the
re-offer at ninety seconds, and the maximum recording length all fire **because time
passed**, so each has to be an ordinary method call that a test can make.
"""

from __future__ import annotations

import pytest

from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.adapters.telephony.simulated import SimulatedTelephonyProvider
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.domain.enums import (
    CallState,
    ConsentScope,
    DegradationReason,
    FinalizeReason,
    IntakeStrategyKind,
    SpeakerRole,
)
from readycall.domain.events import Event, TranscriptTurnAdded
from readycall.domain.models import TranscriptTurn
from readycall.domainpack import DomainPack
from readycall.ports.event_bus import EventBus
from readycall.services.call_orchestrator import CallOrchestrator, InMemoryCallSessionRepository
from readycall.services.intake.hold import HoldMachine, HoldOutcomeKind, HoldPhase
from readycall.services.intake.passive import PassiveRecordIntake
from readycall.services.intake.service import IntakeService
from readycall.services.intake.strategy import IntakeStrategy
from readycall.services.ivr.service import ScriptedChoices
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
def machine(pack: DomainPack, prompts: PromptPack) -> HoldMachine:
    return HoldMachine(prompts=prompts, settings=pack.menu_settings)


def _played(*steps: object) -> list[str]:
    out: list[str] = []
    for step in steps:
        out.extend(line.prompt_id for line in step.lines)  # type: ignore[attr-defined]
    return out


# --- what the caller hears -------------------------------------------------------------


class TestWhatIsSaid:
    def test_no_queue_position_is_ever_spoken(
        self, machine: HoldMachine, prompts: PromptPack
    ) -> None:
        """`D91`, reversing `D89`. There is no line to have a position in: the matcher
        scores every waiting caller against every free agent as `fit x urgency` and
        re-solves the whole matrix each tick, so arrival order is not an input anywhere.

        A number would be a promise the system does not keep — and it would be broken
        most often for the low-urgency callers most likely to have believed it. Asserted
        on the prompt pack as well as the machine, so re-adding the line cannot be done
        by config alone.
        """
        _, step = machine.begin(call_session_id="call_1")
        assert _played(step) == ["queue.hold", "intake.offer"]

        assert "queue.position" not in prompts.prompts
        assert "queue.position_only" not in prompts.prompts
        assert not any("position" in role.value for role in PromptRole)

    def test_the_offer_names_the_keys_the_machine_actually_honours(
        self, prompts: PromptPack
    ) -> None:
        """`D90`'s lesson, applied before it bites here.

        `menu.invalid` spelled "กด 9" into its Thai while `menus.yaml` owned the real
        value, so the two could drift and nothing would fail. The offer's keys live in
        `HoldMachine` rather than in config, but the prompt still spells them out — so
        this is the test that stops the Thai and the code disagreeing about which button
        records.
        """
        for role in (PromptRole.INTAKE_OFFER, PromptRole.INTAKE_REOFFER):
            text = prompts.say(role).text
            assert f"กด {HoldMachine.RECORD_KEY} " in text, role
            assert f"กด {HoldMachine.HOLD_KEY} " in text, role

    def test_the_reoffer_uses_the_shorter_wording(self, machine: HoldMachine) -> None:
        """They have heard the pitch. Repeating it verbatim is nagging with extra words."""
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "2")
        assert _played(machine.reoffer(run)) == ["intake.reoffer"]


# --- the two answers, and the ones that are not answers ---------------------------------


class TestAnsweringTheOffer:
    def test_pressing_one_starts_a_recording(self, machine: HoldMachine) -> None:
        run, _ = machine.begin(call_session_id="call_1")
        step = machine.on_digit(run, "1")

        assert run.phase is HoldPhase.RECORDING
        assert run.consented is True
        assert step.starts_recording
        assert _played(step) == ["intake.start"]

    def test_pressing_two_is_a_first_class_outcome(self, machine: HoldMachine) -> None:
        """`D19`/`D37`: the menu already routed the call, so declining costs nothing that
        matters. The acknowledgement must not sound like a lost opportunity."""
        run, _ = machine.begin(call_session_id="call_1")
        step = machine.on_digit(run, "2")

        assert run.phase is HoldPhase.HOLDING
        assert run.consented is False
        assert _played(step) == ["intake.declined"]
        assert not step.finished  # they are still waiting for an agent

    def test_silence_at_the_offer_closes_it_without_nagging(self, machine: HoldMachine) -> None:
        """Unlike the menu, silence here gets no re-prompt: the second chance already
        exists and is better placed, at `INTAKE_REOFFER_AFTER_S`."""
        run, _ = machine.begin(call_session_id="call_1")
        step = machine.on_timeout(run)

        assert run.phase is HoldPhase.HOLDING
        assert run.consented is None  # never asked and answered — not the same as "no"
        assert _played(step) == ["queue.hold"]

    def test_a_wrong_key_replays_the_offer_and_is_never_a_strike(
        self, machine: HoldMachine
    ) -> None:
        """`D82`, applied here for the same reason: a wrong key proves somebody is there.
        What bounds the loop is silence, not a count."""
        run, _ = machine.begin(call_session_id="call_1")
        for _ in range(12):
            step = machine.on_digit(run, "7")
            assert _played(step) == ["menu.invalid", "intake.offer"]
        assert run.phase is HoldPhase.OFFERING
        assert not run.finished

        assert machine.on_digit(run, "1").starts_recording

    def test_the_repeat_key_means_the_same_thing_it_means_everywhere_else(
        self, machine: HoldMachine, pack: DomainPack
    ) -> None:
        """A caller who learned the repeat key in the menu must not find it means
        something else thirty seconds later. It moved to `0` in `D90` and it moved in
        both machines at once, because both read it from `menus.yaml`."""
        run, _ = machine.begin(call_session_id="call_1")
        step = machine.on_digit(run, pack.menu_settings.repeat_key)
        assert _played(step) == ["intake.offer"]
        assert run.phase is HoldPhase.OFFERING

    def test_a_runaway_sender_is_dropped_to_hold_rather_than_answered_forever(
        self, machine: HoldMachine, pack: DomainPack
    ) -> None:
        """The guard is for stuck hardware, not for a person — `runaway_press_guard` sits
        far past anything a human does, and reaching it just stops replying."""
        run, _ = machine.begin(call_session_id="call_1")
        for _ in range(pack.menu_settings.runaway_press_guard):
            step = machine.on_digit(run, "7")
        assert run.phase is HoldPhase.HOLDING
        assert step.lines == ()

    def test_keys_do_nothing_while_holding(self, machine: HoldMachine) -> None:
        """Nobody asked a question, so nothing answers. An apology here would be replying
        to something the caller never said."""
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "2")
        step = machine.on_digit(run, "5")
        assert step.lines == ()
        assert run.phase is HoldPhase.HOLDING


# --- while they are talking --------------------------------------------------------------


class TestTheRecording:
    def _recording(self, machine: HoldMachine):  # type: ignore[no-untyped-def]
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "1")
        return run

    def test_pressing_one_again_finishes_it(self, machine: HoldMachine) -> None:
        run = self._recording(machine)
        step = machine.on_digit(run, "1")

        assert step.ends_recording
        assert run.finalize_reason is FinalizeReason.CUSTOMER_DONE
        assert run.phase is HoldPhase.HOLDING
        assert _played(step) == ["intake.done"]

    def test_any_other_key_is_ignored_mid_sentence(self, machine: HoldMachine) -> None:
        """Interrupting a recording to apologise for a mis-hit is worse than the mis-hit."""
        run = self._recording(machine)
        step = machine.on_digit(run, "4")
        assert step.lines == ()
        assert run.phase is HoldPhase.RECORDING

    def test_one_silence_re_prompts_and_the_second_gives_up(
        self, machine: HoldMachine, pack: DomainPack
    ) -> None:
        """They pressed 1, so they *want* to be heard — one nudge is warranted here even
        though it was not at the offer."""
        assert pack.menu_settings.max_silences == 2
        run = self._recording(machine)

        first = machine.on_timeout(run)
        assert _played(first) == ["intake.start"]
        assert run.phase is HoldPhase.RECORDING

        second = machine.on_timeout(run)
        assert second.ends_recording
        assert run.finalize_reason is FinalizeReason.SILENCE_TIMEOUT

    def test_the_maximum_duration_stops_it_and_thanks_them(self, machine: HoldMachine) -> None:
        run = self._recording(machine)
        step = machine.on_max_duration(run)
        assert step.ends_recording
        assert run.finalize_reason is FinalizeReason.MAX_DURATION
        assert _played(step) == ["intake.done"]


# --- the endings ---------------------------------------------------------------------------


class TestEndings:
    def test_an_agent_accepting_mid_sentence_is_a_partial_not_a_loss(
        self, machine: HoldMachine
    ) -> None:
        """`D21`: the offer window IS the grace period. Nobody waited longer and no
        sentence was thrown away — but the brief has to know it was cut."""
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "1")
        step = machine.on_agent_accepted(run)

        assert step.outcome is not None
        assert step.outcome.kind is HoldOutcomeKind.CUT_SHORT
        assert step.outcome.is_partial
        assert step.outcome.finalize_reason is FinalizeReason.OFFER_ACCEPTED

    def test_an_agent_accepting_after_a_finished_recording_is_not_partial(
        self, machine: HoldMachine
    ) -> None:
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "1")
        machine.on_digit(run, "1")
        step = machine.on_agent_accepted(run)

        assert step.outcome is not None
        assert step.outcome.kind is HoldOutcomeKind.RECORDED
        assert not step.outcome.is_partial

    def test_a_decliner_ends_as_declined_and_an_ignorer_as_ignored(
        self, machine: HoldMachine
    ) -> None:
        """Two different facts. "They said no" is evidence; "they never answered" is
        our silence, not theirs, and only one of them justifies a thin brief."""
        declined, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(declined, "2")
        out = machine.on_agent_accepted(declined).outcome
        assert out is not None and out.kind is HoldOutcomeKind.DECLINED

        ignored, _ = machine.begin(call_session_id="call_2")
        machine.on_timeout(ignored)
        out2 = machine.on_agent_accepted(ignored).outcome
        assert out2 is not None and out2.kind is HoldOutcomeKind.IGNORED
        assert out2.finalize_reason is FinalizeReason.NO_CONSENT

    def test_hanging_up_mid_sentence_still_keeps_what_was_said(self, machine: HoldMachine) -> None:
        """The voicemail path (`D25`) runs the same pipeline, so a caller who gave up
        halfway still has something worth calling them back about."""
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "1")
        step = machine.on_hangup(run)

        assert step.outcome is not None
        assert step.outcome.kind is HoldOutcomeKind.ABANDONED
        assert step.outcome.spoke
        assert step.outcome.is_partial
        assert step.ends_recording


# --- the re-offer, which fires because time passed ------------------------------------------


class TestTheReoffer:
    def test_it_is_offered_exactly_once_more(self, machine: HoldMachine) -> None:
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "2")

        assert machine.may_reoffer(run)
        machine.reoffer(run)
        machine.on_digit(run, "2")
        assert not machine.may_reoffer(run), "twice is an offer, three times is nagging"

    def test_somebody_already_recording_is_never_interrupted(self, machine: HoldMachine) -> None:
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "1")
        assert not machine.may_reoffer(run)

    def test_somebody_who_already_recorded_has_nothing_left_to_be_offered(
        self, machine: HoldMachine
    ) -> None:
        run, _ = machine.begin(call_session_id="call_1")
        machine.on_digit(run, "1")
        machine.on_digit(run, "1")
        assert run.phase is HoldPhase.HOLDING
        assert not machine.may_reoffer(run)


# --- the strategy seam ----------------------------------------------------------------------


class TestTheStrategySeam:
    def test_the_passive_strategy_satisfies_the_protocol(self) -> None:
        """`D10`'s whole point: downstream must not be able to tell which one ran."""
        strategy = PassiveRecordIntake(clock=ManualClock(), bus=InMemoryEventBus())
        assert isinstance(strategy, IntakeStrategy)
        assert strategy.kind is IntakeStrategyKind.PASSIVE

    async def test_turns_are_published_as_they_arrive_not_at_the_end(self) -> None:
        """A dropped call must still leave the turns that landed before it dropped."""
        bus = InMemoryEventBus()
        seen: list[str] = []

        async def remember(event: Event) -> None:
            assert isinstance(event, TranscriptTurnAdded)
            seen.append(event.text)

        bus.subscribe("transcript.turn", remember)

        session = await _session()
        strategy = PassiveRecordIntake(clock=ManualClock(), bus=bus)
        await strategy.start(session)
        await strategy.on_turn(_turn(session.call_session_id, 1, "ผมโดนชนครับ"))
        await bus.drain()

        assert seen == ["ผมโดนชนครับ"], "the turn must be out before finalize() is called"

    async def test_finalize_is_idempotent(self) -> None:
        """The agent accepting and the caller hanging up genuinely race — `D21` puts them
        a second apart by design — and both paths call this."""
        bus = InMemoryEventBus()
        finals: list[Event] = []

        async def remember(event: Event) -> None:
            finals.append(event)

        bus.subscribe("intake.finalized", remember)

        session = await _session()
        strategy = PassiveRecordIntake(clock=ManualClock(), bus=bus)
        await strategy.start(session)
        first = await strategy.finalize(FinalizeReason.OFFER_ACCEPTED)
        second = await strategy.finalize(FinalizeReason.CALL_ENDED)
        await bus.drain()

        assert first is second
        assert first.finalize_reason is FinalizeReason.OFFER_ACCEPTED
        assert len(finals) == 1

    async def test_a_turn_arriving_after_the_close_is_dropped_not_crashed(self) -> None:
        """The transcriber was mid-utterance when the agent accepted. That belongs to the
        live call now, and it is not an error."""
        session = await _session()
        strategy = PassiveRecordIntake(clock=ManualClock(), bus=InMemoryEventBus())
        await strategy.start(session)
        await strategy.finalize(FinalizeReason.OFFER_ACCEPTED)
        await strategy.on_turn(_turn(session.call_session_id, 1, "ครับ"))
        assert strategy.turns == ()

    async def test_the_strategy_never_invents_a_degradation_reason(self) -> None:
        """No turns can mean the caller said nothing OR that STT was down, and only the
        driver knows which. A strategy guessing `stt_unavailable` puts a claim nothing
        has checked onto the agent's screen."""
        session = await _session()
        strategy = PassiveRecordIntake(clock=ManualClock(), bus=InMemoryEventBus())
        await strategy.start(session)
        result = await strategy.finalize(FinalizeReason.SILENCE_TIMEOUT)
        assert result.turns == ()
        assert result.degraded is DegradationReason.NONE


# --- the service, end to end -----------------------------------------------------------------


class TestTheServiceEndToEnd:
    @pytest.fixture
    def bus(self) -> InMemoryEventBus:
        return InMemoryEventBus()

    @pytest.fixture
    def clock(self) -> ManualClock:
        return ManualClock()

    @pytest.fixture
    def orchestrator(self, bus: EventBus, clock: ManualClock) -> CallOrchestrator:
        return CallOrchestrator(repository=InMemoryCallSessionRepository(), bus=bus, clock=clock)

    @pytest.fixture
    def service(
        self,
        pack: DomainPack,
        prompts: PromptPack,
        bus: InMemoryEventBus,
        clock: ManualClock,
        orchestrator: CallOrchestrator,
    ) -> IntakeService:
        return IntakeService(
            pack=pack,
            prompts=prompts,
            telephony=SimulatedTelephonyProvider(clock=clock),
            orchestrator=orchestrator,
            clock=clock,
            bus=bus,
            settings=Settings(config_dir=CONFIG),
        )

    async def _queued(self, orchestrator: CallOrchestrator):  # type: ignore[no-untyped-def]
        session = await orchestrator.start_cold_call(dialled_did="+6621234000")
        await orchestrator.enter_ivr(session)
        return await orchestrator.enqueue(session, queue_id="q_health_policy")

    async def test_pressing_one_grants_both_scopes_an_intake_needs(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """One unambiguous act, in reply to a line that says exactly what will happen.
        Asking twice for one thing they already agreed to is worse service, not better
        privacy (`D14`)."""
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))

        assert session.has_consent(ConsentScope.RECORDING)
        assert session.has_consent(ConsentScope.AI_PROCESSING)
        assert session.may_run_intake
        assert session.state is CallState.INTAKE_ACTIVE
        assert [c.basis for c in session.consents] == ["ivr_keypress_1"] * 2

    async def test_declining_records_the_refusal_rather_than_nothing(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """ "They said no" and "we never asked" are different facts and the record has to
        be able to tell them apart."""
        session = await self._queued(orchestrator)
        report = await service.run_offer(session, caller=ScriptedChoices(["2"]))

        refusals = [c for c in session.consents if not c.granted]
        assert [c.scope for c in refusals] == [ConsentScope.AI_PROCESSING]
        assert refusals[0].basis == "ivr_keypress_2"
        assert not session.may_run_intake
        assert report.degraded is DegradationReason.INTAKE_DECLINED
        assert session.state is CallState.QUEUED, "declining changes nothing about routing"

    async def test_ignoring_the_offer_is_reported_differently_from_declining(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        session = await self._queued(orchestrator)
        report = await service.run_offer(session, caller=ScriptedChoices([]))

        assert report.consented is None
        assert report.degraded is DegradationReason.NO_CONSENT
        assert session.consents == ()

    async def test_an_empty_transcript_after_an_engine_failure_says_so(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """`D111`. The screen has had a sentence for this since `D106` and nothing could
        reach it: `_degradation()` returned `NONE` unconditionally, so a caller whose
        model was down looked exactly like a caller who said nothing.
        """
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))
        await service.on_transcription_lost(session.call_session_id, 3)

        report = await service.on_agent_accepted(session.call_session_id)

        assert report is not None and report.result is not None
        assert report.result.turns == ()
        assert report.result.degraded is DegradationReason.STT_UNAVAILABLE

    async def test_a_caller_who_simply_said_nothing_is_not_blamed_on_the_engine(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """The half that stops the claim over-reporting, and it is the common case.

        Most callers who take the recording and then wait quietly produce no turns. If
        that read as `stt_unavailable` the agent would be told the system failed on every
        one of them.
        """
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))

        report = await service.on_agent_accepted(session.call_session_id)

        assert report is not None and report.result is not None
        assert report.result.turns == ()
        assert report.result.degraded is DegradationReason.NONE

    async def test_a_partly_lost_transcript_does_not_claim_the_engine_was_unavailable(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """A bigger claim than the evidence supports (`D111`).

        Five sentences arrived and one did not: the brief is thinner, and saying "the
        transcription system was unavailable on this call" about it would be false. It is
        logged loudly instead. If that case ever needs to reach the screen it wants its
        own `DegradationReason`, not this one stretched.
        """
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))
        await service.on_turn(
            session.call_session_id,
            _turn(session.call_session_id, 1, "รถผมชนที่พระราม 9 ครับ"),
        )
        await service.on_transcription_lost(session.call_session_id, 1)

        report = await service.on_agent_accepted(session.call_session_id)

        assert report is not None and report.result is not None
        assert len(report.result.turns) == 1
        assert report.result.degraded is DegradationReason.NONE

    async def test_a_loss_reported_for_an_unknown_call_is_ignored(
        self, service: IntakeService
    ) -> None:
        """The transcriber and the intake close on different requests and genuinely race
        (`D21`). A late report must not raise inside a live call (`D12`)."""
        await service.on_transcription_lost("no_such_call", 2)

    async def test_the_recording_outlives_the_call_that_started_it(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """`D21` in one assertion: `run_offer` returns while the caller is still talking,
        because the thing that ends them is an agent pressing Accept somewhere else."""
        session = await self._queued(orchestrator)
        report = await service.run_offer(session, caller=ScriptedChoices(["1"]))

        assert report.recording
        assert report.result is None
        assert session.call_session_id in service.live_call_ids()

    async def test_accepting_finalises_the_live_intake_as_partial(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))
        await service.on_turn(
            session.call_session_id, _turn(session.call_session_id, 1, "ผมนอนโรงพยาบาลครับ")
        )

        report = await service.on_agent_accepted(session.call_session_id)

        assert report is not None
        assert report.kind is HoldOutcomeKind.CUT_SHORT
        assert report.result is not None
        assert report.result.is_partial
        assert report.result.transcript_text == "ผมนอนโรงพยาบาลครับ"
        assert session.call_session_id not in service.live_call_ids()

    async def test_a_finished_recording_moves_the_call_to_intake_complete(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """Pressing 1 again arrives through `on_digit`, not through the offer loop: by
        then the offer is not the question being asked any more, and the thing listening
        is the open channel."""
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))
        report = await service.on_digit(session.call_session_id, "1")

        assert report is not None
        assert session.state is CallState.INTAKE_COMPLETE
        assert report.result is None, "still holding - the intake is done, the call is not"
        assert session.call_session_id in service.live_call_ids()

        final = await service.on_agent_accepted(session.call_session_id)
        assert final is not None and final.result is not None
        assert not final.result.is_partial
        assert final.result.finalize_reason is FinalizeReason.CUSTOMER_DONE

    async def test_silence_during_a_recording_re_prompts_once_then_closes_it(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """Driven from outside for `B7`'s reason: a VAD silence is something the media
        side reports, so it is an entry point, not a loop this file owns."""
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))

        await service.on_silence(session.call_session_id)
        assert session.state is CallState.INTAKE_ACTIVE, "one nudge, they did ask to speak"

        await service.on_silence(session.call_session_id)
        assert session.state is CallState.INTAKE_COMPLETE

    async def test_the_maximum_duration_closes_the_recording(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["1"]))
        await service.on_max_duration(session.call_session_id)
        assert session.state is CallState.INTAKE_COMPLETE

    async def test_accepting_a_call_that_was_never_held_is_a_no_op(
        self, service: IntakeService
    ) -> None:
        """An app caller skips all of this (`D48`), and the accept endpoint calls this on
        every accept. It must not care."""
        assert await service.on_agent_accepted("call_that_never_held") is None

    async def test_the_reoffer_fires_because_time_passed_and_nothing_else(
        self, service: IntakeService, orchestrator: CallOrchestrator, clock: ManualClock
    ) -> None:
        """`B7`'s lesson, applied before it can bite again: the only thing that happens at
        `INTAKE_REOFFER_AFTER_S` is that the wait got long, so a driver has to exist and a
        test has to move nothing but the clock."""
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["2"]))

        assert await service.reoffer_due() == [], "not due yet"

        clock.advance(Settings(config_dir=CONFIG).intake_reoffer_after_s + 1)
        assert await service.reoffer_due() == [session.call_session_id]

        # And exactly once: the second sweep must not ask again.
        clock.advance(600)
        assert await service.reoffer_due() == []

    async def test_a_reoffer_can_be_accepted_and_starts_a_real_intake(
        self, service: IntakeService, orchestrator: CallOrchestrator, clock: ManualClock
    ) -> None:
        """The whole point of asking twice: a caller who said no at ten seconds may well
        say yes at two minutes."""
        session = await self._queued(orchestrator)
        await service.run_offer(session, caller=ScriptedChoices(["2"]))
        clock.advance(Settings(config_dir=CONFIG).intake_reoffer_after_s + 1)
        await service.reoffer_due()

        report = await service.answer_reoffer(
            session.call_session_id, caller=ScriptedChoices(["1"])
        )

        assert report.recording
        assert report.offers_made == 2
        assert session.has_consent(ConsentScope.AI_PROCESSING)
        assert session.state is CallState.INTAKE_ACTIVE

    async def test_every_answer_leaves_the_queue_exactly_where_the_menu_put_it(
        self, service: IntakeService, orchestrator: CallOrchestrator
    ) -> None:
        """The load-bearing one. Routing is settled before any of this runs (`D37`), so
        recording, declining and ignoring must be indistinguishable from the queue's point
        of view. If this ever fails, the AI has started deciding where calls go."""
        queues = []
        for keys in (["1"], ["2"], []):
            session = await self._queued(orchestrator)
            await service.run_offer(session, caller=ScriptedChoices(keys))
            await service.on_agent_accepted(session.call_session_id)
            queues.append(session.queue_id)
        assert queues == ["q_health_policy"] * 3


# --- helpers --------------------------------------------------------------------------------


async def _session():  # type: ignore[no-untyped-def]
    orchestrator = CallOrchestrator(
        repository=InMemoryCallSessionRepository(), bus=InMemoryEventBus(), clock=ManualClock()
    )
    return await orchestrator.start_cold_call(dialled_did="+6621234000")


def _turn(call_session_id: str, seq: int, text: str) -> TranscriptTurn:
    return TranscriptTurn(
        turn_id=f"turn_{seq}",
        call_session_id=call_session_id,
        seq=seq,
        speaker_role=SpeakerRole.CUSTOMER,
        text=text,
        t_start_ms=0,
        t_end_ms=1200,
        asr_confidence=0.91,
    )
