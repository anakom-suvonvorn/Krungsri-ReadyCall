"""Compare & best-fit: the rules that keep a ranking honest (`D126`).

Every test here is one specific way this feature could lie to a customer who is deciding
whether to change their insurance cover, which is why they are worth spelling out:

* a plan that does not mention a cover must not be shown as offering **zero** of it;
* one enormous figure must not carry a plan that is worse on everything else;
* a 2% difference must not be announced as an improvement;
* the plan they already hold must not be offered back to them as an alternative;
* with no policy to improve on, the order must not fall back to catalogue order — which
  is precisely how an affiliated carrier ends up silently first;
* and the reason sentence must name what is **worse**, or it is a sales script.

The service takes its catalogue as an argument and does no I/O, so all of this runs
against a handful of `Product` objects with no adapter, no config file and no network.
"""

from __future__ import annotations

import pytest

from readycall.domain.enums import PolicyStatus, ProductLine
from readycall.domain.models import Coverage, Policy, Product
from readycall.domainpack import ComparisonAttribute, ComparisonLine, ComparisonSettings
from readycall.services.comparison import ComparisonService, Direction, to_push_payload

ROOM = "ipd_room_board"
LIMIT = "ipd_annual_limit"
OPD = "opd_limit"
DEDUCTIBLE = "deductible"

SPEC = ComparisonLine(
    label_th="ประกันสุขภาพ",
    attributes=(
        ComparisonAttribute(kind=ROOM, label_th="ค่าห้อง", better="higher", weight=3.0),
        ComparisonAttribute(kind=LIMIT, label_th="วงเงินต่อปี", better="higher", weight=3.0),
        ComparisonAttribute(kind=OPD, label_th="ผู้ป่วยนอก", better="higher", weight=1.0),
        ComparisonAttribute(kind=DEDUCTIBLE, label_th="ส่วนแรก", better="lower", weight=2.0),
    ),
)


def cov(kind: str, amount: float | None, unit: str | None = None) -> Coverage:
    return Coverage(kind=kind, label_th=kind, amount=amount, unit=unit)


def plan(code: str, insurer: str, **figures: float | None) -> Product:
    return Product(
        product_code=code,
        line=ProductLine.HEALTH,
        name_th=code,
        insurer=insurer,
        coverages=tuple(cov(k, v) for k, v in figures.items() if v is not None),
    )


def held_policy(code: str = "HELD", **figures: float) -> Policy:
    return Policy(
        policy_no="P-1",
        customer_id="C1",
        product_code=code,
        line=ProductLine.HEALTH,
        insurer="บริษัทเดิม",
        status=PolicyStatus.ACTIVE,
        coverages=tuple(cov(k, v) for k, v in figures.items()),
    )


@pytest.fixture
def service() -> ComparisonService:
    return ComparisonService(
        settings=ComparisonSettings(max_candidates=3, min_material_improvement=0.05)
    )


# --- rule 1: absent is not zero ---------------------------------------------------------


def test_a_plan_silent_on_a_cover_is_not_shown_as_offering_none_of_it(
    service: ComparisonService,
) -> None:
    """`D125`, `D16`. A plan that does not mention outpatient cover and a plan that
    excludes it are different products, and `0` is a claim the data does not support."""
    held = held_policy(**{ROOM: 1500, LIMIT: 200000, OPD: 800, DEDUCTIBLE: 0})
    silent = plan("SILENT", "ก", **{ROOM: 4000, LIMIT: 5000000, DEDUCTIBLE: 0})

    result = service.build(line="health", spec=SPEC, held=held, catalogue=[silent])
    opd = next(d for d in result.candidates[0].differences if d.kind == OPD)

    assert opd.direction is Direction.NOT_STATED
    assert opd.offered is None
    assert opd.improvement == 0.0, "an unstated figure moves the score in neither direction"

    table = to_push_payload(result)
    opd_row = next(r for r in table["rows"] if r["cells"][0] == "ผู้ป่วยนอก")
    assert "—" in opd_row["cells"], f"rendered as {opd_row['cells']}"
    assert "0" not in [c.strip() for c in opd_row["cells"][2:]]


def test_an_unstated_figure_never_wins_its_row(service: ComparisonService) -> None:
    """`best_index` must point at a figure somebody actually stated."""
    held = held_policy(**{ROOM: 1500, LIMIT: 200000, OPD: 800, DEDUCTIBLE: 0})
    silent = plan("SILENT", "ก", **{ROOM: 9000, LIMIT: 9000000, DEDUCTIBLE: 0})

    table = to_push_payload(service.build(line="health", spec=SPEC, held=held, catalogue=[silent]))
    opd_row = next(r for r in table["rows"] if r["cells"][0] == "ผู้ป่วยนอก")
    # Only the held column states it, so that is the only thing that can be best.
    assert opd_row.get("best_index") == 1


