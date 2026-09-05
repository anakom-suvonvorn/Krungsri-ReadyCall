"""Load `config/matching_weights.yaml` into a typed object.

Same discipline as the domain pack (`D28`): the tuning lives in YAML so a supervisor can
retune without a deploy, but "in YAML" must not mean "unchecked". A weight that is missing,
negative, or a string is a startup failure, not a silently wrong route at 2am.

`version` matters more than it looks. It is stamped on every `MatchingDecision`, so when
someone asks *"why did that call go there last Tuesday"*, the answer can include which
weights were in force at the time.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from readycall.domain.enums import Urgency
from readycall.errors import ConfigError


@dataclass(frozen=True, slots=True)
class MatchingWeights:
    version: str

    fit_skill_match: float
    fit_continuity: float
    fit_historical: float
    fit_load_penalty: float
    continuity_max_age_days: int
    continuity_requires_good_outcome: bool

    urgency_wait_pressure: float
    urgency_sla_risk: float
    urgency_customer_priority: float
    urgency_situational: float
    urgency_min: float
    urgency_max: float

    require_skill: bool
    require_language: bool
    min_language_level: str
    respect_max_concurrent: bool
    respect_schedule: bool

    #: The default ceiling, used for any urgency tier the config does not name.
    max_wait_before_any_agent_s: float
    #: Per-tier ceilings (`D94`). Always fully populated — every `Urgency` member has an
    #: entry, filled from the default above when the YAML omits it — so `ceiling_for` is a
    #: lookup rather than a lookup-with-a-fallback-branch that only one tier ever takes.
    max_wait_by_urgency: dict[Urgency, float]
    defer_enabled: bool
    defer_max_wait_s: float
    defer_max_hold_s: float
    defer_min_fit_gap: float
    defer_never_above_urgency: Urgency
    #: How many times a caller may be sent round the whole floor before the system stops
    #: re-offering them (`D113`, closing `Q31`). **0 means no cap — keep circling**, and
    #: that is the default, deliberately: a caller who is cut off has to start again from
    #: the menu, while a caller who is still holding can hang up whenever they choose.
    #: Business logic, so it lives in config rather than in a decision about behaviour.
    max_offer_rounds: int
    hot_spot_window_calls: int
    hot_spot_max_share: float

    @classmethod
    def load(cls, path: Path | str = Path("config/matching_weights.yaml")) -> MatchingWeights:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"matching weights not found: {p}")
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{p} must contain a mapping at the top level")

        fit = raw.get("fit") or {}
        urg = raw.get("urgency") or {}
        hard = raw.get("hard_filters") or {}
        guards = raw.get("guards") or {}

        def num(section: dict[str, Any], key: str, where: str) -> float:
            try:
                value = float(section[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"{p}: {where}.{key} missing or not a number") from exc
            if value < 0:
                raise ConfigError(f"{p}: {where}.{key} must not be negative (got {value})")
            return value

        weights = cls(
            version=str(raw.get("version", "unversioned")),
            fit_skill_match=num(fit, "skill_match", "fit"),
            fit_continuity=num(fit, "continuity", "fit"),
            fit_historical=num(fit, "historical_fit", "fit"),
            fit_load_penalty=num(fit, "load_penalty", "fit"),
            continuity_max_age_days=int(fit.get("continuity_max_age_days", 60)),
            continuity_requires_good_outcome=bool(
                fit.get("continuity_requires_good_outcome", True)
            ),
            urgency_wait_pressure=num(urg, "wait_pressure", "urgency"),
            urgency_sla_risk=num(urg, "sla_risk", "urgency"),
            urgency_customer_priority=num(urg, "customer_priority", "urgency"),
            urgency_situational=num(urg, "situational", "urgency"),
            urgency_min=float(urg.get("min_multiplier", 1.0)),
            urgency_max=float(urg.get("max_multiplier", 3.0)),
            require_skill=bool(hard.get("require_skill", True)),
            require_language=bool(hard.get("require_language", True)),
            min_language_level=str(hard.get("min_language_level", "b1")),
            respect_max_concurrent=bool(hard.get("respect_max_concurrent", True)),
            respect_schedule=bool(hard.get("respect_schedule", True)),
            max_wait_before_any_agent_s=float(guards.get("max_wait_before_any_agent_s", 180)),
            max_wait_by_urgency=cls._ceilings(
                guards.get("max_wait_before_any_agent_by_urgency"),
                default=float(guards.get("max_wait_before_any_agent_s", 180)),
                where=str(p),
            ),
            defer_enabled=bool(guards.get("defer_enabled", True)),
            defer_max_wait_s=float(guards.get("defer_max_wait_s", 60)),
            defer_max_hold_s=float(guards.get("defer_max_hold_s", 25)),
            defer_min_fit_gap=float(guards.get("defer_min_fit_gap", 0.25)),
            defer_never_above_urgency=Urgency(guards.get("defer_never_above_urgency", "high")),
            max_offer_rounds=int(guards.get("max_offer_rounds", 0)),
            hot_spot_window_calls=int(guards.get("hot_spot_window_calls", 20)),
            hot_spot_max_share=float(guards.get("hot_spot_max_share", 0.35)),
        )
        weights.validate()
        return weights

    @staticmethod
    def _ceilings(raw: Any, *, default: float, where: str) -> dict[Urgency, float]:
        """Resolve the per-tier wait ceilings, filling anything unnamed from the default.

        An unknown key is refused rather than ignored: a typo like `urgent:` would
        otherwise leave that tier silently on the default, which is exactly the class of
        wrong-but-plausible config this file exists to catch.
        """
        table = dict.fromkeys(Urgency, default)
        if raw is None:
            return table
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: guards.max_wait_before_any_agent_by_urgency must be a map")
        for key, value in raw.items():
            try:
                tier = Urgency(str(key))
            except ValueError as exc:
                known = ", ".join(u.value for u in Urgency)
                raise ConfigError(
                    f"{where}: unknown urgency '{key}' in "
                    f"max_wait_before_any_agent_by_urgency (known: {known})"
                ) from exc
            try:
                seconds = float(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{where}: ceiling for '{key}' is not a number") from exc
            if seconds <= 0:
                raise ConfigError(f"{where}: ceiling for '{key}' must be positive")
            table[tier] = seconds
        return table

    def ceiling_for(self, urgency: Urgency) -> float:
        """How long THIS caller waits before fit stops mattering at all (`D94`)."""
        return self.max_wait_by_urgency[urgency]

    def validate(self) -> None:
        # A more urgent caller must never be made to wait LONGER for the absolute
        # guarantee than a less urgent one. The table is free-form YAML, so this is
        # trivially easy to get backwards while editing, and getting it backwards is
        # silent: every call still routes, and the crash-scene caller simply waits.
        by_patience = sorted(Urgency, key=lambda u: u.weight)
        for lower, higher in pairwise(by_patience):
            if self.ceiling_for(higher) > self.ceiling_for(lower):
                raise ConfigError(
                    f"guards.max_wait_before_any_agent_by_urgency: '{higher.value}' waits "
                    f"{self.ceiling_for(higher)}s but the less urgent '{lower.value}' waits "
                    f"only {self.ceiling_for(lower)}s - ceilings must not rise with urgency"
                )
        if self.max_offer_rounds < 0:
            raise ConfigError(
                "guards.max_offer_rounds must be 0 (no cap, keep circling) or a positive "
                "number of rounds - a negative cap would stop a caller being offered at all"
            )
        if self.urgency_max < self.urgency_min:
            raise ConfigError("urgency max_multiplier is below min_multiplier")
        if self.urgency_min < 1.0:
            # Below 1.0 urgency would *reduce* a score, so a caller waiting longer would
            # rank lower. That is the opposite of the intent and worth refusing outright.
            raise ConfigError("urgency min_multiplier must be at least 1.0")
        if self.fit_skill_match <= 0:
            raise ConfigError("fit.skill_match must be positive, or skill stops mattering")
        if not 0.0 < self.hot_spot_max_share <= 1.0:
            raise ConfigError("guards.hot_spot_max_share must be in (0, 1]")


__all__ = ["MatchingWeights"]
