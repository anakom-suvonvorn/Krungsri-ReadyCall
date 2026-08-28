"""How close is an agent in after-call work to actually being free? (`D85`)

`D73` established that `expected_free_in(agent)` is dominated by **after-call work**, not
by how nearly the conversation is over — because `D45` put a whole phase between hanging
up and being offerable. It left the second term as "whatever ACW distribution the metrics
show". This is that term.

**The counter-intuitive part, and the reason this is worth doing properly.** The obvious
model is "ACW averages two minutes, so at 1:30 they are 30 seconds away." That is wrong in
a specific and useful way. Task durations are **right-skewed** — most wrap-ups are short,
a few are very long — and for such a distribution the *expected remaining* time first falls
and then **rises again**. An agent five minutes into a two-minute-average wrap-up is not
overdue; they are almost certainly having a long one, and the best estimate of what is left
grows rather than shrinks.

So the preference curve is not a rule anybody tunes. It falls out:

```
 just ended  ──▶  near the mean  ──▶  well past it
   the whole      the remainder      the remainder
   wrap-up is     is small, so       grows again, so
   still ahead    prefer them        stop preferring them
   (low)          (peak)             (declining)
```

which is exactly the shape the product wanted, derived from the data instead of asserted.
`tests/unit/test_acw_stats.py` asserts the curve has that shape rather than trusting it.

**Two guards that matter more than the maths:**

* **Shrinkage.** On demo day an agent has three completed wrap-ups. A mean and a standard
  deviation from three samples is noise dressed as a measurement, so every estimate is
  blended toward the population mean with a prior weight — an agent earns their own curve
  as evidence accumulates, and borrows everyone else's until then.
* **This is a SCORE, never a filter** (`D22`: hard filters exclude, they do not down-rank).
  A busy queue with one agent in long wrap-up must still reach that agent. Preferring
  someone less is not the same as refusing them, and only the first is safe here.

Derived, not stored (`D78`): a completed `Assignment` already carries `acw_started_at` and
`acw_ended_at`, so the durations are a projection of records that exist. No new table.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import exp, log, sqrt
from statistics import NormalDist

from readycall.domain.models import Assignment

#: Wrap-ups shorter than this are not wrap-ups — they are an agent clearing the screen.
#: Including them drags the mean down and makes everybody look imminently free.
MIN_SAMPLE_S = 5.0

#: How much population evidence a brand-new agent borrows, in units of "samples".
#: 5 means an agent with 5 of their own is halfway to trusting themselves.
DEFAULT_PRIOR_WEIGHT = 5.0

#: Used until anybody has finished a single wrap-up. A guess, and labelled as one.
DEFAULT_PRIOR_MEAN_S = 90.0

#: Spread of log-durations when there is nothing to measure. 0.6 is a moderately
#: right-skewed task: most wrap-ups within a factor of two, a thin long tail.
DEFAULT_PRIOR_SIGMA = 0.6

_NORMAL = NormalDist()


@dataclass(frozen=True, slots=True)
class AcwProfile:
    """One agent's wrap-up behaviour, after shrinkage."""

    agent_id: str
    #: Completed wrap-ups actually observed for this agent.
    samples: int
    #: Parameters of the fitted log-normal, already blended with the population.
    log_mu: float
    log_sigma: float
    #: The population values this was blended toward, kept so a screen can say
    #: "mostly borrowed" rather than presenting a guess as a measurement.
    population_samples: int

    @property
    def mean_s(self) -> float:
        """Expected wrap-up length from a standing start."""
        return exp(self.log_mu + self.log_sigma**2 / 2)

    @property
    def median_s(self) -> float:
        return exp(self.log_mu)

    @property
    def is_mostly_borrowed(self) -> bool:
        """True while this curve is more population than person. Say so on any screen
        that shows it — a number sourced mostly from other people's behaviour must not
        look like a measurement of this one."""
        return self.samples < DEFAULT_PRIOR_WEIGHT


def _fit_log_normal(durations: Sequence[float]) -> tuple[float, float]:
    """Mean and stdev of the LOGS. Empty input is the caller's problem, not ours."""
    logs = [log(d) for d in durations]
    mu = sum(logs) / len(logs)
    if len(logs) < 2:
        return mu, DEFAULT_PRIOR_SIGMA
    variance = sum((value - mu) ** 2 for value in logs) / (len(logs) - 1)
    return mu, max(sqrt(variance), 0.05)