# --- rule 2: one huge number must not decide the table ----------------------------------


def test_one_enormous_figure_cannot_carry_a_plan_that_is_worse_everywhere_else(
    service: ComparisonService,
) -> None:
    """A comparison whose order is decided by a single outlier is a sales script with a
    table around it. Improvement is clipped, so the other attributes still count."""
    held = held_policy(**{ROOM: 3000, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 0})
    # A hundred million a year, and worse on everything else.
    outlier = plan("OUTLIER", "ก", **{ROOM: 1000, LIMIT: 100000000, OPD: 100, DEDUCTIBLE: 80000})
    balanced = plan("BALANCED", "ข", **{ROOM: 5000, LIMIT: 3000000, OPD: 2500, DEDUCTIBLE: 0})

    result = service.build(line="health", spec=SPEC, held=held, catalogue=[outlier, balanced])
    assert result.candidates[0].product.product_code == "BALANCED"


# --- rule 3: small differences are not findings -----------------------------------------


def test_a_two_percent_difference_is_not_announced_as_an_improvement(
    service: ComparisonService,
) -> None:
    """It still renders — the customer can see both numbers — it is simply not called
    better, because a 2% higher room rate is not a reason to change carrier."""
    held = held_policy(**{ROOM: 3000, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 0})
    nudge = plan("NUDGE", "ก", **{ROOM: 3060, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 0})

    result = service.build(line="health", spec=SPEC, held=held, catalogue=[nudge])
    room = next(d for d in result.candidates[0].differences if d.kind == ROOM)
    assert room.direction is Direction.SAME
    assert room.offered == 3060.0, "and the figure is still shown"


# --- what a comparison is for -----------------------------------------------------------


def test_the_plan_they_already_hold_is_not_offered_back_to_them(
    service: ComparisonService,
) -> None:
    """It is the column everything is measured against. Offering it as an alternative is
    a table recommending the status quo it was asked to challenge."""
    held = held_policy(code="MINE", **{ROOM: 3000, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 0})
    same = plan("MINE", "บริษัทเดิม", **{ROOM: 3000, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 0})
    other = plan("OTHER", "ก", **{ROOM: 5000, LIMIT: 3000000, OPD: 2000, DEDUCTIBLE: 0})

    result = service.build(line="health", spec=SPEC, held=held, catalogue=[same, other])
    assert [c.product.product_code for c in result.candidates] == ["OTHER"]


def test_a_withdrawn_plan_is_never_a_candidate(service: ComparisonService) -> None:
    live = plan("LIVE", "ก", **{ROOM: 4000, LIMIT: 3000000})
    gone = Product(
        product_code="GONE",
        line=ProductLine.HEALTH,
        name_th="GONE",
        insurer="ข",
        coverages=(cov(ROOM, 9000), cov(LIMIT, 9000000)),
        is_active=False,
    )
    result = service.build(line="health", spec=SPEC, held=None, catalogue=[live, gone])
    assert [c.product.product_code for c in result.candidates] == ["LIVE"]


def test_the_table_is_capped_so_a_phone_can_render_it(service: ComparisonService) -> None:
    catalogue = [plan(f"P{i}", "ก", **{ROOM: 1000 * i, LIMIT: 100000 * i}) for i in range(1, 9)]
    result = service.build(line="health", spec=SPEC, held=None, catalogue=catalogue)
    assert len(result.candidates) == 3
    # And it is the best three, not the first three.
    assert [c.product.product_code for c in result.candidates] == ["P8", "P7", "P6"]


# --- new business: nothing to improve on ------------------------------------------------


def test_with_no_policy_the_order_is_not_the_catalogues_order(
    service: ComparisonService,
) -> None:
    """`D126`, and it was a real bug before it was a test.

    With no held policy every difference is `NOT_STATED`, so every weighted improvement is
    exactly zero — which is not a tie, it is **no ranking at all**, and the order then
    falls back to whatever the catalogue happened to be in. That is precisely how an
    affiliated carrier ends up silently first. Candidates are ranked against each other
    instead.
    """
    weakest = plan("A-WEAKEST", "ก", **{ROOM: 1000, LIMIT: 100000, OPD: 200, DEDUCTIBLE: 50000})
    strongest = plan("Z-STRONGEST", "ข", **{ROOM: 6000, LIMIT: 8000000, OPD: 2500, DEDUCTIBLE: 0})

    # Catalogue order puts the weakest first, and its code sorts first too — so a tie
    # broken by code would also put it first.
    result = service.build(line="health", spec=SPEC, held=None, catalogue=[weakest, strongest])
    assert result.candidates[0].product.product_code == "Z-STRONGEST"
    assert result.candidates[0].score > result.candidates[1].score


