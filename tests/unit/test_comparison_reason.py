"""The model writes the sentence and only the sentence (`D137`, `D126`).

Every test here is one way a model could turn an honest comparison into a sales script, or
into a coverage figure nobody can trace:

* a figure the model was never handed is a coverage amount **invented by a model** (`D16`),
  and it is the reason this guard is provenance rather than absence — the useful sentence
  quotes real amounts;
* a sentence asserting which plan is best is the *ordering* speaking through the model,
  which is exactly the split `D126` drew;
* a price is invented by construction, because `Product` has no premium field;
* and a promise of cover is underwriting, which belongs to the insurer (`D117`).

A refusal keeps the generated sentence, so nothing here can break the table.

Nothing needs a key or a network: the writer is driven with a fake client that returns
whatever the test wants to see refused.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from readycall.domain.enums import PolicyStatus, ProductLine
from readycall.domain.models import Coverage, Policy, Product
from readycall.domainpack import ComparisonAttribute, ComparisonLine, ComparisonSettings
from readycall.errors import DegradedError
from readycall.ports.llm import LlmResult, LlmUsage, PromptRef
from readycall.services.analysis import ComparisonReasonWriter
from readycall.services.comparison import ComparisonService

ROOM = "ipd_room_board"
LIMIT = "ipd_annual_limit"
DEDUCTIBLE = "deductible"

SPEC = ComparisonLine(
    label_th="ประกันสุขภาพ",
    attributes=(
        ComparisonAttribute(kind=ROOM, label_th="ค่าห้อง", better="higher", weight=3.0),
        ComparisonAttribute(kind=LIMIT, label_th="วงเงินต่อปี", better="higher", weight=3.0),
        ComparisonAttribute(kind=DEDUCTIBLE, label_th="ส่วนแรก", better="lower", weight=2.0),
    ),
)


def _cov(kind: str, amount: float) -> Coverage:
    return Coverage(kind=kind, label_th=kind, amount=amount, unit=None)


def _held() -> Policy:
    return Policy(
        policy_no="P-1",
        customer_id="C1",
        product_code="HELD",
        line=ProductLine.HEALTH,
        insurer="บริษัทเดิม",
        status=PolicyStatus.ACTIVE,
        coverages=(_cov(ROOM, 1500), _cov(LIMIT, 200000), _cov(DEDUCTIBLE, 0)),
    )


def _catalogue() -> list[Product]:
    return [
        Product(
            product_code="ALPHA",
            line=ProductLine.HEALTH,
            name_th="แผนอัลฟ่า",
            insurer="เมืองไทยประกันภัย",
            coverages=(_cov(ROOM, 4000), _cov(LIMIT, 1000000), _cov(DEDUCTIBLE, 30000)),
        )
    ]


def _comparison() -> Any:
    service = ComparisonService(
        settings=ComparisonSettings(max_candidates=3, min_material_improvement=0.05)
    )
    return service.build(line="health", spec=SPEC, held=_held(), catalogue=_catalogue())


class _FakeLlm:
    """Returns exactly what the test wants the guards to see."""

    def __init__(
        self, reasons: list[dict[str, str]] | None = None, raises: Exception | None = None
    ):
        self._reasons = reasons or []
        self._raises = raises
        self.calls = 0

    async def complete_structured(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        schema: type[BaseModel],
        *,
        timeout_s: float | None = None,
    ) -> LlmResult[Any]:
        self.calls += 1
        self.variables = variables
        if self._raises is not None:
            raise self._raises
        return LlmResult(
            output=schema.model_validate({"reasons": self._reasons}),
            usage=LlmUsage(provider="fake", model="fake-1", latency_ms=1.0),
        )


# --- guard 1: a figure we did not supply --------------------------------------------------


@pytest.mark.asyncio
async def test_a_figure_we_never_supplied_is_refused() -> None:
    """`D16`, and it is the whole design. `4,500` is plausible, wrong, and traceable to
    nothing — a coverage amount written by a model rather than read from a record."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": "ค่าห้อง 4,500 บาท สูงกว่าแผนปัจจุบัน"}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    written = await writer.write(_comparison())

    assert written == {}, "an invented coverage figure reached the broker"
    assert writer.refused == 1


@pytest.mark.asyncio
async def test_the_figures_we_did_supply_are_allowed_through() -> None:
    """The other half, and without it the guard would be 'no numbers', which would refuse
    every sentence worth having. `4,000` and `1,500` are both in the input."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": "ค่าห้อง 1,500 → 4,000 แต่มีส่วนแรก 30,000"}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    written = await writer.write(_comparison())

    assert "ALPHA" in written
    assert writer.refused == 0


@pytest.mark.asyncio
async def test_a_figure_written_without_separators_is_the_same_claim() -> None:
    """`4000` where we wrote `4,000` is quoting our figure, not inventing one."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": "ค่าห้อง 4000 สูงกว่าเดิม"}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    assert "ALPHA" in await writer.write(_comparison())


