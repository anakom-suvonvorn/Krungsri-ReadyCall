"""The wrap-up prediction, and the curve shape the design actually depends on (`D85`).

The claim under test is not "this fits the data". It is the specific, counter-intuitive
behaviour the matcher would rely on: **expected remaining time falls, then rises again.**
An agent well past their usual wrap-up is not nearly done — they are having a long one.

If that shape ever inverts, deferral would start preferring the agents least likely to be
free, and every individual decision would still look defensible. So it is asserted here
rather than assumed from the formula.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from readycall.domain.models import Assignment
from readycall.services.agents.acw_stats import AcwStats

START = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _assignment(agent_id: str, seconds: float | None, *, index: int = 0) -> Assignment:
    started = START + timedelta(minutes=index)
    return Assignment(
        assignment_id=f"asg_{agent_id}_{index}",
        call_session_id=f"call_{agent_id}_{index}",
        agent_id=agent_id,
        offered_at=started,
        acw_started_at=started,
        acw_ended_at=None if seconds is None else started + timedelta(seconds=seconds),
    )


def _history(agent_id: str, durations: list[float]) -> list[Assignment]:
    return [_assignment(agent_id, d, index=i) for i, d in enumerate(durations)]


class TestTheCurve:
    @pytest.fixture
    def stats(self) -> AcwStats:
        """An agent with a realistic right-skewed history: mostly ~2 minutes, a long tail."""
        durations = [70, 85, 95, 100, 110, 115, 120, 130, 145, 160, 210, 340]
        return AcwStats().rebuild(_history("A001", durations))

    def test_a_standing_start_predicts_roughly_the_whole_wrap_up(self, stats: AcwStats) -> None:
        remaining = stats.expected_remaining_s("A001", 0.0)
        assert remaining == pytest.approx(stats.profile("A001").mean_s)
        assert 90 < remaining < 200, "a sanity band, not a fit"

    def test_remaining_time_falls_then_rises(self, stats: AcwStats) -> None:
        """**The shape the whole idea rests on.** Right-skewed durations mean that having
        already run long is evidence of running longer still."""
        profile = stats.profile("A001")
        elapsed = [0, 15, 30, 60, 90, 120, 180, 300, 600, 1200]
        remaining = [stats.expected_remaining_s("A001", t) for t in elapsed]

        trough = min(range(len(remaining)), key=lambda i: remaining[i])
        assert 0 < trough < len(remaining) - 1, f"no interior minimum: {remaining}"

        # falling up to the trough...
        for before, after in pairwise(remaining[: trough + 1]):
            assert after < before, remaining
        # ...and rising after it
        for before, after in pairwise(remaining[trough:]):
            assert after > before, remaining

        # And the trough sits somewhere sane relative to how long they usually take.
        assert elapsed[trough] >= profile.median_s * 0.5

    def test_readiness_peaks_and_then_declines(self, stats: AcwStats) -> None:
        """The same fact expressed as preference: an agent just off a call and an agent
        far overdue are both poor choices, for opposite reasons."""
        just_ended = stats.readiness("A001", 0.0)
        near_the_mean = stats.readiness("A001", 110.0)
        long_overdue = stats.readiness("A001", 900.0)

        assert near_the_mean > just_ended
        assert near_the_mean > long_overdue

    def test_readiness_stays_inside_zero_and_one(self, stats: AcwStats) -> None:
        for elapsed in (0, 1, 60, 120, 600, 5000):
            assert 0.0 <= stats.readiness("A001", float(elapsed)) <= 1.0


class TestShrinkage:
    def test_an_agent_with_no_history_borrows_the_population(self) -> None:
        """On demo day this is everybody, and it must not blow up or return a wild guess."""
        stats = AcwStats().rebuild(_history("A001", [60, 90, 120, 150]))
        newcomer = stats.profile("A999")

        assert newcomer.samples == 0
        assert newcomer.population_samples == 4
        assert newcomer.is_mostly_borrowed
        assert 30 < newcomer.mean_s < 400

    def test_one_freak_wrap_up_does_not_define_an_agent(self) -> None:
        """A mean from a single sample is noise dressed as a measurement. Shrinkage is
        what stops one 20-minute wrap-up branding somebody slow for the rest of the day."""
        population = _history("A001", [60, 70, 80, 90, 100, 110])
        outlier = _history("A002", [1200])
        stats = AcwStats().rebuild(population + outlier)

        raw = 1200.0
        assert stats.profile("A002").mean_s < raw / 2, "the single sample dominated"
        assert stats.profile("A002").mean_s > stats.profile("A001").mean_s, (
            "it should still pull them slower than the population — just not all the way"
        )

    def test_evidence_wins_as_it_accumulates(self) -> None:
        """An agent earns their own curve. With enough of their own samples the
        population stops dominating."""
        population = _history("A001", [60] * 20)
        few = AcwStats().rebuild(population + _history("A002", [600] * 2))
        many = AcwStats().rebuild(population + _history("A002", [600] * 40))

        assert many.profile("A002").mean_s > few.profile("A002").mean_s
        assert not many.profile("A002").is_mostly_borrowed
        assert few.profile("A002").is_mostly_borrowed


class TestWhatCounts:
    def test_an_unfinished_wrap_up_is_not_a_sample(self) -> None:
        """An agent still in ACW has no duration yet. Counting the elapsed value would
        bias every profile toward whatever is in flight right now."""
        stats = AcwStats().rebuild(
            [*_history("A001", [90, 100]), _assignment("A001", None, index=9)]
        )
        assert stats.profile("A001").samples == 2

    def test_a_two_second_wrap_up_is_not_a_wrap_up(self) -> None:
        """It is an agent clearing the screen. Including them drags the mean down and
        makes the whole floor look imminently free."""
        stats = AcwStats().rebuild(_history("A001", [1.0, 2.0, 3.0, 120.0]))
        assert stats.profile("A001").samples == 1

    def test_it_is_derived_from_assignments_and_holds_no_state_of_its_own(self) -> None:
        """`D78`: a completed assignment already records both ends of ACW, so this is a
        projection. Rebuilding from a different history must forget the old one entirely."""
        stats = AcwStats().rebuild(_history("A001", [600, 700]))
        slow = stats.profile("A001").mean_s
        stats.rebuild(_history("A001", [30, 35]))
        assert stats.profile("A001").mean_s < slow


def test_every_score_can_be_decomposed() -> None:
    """`D18`: a number the system acts on has to be explainable. A readiness score nobody
    can take apart is exactly the kind of thing this project refuses to ship."""
    stats = AcwStats().rebuild(_history("A001", [60, 90, 120, 150, 300]))
    explained = stats.explain("A001", 100.0)

    assert set(explained) >= {
        "samples",
        "mostly_borrowed",
        "median_s",
        "mean_s",
        "elapsed_s",
        "expected_remaining_s",
        "readiness",
    }
    assert explained["samples"] == 5
