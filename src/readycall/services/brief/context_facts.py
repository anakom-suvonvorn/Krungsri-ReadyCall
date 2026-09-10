"""*"What else do we know about this person"* — the facts, worded once, on the server.

`D134`. Everything here was **already assembled on every call and thrown away.**
`ContextAssembler` reads holdings, life events, interactions and claims into the frozen
snapshot; `BriefOut` exposed two counts and one sentence. This turns the rest into
something a broker can read while somebody is talking to them.

Three rules, and each of them is a line somebody could have crossed:

* **It states facts and never recommends** (`D116`). Consent is required before
  *personalised recommendation*; displaying a record an affiliate lawfully shared is
  internal processing (`D74`). "They took a home loan in 2022" is a fact. "So offer them
  mortgage protection" is a recommendation, and it belongs on the far side of a consent
  check this module deliberately does not make.
* **Every fact names its source** (`D18`). A broker who cannot say where something came
  from cannot use it in a conversation — and the difference between a *record* and an
  *inference* is the difference between "you took a loan with us" and "we think you may
  have moved house".
* **No number is invented and none is computed.** A balance band is rendered as the band
  the upstream gave; nothing here adds, converts or estimates. `D16`, in the panel most
  likely to tempt somebody into a total.

The wording lives here rather than in the client for `D68`'s reason: the server knows
these facts, so the server says them, and a screen that re-worded them would be a second
version that drifts.
"""

from __future__ import annotations

from datetime import datetime

from readycall.domain.models import Customer360

#: How a life-event signal reads to a broker. Unknown signals fall through to the raw
#: code rather than being hidden: a signal we have not worded yet is still a fact, and
#: silently dropping it would make the panel quietly incomplete.
_LIFE_EVENT_TH: dict[str, str] = {
    "mortgage": "เพิ่งมีสินเชื่อบ้าน",
    "new_child": "เพิ่งมีบุตร",
    "job_change": "เปลี่ยนงาน / รายได้เปลี่ยนรูปแบบ",
    "relocation": "ย้ายที่อยู่",
}

_HOLDING_TH: dict[str, str] = {
    "deposit": "บัญชีเงินฝาก",
    "loan": "สินเชื่อ",
    "card": "บัตรเครดิต",
    "fund": "กองทุน",
}

#: The product line, for a policy the broker is NOT already looking at (`D140`).
_LINE_TH: dict[str, str] = {
    "health": "ประกันสุขภาพ",
    "motor": "ประกันรถยนต์",
    "life": "ประกันชีวิต",
    "travel": "ประกันเดินทาง",
    "home": "ประกันบ้าน",
    "accident": "ประกันอุบัติเหตุ",
}

_CHANNEL_TH: dict[str, str] = {
    "call": "โทรศัพท์",
    "chat": "แชท",
    "branch": "สาขา",
    "email": "อีเมล",
    "app": "แอป",
}


def _thai_date(value: datetime | object | None) -> str | None:
    """`2022-08-30` -> `30/08/2565`. Buddhist era, because that is what a Thai broker reads.

    Formatted here rather than in the client so there is one answer (`D68`) — and because
    a client computing an era offset is a second place to get a calendar wrong.
    """
    if value is None:
        return None
    year = getattr(value, "year", None)
    month = getattr(value, "month", None)
    day = getattr(value, "day", None)
    if year is None or month is None or day is None:
        return None
    return f"{day:02d}/{month:02d}/{year + 543}"


class KnownFact:
    """One thing we already know. A plain object; the DTO is built from it at the wire."""

    __slots__ = (
        "at_th",
        "confidence",
        "detail_prompt_th",
        "detail_th",
        "kind",
        "label_th",
        "source",
    )

    def __init__(
        self,
        *,
        kind: str,
        label_th: str,
        source: str,
        detail_th: str | None = None,
        at_th: str | None = None,
        confidence: float | None = None,
        detail_prompt_th: str | None = None,
    ) -> None:
        self.kind = kind
        self.label_th = label_th
        self.detail_th = detail_th
        #: What the MODEL is shown, when that must be less than what the screen shows
        #: (`D140`). Defaults to the same string, because for almost every fact they are
        #: the same thing and a second field nobody sets is a second thing to forget.
        self.detail_prompt_th = detail_prompt_th if detail_prompt_th is not None else detail_th
        self.at_th = at_th
        self.source = source
        self.confidence = confidence


