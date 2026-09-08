"""FixtureFileProvider — reads the bank's data from files on disk.

**This is the most likely shape of what the hackathon actually hands us** (`DATA_MODEL.md`
§4), so it is the first adapter built rather than an afterthought. It loads JSON or YAML
from a directory and maps each record into a domain object.

Two behaviours worth stating because the contract suite tests them:

* Phone lookup normalises Thai numbers both ways (`08x` <-> `+668x`). Getting this
  wrong silently breaks every cold call, which is the base case (`D19`).
* An unknown customer returns `None`, never raises. A caller we do not recognise is
  the L0 path, not an error (`D20`).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

import yaml

from readycall.domain.enums import CustomerSegment, PolicyStatus, ProductLine
from readycall.domain.models import (
    Claim,
    Coverage,
    Customer,
    Holding,
    Interaction,
    LifeEvent,
    Policy,
    Product,
)
from readycall.errors import ConfigError
from readycall.logging import get_logger

log = get_logger(__name__)

_THAI_MOBILE_PREFIX = "+66"


def normalize_phone(raw: str) -> str:
    """Thai numbers into E.164. `0812345678` -> `+66812345678`.

    Also tolerates spaces, dashes and a bare `66...`, because real extracts contain
    all of those.
    """
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        return digits
    if digits.startswith("66"):
        return f"+{digits}"
    if digits.startswith("0"):
        return f"{_THAI_MOBILE_PREFIX}{digits[1:]}"
    return digits


def phone_variants(raw: str) -> set[str]:
    """Every form the same number might be stored as, so lookups match either way."""
    e164 = normalize_phone(raw)
    out = {raw, e164}
    if e164.startswith(_THAI_MOBILE_PREFIX):
        out.add("0" + e164[len(_THAI_MOBILE_PREFIX) :])
        out.add(e164[1:])
    return out


def _parse_date(value: Any) -> date | None:
    """Parse a date, tolerating the **Buddhist era** (`DATA_MODEL.md` §4).

    A year >= 2400 is BE; subtract 543. This will come up in real Thai data and is
    exactly the kind of thing that silently produces a 543-year-old customer.
    """
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.year >= 2400:
            parsed = parsed.replace(year=parsed.year - 543)
        return parsed.date()
    log.warning("unparseable date in fixture", value=text)
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        parsed = _parse_date(value)
        return datetime(parsed.year, parsed.month, parsed.day) if parsed else None


def _load_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if data is None:
        return []
    if isinstance(data, dict):
        # Allow either a bare list or {"items": [...]} at the top level.
        data = data.get("items", list(data.values()))
    if not isinstance(data, list):
        raise ConfigError(f"fixture {path} must contain a list of records")
    return [dict(row) for row in data]


class FixtureFileProvider:
    """Read-only `CoreDataProvider` over a directory of JSON/YAML files."""

    FILES: ClassVar[dict[str, tuple[str, ...]]] = {
        "customers": ("customers.json", "customers.yaml"),
        "products": ("products.json", "products.yaml"),
        "policies": ("policies.json", "policies.yaml"),
        "claims": ("claims.json", "claims.yaml"),
        "interactions": ("interactions.json", "interactions.yaml"),
        "holdings": ("holdings.json", "holdings.yaml"),
        "life_events": ("life_events.json", "life_events.yaml"),
    }

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._raw: dict[str, list[dict[str, Any]]] = {}
        self._by_phone: dict[str, str] = {}
        self._loaded = False

    @property
    def name(self) -> str:
        return "fixtures"

    # --- loading ---------------------------------------------------------------------

    def load(self) -> None:
        if self._loaded:
            return
        if not self._dir.exists():
            raise ConfigError(f"core fixtures directory not found: {self._dir}")
        for kind, candidates in self.FILES.items():
            rows: list[dict[str, Any]] = []
            for candidate in candidates:
                path = self._dir / candidate
                if path.exists():
                    rows = _load_file(path)
                    break
            self._raw[kind] = rows
        for row in self._raw.get("customers", []):
            for phone in row.get("phones", []) or []:
                for variant in phone_variants(str(phone)):
                    self._by_phone[variant] = str(row["customer_id"])
        self._loaded = True
        log.info(
            "core fixtures loaded",
            directory=str(self._dir),
            customers=len(self._raw.get("customers", [])),
            policies=len(self._raw.get("policies", [])),
        )

    def _rows(self, kind: str) -> list[dict[str, Any]]:
        self.load()
        return self._raw.get(kind, [])

    # --- mapping (adapters own their own mapping; upstream never sees a raw row) ------

    def _to_customer(self, row: dict[str, Any]) -> Customer:
        return Customer(
            customer_id=str(row["customer_id"]),
            first_name_th=row.get("first_name_th", ""),
            last_name_th=row.get("last_name_th"),
            first_name_en=row.get("first_name_en"),
            last_name_en=row.get("last_name_en"),
            title_th=row.get("title_th"),
            dob=_parse_date(row.get("dob")),
            segment=CustomerSegment(row.get("segment", "other")),
            tier=row.get("tier"),
            occupation=row.get("occupation"),
            income_band=row.get("income_band"),
            marital_status=row.get("marital_status"),
            dependants=row.get("dependants"),
            preferred_language=row.get("preferred_language", "th"),
            phones=tuple(normalize_phone(str(p)) for p in (row.get("phones") or [])),
            email=row.get("email"),
            address_province=row.get("address_province"),
            kyc_status=row.get("kyc_status"),
            is_vulnerable=bool(row.get("is_vulnerable", False)),
        )

    @staticmethod
    def _coverages(rows: list[dict[str, Any]]) -> tuple[Coverage, ...]:
        """One parser for both a policy's figures and a product's (`D125`).

        A gap analysis compares what the customer HOLDS against what a plan OFFERS, so the
        two must be the same shape read the same way — two parsers for one concept is two
        mapping bugs, and they would disagree exactly where the comparison is drawn.
        """
        return tuple(
            Coverage(
                kind=c["kind"],
                label_th=c.get("label_th", c["kind"]),
                amount=c.get("amount"),
                currency=c.get("currency", "THB"),
                unit=c.get("unit"),
                note=c.get("note"),
            )
            for c in rows
        )

    def _to_policy(self, row: dict[str, Any]) -> Policy:
        coverages = self._coverages(row.get("coverages") or [])
        return Policy(
            policy_no=str(row["policy_no"]),
            customer_id=str(row["customer_id"]),
            product_code=str(row["product_code"]),
            line=ProductLine(row.get("line", "unknown")),
            insurer=row.get("insurer"),
            status=PolicyStatus(row.get("status", "active")),
            effective_date=_parse_date(row.get("effective_date")),
            expiry_date=_parse_date(row.get("expiry_date")),
            sum_insured=row.get("sum_insured"),
            premium=row.get("premium"),
            payment_frequency=row.get("payment_frequency"),
            next_due_date=_parse_date(row.get("next_due_date")),
            coverages=coverages,
            riders=tuple(row.get("riders") or ()),
            agent_id_of_record=row.get("agent_id_of_record"),
        )

    # --- the port --------------------------------------------------------------------

    async def get_customer(self, customer_id: str) -> Customer | None:
        for row in self._rows("customers"):
            if str(row["customer_id"]) == customer_id:
                return self._to_customer(row)
        return None

    async def find_customer_by_phone(self, phone_e164: str) -> Customer | None:
        self.load()
        for variant in phone_variants(phone_e164):
            customer_id = self._by_phone.get(variant)
            if customer_id:
                return await self.get_customer(customer_id)
        return None

    async def list_policies(self, customer_id: str, *, active_only: bool = True) -> list[Policy]:
        policies = [
            self._to_policy(row)
            for row in self._rows("policies")
            if str(row["customer_id"]) == customer_id
        ]
        if active_only:
            policies = [p for p in policies if p.is_active]
        return policies

    async def get_policy(self, policy_no: str) -> Policy | None:
        for row in self._rows("policies"):
            if str(row["policy_no"]) == policy_no:
                return self._to_policy(row)
        return None

    async def list_claims(self, policy_no: str) -> list[Claim]:
        return [
            Claim(
                claim_id=str(row["claim_id"]),
                policy_no=str(row["policy_no"]),
                kind=row.get("kind", "unknown"),
                status=row.get("status", "unknown"),
                submitted_at=_parse_dt(row.get("submitted_at")),
                incident_date=_parse_date(row.get("incident_date")),
                amount_claimed=row.get("amount_claimed"),
                amount_paid=row.get("amount_paid"),
                hospital_name=row.get("hospital_name"),
                documents_required=tuple(row.get("documents_required") or ()),
            )
            for row in self._rows("claims")
            if str(row["policy_no"]) == policy_no
        ]

    async def list_interactions(self, customer_id: str, *, limit: int = 20) -> list[Interaction]:
        rows = [
            Interaction(
                interaction_id=str(row["interaction_id"]),
                customer_id=str(row["customer_id"]),
                channel=row.get("channel", "call"),
                direction=row.get("direction", "inbound"),
                occurred_at=_parse_dt(row["occurred_at"]) or datetime.min,
                topic=row.get("topic"),
                summary=row.get("summary"),
                agent_id=row.get("agent_id"),
                outcome=row.get("outcome"),
                duration_s=row.get("duration_s"),
            )
            for row in self._rows("interactions")
            if str(row["customer_id"]) == customer_id
        ]
        # Most recent first: the workstation reads "last contact" from position 0.
        rows.sort(key=lambda i: i.occurred_at, reverse=True)
        return rows[:limit]

    async def list_holdings(self, customer_id: str) -> list[Holding]:
        return [
            Holding(
                holding_id=str(row["holding_id"]),
                customer_id=str(row["customer_id"]),
                kind=row.get("kind", "unknown"),
                opened_at=_parse_date(row.get("opened_at")),
                balance_band=row.get("balance_band"),
                status=row.get("status"),
            )
            for row in self._rows("holdings")
            if str(row["customer_id"]) == customer_id
        ]

    async def list_life_events(self, customer_id: str) -> list[LifeEvent]:
        return [
            LifeEvent(
                event_id=str(row["event_id"]),
                customer_id=str(row["customer_id"]),
                signal=row["signal"],
                detected_at=_parse_dt(row["detected_at"]) or datetime.min,
                confidence=float(row.get("confidence", 1.0)),
                source=row.get("source"),
            )
            for row in self._rows("life_events")
            if str(row["customer_id"]) == customer_id
        ]

    def _product(self, row: dict[str, Any]) -> Product:
        return Product(
            product_code=str(row["product_code"]),
            line=ProductLine(row.get("line", "unknown")),
            name_th=row.get("name_th", ""),
            name_en=row.get("name_en"),
            short_desc=row.get("short_desc"),
            insurer=row.get("insurer"),
            coverages=self._coverages(row.get("coverages") or []),
            features=row.get("features") or {},
            is_active=bool(row.get("is_active", True)),
        )

    async def get_product(self, product_code: str) -> Product | None:
        for row in self._rows("products"):
            if str(row["product_code"]) == product_code:
                return self._product(row)
        return None

    async def list_products(
        self, *, line: ProductLine | None = None, active_only: bool = True
    ) -> list[Product]:
        """File order, and it means nothing (`D125`). Ranking happens in `services/`."""
        out = [self._product(row) for row in self._rows("products")]
        if line is not None:
            out = [p for p in out if p.line is line]
        if active_only:
            out = [p for p in out if p.is_active]
        return out

    async def health_check(self) -> bool:
        try:
            self.load()
        except ConfigError:
            return False
        return bool(self._raw.get("customers"))
