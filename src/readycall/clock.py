"""Time is injected, never read from the wall directly.

Every stage of this system is judged on timing (`ARCHITECTURE.md` §15) and every
scenario replay has to be reproducible (`D18`), which is impossible if code calls
`datetime.now()` wherever it likes. So: one `Clock` port, a real one in production,
and a `ManualClock` that tests and the scenario runner advance by hand.

Rule: nothing outside this module imports `time` or `datetime.now`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, always timezone-aware UTC."""
        ...

    def monotonic_ms(self) -> float:
        """Monotonic milliseconds, for measuring durations (never for timestamps)."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ms(self) -> float:
        """High-resolution monotonic milliseconds.

        `time.perf_counter()`, **not** `time.monotonic()` (`B3`). Both are monotonic, but
        on Windows `monotonic()` is backed by the ~15.6 ms system tick: measured on the dev
        laptop it produced *2 distinct values* across 200,000 samples, where `perf_counter`
        produced 200,000. Every stage faster than a tick therefore recorded as exactly
        `0.0`, which silently emptied the timing records that `D18` exists to provide and
        made the demo report "context assembled in 0.0 ms".
        """
        return time.perf_counter() * 1000.0


class ManualClock:
    """A clock that only moves when told to.

    Used by tests and `scripts/run_scenario.py`: a scripted call can advance 45
    seconds instantly, so a full call lifecycle runs in milliseconds and produces
    the *same* timeline every run.
    """

    def __init__(self, start: datetime | None = None) -> None:
        # WATCH OUT: the default is 09:00 UTC on 1 January, which is 16:00 in Bangkok on
        # **New Year's Day** — a public holiday. Every queue on the `business` schedule is
        # therefore CLOSED under a default `ManualClock`, and a test that places a call
        # will get `queue_closed:holiday` instead of an offer (`config/queue_hours.yaml`).
        # The default is not changed because scenario replays are byte-compared against
        # golden output built on it; pass an explicit `start` when hours matter.
        self._now = start or datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic_ms(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)
        self._mono += seconds * 1000.0


class Stopwatch:
    """Measures one stage, in monotonic milliseconds.

    Every stage records its duration into `call_sessions.stage_timings_json` — that
    is what lets the demo *show* "context ready in 1.2 s" rather than assert it.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._start = clock.monotonic_ms()

    def elapsed_ms(self) -> float:
        return self._clock.monotonic_ms() - self._start

    def reset(self) -> None:
        self._start = self._clock.monotonic_ms()
