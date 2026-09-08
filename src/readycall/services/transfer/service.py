"""Handing a call to another company — `D117`'s banner becoming something the broker DOES.

`D117` split the duties from Krungsri's own slide: the insurer underwrites, rules on
coverage and **pays claims**; the broker analyses needs, selects the plan *and the
company*, services the policy and chases renewals. Every intent whose work belongs to the
insurer already carries `handoff_to_insurer: true`, and since `D117` that reaches the
broker's screen as a banner **before they speak**, because *"I'll check and call you
back"* and *"I'm passing you to the insurer now"* are different promises and only one of
them is a broker's to make.

Until now the banner was all there was. The broker read it, said the sentence, pressed
วางสาย, and typed whatever they remembered into the wrap-up. This service is the other
half: **who** it went to, **why**, and a disposition the agent does not have to compose.

---

### Why this does not use `CallState.TRANSFERRED`

It is tempting: the state exists, it is entered by nothing, and "the call left us" is
exactly what it sounds like. Two reasons not to (`D124`).

`TRANSFERRED` is **terminal**, so a call in it can never reach `WRAP_UP` — and after-call
work on a handoff is real work: what was gathered, what the insurer will need, what the
customer was told. Making it non-terminal instead would give the system **two states that
both mean "the media is over and the agent is filing"**, and every reader of "is this
agent in after-call work" would have to check both. That is the `B25`/`B26` shape — two
places answering one question, which eventually disagree in silence.

So a handoff ends the call the ordinary way, `IN_CALL -> WRAP_UP`, and what makes it a
handoff rather than a hang-up is the record: the transition reason, an event, and a
disposition the agent confirms.

### Why nothing is written until the agent files it

`D45`: nothing auto-saves on the person's behalf. The record here is a **suggestion**
held for the length of the call — the durable statement is the `call_wrapups` row the
broker actually submits, because the broker is the one who made the promise to the
customer. ⚠️ That leaves this in the same family as `Q34`: a real product wants a
`call_handoffs` table with a retention rule (`D14`), and this dies with the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from readycall.clock import Clock, SystemClock
from readycall.domainpack import HandoffReasonSpec, InsurerSpec
from readycall.errors import PermanentError
from readycall.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HandoffRecord:
    """One call handed to one company, for one reason."""

    call_session_id: str
    agent_id: str
    insurer_name_th: str
    reason_code: str
    reason_label_th: str
    at: datetime
    #: `None` when the carrier came from the customer's own policy rather than from
    #: `insurers.yaml`. That is not a gap: the record beats the menu, and on hackathon day
    #: the real extract carries carriers nobody has typed into config (`D124`).
    insurer_code: str | None = None
    note: str = ""

    @property
    def disposition_th(self) -> str:
        """What the wrap-up form is prefilled with.

        A suggestion, not an entry. The broker edits or replaces it and presses save —
        `was_edited` on `WrapupSaved` already distinguishes the two, so a metric can tell
        a confirmed disposition from an accepted default.
        """
        line = f"ส่งต่อ {self.insurer_name_th} — {self.reason_label_th}"
        return f"{line}\n{self.note}".strip() if self.note else line


class TransferService:
    """Where a call goes when it is not staying with this broker.

    Holds the external half (`D124`). The internal half — `D63`'s consulted transfer to
    another desk — is a different mechanism entirely and belongs here when it is built:
    that one keeps the call `IN_CALL` because the customer is still talking to us, and
    this one ends it because they are not.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._by_call: dict[str, HandoffRecord] = {}

    def record_handoff(
        self,
        call_session_id: str,
        *,
        agent_id: str,
        insurer_name_th: str,
        reason: HandoffReasonSpec,
        insurer_code: str | None = None,
        note: str = "",
        holds_policy: bool = True,
    ) -> HandoffRecord:
        """Record who this call is going to and why. Does **not** end the call.

        Ending it is the caller's next step and belongs to `AssignmentService`, which owns
        the ACW clock (`D45`). Two calls rather than one, because the record must exist
        before the call ends: the wrap-up form renders the instant the state changes, and
        a prefill that arrives afterwards is a prefill nobody sees.
        """
        if not insurer_name_th.strip():
            raise PermanentError("a handoff must name the company it is going to")
        if reason.requires_policy and not holds_policy:
            # The reason is incoherent without cover: there is no claim to adjudicate and
            # no policy wording to rule on. Offering it anyway would let a broker file a
            # handoff naming a company that never wrote anything for this customer.
            raise PermanentError(
                f"{reason.label_th} เป็นเรื่องของกรมธรรม์ที่ลูกค้าถืออยู่ "
                "(this reason needs a policy we can see)"
            )
        record = HandoffRecord(
            call_session_id=call_session_id,
            agent_id=agent_id,
            insurer_name_th=insurer_name_th.strip(),
            reason_code=reason.code,
            reason_label_th=reason.label_th,
            at=self._clock.now(),
            insurer_code=insurer_code,
            note=note.strip(),
        )
        self._by_call[call_session_id] = record
        log.info(
            "call handed to insurer",
            call_session_id=call_session_id,
            agent_id=agent_id,
            insurer=insurer_code or "from_policy",
            reason=reason.code,
        )
        return record

    def handoff_for(self, call_session_id: str) -> HandoffRecord | None:
        return self._by_call.get(call_session_id)

    def forget(self, call_session_id: str) -> None:
        """Called when the wrap-up is filed: the suggestion has served its purpose."""
        self._by_call.pop(call_session_id, None)

    @staticmethod
    def offerable_insurers(
        catalogue: tuple[InsurerSpec, ...], *, policy_insurer: str | None
    ) -> tuple[str, ...]:
        """The menu, with the customer's own carrier first and never missing.

        `Policy.insurer` is a fact about this customer; `insurers.yaml` is a list somebody
        typed. When they disagree the record wins — a broker who cannot hand a claim to
        the company that actually wrote the policy has no product (`D124`).
        """
        names = [spec.name_th for spec in catalogue]
        if policy_insurer and policy_insurer not in names:
            return (policy_insurer, *names)
        if policy_insurer:
            return (policy_insurer, *[n for n in names if n != policy_insurer])
        return tuple(names)