class AcwStats:
    """Per-agent wrap-up profiles, projected from completed assignments.

    Rebuilt rather than accumulated: the source is a list of assignments the caller
    already holds, so there is no second copy of the truth to keep in step (`D78`).
    """

    def __init__(
        self,
        *,
        prior_weight: float = DEFAULT_PRIOR_WEIGHT,
        prior_mean_s: float = DEFAULT_PRIOR_MEAN_S,
        prior_sigma: float = DEFAULT_PRIOR_SIGMA,
    ) -> None:
        self._prior_weight = prior_weight
        self._prior_mu = log(prior_mean_s)
        self._prior_sigma = prior_sigma
        self._by_agent: dict[str, list[float]] = {}
        self._population: list[float] = []

    # --- building ---------------------------------------------------------------------

    def rebuild(self, assignments: Iterable[Assignment]) -> AcwStats:
        """Project every completed wrap-up. Idempotent; call it whenever you like."""
        self._by_agent = {}
        self._population = []
        for assignment in assignments:
            seconds = assignment.acw_seconds
            if seconds is None or seconds < MIN_SAMPLE_S:
                continue
            self._by_agent.setdefault(assignment.agent_id, []).append(seconds)
            self._population.append(seconds)
        return self

    # --- reading ----------------------------------------------------------------------

    def profile(self, agent_id: str) -> AcwProfile:
        """This agent's curve, shrunk toward the population (see the module docstring)."""
        own = self._by_agent.get(agent_id, [])
        pop_mu, pop_sigma = (
            _fit_log_normal(self._population)
            if len(self._population) >= 2
            else (self._prior_mu, self._prior_sigma)
        )

        if not own:
            return AcwProfile(
                agent_id=agent_id,
                samples=0,
                log_mu=pop_mu,
                log_sigma=pop_sigma,
                population_samples=len(self._population),
            )

        own_mu, own_sigma = _fit_log_normal(own)
        weight = len(own)
        total = weight + self._prior_weight
        return AcwProfile(
            agent_id=agent_id,
            samples=weight,
            log_mu=(weight * own_mu + self._prior_weight * pop_mu) / total,
            log_sigma=(weight * own_sigma + self._prior_weight * pop_sigma) / total,
            population_samples=len(self._population),
        )

    def expected_remaining_s(self, agent_id: str, elapsed_s: float) -> float:
        """How much longer this wrap-up probably has to run, given it is already
        `elapsed_s` old.

        `E[T - t | T > t]` for the fitted log-normal. The conditioning is the whole point:
        at `t = 0` this is simply the mean, and past the mean it **grows**, because having
        already run long is evidence of being a long one.
        """
        profile = self.profile(agent_id)
        if elapsed_s <= 0:
            return profile.mean_s

        mu, sigma = profile.log_mu, profile.log_sigma
        z = (log(elapsed_s) - mu) / sigma
        survival = 1.0 - _NORMAL.cdf(z)
        if survival <= 1e-9:
            # Numerically past the far tail. The distribution has nothing useful left to
            # say, so fall back to the mean rather than dividing by ~zero and returning a
            # confident absurdity.
            return profile.mean_s
        conditional_mean = profile.mean_s * _NORMAL.cdf(sigma - z) / survival
        return max(0.0, conditional_mean - elapsed_s)

    def readiness(self, agent_id: str, elapsed_s: float, *, horizon_s: float = 60.0) -> float:
        """A bounded 0-1 preference: 1 means "free now", 0 means "not for a long while".

        `horizon_s` sets how quickly preference decays with predicted remaining time — at
        one horizon away the score is about 0.37. It is a knob on *urgency of preference*,
        not on the prediction itself, which is why it lives here and not in the model.
        """
        remaining = self.expected_remaining_s(agent_id, elapsed_s)
        return exp(-remaining / max(horizon_s, 1.0))

    # --- introspection, because a score nobody can decompose is not explainable (D18) ---

    def explain(self, agent_id: str, elapsed_s: float) -> dict[str, float | int | bool]:
        profile = self.profile(agent_id)
        return {
            "samples": profile.samples,
            "population_samples": profile.population_samples,
            "mostly_borrowed": profile.is_mostly_borrowed,
            "median_s": round(profile.median_s, 1),
            "mean_s": round(profile.mean_s, 1),
            "elapsed_s": round(elapsed_s, 1),
            "expected_remaining_s": round(self.expected_remaining_s(agent_id, elapsed_s), 1),
            "readiness": round(self.readiness(agent_id, elapsed_s), 3),
        }


__all__ = [
    "DEFAULT_PRIOR_MEAN_S",
    "DEFAULT_PRIOR_SIGMA",
    "DEFAULT_PRIOR_WEIGHT",
    "MIN_SAMPLE_S",
    "AcwProfile",
    "AcwStats",
]
