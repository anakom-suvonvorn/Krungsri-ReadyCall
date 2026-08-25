"""Which menu options a recognised caller hears first, and the evidence for each (`D37`).

The cheapest possible use of context we already prefetched: no extra fetch, no model, no
disclosure. **Assurance L1 is enough**, because reordering a menu reveals nothing — the
spoken line is still only the number and the plain label, and a caller who hears their own
plate number recited back at them by a machine is a caller having a worse time (`D37`).

Every promotion carries its reasons, for the same reason every other decision in this
system does (`D18`): "why was health first" has to be answerable, and the honest answer is
a list of facts about that customer, not a score nobody can decompose.

Deliberately pure. It takes facts, not stores — so it is trivially testable, and the caller
supplies whatever it actually has. A signal with no evidence available simply scores zero,
which is the same thing as an anonymous caller and needs no special case.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from readycall.domain.enums import ProductLine
from readycall.domain.models import Claim, Customer360, Policy
from readycall.domainpack import MenuSpec, PersonalisationSpec
from readycall.errors import ConfigError

#: The signals this module can actually compute. A weight in `menus.yaml` naming anything
#: else is a config error at startup, not a silently ignored line — config that claims to
#: do something it does not is worse than config that says nothing.
SUPPORTED_SIGNALS = frozenset(
    {
        "has_active_policy_in_line",
        "open_claim_in_line",
        "policy_due_soon",
        "recent_app_view",
    }
)


@dataclass(frozen=True, slots=True)
class PersonalisationInputs:
    """The facts the signals are computed from.

    `viewed_lines` is passed in rather than read here because the app-context store is
    windowed and lives behind its own service; this module stays a function of its inputs.
    """

    today: date
    active_policies: tuple[Policy, ...] = ()
    recent_claims: tuple[Claim, ...] = ()
    viewed_lines: frozenset[ProductLine] = frozenset()

    @classmethod
    def from_snapshot(
        cls,
        payload: Customer360 | None,
        *,
        today: date,
        viewed_lines: frozenset[ProductLine] = frozenset(),
    ) -> PersonalisationInputs:
        if payload is None:
            return cls(today=today, viewed_lines=viewed_lines)
        return cls(
            today=today,
            active_policies=tuple(p for p in payload.active_policies if p.is_active),
            recent_claims=payload.recent_claims,
            viewed_lines=viewed_lines,
        )


@dataclass(frozen=True, slots=True)
class Promotion:
    """One option moved up, and why."""

    key: str
    line: ProductLine
    score: float
    reasons: tuple[str, ...] = field(default=())


def _lines_with_open_claims(inputs: PersonalisationInputs, spec: PersonalisationSpec) -> set[str]:
    """Policy numbers with a claim still in flight, by product line."""
    open_policies = {
        claim.policy_no
        for claim in inputs.recent_claims
        if claim.status in spec.open_claim_statuses
    }
    return open_policies


def score_line(
    line: ProductLine, inputs: PersonalisationInputs, spec: PersonalisationSpec
) -> Promotion:
    """Score one product line for one caller, keeping the evidence attached."""
    score = 0.0
    reasons: list[str] = []
    in_line = [policy for policy in inputs.active_policies if policy.line is line]

    if in_line and (weight := spec.weights.get("has_active_policy_in_line")):
        score += weight
        reasons.append(f"active policy in {line.value}")

    open_policies = _lines_with_open_claims(inputs, spec)
    if (weight := spec.weights.get("open_claim_in_line")) and any(
        policy.policy_no in open_policies for policy in in_line
    ):
        # The strongest single predictor of why somebody is calling, which is why it
        # outweighs merely holding a policy in that line.
        score += weight
        reasons.append(f"open claim in {line.value}")

    horizon = inputs.today + timedelta(days=spec.renewal_window_days)
    if (weight := spec.weights.get("policy_due_soon")) and any(
        policy.next_due_date is not None and inputs.today <= policy.next_due_date <= horizon
        for policy in in_line
    ):
        score += weight
        reasons.append(f"renewal due within {spec.renewal_window_days} days")

    if line in inputs.viewed_lines and (weight := spec.weights.get("recent_app_view")):
        score += weight
        reasons.append("viewed in the app recently")

    return Promotion(key="", line=line, score=score, reasons=tuple(reasons))


def promote(
    menu: MenuSpec, inputs: PersonalisationInputs, spec: PersonalisationSpec
) -> tuple[Promotion, ...]:
    """Canonical keys to read first, best first. Empty when nothing is worth promoting.

    Only options that carry a product line can be promoted — a reason menu is already
    inside one line, and reordering "why are you calling" would need to guess the reason
    rather than the product, which is what the AI intake is for (`D37`).
    """
    if not spec.enabled or spec.max_promoted <= 0:
        return ()

    scored: list[Promotion] = []
    for option in menu.options:
        if option.product_line is None or option.product_line is ProductLine.UNKNOWN:
            continue
        promotion = score_line(option.product_line, inputs, spec)
        if promotion.score <= 0.0:
            continue
        scored.append(
            Promotion(
                key=option.key,
                line=promotion.line,
                score=promotion.score,
                reasons=promotion.reasons,
            )
        )

    # Ties break on the canonical key so the same caller hears the same menu twice.
    scored.sort(key=lambda p: (-p.score, p.key))
    return tuple(scored[: spec.max_promoted])


def validate_signals(spec: PersonalisationSpec | None) -> None:
    """Startup gate: every weighted signal must be one this module computes."""
    if spec is None:
        return
    unknown = sorted(set(spec.weights) - SUPPORTED_SIGNALS)
    if unknown:
        raise ConfigError(
            "menus.yaml personalisation weights name signals nothing computes: "
            f"{', '.join(unknown)}"
        )


def promoted_keys(promotions: Sequence[Promotion]) -> tuple[str, ...]:
    return tuple(promotion.key for promotion in promotions)


__all__ = [
    "SUPPORTED_SIGNALS",
    "PersonalisationInputs",
    "Promotion",
    "promote",
    "promoted_keys",
    "score_line",
    "validate_signals",
]
