# Explanation — P1: Context-Aware Calling

_A plain-language walkthrough of everything built in phase P1, continuing from
`P0_foundations.md`. Read that one first — this builds directly on it._
_Written 2026-08-21, describing the code as of commit `p1: identity, context, brief`._

> Teaching notes, not specifications. If this disagrees with `ARCHITECTURE.md` or the
> code, the other one is right and this is stale. A "changes since" section sits at the
> bottom.

---

## 0. What changed in one paragraph

P0 built a skeleton where a call could move through its lifecycle, but every interesting
decision was made by the scenario file. P1 replaces four of those with **real services**:
working out *who* is calling and how much to believe it, working out *why* they are
calling from what they pressed, gathering everything we know about them *before the phone
is answered*, and turning all of that into the **first version of the brief the agent
reads**. The call still can't actually happen — no real phone, no speech, no AI — but the
thinking part of the product is now genuinely there.

The one-line summary: **P0 could move a call. P1 knows something about it.**

---

## 1. The new pieces

Four new modules, plus one that ties the config together:

```
src/readycall/
├─ domainpack.py                    ← NEW: loads + validates config/*.yaml into typed objects
├─ adapters/core_data/caching.py    ← NEW: TTL cache + stale-serving + circuit breaker
└─ services/
   ├─ identity/                     ← NEW: who is calling, and how sure are we (L0–L3)
   │  ├─ resolver.py
   │  └─ store.py
   ├─ context/assembler.py          ← NEW: Customer360 + provenance, frozen
   └─ brief/builder.py              ← NEW: the context-only case brief
```

And `mock/bank_core/generate.py` (a 2,000-customer dataset) plus
`infra/docker-compose.yml`, both of which were leftover P0 items.

---

## 2. `domainpack.py` — turning YAML into something trustworthy

P0 put all the insurance-specific settings in `config/*.yaml`: the intents, the skills,
the queues, the phone numbers, the keypad menus. Good — but "it's in YAML" is not the same
as "it's correct". Those four files refer to each other constantly by string:

```
menus.yaml   says option 1 → intent "motor.claim.accident"
intents.yaml says that intent → skill "motor.claim"
skills.yaml  says that skill  → queue "q_motor_claim"
```

Rename anything in that chain and nothing complains until a real call falls through a hole
at runtime. So `DomainPack.load()` parses all four **once at startup**, converts them into
typed objects (`IntentSpec`, `QueueSpec`, `DidSpec`, `MenuSpec`), and cross-validates:

- every intent points at a real skill, every skill at a real queue
- overflow queues exist and **cannot form a cycle** (a cycle would hand a call round for ever)
- every menu option points at a real intent or a real next-menu
- reserved keys (`9` repeat, `0` operator) aren't reused as options
- every product line has a catch-all reason
- a DID that says `skip_product_menu: true` must actually know its product line

Any failure raises `ConfigError` with a specific message, at startup, not at 2am.

It also answers the questions the rest of the system actually asks:

```python
pack.queue_for_intent("motor.claim.accident")   # → "q_motor_claim"   (the routing chain)
pack.catch_all_for(ProductLine.TRAVEL)          # → travel.other
pack.walk_menu(["2", "4"])                      # → health, health.coverage.query
```

That last one — `walk_menu` — is the keypad menu made real. Give it the keys someone
pressed and it follows the tree, returning what they selected. An unrecognised key stops
the walk rather than guessing, which is what the real IVR will do before falling back to a
human.

---

## 3. `services/identity/` — who is calling?

This is the `D20` assurance ladder from the design, now actually running.

The core insight worth internalising: **caller ID is a guess, not proof.** Phones get
borrowed, shared, and spoofed. But refusing to show anything without hard verification
throws away the entire product. So identity isn't a yes/no — it's a *level*:

