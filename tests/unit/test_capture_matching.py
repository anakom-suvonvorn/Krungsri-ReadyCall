"""Tiered keypad matching (`D66`, `D67`).

The claim under test is not "does it match" but **"does it report the right rung"** — an
agent deciding whether to attest an identity (`D42`) needs to know they got four trailing
digits rather than the whole number, because those are very different pieces of evidence.
"""

from __future__ import annotations

from datetime import date

from readycall.services.capture.matching import (
    best_digit_match,
    digits_of,
    match_date,
    match_digits,
)

POLICY = "HL-2024-000811"  # digits: 2024000811


def test_the_whole_number_is_an_exact_match() -> None:
    hit = match_digits("2024000811", POLICY)
    assert hit is not None
    assert hit.tier == "exact"
    assert hit.digits == 10


def test_last_four_digits_are_a_suffix_match() -> None:
    """ "ขอเลขกรมธรรม์ 4 ตัวท้ายค่ะ" — the single most common way this is actually asked."""
    hit = match_digits("0811", POLICY)
    assert hit is not None
    assert hit.tier == "suffix"
    assert hit.digits == 4


def test_first_four_digits_are_a_prefix_match() -> None:
    hit = match_digits("2024", POLICY)
    assert hit is not None
    assert hit.tier == "prefix"


def test_digits_from_the_middle_still_match_but_weakly() -> None:
    hit = match_digits("40008", POLICY)
    assert hit is not None
    assert hit.tier == "contains"


def test_a_stronger_rung_always_beats_a_weaker_one() -> None:
    """The ladder must be ordered, or the agent is told the weaker thing happened."""
    exact = match_digits("2024000811", POLICY)
    suffix = match_digits("0811", POLICY)
    prefix = match_digits("2024", POLICY)
    inside = match_digits("40008", POLICY)
    assert exact and suffix and prefix and inside
    assert exact.strength > suffix.strength > prefix.strength > inside.strength


def test_more_digits_outrank_fewer_on_the_same_rung() -> None:
    assert match_digits("000811", POLICY).strength > match_digits("0811", POLICY).strength  # type: ignore[union-attr]


def test_two_digits_are_not_evidence() -> None:
    """A 10-digit number has only 100 possible 2-digit endings.

    A coincidence an agent may reasonably read as confirmation is worse than no answer,
    so the floor refuses rather than returning a weak match.
    """
    assert match_digits("11", POLICY) is None
    assert match_digits("811", POLICY) is not None, "three is the floor, not four"


def test_formatting_is_ignored_on_both_sides() -> None:
    assert digits_of(POLICY) == "2024000811"
    assert match_digits("2024-000811", POLICY) is not None


def test_the_best_match_across_several_policies_wins() -> None:
    """A customer with three policies who keys digits from one of them."""
    found = best_digit_match(
        "0811",
        {"a": "MT-2025-004512", "b": POLICY, "c": "TA-2023-777811"},
    )
    assert found is not None
    key, hit = found
    assert hit.tier == "suffix"
    assert key in {"b", "c"}, "both end 811; either is a defensible answer"

    exact = best_digit_match("2024000811", {"a": "MT-2025-004512", "b": POLICY})
    assert exact is not None and exact[0] == "b" and exact[1].tier == "exact"


# --- dates ------------------------------------------------------------------------------

DOB = date(1987, 9, 3)


def test_a_full_date_matches_in_several_orderings() -> None:
    for keyed in ("03091987", "09031987", "19870903"):
        hit = match_date(keyed, DOB)
        assert hit is not None, keyed
        assert hit.tier == "full"
        assert hit.buddhist_era is False


def test_a_buddhist_era_year_matches_and_is_flagged() -> None:
    """1987 CE is 2530 BE, and a caller reading a Thai ID card keys the BE year.

    Rejecting it would fail an honest caller answering correctly off their own document.
    """
    hit = match_date("03092530", DOB)
    assert hit is not None
    assert hit.tier == "full"
    assert hit.buddhist_era is True


def test_day_and_month_alone_match_more_weakly() -> None:
    """ "ขอวันเดือนเกิดค่ะ" — a real question, and roughly 1-in-365 evidence."""
    hit = match_date("0309", DOB)
    assert hit is not None
    assert hit.tier == "day_month"


def test_the_year_alone_matches_in_either_era_and_is_weakest() -> None:
    ce = match_date("1987", DOB)
    be = match_date("2530", DOB)
    full = match_date("03091987", DOB)
    assert ce is not None and be is not None and full is not None
    assert ce.tier == be.tier == "year"
    assert be.buddhist_era is True and ce.buddhist_era is False
    assert full.strength > match_date("0309", DOB).strength > ce.strength  # type: ignore[union-attr]


def test_a_wrong_date_matches_nothing() -> None:
    assert match_date("01011999", DOB) is None
    assert match_date("1999", DOB) is None
