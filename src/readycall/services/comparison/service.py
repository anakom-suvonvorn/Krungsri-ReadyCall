"""Compare & best-fit: the broker's actual mandate, ranked on facts (`D126`).

Insight deck p.30 lists it as a broker's duty in so many words —
*"คัดสรรแบบประกันและบริษัทฯ ที่ตรงตามความต้องการ"*, select the plan **and the company**
(`D117`) — and the brief marks journey step 3 as the **LEAK สูงสุด**: too many options,
too slow, and it misses the point of what the customer actually needs.

Everything here is arithmetic. That is the design, not a limitation.

---

### The rule: rank on facts, explain with the model

`D16` says a coverage figure is **always data, never model output**. `D115` extends it to
the thing that made this feature worth building: the *ordering* comes from real attributes
too, and a model — when one is configured — writes only the **reason sentence** on top of
an ordering it did not choose. A model that ranks is a model that can be argued with; a
model that explains an arithmetic ranking cannot invent a plan into first place.

⚠️ **The model is not wired in yet.** The reason sentence below is composed from the same
differences the ranking used, which is honest and needs no key. The seam is
`Candidate.reason_th`, and replacing it is one call with the ranking untouched.

### Three rules the arithmetic obeys, each of which was a way to lie

1. **`None` means NOT STATED, never zero.** A plan silent on outpatient cover and a plan
   that excludes it are different products. A missing figure contributes **nothing** to
   the score in either direction and renders as "—" (`D125`).
2. **Improvement is clipped.** One enormous annual limit must not carry a plan that is
   worse on everything else — a comparison whose order is decided by a single outlier is
   a sales script with a table around it.
3. **Below `min_material_improvement`, "better" is not reported as better.** A 2% higher
   room rate is not a reason to change carrier, and presenting it as one is exactly how
   this feature would stop being advice.

### What it must never do

Quote a premium, or imply approval. Pricing and underwriting are **out of scope** (`D115`,
`D117`), and `Product` has no premium field to read even if this wanted one. Surface and
compare; the insurer decides and prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from readycall.domain.models import Coverage, Policy, Product
from readycall.domainpack import ComparisonAttribute, ComparisonLine, ComparisonSettings
from readycall.logging import get_logger

log = get_logger(__name__)

#: One plan cannot be more than this much "better" on a single attribute, however large
#: the real ratio is. Rule 2 above: without it, a 10x annual limit decides the table.
_MAX_IMPROVEMENT = 2.0
_MIN_IMPROVEMENT = -1.0


class Direction(StrEnum):
    """How a candidate's figure compares with what the customer already holds."""

    BETTER = "better"
    WORSE = "worse"
    SAME = "same"
    #: One side or the other does not state this figure. Deliberately its own value rather
    #: than folded into `same`: *"neither plan mentions outpatient cover"* and *"both cover
    #: it identically"* are different things to tell somebody choosing between them.
    NOT_STATED = "not_stated"


@dataclass(frozen=True, slots=True)
class Difference:
    """One attribute, on one candidate, against what the customer holds."""

    kind: str
    label_th: str
    held: float | None
    offered: float | None
    unit: str | None
    direction: Direction
    #: Relative change against what they hold, signed so that positive is always BETTER
    #: regardless of which way the attribute runs. Clipped (rule 2). `0.0` when either
    #: side is unstated — never a guess.
    improvement: float
    weight: float

    @property
    def is_material(self) -> bool:
        return self.direction in (Direction.BETTER, Direction.WORSE)


@dataclass(frozen=True, slots=True)
class Candidate:
    product: Product
    differences: tuple[Difference, ...]
    score: float
    reason_th: str

    @property
    def better_on(self) -> tuple[Difference, ...]:
        """Where it beats what they hold, biggest weighted gap first."""
        rows = [d for d in self.differences if d.direction is Direction.BETTER]
        return tuple(sorted(rows, key=lambda d: -(d.improvement * d.weight)))

    @property
    def worse_on(self) -> tuple[Difference, ...]:
        rows = [d for d in self.differences if d.direction is Direction.WORSE]
        return tuple(sorted(rows, key=lambda d: d.improvement * d.weight))


