# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-24._

---

## Where things stand right now

**P0 · P1 · P1b · P2a complete.** The system knows who is calling and how much to believe
it, why they are calling from what they pressed or tapped, everything we hold about them
assembled before the phone is answered, the brief an agent reads with disclosure gated by
identity — and now **which agent should take the call, and why**.

Verified 2026-08-24: **225 tests pass**, `ruff check` + `ruff format --check` clean,
`mypy --strict` clean over 70 files, all scenarios replay byte-identically, 50/50 diagrams
current.

```bash
uv sync --extra web
uv run pytest -q
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml --quiet
uv run python scripts/run_matching.py --calls 25 --compare
uv run python -m readycall.entrypoints.api          # then http://127.0.0.1:8000/sim
```

Everything runs **in memory, no services, no keys, no GPU**. Nothing needs Docker yet.

## What exists (cumulative)

**P0 — foundations.** Config with startup coherence checks · structured logging with
`call_session_id` bound and secrets redacted · injected `Clock` + swappable ids (`D35`) ·
UTF-8 console (`B1`) · **15** call states, 19 event types, ~30 domain models · 8 ports, each
with a fake · call state machine + orchestrator (single writer, full transition log) ·
in-memory event bus (deterministic, replayable, idempotent).

**P1 — context.** `domainpack.py` (typed, cross-validated YAML; `walk_menu` turns keypresses
into line + intent) · `services/identity/` (the L0–L3 ladder, `D20`) ·
`services/context/assembler.py` (parallel fan-out, per-field provenance, frozen snapshot,
declines to guess a policy) · `services/brief/builder.py` (context-only brief, disclosure
gated at L2) · `adapters/core_data/caching.py` (TTL + serve-stale + circuit breaker).

**P1b — the HTTP layer.** `POST /v1/calls/intents` · `GET /v1/calls/intents/{id}` ·
`POST /v1/app/context-events` · `GET /v1/app/contact-reasons` · `/health` · demo persona
picker and plans under `/v1/demo/` · **customer simulator** at `/sim`, one HTML file, no
build step (`D47`). Identity comes from a `SessionResolver`, never the request body (`D4`).

**P2a — matching.** `ports/agent_directory.py` + 15-agent roster (every skill held by 2+) ·
`config/matching_weights.yaml` with startup validation · hard filters (skill, **graded**
language, capacity) · fit + urgency with full breakdowns · **our own Hungarian solver**
(`D49`) plus greedy for comparison · guard rails (wait ceiling, anti-hot-spot, guarded
deferral) · a `MatchingDecision` per call **including non-assignments**, with every candidate
and a Thai rationale · `scripts/run_matching.py --compare`.

## Next steps (in order)

1. **P2b — the agent workstation.** The offer/accept handshake + RONA + ACW (`D45`), agent
   WebSocket, presence heartbeat, the workstation shell with a stubbed softphone, the
   identity control (`D42`) and the keypad capture panel (`D44`). This is where **React**
   finally appears (`D32`).
2. **The database layer** (`D39`) lands with P2b — agent presence is the first thing that
   genuinely must outlive a process. Postgres + SQLAlchemy 2.0 + Alembic; `infra/` and the
   schema SQL are already written.
3. **P3** — voice/IVR/intake (menu prompts become real audio). **P4** — analysis and brief
   v2+ with Claude and Typhoon compared.

`grep -rn "# P1:" scripts/` still lists what `run_scenario.py` does by hand.

## Still open

| # | Question | Current default |
|---|---|---|
| Q7 | Intent taxonomy + menu wording | **User: leave as-is, revisit during the hackathon.** 28 intents, straw-man Thai |
| Q8 | Typhoon model ids / licence / pricing | Verify against live docs when writing the adapter |
| Q9 | `OFFER_TIMEOUT_S=20`, `ACW_TIMER_S=45` | Guesses; tune against how a real agent works |
| Q11 | Language menu wording when English lands | `preferred` vs `acceptable` modelled (`D38`); Thai-only for now |
| Q12 | Which challenges count for promotion to L3 (`D42`) | DOB, last 4 of citizen id, policy no. Confirm with Krungsri |
| Q13 | Does a third-party caller need a named representative (`D42`) | Assume yes; `Policy` has no `representatives` field yet |
| Q15 | Matching weights are guesses | Tune against real volumes; `--compare` exists to re-measure |

