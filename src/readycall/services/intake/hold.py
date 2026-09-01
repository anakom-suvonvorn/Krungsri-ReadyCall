"""What the caller hears AFTER the queue is decided — the offer, and nothing that routes.

`ARCHITECTURE.md` §6 draws a hard line across the flow:

    >> QUEUE IS NOW KNOWN. Nothing after this point is required for routing. <<

This machine lives entirely below that line, which is why it is a separate machine from
`services/ivr/machine.py` rather than two more states on the end of it. The IVR decides
**where the call goes**; this decides **how much the agent will know when it gets there**.
Blurring them would blur the one distinction the whole product rests on (`D37`): the
keypad is the floor, the AI is the delta, and every failure here has to leave a correctly
routed call behind it.

No I/O, for the same reason as the IVR machine (`B7`): everything that happens because
*time passed* — a silence, the re-offer at 90 seconds, the maximum recording length — is
an ordinary method call here, so it can be tested without a phone line or a clock.

**The rules, and where each comes from:**

* **press 2 is a first-class outcome, not a failure** (`D19`, `D37`). The queue was already
  decided by the menu, so a caller who declines still reaches the right agent with a
  menu-derived brief. Nothing about this is a degraded path.
* **the offer is asked at most twice** (`D14` — enrichment is not something to nag about).
  One offer, and one re-offer if the wait runs past `INTAKE_REOFFER_AFTER_S`; then never
  again, whatever they pressed.
* **a wrong key replays the offer, unlimited** (`D82`), because a wrong key still proves
  somebody is there. What bounds the loop is *silence*, not a strike count: press nothing
  and the offer closes on its own.
* **the agent accepting cuts the recording, and that is not a loss** (`D21`). The offer
  window IS the grace period, so the caller is never hurried and the tail of a sentence
  lands as a partial intake rather than being thrown away.
* **while HOLDING, keys do nothing.** Nobody is being asked a question, so replying to a
  keypress would be answering something the caller did not say.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from readycall.domain.enums import FinalizeReason
from readycall.domainpack import MenuSettings
from readycall.logging import get_logger
from readycall.voiceprompts import PromptPack, PromptRole, SpokenLine

log = get_logger(__name__)


class HoldPhase(StrEnum):
    OFFERING = "offering"  # the offer is out; we are waiting on 1 or 2
    RECORDING = "recording"  # pressed 1 — they are talking
    HOLDING = "holding"  # waiting for an agent, re-offerable at most once
    DONE = "done"


class HoldOutcomeKind(StrEnum):
    """What we ended up with. Deliberately not ranked — `DECLINED` is not a failure."""

    RECORDED = "recorded"  # an intake ran and finished on its own terms
    CUT_SHORT = "cut_short"  # an agent accepted mid-sentence -> partial (D21)
    DECLINED = "declined"  # pressed 2. A normal call, with a menu-derived brief
    IGNORED = "ignored"  # never answered the offer at all
    ABANDONED = "abandoned"  # hung up while waiting


@dataclass(frozen=True, slots=True)
class HoldOutcome:
    kind: HoldOutcomeKind
    #: Why the recording stopped. Meaningless unless `spoke` — nothing was running.
    finalize_reason: FinalizeReason
    #: Whether an intake actually ran. `False` means context-only brief (`D14`).
    spoke: bool
    #: Cut off with more to say (`D21`). The brief must show this rather than imply
    #: the caller finished their thought.
    is_partial: bool
    offers_made: int
    pressed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HoldStep:
    """What the caller hears next, and whether we then wait for a keypress."""

    lines: tuple[SpokenLine, ...]
    expects_input: bool = False
    outcome: HoldOutcome | None = None
    #: True on the step that starts a recording, so the driver knows to open the intake.
    starts_recording: bool = False
    #: True on the step that ends one, so the driver knows to finalise it.
    ends_recording: bool = False

    @property
    def finished(self) -> bool:
        return self.outcome is not None


@dataclass
class HoldRun:
    """Per-call state for the hold. Mutable, owned by whoever is driving this one call."""

    call_session_id: str
    phase: HoldPhase = HoldPhase.OFFERING
    offers_made: int = 0
    #: Silences *inside* a recording. The offer's own silence is not counted — it closes
    #: the offer on the first one, because the re-offer is the second chance.
    silences: int = 0
    wrong_presses: int = 0
    pressed: list[str] = field(default_factory=list)
    #: None until they answer. `False` after pressing 2 — an explicit "no" is evidence
    #: and is worth keeping, not the same thing as never having been asked.
    consented: bool | None = None
    spoke: bool = False
    finalize_reason: FinalizeReason | None = None
    outcome: HoldOutcome | None = None

    @property
    def finished(self) -> bool:
        return self.outcome is not None

    @property
    def recording(self) -> bool:
        return self.phase is HoldPhase.RECORDING


class HoldMachine:
    """One instance per process; a `HoldRun` carries everything about one call."""

    #: The two keys the offer asks for. Not configurable and not personalised: unlike a
    #: menu there is nothing to reorder, and the numbers are spoken in the prompt text.
    RECORD_KEY = "1"
    HOLD_KEY = "2"

    def __init__(self, *, prompts: PromptPack, settings: MenuSettings) -> None:
        self._prompts = prompts
        self._settings = settings

    # --- starting ---------------------------------------------------------------------

    def begin(self, *, call_session_id: str) -> tuple[HoldRun, HoldStep]:
        """Acknowledge the wait, then make the offer.

        **No queue position is spoken, and that is a decision** (`D91`, reversing `D89`).
        There is no line to have a position in: the matcher scores every waiting caller
        against every free agent as `fit x urgency` and re-solves the whole matrix each
        tick, so arrival order is not an input anywhere. A number would be a promise the
        system deliberately does not keep - and it would be broken most often for the
        low-urgency callers most likely to have believed it.
        """
        run = HoldRun(call_session_id=call_session_id)
        return run, self._offer(
            run, [self._prompts.say(PromptRole.QUEUE_HOLD)], role=PromptRole.INTAKE_OFFER
        )

    def _offer(self, run: HoldRun, lines: list[SpokenLine], *, role: PromptRole) -> HoldStep:
        run.phase = HoldPhase.OFFERING
        run.offers_made += 1
        run.wrong_presses = 0
        return HoldStep(
            lines=(*lines, self._prompts.say(role)),
            expects_input=True,
        )

    # --- the re-offer -----------------------------------------------------------------

    def may_reoffer(self, run: HoldRun) -> bool:
        """Once, and only to somebody who has not already told us something.

        A caller who recorded has nothing left to be offered; a caller who is *currently*
        recording must not be interrupted by the machine asking again.
        """
        return (
            not run.finished
            and run.phase is HoldPhase.HOLDING
            and run.offers_made < 2
            and not run.spoke
        )

    def reoffer(self, run: HoldRun) -> HoldStep:
        """Ask once more, with the shorter wording — they have heard the pitch already."""
        if not self.may_reoffer(run):  # pragma: no cover - the driver checks first
            return HoldStep(lines=(), outcome=run.outcome)
        log.info("intake re-offered", call_session_id=run.call_session_id)
        return self._offer(run, [], role=PromptRole.INTAKE_REOFFER)

    # --- input ------------------------------------------------------------------------

    def on_digit(self, run: HoldRun, digit: str) -> HoldStep:
        if run.finished:
            return HoldStep(lines=(), outcome=run.outcome)
        run.pressed.append(digit)

        if run.phase is HoldPhase.RECORDING:
            return self._digit_while_recording(run, digit)
        if run.phase is HoldPhase.OFFERING:
            return self._digit_while_offering(run, digit)
        # HOLDING: nobody asked a question, so nothing answers. Playing an apology here
        # would be replying to something the caller never said.
        return HoldStep(lines=(), expects_input=False)

    def _digit_while_offering(self, run: HoldRun, digit: str) -> HoldStep:
        if digit == self.RECORD_KEY:
            run.consented = True
            run.phase = HoldPhase.RECORDING
            run.silences = 0
            run.spoke = True
            return HoldStep(
                lines=(self._prompts.say(PromptRole.INTAKE_START),),
                expects_input=True,
                starts_recording=True,
            )

        if digit == self.HOLD_KEY:
            # A first-class outcome (`D19`). The acknowledgement says "fine" rather than
            # anything that sounds like a lost opportunity, because it is not one.
            run.consented = False
            run.phase = HoldPhase.HOLDING
            return HoldStep(lines=(self._prompts.say(PromptRole.INTAKE_DECLINED),))

        if digit == self._settings.repeat_key:
            # Same key as everywhere else in the call. A caller who learned the repeat
            # key in the menu must not find it means something different thirty
            # seconds later (`D90` moved it; it moved in both places at once).
            return HoldStep(lines=(self._prompts.say(self._offer_role(run)),), expects_input=True)

        run.wrong_presses += 1
        if run.wrong_presses >= self._settings.runaway_press_guard:
            # A stuck sender, not a person. Stop answering it and just hold.
            run.phase = HoldPhase.HOLDING
            log.info(
                "intake offer abandoned to a runaway sender",
                call_session_id=run.call_session_id,
                presses=run.wrong_presses,
            )
            return HoldStep(lines=())
        return HoldStep(
            lines=(
                self._prompts.render(
                    self._settings.invalid_prompt, repeat_key=self._settings.repeat_key
                ),
                self._prompts.say(self._offer_role(run)),
            ),
            expects_input=True,
        )

    def _offer_role(self, run: HoldRun) -> PromptRole:
        return PromptRole.INTAKE_OFFER if run.offers_made <= 1 else PromptRole.INTAKE_REOFFER

    def _digit_while_recording(self, run: HoldRun, digit: str) -> HoldStep:
        if digit == self.RECORD_KEY:
            return self._stop_recording(run, FinalizeReason.CUSTOMER_DONE)
        # Anything else is ignored on purpose. They are mid-sentence, and interrupting a
        # recording to apologise for a mis-hit is worse than the mis-hit.
        return HoldStep(lines=(), expects_input=True)

    # --- things that happen because time passed ---------------------------------------

    def on_timeout(self, run: HoldRun) -> HoldStep:
        """No keypress within the input window.

        Two very different silences share this method because the driver cannot tell them
        apart: silence at the offer means "not now", silence during a recording means the
        caller has stopped talking.
        """
        if run.finished:
            return HoldStep(lines=(), outcome=run.outcome)

        if run.phase is HoldPhase.OFFERING:
            # The offer closes on the first silence rather than nagging. The second
            # chance already exists and is better placed: the re-offer, once the wait
            # has actually become long enough to change the caller's mind.
            run.phase = HoldPhase.HOLDING
            return HoldStep(lines=(self._prompts.say(PromptRole.QUEUE_HOLD),))

        if run.phase is HoldPhase.RECORDING:
            run.silences += 1
            if run.silences >= self._settings.max_silences:
                return self._stop_recording(run, FinalizeReason.SILENCE_TIMEOUT)
            # One re-prompt: a caller who pressed 1 and then froze usually just needs the
            # instruction again, and they have already told us they want to be heard.
            return HoldStep(lines=(self._prompts.say(PromptRole.INTAKE_START),), expects_input=True)

        return HoldStep(lines=())

    def on_max_duration(self, run: HoldRun) -> HoldStep:
        """`INTAKE_MAX_DURATION_S` reached. Stop, thank them, keep everything said."""
        if run.finished or not run.recording:
            return HoldStep(lines=(), outcome=run.outcome)
        return self._stop_recording(run, FinalizeReason.MAX_DURATION)

    def _stop_recording(self, run: HoldRun, reason: FinalizeReason) -> HoldStep:
        run.phase = HoldPhase.HOLDING
        run.finalize_reason = reason
        return HoldStep(
            lines=(self._prompts.say(PromptRole.INTAKE_DONE),),
            ends_recording=True,
        )

    # --- endings ----------------------------------------------------------------------

    def on_agent_accepted(self, run: HoldRun) -> HoldStep:
        """An agent pressed Accept. The wait is over — that is the good ending (`D21`).

        A recording still in progress is finalised as **partial** rather than discarded.
        The caller was never hurried and never cut off in the sense that matters: they
        are now talking to a person, which is what they rang for.
        """
        if run.finished:
            return HoldStep(lines=(), outcome=run.outcome)
        was_recording = run.recording
        if was_recording:
            run.finalize_reason = FinalizeReason.OFFER_ACCEPTED
        return self._finish(
            run,
            kind=self._ending_kind(run, cut_short=was_recording),
            is_partial=was_recording,
            ends_recording=was_recording,
        )

    def on_hangup(self, run: HoldRun) -> HoldStep:
        """Gave up waiting. Whatever they already said is still worth keeping (`D25`)."""
        if run.finished:
            return HoldStep(lines=(), outcome=run.outcome)
        was_recording = run.recording
        if was_recording:
            run.finalize_reason = FinalizeReason.CALL_ENDED
        return self._finish(
            run,
            kind=HoldOutcomeKind.ABANDONED,
            is_partial=was_recording,
            ends_recording=was_recording,
        )

    def _ending_kind(self, run: HoldRun, *, cut_short: bool) -> HoldOutcomeKind:
        if cut_short:
            return HoldOutcomeKind.CUT_SHORT
        if run.spoke:
            return HoldOutcomeKind.RECORDED
        if run.consented is False:
            return HoldOutcomeKind.DECLINED
        return HoldOutcomeKind.IGNORED

    def _finish(
        self,
        run: HoldRun,
        *,
        kind: HoldOutcomeKind,
        is_partial: bool,
        ends_recording: bool,
    ) -> HoldStep:
        run.phase = HoldPhase.DONE
        outcome = HoldOutcome(
            kind=kind,
            # `NO_CONSENT` when nothing ever ran: the reason there is no transcript is
            # that we were never allowed one, and the brief has to be able to say so.
            finalize_reason=run.finalize_reason or FinalizeReason.NO_CONSENT,
            spoke=run.spoke,
            is_partial=is_partial,
            offers_made=run.offers_made,
            pressed=tuple(run.pressed),
        )
        run.outcome = outcome
        return HoldStep(lines=(), outcome=outcome, ends_recording=ends_recording)


__all__ = [
    "HoldMachine",
    "HoldOutcome",
    "HoldOutcomeKind",
    "HoldPhase",
    "HoldRun",
    "HoldStep",
]
