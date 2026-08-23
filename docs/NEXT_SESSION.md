# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-23._

---

## Where things stand right now

**P0 complete. P1 core complete.** The system now *knows something* about a call: who is
calling and how much to believe it, why they are calling from what they pressed, everything
we hold about them assembled before the phone is answered, and the first version of the
brief an agent reads — with disclosure gated by how sure we are of their identity.

Verified 2026-08-21: **161 tests pass** (~1.4 s), `ruff check` + `ruff format --check`
clean, `mypy --strict` clean over 50 files, all three scenarios replay byte-identically.

```bash
uv sync
uv run pytest -q
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml --quiet
```

Everything runs **in memory, with no services, no keys, no GPU**. Nothing needs Docker yet.

## What exists (cumulative)

**Foundation (P0)** — config with startup coherence checks · structured logging with
`call_session_id` bound and secrets redacted · injected `Clock` + swappable id generator
(`D35`) · UTF-8 console (`B1`) · 16 call states, 19 event types, ~30 domain models · all
7 ports · a fake/null adapter for each · the call state machine + orchestrator (single
writer, full transition log) · in-memory event bus (deterministic, replayable, idempotent).

**P1 services**

- `domainpack.py` — loads and **cross-validates** `intents/skills/dids/menus.yaml` into
  typed objects at startup; dangling references and overflow cycles fail loudly.
  `walk_menu(["2","4"])` turns keypresses into a product line + intent.
- `services/identity/` — the **L0–L3 assurance ladder** (`D20`). Token (hashed) → L3,
  ANI + recent app intent → L2, ANI alone → L1, nothing → L0. Never refuses; an unknown
  caller is a path, not an error.
- `services/context/assembler.py` — parallel fan-out → `Customer360`, **per-field
  provenance**, frozen snapshot, relevant-policy selection that declines to guess between
  unrelated policies.
- `services/brief/builder.py` — the **context-only brief**: intent, urgency, Thai summary,
  playbook actions, suggested opening. Disclosure gated at L2; below it the policy number
  is withheld, a verify-identity step is prepended, and L2-gated actions are dropped.
- `adapters/core_data/caching.py` — TTL cache + **serve-stale-and-say-so** + circuit
  breaker, so a dead upstream degrades in microseconds instead of timing out per lookup.

