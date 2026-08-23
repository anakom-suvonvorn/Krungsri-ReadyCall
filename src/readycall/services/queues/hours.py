"""Is this queue open right now — and if not, when does it open again?

A closed queue is a **routing outcome**, not an error (`D25`). The caller is offered a
briefed callback that runs the same intake pipeline, so "closed" has to be a first-class
answer with a *next open time* attached, not a boolean the caller has to interpret.

Everything here is pure: given a `datetime`, it answers. No clock is read inside, because
the clock is injected everywhere else in this codebase (`D35`) and an hours checker that
secretly calls `now()` cannot be tested against a Sunday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from readycall.errors import ConfigError

#: Monday-first, matching `datetime.weekday()`.
_DAY_KEYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True, slots=True)
class Window:
    """One open period on one weekday. Start inclusive, end exclusive."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class Schedule:
    schedule_id: str
    label_th: str
    always_open: bool
    observes_holidays: bool
    #: Index 0 = Monday, matching `datetime.weekday()`.
    weekly: tuple[tuple[Window, ...], ...]


@dataclass(frozen=True, slots=True)
class OpenState:
    """The answer, with enough detail for the IVR to say something true."""

    is_open: bool
    schedule_id: str
    #: Local-time moment the queue next opens. `None` when already open, or when the
    #: schedule is `always_open` (nothing to wait for), or when no window exists at all.
    next_open_at: datetime | None = None
    #: Populated only when a holiday is the reason, so the prompt can name it.
    holiday_th: str | None = None

    @property
    def closed_reason(self) -> str | None:
        if self.is_open:
            return None
        return "holiday" if self.holiday_th else "outside_hours"


class QueueHours:
    """Named schedules + a holiday calendar, loaded from `config/queue_hours.yaml`."""

    def __init__(
        self,
        *,
        timezone: str,
        schedules: dict[str, Schedule],
        holidays: dict[date, str],
    ) -> None:
        self.timezone_name = timezone
        try:
            self.tz = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            # Windows ships no tz database; `tzdata` supplies it. Worth a real error
            # message because the failure is platform-asymmetric - green on Linux CI,
            # broken on the laptop the demo runs from.
            raise ConfigError(
                f"time zone {timezone!r} not found. On Windows this means the `tzdata` "
                f"package is missing - it is a declared dependency; run `uv sync`."
            ) from exc
        self.schedules = schedules
        self.holidays = holidays

    # --- loading ----------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> QueueHours:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"queue hours config not found: {path}")
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        schedules: dict[str, Schedule] = {}
        for schedule_id, body in (raw.get("schedules") or {}).items():
            schedules[schedule_id] = Schedule(
                schedule_id=schedule_id,
                label_th=body.get("label_th", schedule_id),
                always_open=bool(body.get("always_open", False)),
                observes_holidays=bool(body.get("observes_holidays", True)),
                weekly=cls._parse_weekly(schedule_id, body.get("weekly") or {}),
            )
        if not schedules:
            raise ConfigError(f"{path} defines no schedules")

        holidays: dict[date, str] = {}
        for entry in raw.get("holidays") or []:
            try:
                holidays[date.fromisoformat(str(entry["date"]))] = entry.get("label_th", "")
            except (KeyError, ValueError) as exc:
                raise ConfigError(f"malformed holiday entry {entry!r}: {exc}") from exc

        return cls(
            timezone=raw.get("timezone", "Asia/Bangkok"),
            schedules=schedules,
            holidays=holidays,
        )

    @staticmethod
    def _parse_weekly(schedule_id: str, weekly: dict[str, Any]) -> tuple[tuple[Window, ...], ...]:
        days: list[tuple[Window, ...]] = []
        for key in _DAY_KEYS:
            windows: list[Window] = []
            for spec in weekly.get(key) or []:
                try:
                    start_s, end_s = str(spec).split("-", 1)
                    start = time.fromisoformat(start_s.strip())
                    end = time.fromisoformat(end_s.strip())
                except ValueError as exc:
                    raise ConfigError(
                        f"schedule {schedule_id!r}, {key}: {spec!r} is not 'HH:MM-HH:MM'"
                    ) from exc
                if end <= start:
                    # A window that ends before it starts is silently never open, which
                    # would show up as a queue nobody can reach. Refuse it at load time.
                    raise ConfigError(
                        f"schedule {schedule_id!r}, {key}: {spec!r} ends at or before it starts"
                    )
                windows.append(Window(start=start, end=end))
            days.append(tuple(sorted(windows, key=lambda w: w.start)))
        return tuple(days)

    # --- the question everything else asks --------------------------------------------

    def state(self, schedule_id: str, at: datetime) -> OpenState:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            raise ConfigError(f"unknown schedule {schedule_id!r}")

        local = at.astimezone(self.tz)
        if schedule.always_open:
            return OpenState(is_open=True, schedule_id=schedule_id)

        holiday = self.holidays.get(local.date()) if schedule.observes_holidays else None
        if holiday is None:
            for window in schedule.weekly[local.weekday()]:
                if window.contains(local.time()):
                    return OpenState(is_open=True, schedule_id=schedule_id)

        return OpenState(
            is_open=False,
            schedule_id=schedule_id,
            next_open_at=self._next_open(schedule, local),
            holiday_th=holiday,
        )

    def is_open(self, schedule_id: str, at: datetime) -> bool:
        return self.state(schedule_id, at).is_open

    def _next_open(
        self, schedule: Schedule, local: datetime, *, horizon_days: int = 21
    ) -> datetime | None:
        """Walk forward to the next opening moment.

        A bounded walk rather than arithmetic: holidays, empty Sundays and multi-window
        days make the closed-form version the kind of code nobody trusts. `horizon_days`
        stops a schedule that is open on no day at all from spinning forever - that
        config is legal (a decommissioned queue) and must not hang the IVR.
        """
        for offset in range(horizon_days + 1):
            day = local.date() + timedelta(days=offset)
            if schedule.observes_holidays and day in self.holidays:
                continue
            for window in schedule.weekly[day.weekday()]:
                candidate = datetime.combine(day, window.start, tzinfo=self.tz)
                if candidate > local:
                    return candidate
        return None


__all__ = ["OpenState", "QueueHours", "Schedule", "Window"]
