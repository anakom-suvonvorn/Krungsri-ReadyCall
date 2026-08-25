"""Untyped keypad capture: the agent starts it, the customer keys, the agent stops it.

That is the whole primitive (`D44`). It is deliberately *not* "click policy number, the
customer types their policy number", because two assumptions in that version are unsafe:

1. **That the customer has the thing we asked for.** They may have a citizen ID card, a
   claim SMS, a renewal letter, or nothing. Someone standing next to a crashed car has
   whatever was in the glovebox. A feature built around one named document fails the
   moment they do not have it — which is often.
2. **That a match proves who is holding the phone.** It does not. A daughter calling for
   her father may legitimately hold his documents and key his policy number correctly.

So capture is untyped, interpretation is a separate optional step, and **a lookup returns
evidence, never an action** — it never changes assurance, never unlocks a field, and never
writes to the identity record. Promotion stays the agent's three-way control (`D42`).

**The safe default inverts** (`D44`): because we do not know what the digits *are*, a raw
capture is treated as potentially sensitive. Read that clause precisely, because the first
implementation over-applied it — `D44` says *"masked in transcripts and logs, short
retention, discardable with one click"*. **Transcripts and logs. Not the agent.**

The agent is the person the digits were captured *for*. They asked the caller to key them,
they are on the call, and they have to read them back or act on them. Masking them on the
agent's own screen deletes the feature and keeps none of the protection: `••••••••11` is
not a policy number anybody can use. What must never happen is those digits landing in a
transcript, a log line, an analytics event, or long-term storage — and that is exactly
where `mask()` is applied (`D58`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from readycall import ids
from readycall.clock import Clock
from readycall.errors import PermanentError
from readycall.logging import get_logger

log = get_logger(__name__)

#: Digits kept visible when masking. Enough to tell two captures apart in a log, not
#: enough to be worth exfiltrating.
_VISIBLE_TAIL = 2


class CaptureState(StrEnum):
    OPEN = "open"
    STOPPED = "stopped"
    DISCARDED = "discarded"


def mask(digits: str, *, tail: int = _VISIBLE_TAIL) -> str:
    """`••••••4512`. For logs, transcripts and storage — **never** for the agent's screen.

    The agent sees `Capture.digits`. This is what everything else sees (`D58`).
    """
    if not digits:
        return ""
    if len(digits) <= tail:
        return "•" * len(digits)
    return "•" * (len(digits) - tail) + digits[-tail:]


@dataclass
class Capture:
    capture_id: str
    call_session_id: str
    agent_id: str
    started_at: datetime
    state: CaptureState = CaptureState.OPEN
    digits: str = ""
    stopped_at: datetime | None = None
    #: What the agent said this was, *after* the fact and only if they said anything.
    #: `None` is the normal case and the default mode we build first (`D44`).
    labelled_as: str | None = None
    lookups: list[LookupResult] = field(default_factory=list)

    @property
    def masked(self) -> str:
        """For logs and transcripts. The agent's panel renders `digits` (`D58`)."""
        return mask(self.digits)

    @property
    def length(self) -> int:
        return len(self.digits)

    @property
    def is_named(self) -> bool:
        """Has anything told us **what these digits are**?

        This is the switch on `D44`'s inverted storage default. Capture is untyped, so
        until a lookup matches or the agent labels them, a run of digits could be a citizen
        id, a card number, or a hospital code — and an unknown number is treated as
        sensitive. Once something names it, it is a policy or claim number, which is not a
        secret and is worth having in the record.
        """
        return self.labelled_as is not None or any(x.matched for x in self.lookups)


@dataclass(frozen=True, slots=True)
class LookupResult:
    """Evidence. Never an action (`D44`)."""

    kind: str
    matched: bool
    at: datetime
    #: What it matched *against* — e.g. a policy number. Safe to name because the lookup
    #: has now told us what the digits are; before that they were unknown and masked.
    matched_value: str | None = None
    detail: str | None = None