| Level | How you get there | What the agent sees |
|---|---|---|
| **L0** anonymous | nothing matched | nothing personal |
| **L1** probable | caller ID matched a customer | name, but **no policy number, no coverage figures** |
| **L2** strong | caller ID **and** they tapped Contact in the app minutes ago | everything, flagged as inferred |
| **L3** verified | app token, or they keyed their ID in the IVR | everything |

`IdentityResolver.resolve()` tries each rung in order and returns an `IdentityResolution`
carrying the customer id, the level, the method used, and the evidence.

Three behaviours worth knowing, each with a test:

**It never refuses.** An unknown token, an expired token, an unrecognised number — none of
these raise. They just earn a lower rung. An unidentified caller is the L0 path, not an
error, and the call proceeds either way.

**Tokens are matched by hash.** The app's correlation token is a bearer credential —
whoever holds it is treated as that customer — so it's stored only as a SHA-256 hash and
never logged. Guessing the intent id gets you nothing.

**An old tap doesn't count.** The L2 rung requires a pending app intent *within a window*
(default 15 minutes). Yesterday's tap says nothing about who's holding the phone today.

`store.py` is where pending intents live — a dictionary for now, behind a small Protocol,
so the Postgres version drops in later without touching the resolver.

---

## 4. `adapters/core_data/caching.py` — surviving a flaky upstream

A wrapper you put *around* whatever `CoreDataProvider` the env var picked. Three jobs:

**TTL cache.** Identity, context assembly and the brief all read the same customer. Doing
it once is the difference between one round trip and seven, which is the whole
tap-to-snapshot latency budget.

**Serve stale rather than nothing.** If the upstream fails but we still hold an expired
entry, return the stale value *and set a flag*. A slightly old policy list with a
staleness badge beats an empty screen — but the agent has to be able to see which it is,
so `served_stale` propagates into the snapshot's `DegradationReason`.

**Circuit breaker.** After N failures, stop calling a dying service for a cooldown. This
is the part that keeps the *call* fast when the bank's data is down: we fail in
microseconds and degrade, rather than waiting out a timeout on every single lookup.

Caching is safe here by construction, incidentally — the provider has no write methods, so
nothing can invalidate an entry behind our back.

---

## 5. `services/context/assembler.py` — everything we know, frozen

This is the piece that makes the pitch's central claim literally true. It runs the moment
identity resolves — **before the phone is answered** — not when an agent picks up.

```python
snapshot = await assembler.build(
    customer_id="C000001", product_code="KS-HEALTH-A", product_line=ProductLine.HEALTH
)
```

It fans out to the core data provider **in parallel** (`asyncio.gather`) for the customer,
their active policies, recent interactions, holdings, life events and the selected
product; then fetches claims for whichever policy turns out to be relevant.

Two properties matter as much as the data:

**Provenance.** Every populated field gets a `FieldProvenance` row: which source, which
adapter, fetched when, stale or not. That's what lets the workstation answer *"says who,
and how old is this?"* — without it an agent has no way to judge what's on screen. The
scenario runner prints them:

```
PROVENANCE  (every field can say where it came from and when)
  customer               core:customers             via caching(fixtures)
  active_policies        core:policies              via caching(fixtures)
  recent_interactions    core:interactions          via caching(fixtures)
```

**Frozen.** Once built, it's immutable. The screen shows what the system knew *when it
decided*, and a scenario replay reproduces the same brief even if the data moves later.

### The bit I'd point at: picking the relevant policy

Which policy is this call *about*? The rule is "most specific evidence first":

1. the exact plan they tapped in the app
2. the product line the DID or menu gave us (if several in that line, the one expiring soonest)
3. if they hold exactly one policy, that one
4. **otherwise nothing**

Step 4 is deliberate. A customer with a motor policy and a travel policy and no signal
about which — we show neither. A confidently wrong policy on the agent's screen costs more
time than an empty panel, because they act on it before noticing.

And it never raises. A dead upstream produces a thinner snapshot with
`DegradationReason.CORE_DATA_UNAVAILABLE` set, because the call must proceed regardless.

