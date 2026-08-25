"""Driving one caller through the menu: play, wait, press, repeat.

Everything with a rule in it lives in `machine.py`. This file only knows how to speak to
a telephony provider and how to wait — so swapping the simulated provider for Asterisk at
P5 changes nothing about how the menu behaves, which is the whole point of the port
(`D3`).

Two shapes here are deliberate:

**Input arrives through a `CallerInput`, and it sees the presentation.** A scripted caller
in a scenario file says *which option they wanted*, not which key happened to be under it
— because with an option promoted those differ (`D81`). Scenario files therefore stay
correct when personalisation changes the numbering, instead of silently pressing the wrong
thing.

**Nothing here decides the queue by itself.** `queue_for` walks the evidence from best to
worst and lands on the general queue only when there is genuinely nothing else, because a
caller who told us their product line should reach someone who works on that line even if
they never said why (`D40`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from readycall.clock import Clock
from readycall.domain.enums import ProductLine
from readycall.domain.models import CallSession
from readycall.domainpack import DidSpec, DomainPack
from readycall.logging import get_logger
from readycall.ports.telephony import TelephonyProvider
from readycall.services.call_orchestrator.orchestrator import CallOrchestrator
from readycall.services.ivr.machine import IvrMachine, IvrOutcome, IvrOutcomeKind
from readycall.services.ivr.personalise import PersonalisationInputs
from readycall.services.ivr.presentation import MenuPresentation
from readycall.voiceprompts import PromptPack, SpokenLine

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Press:
    """One caller action. `digit is None` means the wait expired."""

    digit: str | None = None
    hung_up: bool = False

    @property
    def timed_out(self) -> bool:
        return self.digit is None and not self.hung_up


@runtime_checkable
class CallerInput(Protocol):
    async def next_press(
        self, presentation: MenuPresentation | None, *, timeout_s: float
    ) -> Press: ...


class ScriptedChoices:
    """A caller who knows what they want, for scenarios and tests.

    Keys are **canonical** — the ones in `menus.yaml`. If the menu was reordered for this
    caller, the right spoken key is looked up rather than assumed, so a scenario file keeps
    meaning what it says (`D81`). Reserved keys (`9`, `0`) and any key the presentation
    does not know are passed through untranslated, which is what makes "press 0" testable.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self._keys = list(keys)
        self.sent: list[str] = []

    async def next_press(self, presentation: MenuPresentation | None, *, timeout_s: float) -> Press:
        if not self._keys:
            # Out of script: the caller has fallen silent, which is a real thing callers
            # do and a rung the machine has to handle.
            return Press(digit=None)
        wanted = self._keys.pop(0)
        digit = wanted
        if presentation is not None:
            for option in presentation.options:
                if option.option.key == wanted:
                    digit = option.spoken_key
                    break
        self.sent.append(digit)
        return Press(digit=digit)


@dataclass(frozen=True, slots=True)
class IvrResult:
    outcome: IvrOutcome
    queue_id: str
    #: Every line played, in order — the spoken half of `D18`'s "show, do not claim".
    played: tuple[str, ...]
    #: True when this caller heard the options in a different order (`D37`).
    personalised: bool = False
    promoted_because: tuple[str, ...] = ()


