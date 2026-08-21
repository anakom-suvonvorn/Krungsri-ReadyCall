"""Generates a realistic-shaped mock bank dataset.

    uv run python mock/bank_core/generate.py --seed 42 --customers 2000

Two datasets exist, on purpose:

* `mock/bank_core/fixtures/` — **hand-authored**, 3 customers, committed. These are the
  demo personas and the golden-set anchors; they never change under you.
* `mock/bank_core/generated/` — **generated**, thousands of rows, gitignored. For load,
  for matching simulation, and for making sure nothing quietly assumes three customers.

The data deliberately reproduces the competition brief's central finding: a minority hold
several policies while a large group holds one or none, so the **coverage/life-stage
mismatch is visible in the data itself** rather than only in the pitch. It is also spread
across every product line, because insurance here means motor/health/life/travel/PA, not
just health.

Deterministic: same `--seed` in, byte-identical files out.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from readycall.console import enable_utf8

# Thai given/family names, chosen to be common and unremarkable.
FIRST_TH = [
    "สมชาย",
    "สมหญิง",
    "ปรีชา",
    "วิภา",
    "อนันต์",
    "กมล",
    "ณัฐพล",
    "ศิริพร",
    "ธนกร",
    "พิมพ์ชนก",
    "ชัยวัฒน์",
    "อารยา",
    "ภูมิ",
    "เมธาวี",
    "ปิยะ",
    "จิราพร",
    "ณิชา",
    "ธีรศักดิ์",
    "สุนิสา",
    "วรากร",
]
LAST_TH = [
    "ใจดี",
    "รักเรียน",
    "แสงทอง",
    "บุญมี",
    "ศรีสุข",
    "วงศ์ไทย",
    "พูนทรัพย์",
    "อินทรีย์",
    "เจริญพร",
    "มงคลชัย",
    "สุขสมบูรณ์",
    "ทองดี",
    "นาคสุข",
    "พงษ์เพชร",
    "ชูเกียรติ",
]
PROVINCES = [
    "Bangkok",
    "Nonthaburi",
    "Chiang Mai",
    "Khon Kaen",
    "Chonburi",
    "Songkhla",
    "Phuket",
    "Nakhon Ratchasima",
    "Rayong",
    "Udon Thani",
]

SEGMENTS = ("salaried", "sme_owner", "freelancer")
SEGMENT_WEIGHTS = (0.55, 0.20, 0.25)

PRODUCTS: list[dict[str, Any]] = [
    {"product_code": "KS-HEALTH-A", "line": "health", "name_th": "กรุงศรี เฮลท์ แพลน เอ"},
    {"product_code": "KS-HEALTH-B", "line": "health", "name_th": "กรุงศรี เฮลท์ แพลน บี"},
    {"product_code": "KS-MOTOR-1ST", "line": "motor", "name_th": "ประกันรถยนต์ชั้น 1"},
    {"product_code": "KS-MOTOR-3RD", "line": "motor", "name_th": "ประกันรถยนต์ชั้น 3"},
    {"product_code": "KS-TRAVEL-ASIA", "line": "travel", "name_th": "ประกันเดินทางเอเชีย"},
    {"product_code": "KS-PA-BASIC", "line": "pa", "name_th": "ประกันอุบัติเหตุส่วนบุคคล"},
    {"product_code": "KS-LIFE-SAVE10", "line": "life", "name_th": "ออมทรัพย์ 10 ปี"},
]

COVERAGES: dict[str, list[dict[str, Any]]] = {
    "health": [
        {
            "kind": "ipd_room_board",
            "label_th": "ค่าห้องและค่าอาหาร",
            "amount": 3000,
            "unit": "per_day",
        },
        {
            "kind": "ipd_annual_limit",
            "label_th": "วงเงินผู้ป่วยในต่อปี",
            "amount": 1000000,
            "unit": "per_year",
        },
        {
            "kind": "opd_limit",
            "label_th": "ค่ารักษาผู้ป่วยนอก",
            "amount": 1500,
            "unit": "per_visit",
        },
    ],
    "motor": [
        {"kind": "own_damage", "label_th": "ความเสียหายต่อรถยนต์", "amount": 500000},
        {"kind": "third_party_property", "label_th": "ทรัพย์สินบุคคลภายนอก", "amount": 1000000},
        {"kind": "deductible", "label_th": "ค่าเสียหายส่วนแรก", "amount": 2000},
    ],
    "travel": [
        {"kind": "medical_overseas", "label_th": "ค่ารักษาพยาบาลต่างประเทศ", "amount": 2000000},
    ],
    "pa": [{"kind": "accidental_death", "label_th": "การเสียชีวิตจากอุบัติเหตุ", "amount": 300000}],
    "life": [{"kind": "sum_assured", "label_th": "จำนวนเงินเอาประกันภัย", "amount": 500000}],
}

TOPICS = [
    "claim_documents",
    "premium_payment",
    "coverage_query",
    "renewal",
    "address_change",
    "motor_accident",
    "hospital_network",
    "policy_copy",
]


def _weighted(rng: random.Random, options: tuple[str, ...], weights: tuple[float, ...]) -> str:
    return rng.choices(list(options), weights=list(weights), k=1)[0]


def _policy_count(rng: random.Random) -> int:
    """The brief's headline finding, expressed as a distribution.

    A large group holds nothing or one policy; a minority hold several. That mismatch is
    the problem the product exists to surface, so the mock data must actually contain it.
    """
    roll = rng.random()
    if roll < 0.34:
        return 0
    if roll < 0.72:
        return 1
    if roll < 0.90:
        return 2
    return rng.randint(3, 5)


def generate(seed: int, count: int, out_dir: Path) -> dict[str, int]:
    rng = random.Random(seed)
    today = date(2026, 8, 21)

    customers: list[dict[str, Any]] = []
    policies: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    life_events: list[dict[str, Any]] = []

    for i in range(1, count + 1):
        cid = f"G{i:06d}"
        segment = _weighted(rng, SEGMENTS, SEGMENT_WEIGHTS)
        age = rng.randint(23, 62)
        dob = date(today.year - age, rng.randint(1, 12), rng.randint(1, 28))
        # A slice of records carry Buddhist-era dates, because real extracts do and the
        # mapping layer must keep coping with them.
        dob_value = (
            f"{dob.day:02d}/{dob.month:02d}/{dob.year + 543}" if i % 7 == 0 else dob.isoformat()
        )

        customers.append(
            {
                "customer_id": cid,
                "title_th": rng.choice(["นาย", "นาง", "นางสาว"]),
                "first_name_th": rng.choice(FIRST_TH),
                "last_name_th": rng.choice(LAST_TH),
                "dob": dob_value,
                "segment": segment,
                "tier": _weighted(rng, ("standard", "gold", "platinum"), (0.7, 0.22, 0.08)),
                "occupation": {
                    "salaried": "employee",
                    "sme_owner": "business_owner",
                    "freelancer": "freelance",
                }[segment],
                "income_band": rng.choice(
                    ["15000-30000", "30000-50000", "50000-100000", "100000+"]
                ),
                "marital_status": rng.choice(["single", "married", "married", "divorced"]),
                "dependants": rng.choice([0, 0, 1, 2, 3]),
                "preferred_language": "th",
                "phones": [f"0{rng.choice([6, 8, 9])}{rng.randint(10_000_000, 99_999_999)}"],
                "address_province": rng.choice(PROVINCES),
                "kyc_status": "verified",
                "is_vulnerable": rng.random() < 0.03,
            }
        )

        for n in range(_policy_count(rng)):
            product = rng.choice(PRODUCTS)
            line = product["line"]
            start = today - timedelta(days=rng.randint(30, 1200))
            years = 1 if line in {"motor", "travel"} else rng.choice([1, 2, 5])
            expiry = start + timedelta(days=365 * years)
            lapsed = expiry < today
            policies.append(
                {
                    "policy_no": f"{line[:2].upper()}-{start.year}-{i:06d}{n}",
                    "customer_id": cid,
                    "product_code": product["product_code"],
                    "line": line,
                    "status": "lapsed" if lapsed else "active",
                    "effective_date": start.isoformat(),
                    "expiry_date": expiry.isoformat(),
                    "sum_insured": rng.choice([300000, 500000, 750000, 1000000]),
                    "premium": rng.randint(1200, 32000),
                    "payment_frequency": rng.choice(["annual", "monthly"]),
                    "next_due_date": (expiry - timedelta(days=30)).isoformat(),
                    "coverages": COVERAGES[line],
                    "riders": [],
                }
            )

        for n in range(rng.randint(0, 6)):
            occurred = datetime(today.year, today.month, today.day) - timedelta(
                days=rng.randint(1, 540), hours=rng.randint(0, 23)
            )
            interactions.append(
                {
                    "interaction_id": f"GI{i:06d}{n}",
                    "customer_id": cid,
                    "channel": rng.choice(["call", "app", "email", "branch", "chat"]),
                    "direction": "inbound",
                    "occurred_at": occurred.isoformat(),
                    "topic": rng.choice(TOPICS),
                    "summary": "",
                    "agent_id": f"A{rng.randint(1, 20):03d}" if rng.random() < 0.6 else None,
                    "outcome": rng.choice(["resolved", "followup", "no_response"]),
                    "duration_s": rng.randint(60, 1500),
                }
            )

        for n in range(rng.randint(1, 3)):
            holdings.append(
                {
                    "holding_id": f"GH{i:06d}{n}",
                    "customer_id": cid,
                    "kind": rng.choice(["deposit", "card", "loan", "fund"]),
                    "opened_at": (today - timedelta(days=rng.randint(200, 3000))).isoformat(),
                    "balance_band": rng.choice(["0-50k", "50k-100k", "100k-500k", "500k-1m"]),
                    "status": "active",
                }
            )

        # Life events correlate with segment, so the "mortgage -> life cover" and
        # "new child -> health cover" stories are actually present in the data.
        if rng.random() < (0.35 if segment == "sme_owner" else 0.2):
            life_events.append(
                {
                    "event_id": f"GL{i:06d}",
                    "customer_id": cid,
                    "signal": rng.choice(["mortgage", "new_child", "job_change", "relocation"]),
                    "detected_at": (
                        datetime(today.year, today.month, today.day)
                        - timedelta(days=rng.randint(30, 900))
                    ).isoformat(),
                    "confidence": round(rng.uniform(0.55, 0.98), 2),
                    "source": "derived",
                }
            )

    for policy in policies:
        if policy["status"] == "active" and rng.random() < 0.12:
            claims.append(
                {
                    "claim_id": f"GC-{policy['policy_no']}",
                    "policy_no": policy["policy_no"],
                    "kind": policy["line"],
                    "status": rng.choice(["paid", "pending_documents", "assessing", "rejected"]),
                    "submitted_at": (
                        datetime(today.year, today.month, today.day)
                        - timedelta(days=rng.randint(5, 400))
                    ).isoformat(),
                    "incident_date": (today - timedelta(days=rng.randint(6, 410))).isoformat(),
                    "amount_claimed": rng.randint(3000, 120000),
                    "documents_required": [],
                }
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    written = {
        "customers": customers,
        "products": PRODUCTS,
        "policies": policies,
        "claims": claims,
        "interactions": interactions,
        "holdings": holdings,
        "life_events": life_events,
    }
    for name, rows in written.items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {name: len(rows) for name, rows in written.items()}


def main() -> int:
    enable_utf8()
    parser = argparse.ArgumentParser(description="Generate a mock bank-core dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=int, default=2000)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parent / "generated", help="output directory"
    )
    args = parser.parse_args()

    counts = generate(args.seed, args.customers, args.out)
    print(f"generated into {args.out} (seed={args.seed})")
    for name, n in counts.items():
        print(f"  {name:<14} {n:>7,}")
    holders = counts["policies"]
    print(f"\n  policies per customer: {holders / max(counts['customers'], 1):.2f} average")
    print("  point CORE_FIXTURES_DIR at this directory to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