---

## 6. `services/brief/builder.py` — what the agent reads

The first version of the brief: **context-only**. Data plus the keypad menu, no speech, no
AI. That's deliberately the version built first, because it's the one that must never
fail — it's what an agent gets when the caller declines the recording, when the STT worker
is down, and when the LLM times out.

It produces: the intent (with its Thai label), urgency, a factual Thai summary, a
recommended-actions list from the intent's playbook, and a suggested opening line.

### Disclosure gating, which is the important part

Here's the same call at two assurance levels. **L3 verified:**

```
  assurance   l3_verified  ->  policy details shown
  summary     คุณภัทธีรา ติดต่อเรื่องแจ้งเข้ารักษาผู้ป่วยใน / ตรวจสอบค่าห้อง · กรมธรรม์ HL-2024-000811 · ...
  actions
    1. สอบถามโรงพยาบาลและวันที่เข้ารับการรักษา
    2. ตรวจสอบสิทธิ์ผู้ป่วยในและวงเงินค่าห้อง
    3. อธิบายเอกสารที่ต้องเตรียมสำหรับการเคลม
```

**L1 probable** — same customer, same data, caller ID matched but nothing verified it:

```
  assurance   l1_probable  ->  policy details WITHHELD
  degraded    low_assurance
  summary     คุณภัทธีรา ติดต่อเรื่อง... · มีกรมธรรม์ที่เกี่ยวข้อง (ยังไม่ยืนยันตัวตน) · ...
  actions
    0. ยืนยันตัวตนผู้ติดต่อก่อนให้ข้อมูลกรมธรรม์
    1. สอบถามโรงพยาบาลและวันที่เข้ารับการรักษา
    3. อธิบายเอกสารที่ต้องเตรียมสำหรับการเคลม
```

Three things happened automatically: the policy number became "there is a relevant policy,
identity not confirmed"; a **verify-identity step was prepended as step 0**; and the step
that would have read out coverage limits was **dropped**, because it's tagged as requiring
L2. The agent isn't blocked — they're told what to do first.

### No confidence number yet

`brief.confidence` is `None`. There's no speech, so there's nothing calibrated to show,
and `D13` says don't show a number you can't stand behind. A percentage appears at P4 when
there's something to calibrate against.

### The menu is treated as strong evidence

```python
confidence=0.95 if from_menu else 0.2
source="dtmf" if from_menu else "product_line_default"
```

A keypress isn't a guess — **a human told us**. That's the whole `D37` argument expressed
in two lines. A fallback catch-all, by contrast, is weak and says so.

### It structurally cannot invent a number

This module doesn't import an LLM. Every figure comes from a typed `Coverage` field read
out of data. There's a test that scans all generated text and fails on any digit that
doesn't trace back to the fixtures.

---

## 7. What the scenario runner shows now

The `# P0:` markers are mostly gone — identity, the menu walk, context assembly and the
brief are all real services doing real work. The most interesting run is the *worst* case:

```
$ uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml --quiet

NOTES
  - identity: l0_anonymous via none -> nobody (disclose policy details: False)
  - DID +6621234000: General hotline -> line=unknown, menu step 1 needed
  - menu 2/4 -> line=health, intent=health.coverage.query
  - caller pressed 2 - routing already settled by the menu, so the brief is
    menu-derived rather than empty (D37)

CASE BRIEF  (v1, context_only)
  intent      health.coverage.query  [dtmf]
  queue       q_health_policy
  assurance   l0_anonymous  ->  policy details WITHHELD
```

An unrecognised number, on the general hotline, who declined to record anything, with no
AI involved at all — and they still land in the **health policy queue** with the right
intent, from two keypresses. That's the `D37` argument demonstrated rather than asserted.

The motor scenario shows the opposite end: the DID skips menu step 1, one keypress gives
`motor.claim.accident`, and the DID's `urgency_floor: high` combines with the intent's own
`critical` to put the call at the top of the queue — before anyone has spoken.

