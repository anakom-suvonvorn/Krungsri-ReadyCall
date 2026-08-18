"""The CoreDataProvider contract — the hackathon-day integration checklist (`D3`).

**Every** adapter runs this suite, including one written in a hurry on the morning
against whatever data we are handed. When it is green, integration is done. That is the
difference between a config change and an afternoon of debugging (`DATA_MODEL.md` §4).

Split in two:
  * `TestUniversalContract` — invariants true of *any* adapter, data or no data.
  * `TestPopulatedContract`  — invariants that need real records behind them.
"""

from __future__ import annotations

import pytest

from readycall.adapters.core_data.fixtures import (
    FixtureFileProvider,
    normalize_phone,
    phone_variants,
)
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.domain.models import Customer, Interaction, Policy
from readycall.ports.core_data import CoreDataProvider
from tests.conftest import FIXTURES_DIR

ALL_ADAPTERS = [
    pytest.param(lambda: FixtureFileProvider(FIXTURES_DIR), id="fixtures"),
    pytest.param(NullCoreDataProvider, id="null"),
]


class TestUniversalContract:
    """Must hold for every adapter, populated or not."""

    @pytest.fixture(params=ALL_ADAPTERS)
    def provider(self, request: pytest.FixtureRequest) -> CoreDataProvider:
        return request.param()  # type: ignore[no-any-return]

    def test_satisfies_the_protocol(self, provider: CoreDataProvider) -> None:
        assert isinstance(provider, CoreDataProvider)
        assert isinstance(provider.name, str) and provider.name

    async def test_unknown_customer_returns_none_and_does_not_raise(
        self, provider: CoreDataProvider
    ) -> None:
        """A caller we do not recognise is the L0 path, not an error (`D20`)."""
        assert await provider.get_customer("DEFINITELY-NOT-A-CUSTOMER") is None

    async def test_unknown_phone_returns_none_and_does_not_raise(
        self, provider: CoreDataProvider
    ) -> None:
        assert await provider.find_customer_by_phone("+66000000000") is None

    async def test_unknown_policy_and_product_return_none(self, provider: CoreDataProvider) -> None:
        assert await provider.get_policy("NOPE-000") is None
        assert await provider.get_product("NOPE-PRODUCT") is None

    async def test_list_methods_return_empty_lists_not_none(
        self, provider: CoreDataProvider
    ) -> None:
        assert await provider.list_policies("NOPE") == []
        assert await provider.list_claims("NOPE") == []
        assert await provider.list_interactions("NOPE") == []
        assert await provider.list_holdings("NOPE") == []
        assert await provider.list_life_events("NOPE") == []

    async def test_health_check_returns_a_bool_and_never_raises(
        self, provider: CoreDataProvider
    ) -> None:
        assert isinstance(await provider.health_check(), bool)

    async def test_there_is_no_write_path(self, provider: CoreDataProvider) -> None:
        """`D5`: the bank's data is read-only. We cannot corrupt what we cannot reach."""
        forbidden = {"save", "insert", "update", "delete", "write", "put", "upsert"}
        assert not forbidden & {m for m in dir(provider) if not m.startswith("_")}


class TestPopulatedContract:
    """Invariants that need records. Run against every adapter that has data."""

    @pytest.fixture
    def provider(self) -> FixtureFileProvider:
        return FixtureFileProvider(FIXTURES_DIR)

    async def test_returns_domain_objects_never_raw_rows(
        self, provider: FixtureFileProvider
    ) -> None:
        """Adapters own their mapping; upstream never sees the vendor's shape."""
        customer = await provider.get_customer("C000001")
        assert isinstance(customer, Customer)
        policies = await provider.list_policies("C000001")
        assert all(isinstance(p, Policy) for p in policies)

    @pytest.mark.parametrize(
        "dialled",
        ["+66812345678", "0812345678", "66812345678", "081-234-5678", "081 234 5678"],
    )
    async def test_ani_lookup_survives_every_thai_phone_format(
        self, provider: FixtureFileProvider, dialled: str
    ) -> None:
        """Getting this wrong silently breaks every cold call — the base case (`D19`)."""
        found = await provider.find_customer_by_phone(dialled)
        assert found is not None, f"{dialled} did not resolve"
        assert found.customer_id == "C000001"

    async def test_active_only_filter_excludes_lapsed_policies(
        self, provider: FixtureFileProvider
    ) -> None:
        active = await provider.list_policies("C000003", active_only=True)
        everything = await provider.list_policies("C000003", active_only=False)
        assert active == []
        assert len(everything) == 1  # C000003's PA policy has lapsed

    async def test_interactions_are_most_recent_first(self, provider: FixtureFileProvider) -> None:
        """The workstation reads 'last contact' from position 0."""
        interactions = await provider.list_interactions("C000001")
        assert len(interactions) >= 2
        assert all(isinstance(i, Interaction) for i in interactions)
        timestamps = [i.occurred_at for i in interactions]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_interaction_limit_is_honoured(self, provider: FixtureFileProvider) -> None:
        assert len(await provider.list_interactions("C000001", limit=1)) == 1

    async def test_buddhist_era_dates_are_converted(self, provider: FixtureFileProvider) -> None:
        """A BE year in real Thai data must not produce a 543-year-old customer."""
        customer = await provider.get_customer("C000002")
        assert customer is not None and customer.dob is not None
        assert customer.dob.year == 1987  # stored as 2530-09-03

    async def test_coverage_figures_are_typed_data_not_free_text(
        self, provider: FixtureFileProvider
    ) -> None:
        """`D16`: a coverage number can only arrive through a declared field, so the
        LLM has no way to invent one into a brief."""
        policy = await provider.get_policy("HL-2024-000811")
        assert policy is not None
        by_kind = {c.kind: c for c in policy.coverages}
        room = by_kind["ipd_room_board"]
        assert room.amount == 3000.0
        assert room.unit == "per_day"
        assert room.currency == "THB"

    async def test_phones_are_normalised_on_the_way_out(
        self, provider: FixtureFileProvider
    ) -> None:
        customer = await provider.get_customer("C000001")
        assert customer is not None
        assert all(p.startswith("+66") for p in customer.phones)

    async def test_health_check_is_true_when_data_is_present(
        self, provider: FixtureFileProvider
    ) -> None:
        assert await provider.health_check() is True


class TestPhoneNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0812345678", "+66812345678"),
            ("+66812345678", "+66812345678"),
            ("66812345678", "+66812345678"),
            ("081-234-5678", "+66812345678"),
            ("02 123 4567", "+6621234567"),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert normalize_phone(raw) == expected

    def test_variants_cover_both_directions(self) -> None:
        variants = phone_variants("0812345678")
        assert "+66812345678" in variants
        assert "0812345678" in variants
