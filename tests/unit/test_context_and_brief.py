"""Context assembly, the caching layer, and the context-only brief.

The theme running through all of these: **the call must survive whatever the data does.**
A dead upstream, a stale cache, an unrecognised caller, an intent we do not have — each
one produces a thinner brief with a stated reason, never an exception and never a blank
screen (`D12`, `ARCHITECTURE.md` §16).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from readycall.adapters.core_data.caching import CachingCoreDataProvider
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.clock import ManualClock
from readycall.domain.enums import (
    AssuranceLevel,
    BriefKind,
    DegradationReason,
    IdentityMethod,
    ProductLine,
    Urgency,
)
from readycall.domain.models import IdentityResolution
from readycall.domainpack import DomainPack
from readycall.services.brief import BriefBuilder
from readycall.services.context import ContextAssembler
from tests.conftest import FIXTURES_DIR, REPO_ROOT


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    return DomainPack.load(REPO_ROOT / "config")


@pytest.fixture
def assembler(clock: ManualClock) -> ContextAssembler:
    return ContextAssembler(core=FixtureFileProvider(FIXTURES_DIR), clock=clock)


@pytest.fixture
def builder(pack: DomainPack, clock: ManualClock) -> BriefBuilder:
    return BriefBuilder(pack=pack, clock=clock)


def identity(level: AssuranceLevel, customer_id: str | None = "C000001") -> IdentityResolution:
    return IdentityResolution(
        method=IdentityMethod.ANI,
        customer_id=customer_id,
        assurance=level,
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestContextAssembler:
    async def test_builds_a_full_snapshot(self, assembler: ContextAssembler) -> None:
        snapshot = await assembler.build(
            customer_id="C000001", product_code="KS-HEALTH-A", product_line=ProductLine.HEALTH
        )
        payload = snapshot.payload
        assert payload.customer is not None
        assert payload.customer.customer_id == "C000001"
        assert payload.selected_product is not None
        # Three since `D117`: the demo customer holds a broker PORTFOLIO - two health
        # policies from different carriers (one of them employer group cover) and a motor
        # policy from a third. One policy per customer was an insurer's view of the world.
        assert len(payload.active_policies) == 3
        assert len({p.insurer for p in payload.active_policies}) == 3, (
            "and they are with three different carriers, which is the whole broker premise"
        )
        assert payload.recent_interactions
        assert payload.last_contact_at is not None
        assert payload.previous_inquiry == "viewed_hospitalization_coverage"
        assert snapshot.build_ms is not None

    async def test_every_populated_field_carries_provenance(
        self, assembler: ContextAssembler
    ) -> None:
        """`D18`: the workstation must be able to say "says who, and how old?"."""
        snapshot = await assembler.build(customer_id="C000001", product_code="KS-HEALTH-A")
        fields = {p.field for p in snapshot.provenance}
        assert {"customer", "active_policies", "recent_interactions"} <= fields
        for prov in snapshot.provenance:
            assert prov.source.startswith("core:")
            assert prov.provider
            assert prov.fetched_at == snapshot.built_at

    async def test_an_anonymous_caller_still_gets_a_snapshot(
        self, assembler: ContextAssembler
    ) -> None:
        """No special case for the caller of this method — just a thinner result."""
        snapshot = await assembler.build(customer_id=None)
        assert snapshot.customer_id is None
        assert snapshot.payload.customer is None
        assert snapshot.degraded is DegradationReason.LOW_ASSURANCE

    async def test_a_dead_core_degrades_instead_of_raising(self, clock: ManualClock) -> None:
        assembler = ContextAssembler(core=NullCoreDataProvider(), clock=clock)
        snapshot = await assembler.build(customer_id="C000001")
        assert snapshot.degraded is DegradationReason.CORE_DATA_UNAVAILABLE
        assert snapshot.payload.active_policies == ()

    async def test_relevant_policy_prefers_the_tapped_plan(
        self, assembler: ContextAssembler
    ) -> None:
        snapshot = await assembler.build(
            customer_id="C000002", product_code="KS-TRAVEL-ASIA", product_line=ProductLine.TRAVEL
        )
        assert snapshot.payload.relevant_policy is not None
        assert snapshot.payload.relevant_policy.policy_no == "TR-2026-001204"

    async def test_relevant_policy_falls_back_to_the_product_line(
        self, assembler: ContextAssembler
    ) -> None:
        snapshot = await assembler.build(customer_id="C000002", product_line=ProductLine.MOTOR)
        assert snapshot.payload.relevant_policy is not None
        assert snapshot.payload.relevant_policy.line is ProductLine.MOTOR

    async def test_it_declines_to_guess_between_unrelated_policies(
        self, assembler: ContextAssembler
    ) -> None:
        """C000002 holds motor and travel. With no signal, picking one would be worse
        than picking none — a confidently wrong policy on screen costs the agent time."""
        snapshot = await assembler.build(customer_id="C000002")
        assert len(snapshot.payload.active_policies) == 2
        assert snapshot.payload.relevant_policy is None

    async def test_lapsed_policies_are_excluded(self, assembler: ContextAssembler) -> None:
        snapshot = await assembler.build(customer_id="C000003")
        assert snapshot.payload.active_policies == ()


class TestCachingProvider:
    async def test_a_repeat_read_is_served_from_cache(self, clock: ManualClock) -> None:
        core = CachingCoreDataProvider(FixtureFileProvider(FIXTURES_DIR), clock=clock, ttl_s=60)
        await core.get_customer("C000001")
        assert core.cache_size() == 1
        await core.get_customer("C000001")
        assert core.cache_size() == 1
        assert not core.served_stale

    async def test_stale_data_beats_no_data_and_is_marked(self, clock: ManualClock) -> None:
        """`ARCHITECTURE.md` §16: a slightly old policy list with a staleness badge is
        far better than an empty screen."""
        inner = _Flaky(FixtureFileProvider(FIXTURES_DIR))
        core = CachingCoreDataProvider(inner, clock=clock, ttl_s=60)
        first = await core.get_customer("C000001")
        assert first is not None

        inner.failing = True
        clock.advance(120)  # entry is now expired
        stale = await core.get_customer("C000001")
        assert stale is not None, "should serve the expired entry rather than nothing"
        assert core.served_stale is True

    async def test_the_breaker_opens_and_then_recovers(self, clock: ManualClock) -> None:
        inner = _Flaky(FixtureFileProvider(FIXTURES_DIR))
        inner.failing = True
        core = CachingCoreDataProvider(
            inner, clock=clock, ttl_s=1, failure_threshold=2, cooldown_s=30
        )
        for _ in range(2):
            assert await core.get_customer("C000001") is None
        assert core.circuit_is_open

        # While open we do not even try — that is what keeps the CALL fast when the
        # bank's data is down.
        assert await core.get_customer("C000009") is None

        clock.advance(31)
        inner.failing = False
        assert not core.circuit_is_open
        assert await core.get_customer("C000001") is not None

    async def test_a_stale_snapshot_is_flagged_on_the_context(self, clock: ManualClock) -> None:
        inner = _Flaky(FixtureFileProvider(FIXTURES_DIR))
        core = CachingCoreDataProvider(inner, clock=clock, ttl_s=60)
        assembler = ContextAssembler(core=core, clock=clock)

        await assembler.build(customer_id="C000001")
        inner.failing = True
        clock.advance(120)
        snapshot = await assembler.build(customer_id="C000001")

        assert snapshot.degraded is DegradationReason.CORE_DATA_STALE
        assert snapshot.is_stale


class TestBriefBuilder:
    async def test_a_verified_caller_sees_policy_details(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        snapshot = await assembler.build(customer_id="C000001", product_code="KS-HEALTH-A")
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED),
            intent_code="health.claim.notify",
            product_line=ProductLine.HEALTH,
        )
        assert brief.kind is BriefKind.CONTEXT_ONLY
        assert "HL-2024-000811" in (brief.summary_th or "")
        assert brief.degraded is DegradationReason.NONE
        assert brief.recommended_actions[0].order == 1

    async def test_a_probable_caller_does_not(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        """`D74`: an L1 caller IS shown, and what is withheld is permission to act.

        This test used to assert the opposite — that the policy number was hidden below
        L2. `D74` reversed that: the agent is the bank's own employee, showing them the
        record is internal processing rather than disclosure, and they need the number in
        order to check what the caller tells them against it. What stays gated is what the
        agent may SAY and DO, which is `recommended_actions` and `may_act_on_policy`.
        """
        snapshot = await assembler.build(customer_id="C000001", product_code="KS-HEALTH-A")
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L1_PROBABLE),
            intent_code="health.claim.notify",
            product_line=ProductLine.HEALTH,
        )
        assert "HL-2024-000811" in (brief.summary_th or ""), "the agent can see it"
        assert "ยังไม่ยืนยันตัวตน" in (brief.summary_th or ""), "and is told they cannot use it yet"
        assert brief.degraded is DegradationReason.LOW_ASSURANCE
        # A verify-identity step is prepended, and L2-gated steps are dropped.
        assert brief.recommended_actions[0].order == 0
        assert all(
            a.requires_assurance is not AssuranceLevel.L2_STRONG for a in brief.recommended_actions
        )

    async def test_no_confidence_number_without_speech(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        """`D13`: no calibrated evidence yet, so no percentage on screen."""
        snapshot = await assembler.build(customer_id="C000001")
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED),
            intent_code="health.service.policy",
        )
        assert brief.confidence is None
        assert not brief.show_confidence_number

    async def test_a_menu_answer_is_treated_as_strong_evidence(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        """`D37`: a keypress is not a guess — a human told us."""
        snapshot = await assembler.build(customer_id="C000001")
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED),
            intent_code="health.claim.notify",
        )
        assert brief.intent is not None
        assert brief.intent.source == "dtmf"
        assert brief.intent.confidence >= 0.9

    async def test_no_intent_falls_back_to_the_lines_catch_all(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        """Keeping the product context beats dropping to a generic unknown."""
        snapshot = await assembler.build(customer_id="C000002", product_line=ProductLine.MOTOR)
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED, "C000002"),
            intent_code=None,
            product_line=ProductLine.MOTOR,
        )
        assert brief.intent is not None
        assert brief.intent.intent_code == "motor.other"
        assert brief.intent.confidence < 0.5, "a fallback is weak evidence and must say so"

    async def test_a_did_urgency_floor_raises_but_never_lowers(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        snapshot = await assembler.build(customer_id="C000002")
        raised = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED, "C000002"),
            intent_code="motor.renew",  # normally `normal`
            urgency_floor=Urgency.HIGH,
        )
        assert raised.urgency is Urgency.HIGH

        not_lowered = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED, "C000002"),
            intent_code="motor.claim.notify",  # already `critical`
            urgency_floor=Urgency.LOW,
        )
        assert not_lowered.urgency is Urgency.CRITICAL

    async def test_an_anonymous_caller_still_gets_a_usable_brief(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        """The thinnest possible case, and it must still be worth reading."""
        snapshot = await assembler.build(customer_id=None)
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L0_ANONYMOUS, None),
            intent_code="general.billing",
        )
        assert brief.summary_th
        assert brief.suggested_opening_th
        assert brief.recommended_actions
        assert brief.intent is not None and brief.intent.intent_code == "general.billing"

    async def test_the_brief_never_invents_a_number(
        self, assembler: ContextAssembler, builder: BriefBuilder
    ) -> None:
        """`D16`: every figure comes from a typed field, and this module does not even
        depend on an LLM. The only digits allowed are ones we read from data."""
        snapshot = await assembler.build(customer_id="C000001", product_code="KS-HEALTH-A")
        brief = builder.build_context_only(
            call_session_id="call_1",
            snapshot=snapshot,
            identity=identity(AssuranceLevel.L3_VERIFIED),
            intent_code="health.service.policy",
        )
        text = " ".join(
            [brief.summary_th or "", brief.suggested_opening_th or ""]
            + [a.text_th for a in brief.recommended_actions]
        )
        known = {"3000", "1000000", "1500", "0"}  # the coverage figures in the fixture
        digits = {token for token in text.replace("·", " ").split() if token.isdigit()}
        assert not (digits - known - {"HL-2024-000811"}), f"unexplained numbers: {digits}"


class _Flaky:
    """Wraps a provider and fails on demand, to exercise the degradation paths."""

    name = "flaky"

    def __init__(self, inner: FixtureFileProvider) -> None:
        self._inner = inner
        self.failing = False

    def __getattr__(self, item: str) -> object:
        return getattr(self._inner, item)

    async def get_customer(self, customer_id: str) -> object:
        if self.failing:
            raise RuntimeError("core is down")
        return await self._inner.get_customer(customer_id)

    async def list_policies(self, customer_id: str, *, active_only: bool = True) -> object:
        if self.failing:
            raise RuntimeError("core is down")
        return await self._inner.list_policies(customer_id, active_only=active_only)

    async def list_interactions(self, customer_id: str, *, limit: int = 20) -> object:
        if self.failing:
            raise RuntimeError("core is down")
        return await self._inner.list_interactions(customer_id, limit=limit)


# --- D117: the two facts that make this a broker's brief rather than an insurer's --------


@pytest.mark.asyncio
async def test_the_brief_names_which_insurer_underwrote_the_policy(
    core_fixtures: FixtureFileProvider, clock: ManualClock
) -> None:
    """A broker holds one customer across several carriers (`D117`).

    Which carrier is not decoration: it decides who a claim is handed to and whose terms
    a comparison is against. An insurer's own system would never carry the field, which
    is exactly why its absence went unnoticed for six phases.
    """
    assembler = ContextAssembler(core=core_fixtures, clock=clock)
    snapshot = await assembler.build(customer_id="C000001", product_line=ProductLine.HEALTH)

    assert snapshot.payload.relevant_policy is not None
    assert snapshot.payload.relevant_policy.insurer, "the carrier must be on the policy"
    carriers = {p.insurer for p in snapshot.payload.active_policies}
    assert len(carriers) > 1, "and this customer's cover is spread across more than one"


@pytest.mark.asyncio
async def test_a_claim_call_is_marked_as_ending_with_the_insurer(
    core_fixtures: FixtureFileProvider, clock: ManualClock, builder: BriefBuilder
) -> None:
    """`D117`: a broker takes the notification and hands over; it does not adjudicate.

    The agent has to know that before they speak, because *"I will check and call you
    back"* and *"I am passing you to the insurer now"* are different promises and only
    one of them is ours to make.
    """
    assembler = ContextAssembler(core=core_fixtures, clock=clock)
    snapshot = await assembler.build(customer_id="C000001", product_line=ProductLine.HEALTH)

    handed_over = builder.build_context_only(
        call_session_id="c1",
        snapshot=snapshot,
        identity=identity(AssuranceLevel.L3_VERIFIED),
        intent_code="health.claim.notify",
    )
    ours = builder.build_context_only(
        call_session_id="c2",
        snapshot=snapshot,
        identity=identity(AssuranceLevel.L3_VERIFIED),
        intent_code="health.advice.compare",
    )

    assert handed_over.intent is not None and handed_over.intent.handoff_to_insurer is True
    assert ours.intent is not None and ours.intent.handoff_to_insurer is False, (
        "comparing plans is the broker's own work and must not be flagged as a handoff"
    )
