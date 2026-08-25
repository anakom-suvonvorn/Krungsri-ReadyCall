"""Tiered matching for keypad captures: try hardest first, report what actually matched.

`D44` made capture untyped — the caller keys *whatever they have* — and a lookup then
interprets it as evidence (`D66`). The first implementation only ever asked one question
per kind ("does a policy number end with these digits?"), which quietly assumed the agent
had asked for the whole thing. Real calls do not work that way:

    "ขอเลขกรมธรรม์ 4 ตัวท้ายค่ะ"      -> a suffix
    "ขอ 4 ตัวแรกค่ะ"                  -> a prefix
    "ขอปีเกิดค่ะ"                     -> a year, not a date
    "ขอวันเดือนเกิดค่ะ"                -> a date with no year

One fixed comparison answers *no match* to three of those, which is worse than useless: it
tells the agent the caller failed a check they were never asked to pass.

So every lookup runs a **ladder** of comparisons from strongest to weakest and returns the
strongest hit, carrying which rung it landed on. The agent then sees *"ตรงกับ 4 ตัวท้าย"*
rather than a bare tick, which matters because the rungs are not equally good evidence:
four trailing digits of a policy number is much weaker than the whole number, and an agent
deciding whether to attest an identity needs to know which one they got (`D42`).

**Nothing here knows what insurance is** (`D28`). It matches digit strings against digit
strings and a date against a date; the caller decides which fields to feed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Thailand runs on the Buddhist Era, and printed documents disagree with each other: a
#: policy schedule may show 2569 while the app shows 2026. A caller reading their own
#: birth year off an ID card will key the BE year, so both are accepted everywhere a year
#: is compared. `D67`.
BE_OFFSET = 543


@dataclass(frozen=True, slots=True)
class DigitMatch:
    """One rung of the ladder, with enough detail for the screen to be specific."""

    #: Machine-readable rung: `exact` | `suffix` | `prefix` | `contains`.
    tier: str
    #: Higher is stronger evidence. Compared across candidates to pick the best hit.
    strength: int
    #: How many digits the caller actually keyed. Part of the strength story: an `exact`
    #: match on three digits is not the same claim as an exact match on ten.
    digits: int
    #: The value that matched, in full, for the agent to read back.
    value: str


@dataclass(frozen=True, slots=True)
class DateMatch:
    """As above, for a date. `tier` is `full` | `day_month` | `year`."""

    tier: str
    strength: int
    #: True when the caller keyed a Buddhist-era year — worth surfacing, because it tells
    #: the agent the caller was reading off a Thai document rather than reciting.
    buddhist_era: bool


def digits_of(value: str) -> str:
    """Everything a keypad could have produced. `MT-2025-004512` -> `2025004512`."""
    return "".join(ch for ch in value if ch.isdigit())


def match_digits(entered: str, candidate: str, *, min_digits: int = 3) -> DigitMatch | None:
    """Strongest way `entered` sits inside `candidate`, or `None`.

    `min_digits` refuses to call two digits a match. Short strings collide constantly —
    with a 10-digit policy number there are only a hundred possible 2-digit endings — and
    a match that would happen by chance is not evidence, it is noise that an agent may
    reasonably mistake for confirmation.
    """
    entered = digits_of(entered)
    target = digits_of(candidate)
    if len(entered) < min_digits or not target:
        return None

    # The ladder. Order is the whole point: a caller who keyed the entire number should
    # never be told they matched a suffix.
    if entered == target:
        tier, base = "exact", 400
    elif target.endswith(entered):
        tier, base = "suffix", 300
    elif target.startswith(entered):
        tier, base = "prefix", 200
    elif entered in target:
        tier, base = "contains", 100
    else:
        return None

    # Length breaks ties within a rung and across them, so eight matching digits anywhere
    # outranks four matching digits at the end. It cannot cross a rung boundary on its
    # own, because the gap between rungs (100) exceeds any realistic digit count.
    return DigitMatch(tier=tier, strength=base + len(entered), digits=len(entered), value=candidate)


def best_digit_match(entered: str, candidates: dict[str, str]) -> tuple[str, DigitMatch] | None:
    """The strongest match across several candidate values, with its key.

    Used where a customer holds more than one of something — three policies, five claims —
    and the caller keyed digits from one of them without saying which.
    """
    best: tuple[str, DigitMatch] | None = None
    for key, candidate in candidates.items():
        found = match_digits(entered, candidate)
        if found is not None and (best is None or found.strength > best[1].strength):
            best = (key, found)
    return best


def match_date(entered: str, target: date) -> DateMatch | None:
    """Strongest way `entered` describes `target`, across orderings and both eras.

    Deliberately permissive about *ordering*. The agent asks in Thai and the caller keys
    what they read; insisting on one layout would fail honest callers, and the ambiguity
    is cheap to resolve here — `31121990` cannot be `yyyymmdd`, and a genuinely ambiguous
    string like `01021990` is the same date under either reading of the first four digits
    only when it is not, in which case one of the two simply does not parse.
    """
    entered = digits_of(entered)
    if not entered:
        return None

    ce, be = target.year, target.year + BE_OFFSET
    dd, mm = f"{target.day:02d}", f"{target.month:02d}"

    full_ce = {f"{dd}{mm}{ce}", f"{mm}{dd}{ce}", f"{ce}{mm}{dd}"}
    full_be = {f"{dd}{mm}{be}", f"{mm}{dd}{be}", f"{be}{mm}{dd}"}
    if entered in full_ce:
        return DateMatch(tier="full", strength=300, buddhist_era=False)
    if entered in full_be:
        return DateMatch(tier="full", strength=300, buddhist_era=True)

    # Day and month, no year. A real answer to "ขอวันเดือนเกิดค่ะ", and much weaker
    # evidence: roughly one in 365 rather than one in a lifetime.
    if entered in {f"{dd}{mm}", f"{mm}{dd}"}:
        return DateMatch(tier="day_month", strength=200, buddhist_era=False)

    # Year alone. Weakest rung by a wide margin and still worth having — it is the one
    # thing a caller can always produce, and it eliminates most of the population.
    if entered == str(ce):
        return DateMatch(tier="year", strength=100, buddhist_era=False)
    if entered == str(be):
        return DateMatch(tier="year", strength=100, buddhist_era=True)

    return None


__all__ = [
    "BE_OFFSET",
    "DateMatch",
    "DigitMatch",
    "best_digit_match",
    "digits_of",
    "match_date",
    "match_digits",
]
