"""The model writes the comparison's reason sentence, and nothing else (`D137`, `D126`).

`D126` split this feature deliberately: **the ordering is arithmetic over weights in
config, and a model rewrites only the sentence sitting beside it.** A model that ranks is a
model that can be argued into putting a plan first; a model that explains an arithmetic
ranking cannot. `ComparisonService.reason_th` has been the seam since that decision, filled
by a generated sentence that is duller and cannot invent anything.

This fills it, and keeps the ranking untouched — the service that computes the order never
learns this module exists.

### Four guards, and the first one is the whole design

1. **EVERY DIGIT RUN IN THE OUTPUT MUST APPEAR IN THE INPUT** (`D16`). Not "no figures":
   the useful sentence *does* quote coverage amounts, because that is what makes it a
   comparison rather than an adjective. So the check is provenance rather than absence —
   a number the model wrote that was not handed to it is a coverage figure invented by a
   model, which is the one thing `D16` exists to prevent. Verified by feeding a fake
   sentence with a plausible wrong figure and watching it be refused.
2. **No ranking language.** The model may say a plan differs; it may not say a plan is
   best, most suitable or the one to choose. That is the ordering asserting itself through
   the sentence, which is exactly the split `D126` drew.
3. **No price, premium or discount.** Pricing is out of scope (`D115`, `D117`) and
   `Product` carries no premium field, so any such word is invented by construction.
4. **No promise of cover or approval.** Underwriting belongs to the insurer.

A refusal is not an error: the generated sentence stands, the table renders, and the broker
loses nothing (`D12`).
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

from readycall.errors import DegradedError
from readycall.ports.llm import LlmClient, PromptRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from readycall.services.comparison.service import Comparison

log = structlog.get_logger(__name__)

#: Any run of digits, with thousands separators and decimals kept together so that
#: `30,000` is one token rather than two. Compared against the figures we handed in.
_NUMBER = re.compile(r"\d[\d,\.]*")

#: Guard 2. The model may describe a difference; it may not assert the ordering.
_RANKS = re.compile(
    r"ดีที่สุด|ดีกว่าทุก|คุ้มที่สุด|เหมาะที่สุด|ควรเลือก|ควรซื้อ|แนะนำให้เลือก"
    r"|อันดับ\s*\d|เป็นตัวเลือกที่ดี|น่าสนใจที่สุด|ตัวเลือกที่ดีที่สุด"
)

#: Guard 3. There is no premium anywhere in the input, so any of these is invented.
_PRICES = re.compile(r"เบี้ยประกัน|ราคา|ส่วนลด|ถูกกว่า|แพงกว่า|คุ้มค่าเงิน|ประหยัดเงิน|บาทต่อเดือน")

#: Guard 4. Underwriting is the insurer's, not ours (`D117`).
_PROMISES = re.compile(r"รับประกันว่า|การันตี|อนุมัติแน่|ได้รับความคุ้มครองแน่|ผ่านการพิจารณา")


class ComparisonReason(BaseModel):
    """One sentence for one plan."""

    product_code: str = Field(min_length=1, max_length=64)
    reason_th: str = Field(min_length=1, max_length=400)


class ComparisonReasons(BaseModel):
    """What the model may return. One entry per candidate it was given."""

    reasons: list[ComparisonReason] = Field(default_factory=list, max_length=12)


def _figures_allowed(comparison: Comparison) -> set[str]:
    """Every number the model is permitted to write, as the string it would write it.

    Built from the same `Difference` rows the ranking used, so the permitted set is
    literally *what we showed it* — nothing derived, nothing rounded.
    """
    allowed: set[str] = set()
    for candidate in comparison.candidates:
        for diff in candidate.differences:
            for value in (diff.held, diff.offered):
                if value is None:
                    continue
                allowed.add(_fmt(value))
                # A model writing `30000` where we wrote `30,000` is quoting our figure,
                # not inventing one. Both spellings are the same claim.
                allowed.add(_fmt(value).replace(",", ""))
    return allowed


def _fmt(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _invented_figures(text: str, allowed: set[str]) -> list[str]:
    """Numbers in the sentence that we did not hand the model.

    ⚠️ Bare one- and two-digit runs are ignored on purpose. They are overwhelmingly
    ordinary Thai prose — *"ชั้น 2+"*, *"24 ชั่วโมง"*, a year — and treating them as
    coverage figures would refuse almost every correct sentence. A coverage amount in this
    catalogue is never smaller than three digits.
    """
    invented = []
    for match in _NUMBER.findall(text):
        token = match.rstrip(".,")
        if len(token.replace(",", "").replace(".", "")) < 3:
            continue
        if token in allowed or token.replace(",", "") in allowed:
            continue
        invented.append(token)
    return invented


class ComparisonReasonWriter:
    """Rewrites `Candidate.reason_th`, on top of an ordering it never sees a score for.

    Deliberately **not** a method on `ComparisonService`: that service is pure and does no
    I/O, which is what lets the ranking be tested against a list of floats with no model,
    no key and no network. Putting a provider call inside it would trade that away for a
    sentence.
    """

    PROMPT = PromptRef(id="comparison_reason", version="v1")

    def __init__(self, *, llm: LlmClient, timeout_s: float = 4.0) -> None:
        self._llm = llm
        self._timeout_s = timeout_s
        self.attempted = 0
        self.refused = 0
        self.failed = 0

    async def write(self, comparison: Comparison) -> dict[str, str] | None:
        """`{product_code: sentence}` for whatever survived the guards, or `None`.

        Never raises and never returns a sentence it could not verify. The caller keeps
        the generated one for every code missing from the result.

        ⚠️ **`None` and `{}` are different answers and the caller must not merge them.**
        `{}` means the model answered and nothing survived the guards — deterministic, so
        asking again wastes money to be refused again. `None` means the call never
        completed: a timeout, or the provider's one-off `max_tokens` rejection landing on
        this call because the startup warm-up did not absorb it. That is **transient**, and
        caching it would make one cold-start failure permanent for the rest of the call.
        Measured, not hypothesised: it happened on the first live run of this feature.
        """
        if not comparison.candidates:
            return {}

        self.attempted += 1
        try:
            result = await asyncio.wait_for(
                self._llm.complete_structured(
                    self.PROMPT,
                    {
                        "line_label_th": comparison.line_label_th,
                        "held_label_th": comparison.held_label_th,
                        "candidates": _candidates_for_prompt(comparison),
                    },
                    ComparisonReasons,
                    timeout_s=self._timeout_s,
                ),
                timeout=self._timeout_s + 1.0,
            )
        except (DegradedError, TimeoutError) as exc:
            self.failed += 1
            log.warning("comparison reasons unavailable", error=str(exc)[:200])
            return None
        except Exception as exc:
            self.failed += 1
            log.warning("comparison reasons raised", error=f"{type(exc).__name__}: {exc}"[:200])
            return None

        allowed = _figures_allowed(comparison)
        known = {c.product.product_code for c in comparison.candidates}
        out: dict[str, str] = {}
        for row in result.output.reasons:
            if row.product_code not in known:
                # A code we never sent. Not an attack, just a model filling a list - but a
                # sentence about a plan that is not in this table has nowhere to render.
                self.refused += 1
                continue
            text = row.reason_th.strip()
            invented = _invented_figures(text, allowed)
            if invented:
                self.refused += 1
                log.warning(
                    "comparison reason refused: it stated a figure we did not supply",
                    product_code=row.product_code,
                    figures=invented[:4],
                )
                continue
            if _RANKS.search(text):
                self.refused += 1
                log.warning(
                    "comparison reason refused: it asserted a ranking",
                    product_code=row.product_code,
                    sample=text[:120],
                )
                continue
            if _PRICES.search(text) or _PROMISES.search(text):
                self.refused += 1
                log.warning(
                    "comparison reason refused: it priced or promised",
                    product_code=row.product_code,
                    sample=text[:120],
                )
                continue
            out[row.product_code] = text

        log.info(
            "comparison reasons ready",
            wrote=len(out),
            asked=len(comparison.candidates),
            refused=self.refused,
            model=result.usage.model,
            latency_ms=(
                round(result.usage.latency_ms, 1) if result.usage.latency_ms is not None else None
            ),
        )
        return out


def _candidates_for_prompt(comparison: Comparison) -> str:
    """The ranked candidates as plain lines, differences and all.

    The model is handed **exactly** the rows the arithmetic used — which is what makes
    `_figures_allowed` a real provenance check rather than a rough filter, and what stops
    the sentence being about anything the table does not show.
    """
    from readycall.services.comparison.service import Direction

    blocks: list[str] = []
    for index, candidate in enumerate(comparison.candidates, start=1):
        product = candidate.product
        lines = [f"{index}. product_code={product.product_code} · {product.name_th}"]
        if product.insurer:
            lines.append(f"   บริษัท: {product.insurer}")
        for diff in candidate.differences:
            held = _fmt(diff.held) if diff.held is not None else "ไม่ระบุ"
            offered = _fmt(diff.offered) if diff.offered is not None else "ไม่ระบุ"
            if diff.direction is Direction.BETTER:
                verdict = "ดีกว่าแผนปัจจุบัน"
            elif diff.direction is Direction.WORSE:
                verdict = "ด้อยกว่าแผนปัจจุบัน"
            elif diff.direction is Direction.SAME:
                verdict = "เท่ากัน"
            else:
                verdict = "ไม่มีข้อมูลเปรียบเทียบ"
            lines.append(f"   - {diff.label_th}: ปัจจุบัน {held} → แผนนี้ {offered} ({verdict})")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


__all__ = [
    "ComparisonReason",
    "ComparisonReasonWriter",
    "ComparisonReasons",
]