Resolved: **Q14 — the rating is an event, not a call state (`D46`)** · single project
(`D34`) · Asterisk · RTX 3050 · Claude + Typhoon compared · React workstation with the
softphone in it · web customer simulator · menu-first flow (`D37`) · menu options are
reordered but never speak customer detail aloud.

## Things to be careful about (live landmines)

- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`).
- **`enable_utf8()` before printing domain text** — Thai + cp1252 kills the process (`B1`).
- **`event` is not usable as a structlog kwarg** (`B2`).
- **`time.monotonic()` is useless for stage timings on Windows** (`B3`) — ~15.6 ms tick, so
  everything measured `0.0`. `SystemClock` uses `perf_counter`; do not "simplify" it back.
- **The client never sends `customer_id`** (`D4`). A test asserts the request schema has no
  such field.
- **Routing must never depend on the AI** (`D37`). The menu settles the queue; speech refines.
- **Never let AI delay the call** (`D12`). **Never let the model produce coverage numbers** (`D16`).
- **An ANI match is probable, not verified** (`D20`) — below L2, no policy numbers.
- **Assurance goes UP mid-call** (`D42`) — promotion is a **re-render, not a re-fetch**, and
  the gate is **server-side at the wire**, never hidden fields in React.
- **Keypad capture is UNTYPED** (`D44`). A lookup returns evidence; only the agent attests.
- **Saving the wrap-up form is NOT "done"** (`D45`). ACW runs from media disconnect until the
  agent declares *any* next state. Nothing is ever auto-saved.
- **A rating is NOT a call state** (`D46`). Call state describes progress, never data completeness.
- **Hard filters exclude, they do not down-rank** (`D22`). A failed filter is not a low score.
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `datetime.now()` or a raw random id** outside `clock.py`/`ids.py` (`D35`).
- **`docs/` is excluded from `ruff format`** — the explanations are verbatim records.
- Python is pinned **3.11**: PEP 695 generics are a syntax error; use `Generic[T]`.
- Windows: paths have spaces (quote them); heredocs with apostrophes fail — use the Write tool.

## Diagrams (visual walkthroughs)

`docs/diagrams/` — **50 diagrams** with explanations, in ten themed pages. Start at
`docs/diagrams/README.md`. Twelve are **generated from source** (transition table, domain
pack, pydantic models, adapters on disk), so they cannot drift;
`tests/unit/test_diagrams.py` fails if a committed one falls behind. It has already caught
two real changes.

```bash
uv run python scripts/gen_diagrams.py      # rebuild derived .mmd sources
uv run python scripts/render_diagrams.py   # render all .mmd -> .svg  (needs mermaid-cli)
uv run python scripts/render_diagrams.py --check   # content-hash staleness check
```

## Explanations (plain-language walkthroughs)

`docs/explanations/` — teaching notes, one per phase, written for building understanding a
layer at a time. **Snapshots, not specifications**; each has a "changes since" section.
Write one per phase as it lands — the user reads these to follow along.

- `P0_foundations.md` — the skeleton, bottom-up.
- `P1_context.md` — domain pack, assurance ladder, caching, assembler, context-only brief.
- `P1b_http_layer.md` — the session seam, why the request schema *is* the enforcement of
  `D4`, prefetch off the request path, the simulator, and the `B3` timing bug.
- `P2a_matching.md` — hard filters vs scores, why urgency multiplies, the Hungarian solver
  and the silent bug that made it return nothing, and what measuring the greedy gap said.

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock p.7). Brief:
  `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
- Mermaid CLI lives in the scratchpad this session; `npm i -g @mermaid-js/mermaid-cli` for a
  permanent one, or set `MMDC=/path/to/mmdc`.
