"""The keypad walk, as a state machine with no I/O.

**This is the thing that routes the call** (`D37`). Not the AI — the AI runs afterwards,
while the caller waits, and adds detail a keypad cannot capture. So the floor here is
parity with an ordinary call centre, and every rule below exists to keep it there:

* **a wrong key is never a strike** (`D82`) — sorry, here are the options again, for as
  long as they keep pressing. Giving up after N mistakes traded the good answer we were
  about to get for a guaranteed mediocre one;
* silence IS bounded, because it does not prove anybody is there: one re-prompt, then
  route to a human;
* `0` repeats and costs nothing — re-listening is not an error, and `0` is where a lost
  caller's thumb already goes (`D90`);
* **there is no operator key** (`D86`). Every menu ends in a spoken "เรื่องอื่นๆ" option that
  routes to the same place `0` used to, so the shortcut cost a reserved key and bought two
  keypresses.

Modelled as `begin` / `on_digit` / `on_timeout` / `on_hangup` returning *what to play
next*, rather than as a loop that awaits input. The reason is `B7`: an IVR written as a
coroutine that awaits DTMF is only testable by feeding it a fake phone line, so the parts
that fire because **time passed** end up untested. Here a timeout is an ordinary method
call, and the async driver in `service.py` holds no rules at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from readycall.domain.enums import ProductLine
from readycall.domainpack import DidSpec, DomainPack, MenuSpec
from readycall.logging import get_logger
from readycall.services.ivr.personalise import (
    PersonalisationInputs,
    Promotion,
    promote,
    promoted_keys,
    validate_signals,
)
from readycall.services.ivr.presentation import MenuPresentation, present
from readycall.voiceprompts import PromptPack, PromptRole, SpokenLine

log = get_logger(__name__)


class IvrOutcomeKind(StrEnum):
    ROUTED = "routed"  # the caller chose; we know the line and the reason
    SKIPPED = "skipped"  # we already knew, so we did not ask (D37)
    EXHAUSTED = "exhausted"  # gave up on the menu -> general queue, never a hang-up
    ABANDONED = "abandoned"  # hung up during the menu


@dataclass(frozen=True, slots=True)
class IvrOutcome:
    kind: IvrOutcomeKind
    product_line: ProductLine
    intent_code: str | None
    #: Canonical keys, so the same path always means the same thing (`D81`).
    path: tuple[str, ...]
    #: What the caller physically pressed, including mistakes. A UX signal, not routing.
    pressed: tuple[str, ...]
    reason: str

    @property
    def routed_by_keypad(self) -> bool:
        return self.kind is IvrOutcomeKind.ROUTED


@dataclass(frozen=True, slots=True)
class IvrStep:
    """What the caller hears next, and whether we then wait for them."""

    lines: tuple[SpokenLine, ...]
    expects_input: bool = False
    outcome: IvrOutcome | None = None

    @property
    def finished(self) -> bool:
        return self.outcome is not None


@dataclass
class IvrRun:
    """Per-call state. Mutable, owned by whoever is driving this one call."""

    call_session_id: str
    product_line: ProductLine = ProductLine.UNKNOWN
    intent_code: str | None = None
    menu: MenuSpec | None = None
    presentation: MenuPresentation | None = None
    path: list[str] = field(default_factory=list)
    pressed: list[str] = field(default_factory=list)
    attempts: int = 0
    timeouts: int = 0
    #: Promotions on the menu currently being spoken. Reset on every menu, because
    #: they describe *this* menu's order.
    promotions: tuple[Promotion, ...] = ()
    #: Every promotion made anywhere on this call. Kept separately because the answer to
    #: "was this caller's menu personalised" outlives the menu that was personalised —
    #: entering the reason menu used to wipe the evidence that the first one was.
    personalisations: list[Promotion] = field(default_factory=list)
    outcome: IvrOutcome | None = None

    @property
    def finished(self) -> bool:
        return self.outcome is not None


class IvrMachine:
    """One instance per process; `IvrRun` carries everything about one call."""

    def __init__(self, *, pack: DomainPack, prompts: PromptPack) -> None:
        self._pack = pack
        self._prompts = prompts
        self._settings = pack.menu_settings
        # Startup gate rather than a per-call surprise: a weight naming a signal nothing
        # computes is config that claims to do something it does not.
        validate_signals(pack.personalisation)

    # --- starting ---------------------------------------------------------------------

    def begin(
        self,
        *,
        call_session_id: str,
        did: DidSpec | None = None,
        known_intent: str | None = None,
        known_line: ProductLine = ProductLine.UNKNOWN,
        inputs: PersonalisationInputs | None = None,
    ) -> tuple[IvrRun, IvrStep]:
        """Greet, give the notice, and ask the first question we do not know the answer to.

        `known_intent` is the app path (`D48`): tapping Contact answers both menu questions
        before the phone rings, so the caller is never asked either of them.
        """
        run = IvrRun(call_session_id=call_session_id)
        lines: list[SpokenLine] = [
            self._prompts.render(did.greeting_prompt if did else self._default_greeting()),
            # Always, whichever number was dialled, and before any menu. It has to be first.
            self._prompts.say(PromptRole.RECORDING_NOTICE),
        ]

        if did is not None:
            run.product_line = did.product_line
        if known_line is not ProductLine.UNKNOWN:
            run.product_line = known_line

        if known_intent is not None:
            run.intent_code = known_intent
            if run.product_line is ProductLine.UNKNOWN:
                run.product_line = self._pack.intent(known_intent).line
            return run, self._finish(
                run, IvrOutcomeKind.SKIPPED, reason="app_supplied_line_and_reason", lines=lines
            )

        menu = self._start_menu(did)
        if menu is None:
            # No menu to walk and nothing known. Not a crash: the general queue exists
            # precisely so a caller is never stranded.
            return run, self._finish(
                run, IvrOutcomeKind.EXHAUSTED, reason="no_menu_configured", lines=lines
            )
        return run, self._enter(run, menu, inputs=inputs, lines=lines)

    def _default_greeting(self) -> str:
        """The greeting for a number with no entry in `dids.yaml`.

        A role rather than a literal, so the fallback is config like every other line
        (`D28`) and the guard test checks it resolves.
        """
        return self._prompts.id_for(PromptRole.GREETING_FALLBACK)

    def _start_menu(self, did: DidSpec | None) -> MenuSpec | None:
        """Skip what we already know (`D37`) — asking is bad service, not thoroughness."""
        if did is not None and did.skip_product_menu:
            reason_menu = self._pack.reason_menu_for(did.product_line)
            if reason_menu is not None:
                return reason_menu
        return self._pack.menus.get("product_line")

    def _enter(
        self,
        run: IvrRun,
        menu: MenuSpec,
        *,
        inputs: PersonalisationInputs | None,
        lines: list[SpokenLine] | None = None,
    ) -> IvrStep:
        spec = self._pack.personalisation
        promotions = promote(menu, inputs, spec) if (inputs is not None and spec) else ()
        run.menu = menu
        run.promotions = promotions
        run.personalisations.extend(promotions)
        run.attempts = 0
        run.timeouts = 0
        run.presentation = present(
            menu,
            prompts=self._prompts,
            settings=self._settings,
            promoted=promoted_keys(promotions),
            renumber=spec.renumber if spec else True,
        )
        if promotions:
            log.info(
                "menu personalised",
                menu=menu.menu_id,
                promoted=[p.key for p in promotions],
                why=[reason for p in promotions for reason in p.reasons],
            )
        return IvrStep(
            lines=tuple(lines or []) + run.presentation.lines,
            expects_input=True,
        )

    # --- input ------------------------------------------------------------------------

    def on_digit(self, run: IvrRun, digit: str) -> IvrStep:
        if run.finished or run.presentation is None or run.menu is None:
            return IvrStep(lines=(), outcome=run.outcome)

        run.pressed.append(digit)

        if digit == self._settings.repeat_key:
            # Re-listening is not a mistake, so it does not spend an attempt.
            run.timeouts = 0
            return IvrStep(lines=run.presentation.lines, expects_input=True)

        option = run.presentation.resolve(digit)
        if option is None:
            run.attempts += 1
            if run.attempts >= self._settings.runaway_press_guard:
                # Not a caller who has run out of chances — a sender that is stuck. A
                # person cannot reach this, and if they somehow did they still get a
                # queue rather than a dial tone.
                return self._finish(
                    run,
                    IvrOutcomeKind.EXHAUSTED,
                    reason=f"runaway_input_x{run.attempts}",
                    lines=[],
                )
            # Otherwise: apologise, point at the two keys that always work, and offer the
            # menu again. Unlimited, on purpose (`D82`).
            return IvrStep(
                lines=(
                    self._prompts.render(
                        self._settings.invalid_prompt, repeat_key=self._settings.repeat_key
                    ),
                    *run.presentation.lines,
                ),
                expects_input=True,
            )

        # Canonical, not what they pressed: with an option promoted the two differ (`D81`).
        run.path.append(option.key)
        run.timeouts = 0
        if option.product_line is not None:
            run.product_line = option.product_line

        if option.intent is not None:
            run.intent_code = option.intent
            if run.product_line is ProductLine.UNKNOWN:
                run.product_line = self._pack.intent(option.intent).line
            return self._finish(run, IvrOutcomeKind.ROUTED, reason="menu_completed", lines=[])

        if option.next_menu is not None:
            next_menu = self._pack.menus.get(option.next_menu)
            if next_menu is not None:
                return self._enter(run, next_menu, inputs=None)

        # `DomainPack.validate()` rejects an option that leads nowhere, so reaching this
        # means the config changed under a running process. Still not a hang-up.
        return self._finish(run, IvrOutcomeKind.EXHAUSTED, reason="option_leads_nowhere", lines=[])

    def on_timeout(self, run: IvrRun) -> IvrStep:
        """Silence. Re-prompt once, then route to the general queue — never hang up."""
        if run.finished or run.presentation is None:
            return IvrStep(lines=(), outcome=run.outcome)
        run.timeouts += 1
        if run.timeouts >= self._settings.max_silences:
            return self._finish(
                run, IvrOutcomeKind.EXHAUSTED, reason=f"silence_x{run.timeouts}", lines=[]
            )
        return IvrStep(
            lines=(self._prompts.say(PromptRole.NO_INPUT), *run.presentation.lines),
            expects_input=True,
        )

    def on_hangup(self, run: IvrRun) -> IvrStep:
        return self._finish(run, IvrOutcomeKind.ABANDONED, reason="caller_hung_up", lines=[])

    # --- finishing --------------------------------------------------------------------

    def _finish(
        self,
        run: IvrRun,
        kind: IvrOutcomeKind,
        *,
        reason: str,
        lines: list[SpokenLine],
    ) -> IvrStep:
        outcome = IvrOutcome(
            kind=kind,
            product_line=run.product_line,
            intent_code=run.intent_code,
            path=tuple(run.path),
            pressed=tuple(run.pressed),
            reason=reason,
        )
        run.outcome = outcome
        return IvrStep(lines=tuple(lines), expects_input=False, outcome=outcome)


__all__ = ["IvrMachine", "IvrOutcome", "IvrOutcomeKind", "IvrRun", "IvrStep"]
