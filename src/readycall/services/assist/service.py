"""Binding a phone call to the customer's screen, and pushing things onto it (`D120`).

**The problem.** A broker on the phone says *"go to the website, tap the menu at the top
right, then Documents, then Upload"*. The brief prices this: step 4 of the journey leaks
because *เอกสารเยอะ ลูกค้า drop-off กลางทาง*. The customer who suffers most is the one
least able to follow spoken navigation.

**The answer is to stop describing a screen and start filling it.** Once the call and the
screen are bound, the broker pushes; the customer never navigates.

### How the binding is established, and why it is a link rather than a code

The phone number is already the strongest binding available: the call is on it, and ANI
tells us which. So a link *sent to the number we are already talking to* is
self-authenticating to exactly the level the ANI itself is worth (`D20`'s L1 — probable,
not verified), and costs the customer one tap. A read-back code is *stronger* — it proves
they are looking at the screen AND on the call — and costs a distressed person a chore.
We take the link, and let assurance do the rest.

Three entry paths, one destination:

* **In-app call.** The pairing is implicit: the app started the call and carries the
  correlation token already (`D6`). Nothing to establish.
* **Phone call, then a link.** The broker presses *send link*; whoever taps it is holding
  the phone we are on.
* **Already in the app, called separately.** The only case needing a prompt — and the app
  is *told*, rather than showing a button nobody can find. See `AssistService.live_for`.

### Two tiers, and they are `D74`'s rule applied to the customer's own screen

| tier | reached by | what may be pushed |
|---|---|---|
| `GUEST` | tapping the link | things true for anybody: plan comparisons, product
  information, a document checklist, how-to steps |
| `VERIFIED` | signing in | things about *them*: their policies, a prefilled form,
  an upload, a signature |

That split is the whole answer to *"do we have to make them register?"* — **no**, not to
receive help. A customer comparing plans gets everything they need without an account, and
the wall only appears at the point where the thing on screen is personal. Putting
registration first would gate the part that has no privacy cost at all.

### What this service does not do

It does not send the SMS or the LINE message — that is `NotifierPort`'s job at P5, and
until then the link is returned to the agent to read out or copy. It stores nothing
durably: a pairing dies with the call, which is the correct retention for a token whose
only purpose is one conversation (`D14`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from readycall.clock import Clock, SystemClock
from readycall.errors import PermanentError
from readycall.ids import generate as new_id
from readycall.logging import get_logger

log = get_logger(__name__)

#: A pairing outlives the call by a few minutes on purpose: a form the customer is halfway
#: through when the broker hangs up should still submit. It is not a login.
PAIRING_GRACE = timedelta(minutes=10)


class AssistTier(StrEnum):
    """What the paired screen is allowed to be shown."""

    GUEST = "guest"
    VERIFIED = "verified"


class PushKind(StrEnum):
    """What a broker can put on the customer's screen.

    Closed on purpose, the same reason the intent taxonomy is (`D28`): the customer's
    page renders a known set of shapes, and an unknown `kind` is a blank panel on
    somebody's phone mid-call.
    """

    INFO = "info"
    COMPARISON = "comparison"
    FORM = "form"
    DOCUMENT_REQUEST = "document_request"
    NAVIGATE = "navigate"


#: Which pushes are about *this customer* and therefore need a signed-in screen. A
#: comparison table and a how-to are true for anybody; a prefilled form is not.
_NEEDS_VERIFIED = {PushKind.FORM, PushKind.DOCUMENT_REQUEST}


@dataclass(frozen=True, slots=True)
class PushedItem:
    item_id: str
    kind: PushKind
    title_th: str
    payload: dict[str, Any]
    pushed_at: datetime
    #: Filled when the customer sends something back. A form the customer has submitted is
    #: not removed from their screen - they should still be able to see what they sent.
    response: dict[str, Any] | None = None
    responded_at: datetime | None = None


@dataclass(slots=True)
class AssistSession:
    """One paired screen, for one call."""

    token: str
    call_session_id: str
    created_at: datetime
    expires_at: datetime
    tier: AssistTier = AssistTier.GUEST
    customer_id: str | None = None
    paired_at: datetime | None = None
    items: list[PushedItem] = field(default_factory=list)

    @property
    def is_paired(self) -> bool:
        return self.paired_at is not None


class AssistService:
    """Mints pairings, holds what was pushed, and takes what comes back."""

    def __init__(self, *, clock: Clock | None = None, base_path: str = "/assist") -> None:
        self._clock = clock or SystemClock()
        self._base_path = base_path
        self._by_token: dict[str, AssistSession] = {}
        self._by_call: dict[str, str] = {}

    # --- pairing ---------------------------------------------------------------------

    def open_for_call(
        self, call_session_id: str, *, customer_id: str | None = None, ttl_s: float = 1800.0
    ) -> AssistSession:
        """Mint (or return) the pairing for a call. Idempotent per call.

        Idempotent because the broker may press *send link* twice — once because the SMS
        was slow and once because the customer said they had not got it — and two live
        tokens for one call would put half the pushes on a screen nobody is looking at.
        That is `D110`'s `open_leg` lesson in a new place.
        """
        existing = self._by_call.get(call_session_id)
        if existing is not None:
            session = self._by_token[existing]
            if session.expires_at > self._clock.now():
                return session

        now = self._clock.now()
        session = AssistSession(
            token=new_id("ast"),
            call_session_id=call_session_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_s),
            customer_id=customer_id,
        )
        self._by_token[session.token] = session
        self._by_call[call_session_id] = session.token
        log.info("assist pairing opened", call_session_id=call_session_id, token=session.token[:12])
        return session

    def link_for(self, session: AssistSession) -> str:
        return f"{self._base_path}/{session.token}"

    def pair(self, token: str) -> AssistSession:
        """The customer opened the link. Raises if it is unknown or expired."""
        session = self._by_token.get(token)
        if session is None:
            raise PermanentError("this link is not valid")
        if session.expires_at <= self._clock.now():
            raise PermanentError("this link has expired")
        if session.paired_at is None:
            session.paired_at = self._clock.now()
            log.info(
                "assist screen paired",
                call_session_id=session.call_session_id,
                tier=str(session.tier),
            )
        return session

    def sign_in(self, token: str, *, customer_id: str) -> AssistSession:
        """The customer signed in, so personal things may now be pushed.

        This is the ONLY way to reach `VERIFIED`. A pairing established by tapping a link
        is worth exactly what the phone number is worth, and `D42`'s whole argument is
        that possession of a device is not proof of identity.
        """
        session = self.pair(token)
        session.customer_id = customer_id
        session.tier = AssistTier.VERIFIED
        log.info("assist screen verified", call_session_id=session.call_session_id)
        return session

    def for_call(self, call_session_id: str) -> AssistSession | None:
        token = self._by_call.get(call_session_id)
        return self._by_token.get(token) if token else None

    def live_for(self, customer_id: str, live_call_ids: set[str]) -> AssistSession | None:
        """Is this signed-in customer on a call right now?

        This is what lets the app *tell* somebody that help is available instead of
        showing a permanently visible "let an agent help me" button — which would be odd
        on a screen nobody is calling from, and which the customer would have to go
        looking for at exactly the moment they are least able to.
        """
        for call_session_id, token in self._by_call.items():
            if call_session_id not in live_call_ids:
                continue
            session = self._by_token.get(token)
            if session and session.customer_id == customer_id:
                return session
        return None

    # --- pushing ---------------------------------------------------------------------

    def push(
        self,
        call_session_id: str,
        *,
        kind: PushKind,
        title_th: str,
        payload: dict[str, Any] | None = None,
    ) -> PushedItem:
        """Put something on the paired screen.

        Refuses a personal push to a screen that has only tapped a link. The refusal is
        the feature: the broker sees *why* and asks the customer to sign in, instead of a
        stranger's policy appearing on whoever is holding that phone.
        """
        session = self.for_call(call_session_id)
        if session is None or not session.is_paired:
            raise PermanentError("no paired screen for this call")
        if kind in _NEEDS_VERIFIED and session.tier is not AssistTier.VERIFIED:
            raise PermanentError(
                f"{kind} needs a signed-in screen; ask the customer to sign in first"
            )
        item = PushedItem(
            item_id=new_id("psh"),
            kind=kind,
            title_th=title_th,
            payload=payload or {},
            pushed_at=self._clock.now(),
        )
        session.items.append(item)
        log.info(
            "pushed to the customer screen",
            call_session_id=call_session_id,
            kind=str(kind),
            tier=str(session.tier),
        )
        return item

    def respond(self, token: str, *, item_id: str, response: dict[str, Any]) -> PushedItem:
        """The customer sent something back — a filled form, an acknowledgement."""
        session = self.pair(token)
        for index, item in enumerate(session.items):
            if item.item_id != item_id:
                continue
            updated = PushedItem(
                item_id=item.item_id,
                kind=item.kind,
                title_th=item.title_th,
                payload=item.payload,
                pushed_at=item.pushed_at,
                response=response,
                responded_at=self._clock.now(),
            )
            session.items[index] = updated
            log.info(
                "customer responded",
                call_session_id=session.call_session_id,
                kind=str(item.kind),
                fields=sorted(response),
            )
            return updated
        raise PermanentError("unknown item")

    def close(self, call_session_id: str) -> None:
        """The call ended. The pairing outlives it briefly, then goes.

        A form somebody is halfway through when the broker hangs up should still submit;
        a token that lived for the rest of the day would be a standing key to a screen.
        """
        token = self._by_call.get(call_session_id)
        if token is None:
            return
        session = self._by_token.get(token)
        if session is not None:
            session.expires_at = min(session.expires_at, self._clock.now() + PAIRING_GRACE)

    def sweep(self) -> int:
        """Drop expired pairings. Driven by the same sweep as everything else (`B7`)."""
        now = self._clock.now()
        dead = [t for t, s in self._by_token.items() if s.expires_at <= now]
        for token in dead:
            session = self._by_token.pop(token)
            if self._by_call.get(session.call_session_id) == token:
                self._by_call.pop(session.call_session_id, None)
        return len(dead)


__all__ = [
    "PAIRING_GRACE",
    "AssistService",
    "AssistSession",
    "AssistTier",
    "PushKind",
    "PushedItem",
]