class IvrService:
    def __init__(
        self,
        *,
        pack: DomainPack,
        prompts: PromptPack,
        telephony: TelephonyProvider,
        orchestrator: CallOrchestrator,
        clock: Clock,
        engine_name: str = "null",
    ) -> None:
        self._pack = pack
        self._prompts = prompts
        self._telephony = telephony
        self._orchestrator = orchestrator
        self._clock = clock
        self._engine = engine_name
        self._machine = IvrMachine(pack=pack, prompts=prompts)

    # --- the walk ---------------------------------------------------------------------

    async def run(
        self,
        session: CallSession,
        *,
        caller: CallerInput,
        did: DidSpec | None = None,
        known_intent: str | None = None,
        inputs: PersonalisationInputs | None = None,
        max_steps: int = 24,
    ) -> IvrResult:
        """Greet, ask what we do not know, and leave the call ready to be queued."""
        await self._orchestrator.enter_ivr(session)

        run, step = self._machine.begin(
            call_session_id=session.call_session_id,
            did=did,
            known_intent=known_intent,
            known_line=session.product_line,
            inputs=inputs,
        )
        played: list[str] = []
        await self._play(session, step.lines, played)

        steps = 0
        while not step.finished:
            steps += 1
            if steps > max_steps:
                # A caller cannot loop for ever, even by pressing 9 all day.
                step = self._machine.on_timeout(run)
                await self._play(session, step.lines, played)
                continue
            press = await caller.next_press(
                run.presentation, timeout_s=self._pack.menu_settings.timeout_s
            )
            if press.hung_up:
                step = self._machine.on_hangup(run)
            elif press.timed_out:
                step = self._machine.on_timeout(run)
            else:
                assert press.digit is not None
                step = self._machine.on_digit(run, press.digit)
            await self._play(session, step.lines, played)

        outcome = step.outcome
        assert outcome is not None
        self._apply(session, outcome)

        result = IvrResult(
            outcome=outcome,
            queue_id=self.queue_for(outcome, did),
            played=tuple(played),
            personalised=bool(run.personalisations),
            promoted_because=tuple(
                reason for promotion in run.personalisations for reason in promotion.reasons
            ),
        )
        log.info(
            "ivr finished",
            call_session_id=session.call_session_id,
            outcome=outcome.kind.value,
            reason=outcome.reason,
            path="/".join(outcome.path) or "-",
            intent=outcome.intent_code or "-",
            queue=result.queue_id,
            lines=len(played),
        )
        return result

    async def _play(
        self, session: CallSession, lines: Sequence[SpokenLine], played: list[str]
    ) -> None:
        """Play each clip. Barge-in is the provider's job — a keypress stops playback."""
        for line in lines:
            played.append(line.prompt_id)
            if session.telephony_call_id is None:
                # No channel (a scenario, or the demo endpoint): the walk is still real,
                # only the audio is absent. Recording what *would* have played keeps the
                # spoken half visible rather than silently skipped.
                continue
            await self._telephony.play(session.telephony_call_id, line.audio_ref(self._engine))

    def _apply(self, session: CallSession, outcome: IvrOutcome) -> None:
        """Write what the keypad established onto the call.

        `menu_path` holds canonical keys, never the spoken ones — otherwise a personalised
        menu would make two identical-looking calls mean different things (`D81`).
        """
        session.menu_path = outcome.path
        session.menu_intent_code = outcome.intent_code
        if outcome.product_line is not ProductLine.UNKNOWN:
            session.product_line = outcome.product_line

    # --- routing ----------------------------------------------------------------------

    def queue_for(self, outcome: IvrOutcome, did: DidSpec | None = None) -> str:
        """Best evidence first. Each rung beats falling through to the general queue.

        An explicit intent beats the line's catch-all, which beats the DID's default.
        Knowing only the product line must still land the caller with someone who works
        on that line: dropping them into `q_general` throws away what they told us.
        """
        if outcome.kind is IvrOutcomeKind.OPERATOR:
            # They asked for a person, not a specialist. Send them where people are, and
            # keep whatever the menu already established for the brief.
            return did.default_queue if did else "q_general"
        if outcome.intent_code:
            return self._pack.queue_for_intent(outcome.intent_code)
        if outcome.product_line is not ProductLine.UNKNOWN:
            return self._pack.queue_for_intent(self._pack.catch_all_for(outcome.product_line).code)
        if did is not None:
            return did.default_queue
        return "q_general"


__all__ = ["CallerInput", "IvrResult", "IvrService", "Press", "ScriptedChoices"]