def known_facts(payload: Customer360, *, max_interactions: int = 4) -> list[KnownFact]:
    """Everything worth showing, most decision-relevant first.

    **The ORDER is the product decision here.** Life events come first because they are the
    only thing on this panel that answers *"why now"* — the challenge statement's
    **right time** — and because they are the one class of fact the broker could not have
    guessed from the policy list they are already looking at.

    **The customer's OTHER policies follow** (`D140`), because a broker's second question
    after *"why now"* is *"what else do they hold, and with whom"* — and the brief answers
    that with a bare count. Then bank holdings, then the conversation history, which is
    context rather than a trigger.

    Two things are deliberately **not** repeated here, for one reason: they are already on
    the screen in full, and a second rendering of one fact is how two parts of a screen come
    to disagree. Those are **claims** (`recent_claim_count` plus the policy panel) and
    **the relevant policy itself**, which the brief renders with its carrier and sum
    insured directly above this panel.
    """
    facts: list[KnownFact] = []

    for event in sorted(payload.life_events, key=lambda e: e.detected_at, reverse=True):
        facts.append(
            KnownFact(
                kind="life_event",
                label_th=_LIFE_EVENT_TH.get(event.signal, event.signal),
                at_th=_thai_date(event.detected_at),
                source=f"core:life_events · {event.source or 'ไม่ระบุที่มา'}",
                # ⚠️ Carried, not hidden. A life-event signal is INFERRED — `income_pattern`
                # at 0.6 is a guess and `loan_origination` at 0.95 is nearly a record — and
                # a broker who cannot tell them apart will say the wrong one out loud.
                confidence=event.confidence,
            )
        )

    # ⚠️ **The OTHER policies, and never the one already on screen** (`D140`).
    #
    # The brief renders `relevant_policy` in full and then says `· อีก 2 ฉบับ` — a
    # **count**, with no way to tell which. For a broker that is the wrong half: the whole
    # point of `D117` is that this customer holds cover across several carriers, and
    # "which company underwrote what" is the thing they cannot guess and cannot ask
    # without admitting they do not know.
    #
    # Excluding the relevant policy is the same rule that excludes claims here: it is
    # already on the screen in full, and two renderings of one fact is how two halves of a
    # screen come to disagree (`D134`).
    relevant_no = payload.relevant_policy.policy_no if payload.relevant_policy else None
    for policy in payload.active_policies:
        if policy.policy_no == relevant_no:
            continue
        line_th = _LINE_TH.get(str(policy.line), str(policy.line))
        carrier = policy.insurer or "ไม่ทราบบริษัทผู้รับประกัน"
        detail = carrier
        if policy.sum_insured is not None:
            # The figure exactly as the record holds it. Nothing here sums two policies or
            # converts a currency (`D16`).
            detail += f" · ทุนประกัน {policy.sum_insured:,.0f} บาท"
        facts.append(
            KnownFact(
                kind="policy",
                label_th=line_th,
                detail_th=detail,
                # ⚠️ **The model is shown the carrier and NOT the sum insured** (`D140`).
                # The prompt's own rule 3 already forbids it from stating any amount — so
                # handing it one is input it is instructed never to use, and the only
                # thing that can come of it is a copy that trips `_FIGURE` and throws the
                # whole summary away. The screen still shows the figure; the sentence
                # beside it was never allowed to say it.
                detail_prompt_th=carrier,
                # The renewal date, not the start date: "when does this lapse" is the
                # question a broker acts on, and it is what makes this a *timing* fact
                # rather than a list.
                at_th=_thai_date(policy.expiry_date or policy.effective_date),
                source="core:policies",
            )
        )

    for holding in payload.holdings:
        if holding.status and holding.status != "active":
            continue
        facts.append(
            KnownFact(
                kind="holding",
                label_th=_HOLDING_TH.get(holding.kind, holding.kind),
                # The band exactly as the upstream gave it. Nothing here converts a band
                # into a number or sums two of them (`D16`).
                detail_th=holding.balance_band,
                at_th=_thai_date(holding.opened_at),
                source="core:financial_products",
            )
        )

    for interaction in payload.recent_interactions[:max_interactions]:
        who = _CHANNEL_TH.get(interaction.channel, interaction.channel)
        facts.append(
            KnownFact(
                kind="interaction",
                label_th=interaction.summary or interaction.topic or "ติดต่อเข้ามา",
                detail_th=f"ช่องทาง{who}"
                + (f" · {interaction.outcome}" if interaction.outcome else ""),
                at_th=_thai_date(interaction.occurred_at),
                source="core:interactions",
            )
        )

    return facts


def facts_for_prompt(facts: list[KnownFact]) -> str:
    """The same facts as plain lines, for the model to summarise (`D134`, `D140`).

    One flat list with the source on every line, so a model cannot quietly promote an
    inference into a statement — and so **the prompt has nothing in it the panel does not
    also show**. A summary built from more than the screen displays is unverifiable by the
    person reading it.

    ⚠️ The reverse is allowed and is what `detail_prompt_th` is for: **the panel may show
    more than the prompt does.** A sum insured belongs on the screen and has no business in
    a prompt whose own rules forbid stating an amount — sending it can only produce a copy
    that trips `_FIGURE` and throws the whole summary away (`D140`).
    """
    lines: list[str] = []
    for fact in facts:
        parts = [fact.label_th]
        if fact.detail_prompt_th:
            parts.append(fact.detail_prompt_th)
        if fact.at_th:
            parts.append(f"เมื่อ {fact.at_th}")
        if fact.confidence is not None:
            parts.append(f"ความมั่นใจ {fact.confidence:.2f}")
        parts.append(f"[{fact.source}]")
        lines.append("- " + " · ".join(parts))
    return "\n".join(lines)


__all__ = ["KnownFact", "facts_for_prompt", "known_facts"]