@dataclass(frozen=True, slots=True)
class Comparison:
    """A ranked answer to *"is what I have still the right cover?"*"""

    line: str
    line_label_th: str
    held: Policy | None
    held_label_th: str
    candidates: tuple[Candidate, ...]
    attributes: tuple[ComparisonAttribute, ...]

    @property
    def is_empty(self) -> bool:
        return not self.candidates


def _amount(rows: tuple[Coverage, ...], kind: str) -> tuple[float | None, str | None]:
    for row in rows:
        if row.kind == kind:
            return row.amount, row.unit
    return None, None


def _improvement(attribute: ComparisonAttribute, held: float, offered: float) -> float:
    """Signed so positive is always BETTER, whichever way the attribute runs."""
    base = max(abs(held), 1.0)
    raw = (offered - held) / base
    if not attribute.higher_is_better:
        raw = -raw
    return max(_MIN_IMPROVEMENT, min(_MAX_IMPROVEMENT, raw))


class ComparisonService:
    """Builds one ranked comparison. No I/O: the catalogue is handed in.

    That is deliberate and it is `D10`'s shape — the thing that decides is testable with a
    list of plans and no adapter, no network and no configuration, which is why the rules
    above can each have a test that fails when they are broken.
    """

    def __init__(self, *, settings: ComparisonSettings) -> None:
        self._settings = settings

    def build(
        self,
        *,
        line: str,
        spec: ComparisonLine,
        held: Policy | None,
        catalogue: list[Product],
    ) -> Comparison:
        held_code = held.product_code if held else None
        # The plan they already hold is not a candidate to switch to. It is the column
        # everything else is measured against, and offering it as an alternative is how a
        # comparison table recommends the status quo it was asked to challenge.
        candidates = [p for p in catalogue if p.product_code != held_code and p.is_active]

        rows = [
            (
                product,
                tuple(self._difference(attribute, held, product) for attribute in spec.attributes),
            )
            for product in candidates
        ]

        # Two different questions, two different sums (`D126`).
        #
        # With a policy in hand the question is *"is this better than what I have?"*, and
        # the answer is the weighted improvement against it.
        #
        # With **no** policy — new business, which `D123` just made reachable from the app
        # — there is nothing to improve on, so every difference is `NOT_STATED` and every
        # score would be exactly zero. That is not a tie: it is no ranking at all, and the
        # order then falls back to whatever the catalogue happened to be in, which is
        # precisely how an affiliated carrier ends up silently first. So the candidates
        # are ranked against **each other** instead, per attribute, min-max normalised.
        if held is not None:
            scores = [sum(d.improvement * d.weight for d in diffs) for _, diffs in rows]
        else:
            scores = self._relative_scores([diffs for _, diffs in rows], spec)

        scored = [
            Candidate(
                product=product,
                differences=diffs,
                score=score,
                reason_th="",  # filled below, once the ordering is known
            )
            for (product, diffs), score in zip(rows, scores, strict=True)
        ]

        scored.sort(key=lambda c: (-c.score, c.product.product_code))
        top = scored[: self._settings.max_candidates]
        top = [
            Candidate(
                product=c.product,
                differences=c.differences,
                score=c.score,
                reason_th=self._reason(c, held=held),
            )
            for c in top
        ]

        return Comparison(
            line=line,
            line_label_th=spec.label_th,
            held=held,
            held_label_th="แผนปัจจุบันของคุณ" if held else "ยังไม่มีความคุ้มครอง",
            candidates=tuple(top),
            attributes=spec.attributes,
        )

    @staticmethod
    def _relative_scores(rows: list[tuple[Difference, ...]], spec: ComparisonLine) -> list[float]:
        """Rank candidates against each other when there is nothing they improve on.

        Min-max per attribute, so a plan is scored 0..1 on where it sits between the worst
        and best figure **among the plans actually offered** — then weighted, and the
        direction applied. A plan that does not state a figure scores 0 on it rather than
        being guessed at, which is rule 1 in the other branch too.

        An attribute every candidate states identically contributes nothing to anybody,
        because a spread of zero carries no information about which to choose.
        """
        totals = [0.0 for _ in rows]
        for index, attribute in enumerate(spec.attributes):
            values = [row[index].offered for row in rows]
            stated = [v for v in values if v is not None]
            if len(stated) < 2:
                continue
            low, high = min(stated), max(stated)
            if high == low:
                continue
            for candidate_index, value in enumerate(values):
                if value is None:
                    continue
                position = (value - low) / (high - low)
                if not attribute.higher_is_better:
                    position = 1.0 - position
                totals[candidate_index] += position * attribute.weight
        return totals

    def _difference(
        self, attribute: ComparisonAttribute, held: Policy | None, product: Product
    ) -> Difference:
        offered, offered_unit = _amount(product.coverages, attribute.kind)
        held_amount, held_unit = _amount(held.coverages, attribute.kind) if held else (None, None)

        if held_amount is None or offered is None:
            # Rule 1. Neither side may be guessed at, and a plan that does not mention a
            # cover contributes nothing rather than losing on it.
            return Difference(
                kind=attribute.kind,
                label_th=attribute.label_th,
                held=held_amount,
                offered=offered,
                unit=offered_unit or held_unit,
                direction=Direction.NOT_STATED,
                improvement=0.0,
                weight=attribute.weight,
            )

        improvement = _improvement(attribute, held_amount, offered)
        floor = self._settings.min_material_improvement
        if abs(improvement) < floor:
            # Rule 3: a 2% difference is not a finding. It still renders in the table —
            # the customer can see both numbers — it simply is not called an improvement.
            direction = Direction.SAME
        else:
            direction = Direction.BETTER if improvement > 0 else Direction.WORSE

        return Difference(
            kind=attribute.kind,
            label_th=attribute.label_th,
            held=held_amount,
            offered=offered,
            unit=offered_unit or held_unit,
            direction=direction,
            improvement=improvement,
            weight=attribute.weight,
        )

    def _reason(self, candidate: Candidate, *, held: Policy | None) -> str:
        """One sentence, composed from the differences the ranking already used.

        ⚠️ **This is the seam a model takes over** (`D115`, `D16`): the ordering stays
        arithmetic and the model rewrites only this string. Until then it is generated,
        which is duller and cannot invent anything.

        It names what is **worse** as well as what is better. A comparison that lists only
        the upside is a sales script, and the plan ranked first here is frequently the one
        with a deductible attached.
        """
        product = candidate.product
        if held is None:
            best = max(candidate.differences, key=lambda d: d.weight, default=None)
            head = f"{product.name_th} จาก{product.insurer or 'บริษัทผู้รับประกัน'}"
            if best is not None and best.offered is not None:
                return f"{head} — {best.label_th} {_money(best.offered, best.unit)}"
            return head

        better = candidate.better_on
        worse = candidate.worse_on
        parts: list[str] = []
        if better:
            top = better[0]
            parts.append(
                f"{top.label_th}สูงกว่าแผนปัจจุบัน "
                f"({_money(top.held, top.unit)} → {_money(top.offered, top.unit)})"
            )
        if worse:
            first = worse[0]
            parts.append(
                f"แต่{first.label_th} {_money(first.held, first.unit)} → "
                f"{_money(first.offered, first.unit)}"
            )
        if not parts:
            return f"{product.name_th} — ความคุ้มครองใกล้เคียงกับแผนปัจจุบัน"
        return f"{product.name_th} จาก{product.insurer or ''} — " + " ".join(parts)


