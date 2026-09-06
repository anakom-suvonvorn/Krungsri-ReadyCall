"""Builds the `CaseBrief` the agent reads.

P1 builds the **context-only** brief: everything that comes from data and the keypad menu,
with no speech and no AI involved. That is deliberately the first version, because it is
the one that must never fail — it is what an agent gets when the caller declines the
recording, when the STT worker is down, and when the LLM times out (`ARCHITECTURE.md` §16).

Two rules are enforced here rather than trusted:

* **Disclosure is gated by assurance** (`D20`). Below L2, policy numbers and coverage
  figures are not rendered at all. A borrowed phone must not surrender someone's cover.
* **Numbers come from data, never from a model** (`D16`). Every figure on the brief is
  read out of a typed `Coverage` field. There is no code path here that could put a
  generated number on screen — the LLM is not even a dependency of this module.
"""

from __future__ import annotations

from readycall import ids
from readycall.clock import Clock, Stopwatch
from readycall.domain.enums import (
    AssuranceLevel,
    BriefKind,
    DegradationReason,
    ProductLine,
    Urgency,
)
from readycall.domain.models import (
    CaseBrief,
    ContextSnapshot,
    IdentityResolution,
    IntentPrediction,
    RecommendedAction,
)
from readycall.domainpack import DomainPack, IntentSpec
from readycall.logging import get_logger

log = get_logger(__name__)

MASKED = "•••"