class CaptureStore(Protocol):
    """The durable half of keypad capture (`D78`).

    Written at **milestones only** — started, stopped, discarded, labelled, looked up —
    and never per keystroke. A row per digit would be a write amplification for no reader,
    and an in-flight capture is transient by nature: the DTMF stream belongs to a call leg
    that does not outlive the process either.
    """

    async def save(self, capture: Capture, *, store_digits: bool) -> None: ...

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Capture]: ...


class InMemoryCaptureStore:
    """The fake. Same contract suite as the real one (`D3`)."""

    def __init__(self) -> None:
        self._rows: dict[str, Capture] = {}

    async def save(self, capture: Capture, *, store_digits: bool) -> None:
        # A copy, because `Capture` is mutable and the service keeps editing the one it
        # handed us. Storing the live object would make the "durable" row change under
        # the reader, which is exactly the illusion this suite exists to disprove.
        self._rows[capture.capture_id] = replace(
            capture,
            digits=capture.digits if store_digits else "",
            lookups=list(capture.lookups),
        )

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Capture]:
        wanted = set(call_session_ids)
        return [
            replace(c, lookups=list(c.lookups))
            for c in self._rows.values()
            if c.call_session_id in wanted
        ]


class KeypadCaptureService:
    def __init__(
        self, *, clock: Clock, max_digits: int = 32, store: CaptureStore | None = None
    ) -> None:
        self._clock = clock
        self._max_digits = max_digits
        self._store = store
        self._captures: dict[str, Capture] = {}
        self._open_by_call: dict[str, str] = {}

    # --- the primitive ------------------------------------------------------------------

    async def start(self, *, call_session_id: str, agent_id: str) -> Capture:
        if call_session_id in self._open_by_call:
            raise PermanentError(f"call {call_session_id} already has an open capture")
        capture = Capture(
            capture_id=ids.capture_id(),
            call_session_id=call_session_id,
            agent_id=agent_id,
            started_at=self._clock.now(),
        )
        self._captures[capture.capture_id] = capture
        self._open_by_call[call_session_id] = capture.capture_id
        log.info("keypad capture started", call_session_id=call_session_id, agent_id=agent_id)
        await self._persist(capture)
        return capture

    def key(self, capture_id: str, digits: str) -> Capture:
        """A DTMF digit (or several) arrived. Idempotent per keypress at the transport."""
        capture = self._require(capture_id, must_be_open=True)
        if not digits.isdigit():
            # `*` and `#` are control keys on a phone, not data. Accepting them here
            # would put a caller's attempt to correct themselves into the value.
            raise PermanentError("only digits may be captured")
        if capture.length + len(digits) > self._max_digits:
            raise PermanentError(f"capture exceeds {self._max_digits} digits")
        capture.digits += digits
        # NOTE: length only. The digits themselves are never logged (`D44`).
        log.info(
            "keypad digits", capture_id=capture_id, length=capture.length, masked=capture.masked
        )
        return capture

    def backspace(self, capture_id: str) -> Capture:
        """Callers mistype. Without this the only fix is discard-and-restart."""
        capture = self._require(capture_id, must_be_open=True)
        capture.digits = capture.digits[:-1]
        return capture

    async def stop(self, capture_id: str) -> Capture:
        capture = self._require(capture_id, must_be_open=True)
        capture.state = CaptureState.STOPPED
        capture.stopped_at = self._clock.now()
        self._open_by_call.pop(capture.call_session_id, None)
        await self._persist(capture)
        return capture

    async def discard(self, capture_id: str) -> Capture:
        """One click, and the digits are gone. The inverted default (`D44`)."""
        capture = self._require(capture_id)
        capture.digits = ""
        capture.state = CaptureState.DISCARDED
        capture.stopped_at = capture.stopped_at or self._clock.now()
        self._open_by_call.pop(capture.call_session_id, None)
        log.info("keypad capture discarded", capture_id=capture_id)
        await self._persist(capture)
        return capture

    # --- interpretation, which is optional and separate ----------------------------------

    async def label(self, capture_id: str, labelled_as: str) -> Capture:
        """The agent says what these digits were. Free text; it is their note, not ours."""
        capture = self._require(capture_id)
        capture.labelled_as = labelled_as
        # Naming the digits is what makes them storable in the clear (`D44`), so this
        # write is the moment `is_named` flips and the row gains its `digits` column.
        await self._persist(capture)
        return capture

    async def record_lookup(
        self,
        capture_id: str,
        *,
        kind: str,
        matched: bool,
        matched_value: str | None = None,
        detail: str | None = None,
    ) -> LookupResult:
        """Attach evidence to a capture.

        Returns the result and stores it. It changes **nothing** about identity — that is
        the entire discipline of `D44`, and the reason this service has no reference to
        assurance, the identity resolver, or the brief.
        """
        capture = self._require(capture_id)
        result = LookupResult(
            kind=kind,
            matched=matched,
            at=self._clock.now(),
            matched_value=matched_value,
            detail=detail,
        )
        capture.lookups.append(result)
        log.info("keypad lookup", capture_id=capture_id, kind=kind, matched=matched)
        await self._persist(capture)
        return result

    # --- reading --------------------------------------------------------------------------

    def get(self, capture_id: str) -> Capture | None:
        return self._captures.get(capture_id)

    def open_for(self, call_session_id: str) -> Capture | None:
        capture_id = self._open_by_call.get(call_session_id)
        return self._captures.get(capture_id) if capture_id else None

    def for_call(self, call_session_id: str) -> list[Capture]:
        return [c for c in self._captures.values() if c.call_session_id == call_session_id]

    async def restore(self, call_session_ids: Sequence[str]) -> int:
        """Reload captures for live calls. Returns how many.

        **A restored `OPEN` capture comes back `STOPPED`**, which is a statement of fact
        rather than a tidy-up: DTMF arrives on a call leg held by the telephony layer, and
        after a restart there is no listener on it. Leaving it open would also block the
        agent from starting a new one — the service refuses a second open capture per call
        — so the screen would show a capture that can never receive a digit and cannot be
        replaced.

        The digits of an unnamed capture do not come back at all, because they were never
        stored (`D44`). What survives is the audit fact: a capture happened, this long, and
        these lookups were run against it.
        """
        if self._store is None:
            return 0
        rows = await self._store.for_calls(call_session_ids)
        for capture in rows:
            if capture.state is CaptureState.OPEN:
                capture.state = CaptureState.STOPPED
                capture.stopped_at = capture.stopped_at or self._clock.now()
            self._captures[capture.capture_id] = capture
        if rows:
            log.info("keypad captures restored", captures=len(rows))
        return len(rows)

    async def _persist(self, capture: Capture) -> None:
        """Write-through at a milestone (`D78`), honouring `D44`'s inverted default.

        The stored row always carries the mask and the digit count. It carries the digits
        themselves **only once something has named them** — because with untyped capture we
        do not know what an unnamed run of digits is, and the safe assumption for an unknown
        number is that it is sensitive.
        """
        if self._store is None:
            return
        # The **policy** lives here; the store only carries it out. Every backend records
        # the mask and the digit count either way, so the audit fact — a capture of this
        # length happened — survives regardless of whether the value does.
        await self._store.save(capture, store_digits=capture.is_named)

    def _require(self, capture_id: str, *, must_be_open: bool = False) -> Capture:
        capture = self._captures.get(capture_id)
        if capture is None:
            raise PermanentError(f"unknown capture {capture_id}")
        if must_be_open and capture.state is not CaptureState.OPEN:
            raise PermanentError(f"capture {capture_id} is {capture.state}")
        return capture


__all__ = [
    "Capture",
    "CaptureState",
    "CaptureStore",
    "InMemoryCaptureStore",
    "KeypadCaptureService",
    "LookupResult",
    "mask",
]
