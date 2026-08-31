"""Driving the hold: play the offer, honour the answer, and hold the live intake open.

The split mirrors `services/ivr/`: `hold.py` holds every rule and touches nothing, this
file knows how to talk to a telephony provider and how to wait. Swapping the simulated
provider for Asterisk at P5 therefore changes nothing about how the offer behaves (`D3`).

**Why an intake outlives this function.** `D21` makes the offer window the intake's grace
period: the caller keeps talking until an agent presses *Accept*, which happens somewhere
else entirely, seconds later, on a different connection. So `run_offer` returns as soon as
the caller has answered — and a call that is still recording stays in `_live`, waiting for
`on_agent_accepted`. A design that ran the intake to completion inside one call would have
to either cut the caller off early or hold the agent back, and `D12` forbids the second.

**Consent is a keypress, and the refusal is kept too** (`D14`). Pressing 1 grants the two
scopes an intake needs, in one unambiguous act, in response to a line that says exactly
what will happen. Pressing 2 writes a `granted=False` row rather than nothing: "they said
no" and "we never asked" are different facts, and only one of them is a reason to re-offer
with a clear conscience.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from readycall.clock import Clock
from readycall.config import Settings
from readycall.domain.enums import CallState, ConsentScope, DegradationReason, FinalizeReason
from readycall.domain.models import CallSession, IntakeResult, TranscriptTurn
from readycall.domainpack import DomainPack
from readycall.logging import get_logger
from readycall.ports.event_bus import EventBus
from readycall.ports.telephony import TelephonyProvider
from readycall.services.call_orchestrator.orchestrator import CallOrchestrator
from readycall.services.intake.hold import (
    HoldMachine,
    HoldOutcomeKind,
    HoldPhase,
    HoldRun,
    HoldStep,
)
from readycall.services.intake.passive import PassiveRecordIntake
from readycall.services.ivr.service import CallerInput
from readycall.voiceprompts import PromptPack

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HoldReport:
    """What the hold produced, for the brief and for the screen.

    `degraded` is the honest half: an agent looking at a thin brief is owed the reason,
    and "they declined" reads very differently from "our transcriber was down" (`D14`).
    """

    call_session_id: str
    kind: HoldOutcomeKind | None
    recording: bool
    consented: bool | None
    offers_made: int
    played: tuple[str, ...]
    pressed: tuple[str, ...]
    intake_id: str | None = None
    result: IntakeResult | None = None
    degraded: DegradationReason = DegradationReason.NONE


@dataclass
class _LiveHold:
    """One call that is on hold — still recording, or still re-offerable."""

    session: CallSession
    run: HoldRun
    strategy: PassiveRecordIntake
    played: list[str] = field(default_factory=list)
    #: `monotonic_ms` when the caller was queued, so the re-offer fires on elapsed wait
    #: rather than on wall-clock arithmetic across a clock correction (`B8`).
    queued_at_ms: float = 0.0
    reoffered: bool = False


class IntakeService:
    def __init__(
        self,
        *,
        pack: DomainPack,
        prompts: PromptPack,
        telephony: TelephonyProvider,
        orchestrator: CallOrchestrator,
        clock: Clock,
        bus: EventBus,
        settings: Settings,
        engine_name: str = "null",
    ) -> None:
        self._pack = pack
        self._prompts = prompts
        self._telephony = telephony
        self._orchestrator = orchestrator
        self._clock = clock
        self._bus = bus
        self._settings = settings
        self._engine = engine_name
        self._machine = HoldMachine(prompts=prompts, settings=pack.menu_settings)
        self._live: dict[str, _LiveHold] = {}

    # --- the walk ---------------------------------------------------------------------

    async def run_offer(
        self,
        session: CallSession,
        *,
        caller: CallerInput,
        position: int | None = None,
        wait_minutes: int | None = None,
        max_steps: int = 200,
    ) -> HoldReport:
        """Announce the wait, make the offer, and return the moment it is answered.

        **It stops at the answer, deliberately.** A caller who pressed 1 is now talking,
        and what ends that is a silence the media side detects, the maximum duration, or
        an agent pressing Accept - none of which this function can see, and the last of
        which is a different request entirely (`D21`). Looping on would mean owning a
        media loop that does not exist yet and inventing what it reports.
        """
        run, step = self._machine.begin(
            call_session_id=session.call_session_id,
            position=position,
            wait_minutes=wait_minutes,
        )
        live = _LiveHold(
            session=session,
            run=run,
            strategy=PassiveRecordIntake(clock=self._clock, bus=self._bus),
            queued_at_ms=self._clock.monotonic_ms(),
        )
        self._live[session.call_session_id] = live
        await self._apply(live, step)
        return await self._collect(live, step, caller=caller, max_steps=max_steps)

    async def _collect(
        self,
        live: _LiveHold,
        step: HoldStep,
        *,
        caller: CallerInput,
        max_steps: int = 200,
    ) -> HoldReport:
        """Take keypresses for as long as the offer is the question being asked."""
        run = live.run
        steps = 0
        while step.expects_input and not run.finished and run.phase is HoldPhase.OFFERING:
            steps += 1
            if steps > max_steps:  # pragma: no cover - a broken input source, not a caller
                step = self._machine.on_timeout(run)
                await self._apply(live, step)
                continue
            press = await caller.next_press(None, timeout_s=self._pack.menu_settings.timeout_s)
            if press.hung_up:
                step = self._machine.on_hangup(run)
            elif press.timed_out:
                step = self._machine.on_timeout(run)
            else:
                assert press.digit is not None
                step = self._machine.on_digit(run, press.digit)
            await self._apply(live, step)

        return await self._report(live, step)

    async def _apply(self, live: _LiveHold, step: HoldStep) -> None:
        """Play what the step says, and open or close the intake if it asked."""
        if step.starts_recording:
            await self._grant_consent(live.session)
            await self._orchestrator.transition(
                live.session, CallState.INTAKE_ACTIVE, reason="intake_offer_accepted"
            )
            await live.strategy.start(live.session)
        for line in step.lines:
            live.played.append(line.prompt_id)
            if live.session.telephony_call_id is None:
                # No channel (a scenario, or the demo endpoint): the walk is still real,
                # only the audio is absent. Recording what *would* have played keeps the
                # spoken half visible rather than silently skipped.
                continue
            await self._telephony.play(live.session.telephony_call_id, line.audio_ref(self._engine))
        if step.ends_recording and live.strategy.running:
            await self._close(live, live.run.finalize_reason or FinalizeReason.CUSTOMER_DONE)

    async def _grant_consent(self, session: CallSession) -> None:
        """One keypress, both scopes an intake needs (`D14`).

        Splitting them would mean asking twice for one thing the caller already agreed to
        in a single sentence — the prompt says the recording is made *and* that the agent
        will see what it produced.
        """
        for scope in (ConsentScope.RECORDING, ConsentScope.AI_PROCESSING):
            await self._orchestrator.record_consent(
                session, scope=scope, granted=True, basis="ivr_keypress_1", channel="ivr"
            )

    async def _decline_consent(self, session: CallSession) -> None:
        """An explicit no is evidence, and it is not the same as never having asked."""
        await self._orchestrator.record_consent(
            session,
            scope=ConsentScope.AI_PROCESSING,
            granted=False,
            basis="ivr_keypress_2",
            channel="ivr",
        )

    async def _close(self, live: _LiveHold, reason: FinalizeReason) -> IntakeResult:
        """Finalise the intake and move the call on. Safe to call twice (`D21`'s race)."""
        result = await live.strategy.finalize(reason, degraded=self._degradation(live))
        if live.session.state is CallState.INTAKE_ACTIVE:
            await self._orchestrator.transition(
                live.session, CallState.INTAKE_COMPLETE, reason=f"intake_{reason}"
            )
        return result

    def _degradation(self, live: _LiveHold) -> DegradationReason:
        """Why the brief will be thinner than it could have been, if it will be.

        Deliberately not decided inside the strategy: an intake with no turns can mean
        the caller said nothing or that the transcriber was down, and only a layer that
        can see the transcriber knows which. That layer is the next slice, so this
        returns `NONE` today rather than guessing `stt_unavailable` and putting a claim
        on the agent's screen that nothing has checked.
        """
        _ = live
        return DegradationReason.NONE

    # --- the endings that arrive from somewhere else ----------------------------------

    async def on_agent_accepted(self, call_session_id: str) -> HoldReport | None:
        """An agent pressed Accept. This is what `D21` exists for.

        A recording in flight is finalised as **partial** — everything said so far is
        kept, the brief renders it as unfinished, and nobody waited a second longer.
        """
        live = self._live.get(call_session_id)
        if live is None:
            return None
        step = self._machine.on_agent_accepted(live.run)
        await self._apply(live, step)
        return await self._report(live, step)

    async def on_hangup(self, call_session_id: str) -> HoldReport | None:
        """Gave up waiting. Whatever they already said still reaches a callback (`D25`)."""
        live = self._live.get(call_session_id)
        if live is None:
            return None
        step = self._machine.on_hangup(live.run)
        await self._apply(live, step)
        return await self._report(live, step)

    async def on_turn(self, call_session_id: str, turn: TranscriptTurn) -> None:
        """One transcribed utterance from the media side. Nothing feeds this yet — the
        transcriber is the next slice — so it is exercised by tests and by scenarios."""
        live = self._live.get(call_session_id)
        if live is not None:
            await live.strategy.on_turn(turn)

    # --- things that happen because time passed (`B7`) ---------------------------------

    async def reoffer_due(self) -> list[str]:
        """Re-offer to anyone whose wait has run past `INTAKE_REOFFER_AFTER_S`.

        Driven by the sweep, not by the request that queued the call, for exactly the
        reason `B7` cost a session: the one thing nobody remembers to call is the thing
        that has to fire because time passed and nothing else happened.
        """
        due: list[str] = []
        now_ms = self._clock.monotonic_ms()
        after_ms = self._settings.intake_reoffer_after_s * 1000.0
        for call_session_id, live in list(self._live.items()):
            if live.reoffered or not self._machine.may_reoffer(live.run):
                continue
            if now_ms - live.queued_at_ms < after_ms:
                continue
            live.reoffered = True
            await self._apply(live, self._machine.reoffer(live.run))
            due.append(call_session_id)
        return due

    async def answer_reoffer(self, call_session_id: str, *, caller: CallerInput) -> HoldReport:
        """Collect the answer to a re-offer. Same loop, same rules, one more chance."""
        live = self._live[call_session_id]
        return await self._collect(live, HoldStep(lines=(), expects_input=True), caller=caller)

    # --- what the media side will drive, once there is one ----------------------------
    #
    # P3-media: the three things that end a recording all come from a layer that does not
    # exist yet - a keypress on the open channel, a VAD-detected silence, and the elapsed
    # duration. They are entry points rather than a loop inside `run_offer` precisely so
    # that layer can drive them, instead of this file guessing what it would report.

    async def on_digit(self, call_session_id: str, digit: str) -> HoldReport | None:
        """A keypress while the caller is holding or recording (`1` again finishes)."""
        live = self._live.get(call_session_id)
        if live is None:
            return None
        step = self._machine.on_digit(live.run, digit)
        await self._apply(live, step)
        return await self._report(live, step)

    async def on_silence(self, call_session_id: str) -> HoldReport | None:
        """`INTAKE_SILENCE_TIMEOUT_S` of nothing. One re-prompt, then close."""
        live = self._live.get(call_session_id)
        if live is None:
            return None
        step = self._machine.on_timeout(live.run)
        await self._apply(live, step)
        return await self._report(live, step)

    async def on_max_duration(self, call_session_id: str) -> HoldReport | None:
        """`INTAKE_MAX_DURATION_S` reached. Everything said is kept."""
        live = self._live.get(call_session_id)
        if live is None:
            return None
        step = self._machine.on_max_duration(live.run)
        await self._apply(live, step)
        return await self._report(live, step)

    # --- reporting --------------------------------------------------------------------

    async def _report(self, live: _LiveHold, step: HoldStep) -> HoldReport:
        run = live.run
        if run.consented is False and not any(
            c.scope is ConsentScope.AI_PROCESSING for c in live.session.consents
        ):
            await self._decline_consent(live.session)

        result = live.strategy.result if run.finished else None
        if run.finished:
            self._live.pop(live.session.call_session_id, None)

        report = HoldReport(
            call_session_id=live.session.call_session_id,
            kind=run.outcome.kind if run.outcome else None,
            recording=run.recording,
            consented=run.consented,
            offers_made=run.offers_made,
            played=tuple(live.played),
            pressed=tuple(run.pressed),
            intake_id=live.strategy.intake_id,
            result=result,
            degraded=self._declined_degradation(run.consented, live),
        )
        log.info(
            "hold settled",
            call_session_id=report.call_session_id,
            outcome=str(report.kind) if report.kind else "waiting",
            consented=report.consented,
            recording=report.recording,
            offers=report.offers_made,
            lines=len(report.played),
        )
        return report

    def _declined_degradation(self, consented: bool | None, live: _LiveHold) -> DegradationReason:
        """A thin brief needs a reason on the screen, and the two thin cases differ.

        `intake_declined` means the caller was asked and said no. `no_consent` means they
        were never asked or never answered — which is our situation, not theirs.
        """
        if consented is True:
            return self._degradation(live)
        if consented is False:
            return DegradationReason.INTAKE_DECLINED
        return DegradationReason.NO_CONSENT

    def live_call_ids(self) -> tuple[str, ...]:
        return tuple(self._live)


__all__ = ["HoldReport", "IntakeService"]
