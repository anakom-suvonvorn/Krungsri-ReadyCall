"""Opening hours. Mostly boundary cases, because that is where a schedule is wrong.

The dates below are real and checked: 2026-08-24 is a Monday, 2026-08-22 a Saturday,
2026-08-23 a Sunday, and 2026-12-10 is วันรัฐธรรมนูญ (Constitution Day).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from readycall.errors import ConfigError
from readycall.services.queues.hours import QueueHours, Schedule, Window
from tests.conftest import REPO_ROOT

HOURS_YAML = REPO_ROOT / "config" / "queue_hours.yaml"


@pytest.fixture
def hours() -> QueueHours:
    return QueueHours.load(HOURS_YAML)


def local(hours: QueueHours, *args: int) -> datetime:
    return datetime(*args, tzinfo=hours.tz)  # type: ignore[arg-type]


def test_business_hours_open_on_a_weekday_morning(hours: QueueHours) -> None:
    assert hours.is_open("business", local(hours, 2026, 8, 24, 10, 0))


def test_business_hours_closed_overnight(hours: QueueHours) -> None:
    state = hours.state("business", local(hours, 2026, 8, 24, 3, 0))
    assert not state.is_open
    assert state.closed_reason == "outside_hours"
    assert state.next_open_at == local(hours, 2026, 8, 24, 8, 30)


def test_sunday_is_closed_and_points_at_monday(hours: QueueHours) -> None:
    state = hours.state("business", local(hours, 2026, 8, 23, 10, 0))
    assert not state.is_open
    assert state.next_open_at == local(hours, 2026, 8, 24, 8, 30)


def test_saturday_has_its_own_shorter_window(hours: QueueHours) -> None:
    assert hours.is_open("business", local(hours, 2026, 8, 22, 10, 0))
    assert not hours.is_open("business", local(hours, 2026, 8, 22, 17, 0))


def test_a_holiday_closes_business_and_names_itself(hours: QueueHours) -> None:
    state = hours.state("business", local(hours, 2026, 12, 10, 10, 0))
    assert not state.is_open
    assert state.closed_reason == "holiday"
    assert state.holiday_th
    assert state.next_open_at == local(hours, 2026, 12, 11, 8, 30)


def test_the_extended_schedule_covers_evenings_but_not_the_night(hours: QueueHours) -> None:
    """`q_claims`: hospitals admit at 21:00; nobody pre-authorises at 04:00."""
    assert hours.is_open("extended", local(hours, 2026, 8, 24, 21, 0))
    assert hours.is_open("extended", local(hours, 2026, 8, 23, 9, 0))  # Sunday
    assert not hours.is_open("extended", local(hours, 2026, 8, 24, 4, 0))


def test_a_247_queue_ignores_holidays(hours: QueueHours) -> None:
    """A crash does not check the calendar — motor claims runs on New Year's Day."""
    assert hours.is_open("always", local(hours, 2026, 1, 1, 3, 0))
    assert hours.is_open("always", local(hours, 2026, 12, 10, 3, 0))


def test_the_window_boundary_is_inclusive_start_exclusive_end(hours: QueueHours) -> None:
    assert hours.is_open("business", local(hours, 2026, 8, 24, 8, 30))
    assert not hours.is_open("business", local(hours, 2026, 8, 24, 8, 29, 59))
    assert not hours.is_open("business", local(hours, 2026, 8, 24, 18, 0))
    assert hours.is_open("business", local(hours, 2026, 8, 24, 17, 59, 59))


def test_a_utc_instant_is_converted_before_it_is_judged(hours: QueueHours) -> None:
    """03:00 UTC is 10:00 in Bangkok — open. Comparing naive local times would say no."""
    assert hours.is_open("business", datetime(2026, 8, 24, 3, 0, tzinfo=UTC))
    # ...and the mirror case: 20:00 UTC Sunday is 03:00 Monday local, still shut.
    assert not hours.is_open("business", datetime(2026, 8, 23, 20, 0, tzinfo=UTC))


def test_an_unknown_schedule_is_a_config_error(hours: QueueHours) -> None:
    with pytest.raises(ConfigError):
        hours.state("nonexistent", local(hours, 2026, 8, 24, 10, 0))


def test_a_backwards_window_is_refused_at_load(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Silently never-open is the worst outcome: a queue nobody can reach, no error."""
    bad = tmp_path / "hours.yaml"
    bad.write_text(
        "timezone: Asia/Bangkok\n"
        "schedules:\n"
        "  business:\n"
        "    weekly:\n"
        "      mon: ['18:00-09:00']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="ends at or before it starts"):
        QueueHours.load(bad)


def test_every_queue_names_a_schedule_that_exists() -> None:
    """The config cross-check that actually protects the demo."""
    from readycall.domainpack import DomainPack

    pack = DomainPack.load(REPO_ROOT / "config")
    hours = QueueHours.load(HOURS_YAML)
    unknown = {q.queue_id: q.hours for q in pack.queues.values() if q.hours not in hours.schedules}
    assert not unknown, f"queues point at schedules that do not exist: {unknown}"


def test_next_open_gives_up_rather_than_spinning() -> None:
    """A queue open on no day at all is legal config; it must not hang the IVR."""
    empty = QueueHours(
        timezone="Asia/Bangkok",
        schedules={
            "dead": Schedule(
                schedule_id="dead",
                label_th="",
                always_open=False,
                observes_holidays=False,
                weekly=tuple(() for _ in range(7)),
            )
        },
        holidays={},
    )
    state = empty.state("dead", datetime(2026, 8, 24, 10, 0, tzinfo=empty.tz))
    assert not state.is_open
    assert state.next_open_at is None


def test_window_contains_is_half_open() -> None:
    window = Window(start=time(9, 0), end=time(17, 0))
    assert window.contains(time(9, 0))
    assert not window.contains(time(17, 0))
    assert window.contains((datetime(2026, 1, 1, 17, 0) - timedelta(seconds=1)).time())