@pytest.mark.asyncio
async def test_short_numbers_in_ordinary_prose_are_not_treated_as_figures() -> None:
    """A coverage amount here is never two digits. Refusing `24 ชั่วโมง` would refuse
    almost every correct sentence, which is a guard that gets switched off."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": "ดูแล 24 ชั่วโมง ค่าห้อง 4,000"}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    assert "ALPHA" in await writer.write(_comparison())


# --- guard 2: the model must not assert the ordering --------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sentence",
    [
        "แผนนี้ดีที่สุดสำหรับคุณ",
        "เป็นแผนที่เหมาะที่สุด",
        "ควรเลือกแผนนี้",
        "อันดับ 1 ของตาราง",
    ],
)
async def test_a_sentence_that_ranks_is_refused(sentence: str) -> None:
    """`D126`'s split. The ordering is arithmetic; a model that can announce a winner is a
    model that can be argued into one."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": sentence}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    assert await writer.write(_comparison()) == {}
    assert writer.refused == 1


# --- guards 3 and 4: pricing and promises -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sentence",
    ["เบี้ยประกันถูกกว่าเดิม", "ราคาคุ้มค่ากว่า", "มีส่วนลดพิเศษ"],
)
async def test_a_sentence_that_prices_is_refused(sentence: str) -> None:
    """Pricing is out of scope (`D115`, `D117`) and there is no premium in the input, so
    any price is invented by construction."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": sentence}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    assert await writer.write(_comparison()) == {}


@pytest.mark.asyncio
async def test_a_sentence_that_promises_cover_is_refused() -> None:
    """Underwriting is the insurer's decision, not ours."""
    llm = _FakeLlm([{"product_code": "ALPHA", "reason_th": "การันตีว่าผ่านการพิจารณาแน่นอน"}])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    assert await writer.write(_comparison()) == {}


# --- the shape of the thing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sentence_about_a_plan_not_in_the_table_is_dropped() -> None:
    """A code we never sent has nowhere to render, so it is not a sentence about anything
    the broker can see."""
    llm = _FakeLlm(
        [
            {"product_code": "ALPHA", "reason_th": "ค่าห้อง 4,000"},
            {"product_code": "NOT-IN-TABLE", "reason_th": "แผนอื่น"},
        ]
    )
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    written = await writer.write(_comparison())

    assert set(written) == {"ALPHA"}


@pytest.mark.asyncio
async def test_a_failing_model_returns_none_and_never_raises() -> None:
    """`D12`. The table renders either way; the generated sentence is the fallback and the
    broker loses nothing.

    ⚠️ `None`, not `{}` — see the test below for why the difference is load-bearing.
    """
    writer = ComparisonReasonWriter(
        llm=_FakeLlm(
            raises=DegradedError(
                "nope", stage="comparison_reason", fallback="the generated sentence"
            )
        ),
        timeout_s=1.0,
    )

    assert await writer.write(_comparison()) is None
    assert writer.failed == 1


@pytest.mark.asyncio
async def test_a_transient_failure_is_told_apart_from_a_refusal() -> None:
    """⚠️ The caller CACHES this answer, so the two must not look alike (`D137`).

    `{}` means the model answered and every sentence was refused — deterministic, so
    asking again buys nothing and caching it is right. `None` means the call never
    completed, which is transient: caching it would make one cold-start failure permanent
    for the rest of the call.

    **This is not hypothetical.** On the first live run of this feature the provider's
    one-off `max_tokens` rejection landed on the comparison call, and the empty result was
    cached — so the panel could never recover for that call even though the very next
    request would have worked.
    """
    refused = ComparisonReasonWriter(
        llm=_FakeLlm([{"product_code": "ALPHA", "reason_th": "แผนนี้ดีที่สุด"}]), timeout_s=1.0
    )
    transient = ComparisonReasonWriter(
        llm=_FakeLlm(raises=DegradedError("cold", stage="comparison_reason", fallback="generated")),
        timeout_s=1.0,
    )

    assert await refused.write(_comparison()) == {}, "a guard refusal is deterministic"
    assert await transient.write(_comparison()) is None, "a failed call is not a refusal"


@pytest.mark.asyncio
async def test_the_model_is_handed_the_same_rows_the_ranking_used() -> None:
    """What makes the figure guard a real provenance check rather than a rough filter: the
    permitted set is literally what the prompt was shown."""
    llm = _FakeLlm([])
    writer = ComparisonReasonWriter(llm=llm, timeout_s=1.0)

    await writer.write(_comparison())

    candidates = llm.variables["candidates"]
    assert "ALPHA" in candidates
    assert "1,500" in candidates and "4,000" in candidates
    assert "30,000" in candidates, "the deductible that decides this ranking was not shown"
    assert "ด้อยกว่าแผนปัจจุบัน" in candidates, "the model was not told what is worse"


@pytest.mark.asyncio
async def test_the_ranking_is_untouched_by_any_of_this() -> None:
    """`D126`. The writer returns sentences; it is handed no way to reorder anything, and
    the service that ranks never learns this module exists."""
    comparison = _comparison()
    before = [c.product.product_code for c in comparison.candidates]
    scores = [c.score for c in comparison.candidates]

    await ComparisonReasonWriter(
        llm=_FakeLlm([{"product_code": "ALPHA", "reason_th": "ค่าห้อง 4,000"}]), timeout_s=1.0
    ).write(comparison)

    assert [c.product.product_code for c in comparison.candidates] == before
    assert [c.score for c in comparison.candidates] == scores