class BriefBuilder:
    def __init__(self, *, pack: DomainPack, clock: Clock) -> None:
        self._pack = pack
        self._clock = clock

    def build_context_only(
        self,
        *,
        call_session_id: str,
        snapshot: ContextSnapshot,
        identity: IdentityResolution,
        intent_code: str | None = None,
        product_line: ProductLine = ProductLine.UNKNOWN,
        urgency_floor: Urgency | None = None,
        version: int = 1,
        degraded: DegradationReason = DegradationReason.NONE,
    ) -> CaseBrief:
        """Version 1 of the brief: data + menu, no speech, no AI."""
        watch = Stopwatch(self._clock)
        spec = self._resolve_intent(intent_code, product_line)
        intent = self._prediction(spec, intent_code)

        urgency = spec.default_urgency
        if urgency_floor is not None and urgency_floor.weight > urgency.weight:
            # A motor-claims DID raises the floor before anyone has said a word.
            urgency = urgency_floor

        may_disclose = identity.may_act_on_policy
        # Whether there is anyone to show at all (`D74`). At L0 the frozen snapshot may
        # still hold a policy from a match the agent has since REJECTED, so this gate has
        # to come from the identity rather than from the payload — otherwise the rendered
        # Thai sentence leaks what the structured fields correctly withheld, which is `B5`
        # with the two halves swapped.
        may_see = identity.may_see_record
        if not may_disclose and degraded is DegradationReason.NONE:
            degraded = DegradationReason.LOW_ASSURANCE

        brief = CaseBrief(
            brief_id=ids.brief_id(),
            call_session_id=call_session_id,
            version=version,
            kind=BriefKind.CONTEXT_ONLY,
            built_at=self._clock.now(),
            intent=intent,
            confidence=None,  # no number until speech and calibration exist (D13)
            summary_th=self._summary(snapshot, spec, may_disclose=may_disclose, may_see=may_see),
            entities=(),
            recommended_actions=self._actions(spec, may_disclose=may_disclose),
            next_best_action=None,
            suggested_opening_th=self._opening(snapshot, spec, may_disclose=may_disclose),
            urgency=urgency,
            snapshot=snapshot,
            identity=identity,
            degraded=degraded,
            sources={
                "intent_from": "menu" if intent_code else "product_line_default",
                "assurance": str(identity.assurance),
                "policy_details_disclosed": may_disclose,
                "provider": snapshot.provider_name,
            },
            build_ms=watch.elapsed_ms(),
        )
        log.info(
            "context-only brief built",
            call_session_id=call_session_id,
            intent=intent.intent_code,
            urgency=str(urgency),
            disclosed=may_disclose,
            build_ms=round(brief.build_ms or 0.0, 2),
        )
        return brief

    # --- pieces -----------------------------------------------------------------------

    def _resolve_intent(self, intent_code: str | None, line: ProductLine) -> IntentSpec:
        if intent_code and intent_code in self._pack.intents:
            return self._pack.intents[intent_code]
        # No menu answer yet: fall back to the line's catch-all, which still keeps the
        # product context rather than dropping to a fully generic unknown.
        return self._pack.catch_all_for(line)

    @staticmethod
    def _prediction(spec: IntentSpec, intent_code: str | None) -> IntentPrediction:
        # A keypress is not a guess. Confidence is high *because a human told us*, which
        # is exactly why the menu leads (`D37`) — but a fallback catch-all is not.
        from_menu = bool(intent_code)
        return IntentPrediction(
            intent_code=spec.code,
            label_th=spec.label_th,
            label_en=spec.label_en,
            confidence=0.95 if from_menu else 0.2,
            source="dtmf" if from_menu else "product_line_default",
            handoff_to_insurer=spec.handoff_to_insurer,
        )

    def _summary(
        self,
        snapshot: ContextSnapshot,
        spec: IntentSpec,
        *,
        may_disclose: bool,
        may_see: bool,
    ) -> str:
        """A factual Thai sentence assembled from fields. No model, so no invention."""
        customer = snapshot.payload.customer if may_see else None
        who = customer.polite_name_th if customer else "ผู้ติดต่อ (ยังไม่ระบุตัวตน)"
        parts = [f"{who} ติดต่อเรื่อง{spec.label_th}"]

        policy = snapshot.payload.relevant_policy if may_see else None
        if policy:
            # The number is shown whatever the level (`D74`). What changes below L2 is the
            # standing note that it may not be spoken or acted on yet — the agent needs
            # the number precisely so they can check what the caller tells them against it.
            suffix = "" if may_disclose else " (ยังไม่ยืนยันตัวตน — ยังใช้/อ้างอิงกับผู้ติดต่อไม่ได้)"
            parts.append(f"กรมธรรม์ {policy.policy_no}{suffix}")

        previous = snapshot.payload.previous_inquiry if may_see else None
        if previous:
            parts.append(f"ติดต่อครั้งล่าสุดเรื่อง {previous}")

        return " · ".join(parts)

    def _actions(self, spec: IntentSpec, *, may_disclose: bool) -> tuple[RecommendedAction, ...]:
        """From the intent's playbook — never improvised.

        The steps come from `config/playbooks.yaml` since `D118`, which closed `Q19`. They
        used to be a dict in this file, which was insurance content living in `services/`
        against `D28` — tolerable while it was six short lists, and not once the broker
        rewrite (`D117`) doubled it. A startup check now refuses an intent whose playbook
        does not exist AND a playbook no intent reaches, so neither half can rot.
        """
        playbook = self._pack.playbooks.get(spec.playbook) or self._pack.playbooks["generic"]
        actions: list[RecommendedAction] = []
        for order, step in enumerate(playbook.steps, start=1):
            if step.needs.rank > AssuranceLevel.L1_PROBABLE.rank and not may_disclose:
                continue
            actions.append(
                RecommendedAction(order=order, text_th=step.text_th, requires_assurance=step.needs)
            )
        if not may_disclose:
            actions.insert(
                0,
                RecommendedAction(
                    order=0,
                    text_th="ยืนยันตัวตนผู้ติดต่อก่อนให้ข้อมูลกรมธรรม์",
                    requires_assurance=AssuranceLevel.L0_ANONYMOUS,
                ),
            )
        return tuple(actions)

    @staticmethod
    def _opening(snapshot: ContextSnapshot, spec: IntentSpec, *, may_disclose: bool) -> str:
        """The line the agent says out loud — and below L2 it must not contain a name.

        `D55`. The screen may show "ภัทธีรา เสรีวัฒนชัย"; the agent may not *say* it until
        identity is established, for two reasons and the second is the stronger one:

        1. Greeting someone by name confirms to whoever is holding that phone that the
           number belongs to that person. A small leak, and free to avoid.
        2. **A leading question is weaker verification.** "ใช่คุณภัทธีราไหมคะ" can be
           answered "ใช่ครับ" by anybody. "ขอทราบชื่อผู้ติดต่อด้วยค่ะ" has to be
           *produced* — it is the difference between recognition and recall, and only one
           of them is evidence.

        This is the spoken half of action 0 (*ยืนยันตัวตนผู้ติดต่อก่อนให้ข้อมูลกรมธรรม์*),
        and it is drawn in `diagrams/src/identity_promotion.mmd`, which has said so since
        `D42` was written.
        """
        customer = snapshot.payload.customer
        if customer is None or not may_disclose:
            # Note what is deliberately absent even when `customer` exists: their name.
            return f"สวัสดีค่ะ ยินดีให้บริการเรื่อง{spec.label_th} ขอทราบชื่อผู้ติดต่อด้วยค่ะ"
        return f"สวัสดีค่ะ {customer.polite_name_th} ทราบว่าติดต่อเรื่อง{spec.label_th} ยินดีช่วยดูแลค่ะ"
