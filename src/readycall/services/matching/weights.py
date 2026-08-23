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

    max_wait_before_any_agent_s: float
    defer_enabled: bool
    defer_max_wait_s: float
    defer_max_hold_s: float
    defer_min_fit_gap: float
    defer_never_above_urgency: Urgency
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
            defer_enabled=bool(guards.get("defer_enabled", True)),
            defer_max_wait_s=float(guards.get("defer_max_wait_s", 60)),
            defer_max_hold_s=float(guards.get("defer_max_hold_s", 25)),
            defer_min_fit_gap=float(guards.get("defer_min_fit_gap", 0.25)),
            defer_never_above_urgency=Urgency(guards.get("defer_never_above_urgency", "high")),
            hot_spot_window_calls=int(guards.get("hot_spot_window_calls", 20)),
            hot_spot_max_share=float(guards.get("hot_spot_max_share", 0.35)),
        )
        weights.validate()
        return weights

    def validate(self) -> None:
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
