"""The assurance ladder (`D20`).

The rule these tests protect is a security one, not a UX one: **an ANI match is a guess.**
Phones get borrowed, shared and spoofed, so caller ID alone must never unlock somebody's
policy details — but it must also never *block* the call (`D19`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from readycall import ids
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.clock import ManualClock
from readycall.domain.enums import AssuranceLevel, IdentityMethod
from readycall.domain.models import CallIntent
from readycall.services.identity import (
    IdentityResolver,
    InMemoryCallIntentStore,
    hash_token,
)
from tests.conftest import FIXTURES_DIR


@pytest.fixture
def store() -> InMemoryCallIntentStore:
    return InMemoryCallIntentStore()


@pytest.fixture
def resolver(clock: ManualClock, store: InMemoryCallIntentStore) -> IdentityResolver:
    return IdentityResolver(
        core=FixtureFileProvider(FIXTURES_DIR),
        intents=store,
        clock=clock,
        pending_intent_window_s=900.0,
    )


async def _save_intent(
    store: InMemoryCallIntentStore,
    clock: ManualClock,
    *,
    token: str,
    customer_id: str = "C000001",
    ttl_minutes: int = 15,
    age_seconds: float = 0.0,
) -> CallIntent:
    created = clock.now() - timedelta(seconds=age_seconds)
    intent = CallIntent(
        intent_id=ids.intent_id(),
        customer_id=customer_id,
        product_code="KS-HEALTH-A",
        correlation_token_hash=hash_token(token),
        created_at=created,
        expires_at=created + timedelta(minutes=ttl_minutes),
    )
    await store.save(intent)
    return intent


class TestL3Verified:
    async def test_a_valid_app_token_is_verified_identity(
        self, resolver: IdentityResolver, store: InMemoryCallIntentStore, clock: ManualClock
    ) -> None:
        await _save_intent(store, clock, token="tok-abc")
        result = await resolver.resolve(correlation_token="tok-abc")
        assert result.assurance is AssuranceLevel.L3_VERIFIED
        assert result.method is IdentityMethod.APP_TOKEN
        assert result.customer_id == "C000001"
        assert result.may_disclose_policy_details

    async def test_ivr_verification_also_reaches_l3(self, resolver: IdentityResolver) -> None:
        result = await resolver.resolve(ivr_verified_customer_id="C000002")
        assert result.assurance is AssuranceLevel.L3_VERIFIED
        assert result.method is IdentityMethod.IVR_VERIFY


class TestTokenFailureModes:
    async def test_an_expired_token_falls_through_rather_than_failing(
        self, resolver: IdentityResolver, store: InMemoryCallIntentStore, clock: ManualClock
    ) -> None:
        """An expired token earns nothing — but must not kill the call (`D19`)."""
        await _save_intent(store, clock, token="tok-old", ttl_minutes=15)
        clock.advance(60 * 60)  # an hour later
        result = await resolver.resolve(correlation_token="tok-old", caller_number="+66812345678")
        assert result.assurance is AssuranceLevel.L1_PROBABLE
        assert result.method is IdentityMethod.ANI

    async def test_an_unknown_token_degrades_to_anonymous(self, resolver: IdentityResolver) -> None:
        result = await resolver.resolve(correlation_token="never-issued")
        assert result.assurance is AssuranceLevel.L0_ANONYMOUS
        assert result.customer_id is None

    async def test_a_forged_token_cannot_impersonate(
        self, resolver: IdentityResolver, store: InMemoryCallIntentStore, clock: ManualClock
    ) -> None:
        """Tokens are matched by hash, so guessing the intent id gets you nothing."""
        intent = await _save_intent(store, clock, token="the-real-token")
        result = await resolver.resolve(correlation_token=intent.intent_id)
        assert result.customer_id is None


class TestAniLadder:
    async def test_ani_alone_is_probable_not_verified(self, resolver: IdentityResolver) -> None:
        result = await resolver.resolve(caller_number="+66812345678")
        assert result.assurance is AssuranceLevel.L1_PROBABLE
        assert result.customer_id == "C000001"
        assert not result.may_disclose_policy_details, "a borrowed phone must not disclose"

    async def test_ani_plus_a_recent_app_intent_is_strong(
        self, resolver: IdentityResolver, store: InMemoryCallIntentStore, clock: ManualClock
    ) -> None:
        """They tapped Contact minutes ago and are now calling from their own number."""
        await _save_intent(store, clock, token="tok-1", age_seconds=120)
        result = await resolver.resolve(caller_number="0812345678")
        assert result.assurance is AssuranceLevel.L2_STRONG
        assert result.method is IdentityMethod.PENDING_INTENT
        assert result.may_disclose_policy_details

    async def test_an_old_intent_does_not_strengthen_ani(
        self, resolver: IdentityResolver, store: InMemoryCallIntentStore, clock: ManualClock
    ) -> None:
        """Yesterday's tap says nothing about who is holding the phone today."""
        await _save_intent(store, clock, token="tok-2", ttl_minutes=15, age_seconds=7200)
        result = await resolver.resolve(caller_number="+66812345678")
        assert result.assurance is AssuranceLevel.L1_PROBABLE

    async def test_another_customers_pending_intent_does_not_leak(
        self, resolver: IdentityResolver, store: InMemoryCallIntentStore, clock: ManualClock
    ) -> None:
        await _save_intent(store, clock, token="tok-3", customer_id="C000003")
        result = await resolver.resolve(caller_number="+66812345678")
        assert result.customer_id == "C000001"
        assert result.assurance is AssuranceLevel.L1_PROBABLE

    @pytest.mark.parametrize("dialled", ["+66812345678", "0812345678", "081-234-5678"])
    async def test_every_thai_phone_format_resolves(
        self, resolver: IdentityResolver, dialled: str
    ) -> None:
        result = await resolver.resolve(caller_number=dialled)
        assert result.customer_id == "C000001"