_UNIT_TH = {
    "per_day": "ต่อวัน",
    "per_year": "ต่อปี",
    "per_visit": "ต่อครั้ง",
    "per_accident": "ต่อครั้ง",
    "percent": "%",
}


def _money(amount: float | None, unit: str | None) -> str:
    """A figure as the customer reads it, or an em dash when we do not hold one.

    **Never `0` for a missing value** — that is a statement about the plan, and it is the
    one this whole module exists not to make (`D125`).
    """
    if amount is None:
        return "—"
    if unit == "percent":
        return f"{amount:,.0f}%"
    text = f"{amount:,.0f} บาท"
    suffix = _UNIT_TH.get(unit or "")
    return f"{text}{suffix}" if suffix else text


def to_push_payload(comparison: Comparison, *, include_held: bool = True) -> dict[str, object]:
    """The table as the customer's paired screen renders it (`D120`).

    **Built on the server**, which is the point: until now `compare.plans` pushed whatever
    payload the client sent, so the figures on a customer's phone would have been composed
    in a browser. Coverage numbers are read from the record, never assembled by a client
    (`D16`) — the same argument as `D121`'s `personal` flag, applied to content instead of
    permission.

    ⚠️ **`include_held` is a disclosure gate, not a layout option** (`D126`, `D120`).

    `compare.plans` is declared `personal: false`, and it should be: comparing what the
    market offers is true for anybody, and gating it would wall off the one part of this
    journey with no privacy cost at all. But the moment the table gains a column headed
    *"แผนปัจจุบันของคุณ"* carrying real coverage figures, **that column is about one
    person** — and a screen that has only tapped a link has proved possession of a phone,
    not identity (`D42`).

    So the tool stays guest-safe and the *column* is what moves: a guest sees the market,
    a signed-in customer sees the market **against their own cover**. That is `D121`'s
    correction — look at the purpose of what is being pushed, not the shape of it —
    arriving inside a single push rather than between two tools.
    """
    show_held = include_held and comparison.held is not None

    columns = ["ความคุ้มครอง"]
    if show_held:
        columns.append(comparison.held_label_th)
    columns.extend(c.product.name_th for c in comparison.candidates)

    rows: list[dict[str, object]] = []
    for index, attribute in enumerate(comparison.attributes):
        cells = [attribute.label_th]
        values: list[float | None] = []
        unit: str | None = None

        if show_held:
            assert comparison.held is not None  # narrowed by `show_held`
            held_amount, held_unit = _amount(comparison.held.coverages, attribute.kind)
            unit = unit or held_unit
            values.append(held_amount)

        for candidate in comparison.candidates:
            difference = candidate.differences[index]
            unit = unit or difference.unit
            values.append(difference.offered)

        cells.extend(_money(v, unit) for v in values)

        # `best_index` is into the CELL list, and cell 0 is the label — so it is offset by
        # one. Getting that wrong highlights the row's name instead of a figure, which
        # looks like a rendering glitch rather than a wrong answer.
        stated = [(i, v) for i, v in enumerate(values) if v is not None]
        best_index: int | None = None
        if stated:
            pick = max if attribute.higher_is_better else min
            best_index = pick(stated, key=lambda pair: pair[1])[0] + 1

        row: dict[str, object] = {"cells": cells}
        if best_index is not None:
            row["best_index"] = best_index
        rows.append(row)

    return {
        "columns_th": columns,
        "rows": rows,
        # Said out loud rather than left as an absence: a guest looking at a market
        # comparison should know their own plan is missing because they are not signed
        # in, not conclude we do not hold it.
        "held_hidden": bool(comparison.held is not None and not include_held),
        # Says what the table is and what it is NOT. The brief puts pricing out of scope
        # and a customer reading a comparison will assume a price is implied unless told.
        "note_th": (
            "เปรียบเทียบความคุ้มครองตามข้อมูลกรมธรรม์ ไม่ใช่ใบเสนอราคา — "
            "เบี้ยประกันขึ้นกับการพิจารณาของบริษัทประกันแต่ละแห่ง"
        ),
    }