def test_an_attribute_every_plan_states_identically_decides_nothing(
    service: ComparisonService,
) -> None:
    """A spread of zero carries no information about which to choose, so it must not
    quietly add the same constant to everybody and look like it mattered."""
    a = plan("A", "ก", **{ROOM: 4000, LIMIT: 1000000})
    b = plan("B", "ข", **{ROOM: 4000, LIMIT: 5000000})
    result = service.build(line="health", spec=SPEC, held=None, catalogue=[a, b])
    assert result.candidates[0].product.product_code == "B", "decided by the limit alone"


# --- the sentence -----------------------------------------------------------------------


def test_the_reason_names_what_is_worse_not_only_what_is_better(
    service: ComparisonService,
) -> None:
    """A comparison that lists only the upside is a sales script — and the plan ranked
    first is frequently the one with a deductible attached."""
    held = held_policy(**{ROOM: 1500, LIMIT: 200000, OPD: 800, DEDUCTIBLE: 0})
    trade_off = plan("TRADEOFF", "ก", **{ROOM: 6000, LIMIT: 10000000, OPD: 2500, DEDUCTIBLE: 30000})

    result = service.build(line="health", spec=SPEC, held=held, catalogue=[trade_off])
    reason = result.candidates[0].reason_th
    assert "แต่" in reason, reason
    assert "30,000" in reason, reason


def test_no_reason_sentence_ever_mentions_a_premium(service: ComparisonService) -> None:
    """Pricing and underwriting are out of scope (`D115`), and `Product` has no premium
    field to read even if this wanted one. Surface and compare; never quote."""
    held = held_policy(**{ROOM: 1500, LIMIT: 200000, OPD: 800, DEDUCTIBLE: 0})
    catalogue = [
        plan("A", "ก", **{ROOM: 4000, LIMIT: 3000000, OPD: 1200, DEDUCTIBLE: 0}),
        plan("B", "ข", **{ROOM: 5000, LIMIT: 5000000, OPD: 2000, DEDUCTIBLE: 20000}),
    ]
    result = service.build(line="health", spec=SPEC, held=held, catalogue=catalogue)
    for candidate in result.candidates:
        assert "เบี้ย" not in candidate.reason_th, candidate.reason_th
    assert "ไม่ใช่ใบเสนอราคา" in str(to_push_payload(result)["note_th"])


# --- the payload the customer's phone renders -------------------------------------------


def test_the_customers_own_plan_is_the_first_column(service: ComparisonService) -> None:
    """The gap is the thing they should see first, not something to work out."""
    held = held_policy(**{ROOM: 1500, LIMIT: 200000, OPD: 800, DEDUCTIBLE: 0})
    result = service.build(
        line="health",
        spec=SPEC,
        held=held,
        catalogue=[plan("A", "ก", **{ROOM: 4000, LIMIT: 3000000, OPD: 1200, DEDUCTIBLE: 0})],
    )
    table = to_push_payload(result)
    assert table["columns_th"][0] == "ความคุ้มครอง"
    assert table["columns_th"][1] == "แผนปัจจุบันของคุณ"


def test_best_index_points_at_a_figure_not_at_the_row_label(
    service: ComparisonService,
) -> None:
    """Cell 0 is the attribute's name, so the index is offset by one. Getting that wrong
    highlights the row's title and reads as a rendering glitch rather than a wrong answer.
    """
    held = held_policy(**{ROOM: 1500, LIMIT: 200000, OPD: 800, DEDUCTIBLE: 0})
    result = service.build(
        line="health",
        spec=SPEC,
        held=held,
        catalogue=[plan("A", "ก", **{ROOM: 4000, LIMIT: 3000000, OPD: 1200, DEDUCTIBLE: 0})],
    )
    for row in to_push_payload(result)["rows"]:
        best = row.get("best_index")
        assert best is None or best >= 1, row


def test_lower_is_better_actually_runs_the_other_way(service: ComparisonService) -> None:
    """The one thing in `comparison.yaml` that is silent when wrong: every plan still
    renders, in the wrong order, with a confident reason attached."""
    held = held_policy(**{ROOM: 3000, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 10000})
    cheaper_excess = plan("LOW", "ก", **{ROOM: 3000, LIMIT: 1000000, OPD: 1500, DEDUCTIBLE: 1000})
    result = service.build(line="health", spec=SPEC, held=held, catalogue=[cheaper_excess])
    excess = next(d for d in result.candidates[0].differences if d.kind == DEDUCTIBLE)
    assert excess.direction is Direction.BETTER, "a SMALLER excess is better"
    assert excess.improvement > 0

    table = to_push_payload(result)
    row = next(r for r in table["rows"] if r["cells"][0] == "ส่วนแรก")
    assert row["best_index"] == 2, "the 1,000 column, not the 10,000 one"