class TestAnonymous:
    async def test_an_unknown_number_is_l0_and_not_an_error(
        self, resolver: IdentityResolver
    ) -> None:
        result = await resolver.resolve(caller_number="+66000000000")
        assert result.assurance is AssuranceLevel.L0_ANONYMOUS
        assert result.customer_id is None
        assert not result.may_disclose_policy_details

    async def test_no_information_at_all_still_resolves(self, resolver: IdentityResolver) -> None:
        result = await resolver.resolve()
        assert result.assurance is AssuranceLevel.L0_ANONYMOUS
        assert result.method is IdentityMethod.NONE

    async def test_a_dead_core_degrades_to_anonymous_rather_than_raising(
        self, clock: ManualClock, store: InMemoryCallIntentStore
    ) -> None:
        resolver = IdentityResolver(core=NullCoreDataProvider(), intents=store, clock=clock)
        result = await resolver.resolve(caller_number="+66812345678")
        assert result.assurance is AssuranceLevel.L0_ANONYMOUS


class TestDisclosureBoundary:
    @pytest.mark.parametrize(
        ("level", "may_disclose"),
        [
            (AssuranceLevel.L0_ANONYMOUS, False),
            (AssuranceLevel.L1_PROBABLE, False),
            (AssuranceLevel.L2_STRONG, True),
            (AssuranceLevel.L3_VERIFIED, True),
        ],
    )
    def test_the_line_is_drawn_at_l2(self, level: AssuranceLevel, may_disclose: bool) -> None:
        from readycall.domain.models import IdentityResolution

        resolution = IdentityResolution(
            method=IdentityMethod.ANI,
            customer_id="C000001",
            assurance=level,
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert resolution.may_disclose_policy_details is may_disclose


def test_token_hashing_is_stable_and_not_reversible() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert "abc" not in hash_token("abc")
    assert len(hash_token("abc")) == 64


def test_fixtures_dir_is_where_we_think() -> None:
    assert Path(FIXTURES_DIR).exists()