---

## 8. The leftover P0 items

**`mock/bank_core/generate.py`** — 2,000 customers, deterministic from a seed. Two datasets
now exist on purpose: the three hand-authored personas in `fixtures/` (committed, stable,
the demo anchors) and thousands of generated rows in `generated/` (gitignored, for load and
for making sure nothing quietly assumes three customers).

The distribution deliberately reproduces the brief's central finding — a third hold no
policy, most hold one, a minority hold several (1.15 average) — so the coverage/life-stage
mismatch is visible in the data itself rather than only in the pitch. Every 7th record
carries a **Buddhist-era birthdate**, because real Thai extracts do and the mapping layer
must keep coping with it.

**`infra/docker-compose.yml`** — Postgres, Redis, MinIO, pgweb. Nothing needs them yet
(everything still runs in memory), but the Postgres init script is worth a look: it creates
the two schemas and a `readycall_ro` role with **`SELECT` only** on `core`. So `D5` is
enforced twice over — the port has no write methods, *and* the database wouldn't accept a
write if it did.

---

## 9. What's still fake

| Piece | Status after P1 |
|---|---|
| Identity + assurance ladder | **Real** |
| Menu walk / intent from keypresses | **Real** (the IVR that plays the prompts is P3) |
| Context assembly + provenance + freezing | **Real** |
| Context-only brief + disclosure gating | **Real** |
| Caching, stale-serving, circuit breaker | **Real** |
| Phone network | Fake (Asterisk at P5) |
| Speech-to-text | Scripted (Thonburian at P3) |
| AI analysis | Rule-based (Claude/Typhoon at P4) |
| Matching | Scenario names the agent (engine at P2) |
| Agent workstation | Doesn't exist yet (P2) |
| Database | In-memory dicts (compose file ready, models not written) |
| HTTP API + customer simulator | Not built (P1b) |

**161 tests**, `ruff` + `mypy --strict` clean, all three scenarios replay byte-identically.

---

## 10. What comes next

**P1b** — the HTTP layer: `POST /v1/calls/intents`, the app context-events endpoint, and
the web customer simulator that talks to the same public API the real Krungsri app would.
Then **P2** — the matching engine and the agent workstation, which is where the offer/accept
handshake and the softphone UI live.

---

## Changes since this was written

_Append here rather than editing above._

- **2026-08-23 — the ladder now goes up during the call (`D42`).** Section 4's table shows
  L1 as a dead end ("name, but no policy number"). It is no longer one. The agent has an
  identity control with three outcomes — *Confirmed* (promotes to L3), *Not this person*
  (drops to L0 and suppresses the rejected id), and *Third party acting for them* (context
  visible, disclosure still locked). Action 0, "ยืนยันตัวตนผู้ติดต่อก่อนให้ข้อมูลกรมธรรม์",
  is the on-screen prompt for exactly this.
- **2026-08-23 — promotion costs nothing, and the reason is in section 6.** The assembler
  is *not* gated by assurance; only `BriefBuilder`'s rendering is. Measured at L1 in the
  motor scenario: the snapshot already holds `MT-2025-004512` and all four `Coverage`
  figures while the brief prints "มีกรมธรรม์ที่เกี่ยวข้อง (ยังไม่ยืนยันตัวตน)". Confirming
  identity re-renders from memory — no bank-core round trip. **The gate must stay
  server-side at the wire**: never ship the full brief and hide fields in the browser.
- **2026-08-23 — the keypad stays live for the whole call (`D43`).** Section 4 explains
  "keyed their id in the IVR" as an arrival-time thing. It is now available mid-call too:
  the agent asks the caller to *type* a policy or claim number instead of reading it out,
  and when the typed value matches a policy we already hold, assurance promotes
  automatically — stronger evidence than an agent's judgment of a spoken answer.