**Also landed** — `mock/bank_core/generate.py` (2,000 customers, deterministic, reproduces
the brief's holding distribution, every 7th record Buddhist-era dated) ·
`infra/docker-compose.yml` (postgres/redis/minio/pgweb) + schema SQL that enforces `D5`
with a `SELECT`-only role.

## Next steps (in order)

1. **P1b — the HTTP layer.** `POST /v1/calls/intents` (session auth → `customer_id`,
   correlation token, expiry), `POST /v1/app/context-events`, and the **web customer
   simulator** with a demo persona picker. The rule that matters: the simulator talks to
   the *same public API the real Krungsri app would*, so replacing it later changes
   nothing server-side. Needs `fastapi` + `uvicorn` added to the `web` extra.
2. **P2 — matching + the agent workstation** (`PLAN.md`). Also the natural moment to add
   the **Postgres/SQLAlchemy/Alembic layer** (`D39`), since agent presence and matching
   decisions are the first things that must outlive a process.
3. **P3** — voice/IVR/intake (the menu prompts become real audio). **P4** — analysis and
   brief v2+ with Claude and Typhoon compared.

`grep -rn "# P1:" scripts/` lists what the scenario runner still does by hand: the IVR
prompts (P3), the media gateway (P3), and the matching engine (P2).

## Still open

| # | Question | Current default |
|---|---|---|
| Q7 | Intent taxonomy + menu wording (`config/intents.yaml`, `config/menus.yaml`) | **User said: leave as-is, revisit during the hackathon.** 28 intents, straw-man Thai wording |
| Q8 | Typhoon model ids / licence / pricing | Verify against live docs when writing the adapter |
| Q9 | `OFFER_TIMEOUT_S=20`, `ACW_TIMER_S=45` | Guesses; tune against how a real agent works |
| Q11 | Language menu wording when English lands | `preferred` vs `acceptable` modelled (`D38`); Thai-only for now, likely for the hackathon too |
| Q12 | Which verification challenges count for promotion to L3 (`D42`) | DOB, last 4 of citizen id, policy no. Confirm the real list with Krungsri |
| Q13 | Does a third-party caller need a named representative on the policy (`D42`) | Assume yes; `Policy` has no `representatives` field yet |
| Q14 | **`WRAP_UP → RATING` is ordered backwards.** The customer rates in the IVR seconds after hanging up; the agent may save their wrap-up minutes later. The table says rating *follows* wrap-up, and `run_scenario.py` even transitions to `RATING` with reason `wrapup_saved`. In reality they are concurrent. | Fix at **P2/P6**: either make the rating an event that can arrive at any time rather than a call state, or reach `CLOSED` only once both the wrap-up and the rating have landed (or timed out). Touches `machine.py`, the runner and 3 scenarios, so it wants doing deliberately, not in passing. |

Resolved: single project (`D34`) · Asterisk · RTX 3050 · Claude + Typhoon compared ·
React workstation with the softphone in it · web customer simulator · menu-first flow
(`D37`) · **menu options are reordered but never speak customer detail aloud**.

## Things to be careful about (live landmines)

- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`).
- **`enable_utf8()` before printing domain text** — Thai + cp1252 kills the process (`B1`).
- **`event` is not usable as a structlog kwarg** (`B2`).
- **Routing must never depend on the AI** (`D37`). The menu settles the queue; speech refines it.
- **Never let AI delay the call** (`D12`). **Never let the model produce coverage numbers** (`D16`).
- **An ANI match is probable, not verified** (`D20`) — below L2, no policy numbers.
- **Assurance goes UP mid-call** (`D42`) — and promotion must be a **re-render, not a re-fetch**.
  The assembler is deliberately not gated; keep it that way. **Gate at the wire, server-side** —
  never send the full brief and hide fields in React.
- **Keypad capture is UNTYPED** (`D44`). Never assume the caller holds a particular document.
  A lookup returns evidence (`matched` / `not matched`); it must never promote assurance by
  itself — a match cannot tell the policyholder from a relative holding their papers. And
  because we do not know what the digits are, treat raw captures as sensitive by default.
- **Saving the wrap-up form is NOT "done"** (`D45`). It closes the call record. ACW runs from
  **media disconnect** until the agent declares their next state — **any** state (Break and
  Lunch end it too, not just Ready). **Nothing is ever auto-saved**: the agent owns the record,
  and an unsaved wrap-up is honest data. Never auto-ready — an agent marked available while
  working elsewhere means the next caller rings an empty desk (RONA).
- **Never ask a leading identity question.** "ขอทราบชื่อผู้ติดต่อ", not "ใช่คุณ X ไหมคะ" — naming
  the customer first both leaks that the number belongs to them and weakens the check.
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `datetime.now()` or a raw random id** outside `clock.py`/`ids.py` (`D35`).
- **Menu options never speak customer detail** — reordering only.
- **`docs/` is excluded from `ruff format`** — the explanations are verbatim records.
- Python is pinned **3.11**: PEP 695 generics are a syntax error; use `Generic[T]`.
- Windows: paths have spaces (quote them).

## Diagrams (visual walkthroughs)

`docs/diagrams/` — **45 diagrams** with explanations, in nine themed pages. Start at
`docs/diagrams/README.md`. Roughly a quarter are **generated from source** (the transition
table, the domain pack, the pydantic models, the adapters on disk), so they cannot drift;
`tests/unit/test_diagrams.py` fails if a committed one falls behind the code.

```bash
uv run python scripts/gen_diagrams.py      # rebuild derived .mmd sources
uv run python scripts/render_diagrams.py   # render all .mmd -> .svg  (needs mermaid-cli)
uv run python scripts/render_diagrams.py --check   # report stale SVGs
```

## Explanations (plain-language walkthroughs)

`docs/explanations/` — teaching notes written for building understanding a layer at a time.
**Snapshots, not specifications**; each has a "changes since" section at the bottom.
Write one per phase as it lands — the user reads these to follow along.

- `P0_foundations.md` — the skeleton, bottom-up: utilities, domain, ports, adapters, the
  state machine, the scenario runner, the tests.
- `P1_context.md` — the domain pack, the assurance ladder, the caching layer, the context
  assembler, the context-only brief, and the mock generator.

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock is p.7). Brief:
  `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
