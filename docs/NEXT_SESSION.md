# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-24._

---

## Where things stand right now

**P0 · P1 · P1b · P2a · P2b complete.** The system knows who is calling and how much to
believe it, why they are calling, everything we hold about them assembled before the phone
is answered, which agent should take it and why — and now **the desk actually rings, a
human accepts, and the screen is already right**.

Verified 2026-08-24: **292 tests pass**, `ruff check` + `ruff format --check` clean,
`mypy --strict` clean over 81 files, all scenarios replay byte-identically, 50/50 diagrams
current. The whole workstation flow was also driven by hand in a browser.

```bash
uv sync --extra web
uv run pytest -q
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml --quiet
uv run python scripts/run_matching.py --calls 25 --compare
cd apps/workstation && npm install && npm run build && cd ../..   # once; node not needed to RUN
uv run python -m readycall.entrypoints.api
#   http://127.0.0.1:8000/sim          the customer
#   http://127.0.0.1:8000/workstation  the agent
```

Everything runs **in memory, no services, no keys, no GPU**. Nothing needs Docker yet.

## What exists (cumulative)

**P0 — foundations.** Config with startup coherence checks · structured logging · injected
`Clock` + swappable ids (`D35`) · UTF-8 console (`B1`) · **15** call states, 19 event types
· 8 ports each with a fake · call state machine + orchestrator (single writer) · in-memory
event bus.

**P1 — context.** `domainpack.py` · `services/identity/` (the L0–L3 ladder) ·
`services/context/assembler.py` (parallel fan-out, per-field provenance, frozen snapshot) ·
`services/brief/builder.py` · `adapters/core_data/caching.py` (TTL + serve-stale + breaker).

**P1b — the HTTP layer.** `POST /v1/calls/intents` · app context events · contact reasons ·
**customer simulator** at `/sim`, one HTML file, no build step (`D47`). Identity comes from
a `SessionResolver`, never the request body (`D4`).

**P2a — matching.** 15-agent roster · tunable weights with startup validation · hard filters
(skill, **graded** language, capacity, `already_offered`) · fit × urgency · **our own
Hungarian solver** (`D49`) · guard rails · a `MatchingDecision` per call **including
non-assignments**, saying **which** of the two unplaced reasons applies (`D50`).

**P2b — the workstation.** `services/agents/` (presence with both axes, the offer handshake,
dispatch) · `services/queues/hours.py` + `queue_hours.yaml` · `services/capture/keypad.py`
(`D44`) · `services/identity/attestation.py` (`D42`) · `api/realtime.py` (per-agent
sequencing, replay-on-reconnect) · `api/routers/agent.py` · **React workstation** at
`/workstation` (`D32`) · `POST /v1/demo/calls` standing in for telephony.

## Next steps (in order)

1. **P2c — the database layer** (`D39`). It did **not** land with P2b and that is the
   biggest outstanding debt: presence, assignments and `agent_state_log` are in memory, so
   a restart loses a shift. Postgres + SQLAlchemy 2.0 + Alembic; `infra/docker-compose.yml`
   and the schema/role SQL already exist. **Docker was not running on this machine**, so
   anything written against it must actually be started and verified, not assumed.
2. **P3** — voice/IVR/intake: menu prompts become real audio, VAD, streaming STT, the
   bake-off on the 3050.
3. **P4** — analysis and brief v2+ with Claude and Typhoon compared.

`grep -rn "# P2b:" src/` lists the steps a real IVR will drive that the demo endpoint fakes.

## Still open

| # | Question | Current default |
|---|---|---|
| Q7 | Intent taxonomy + menu wording | **User: leave as-is, revisit during the hackathon.** |
| Q8 | Typhoon model ids / licence / pricing | Verify against live docs when writing the adapter |
| Q9 | `OFFER_TIMEOUT_S=20`, ACW thresholds | Guesses; tune against how a real agent works |
| Q11 | Language menu wording when English lands | `preferred` vs `acceptable` modelled (`D38`) |
| Q12 | Which challenges count for promotion to L3 | 4 in `KNOWN_CHALLENGES`; confirm with Krungsri |
| Q13 | Does a third-party caller need a named representative | Assume yes; `Policy` has no `representatives` field yet |
| Q15 | Matching weights are guesses | Tune against real volumes; `--compare` exists to re-measure |
| **Q16** | **A keypad lookup confirms a policy number at L1.** The caller supplied the digits and the agent must not read them aloud below L2 — but it is a confirmation oracle. Designed this way in `D44`; worth a second look. | Allowed |
| **Q17** | **Commit `apps/workstation/dist/`?** It is gitignored, so a fresh clone has no workstation until `npm run build` runs — and on a venue with no internet, `npm install` is what fails. | Not committed |

Resolved: rating is an event (`D46`) · single project (`D34`) · Asterisk · RTX 3050 · Claude
+ Typhoon compared · React workstation with the softphone in it · web customer simulator ·
menu-first flow (`D37`).

## Things to be careful about (live landmines)

- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`).
- **NEVER serialise a domain model where a permission boundary exists** (`D53`, `B5`). It
  shipped a real leak: `CaseBrief.model_dump()` sent policy numbers and coverage figures to
  a call at L1. Use a wire DTO, and **test the raw bytes** — an assertion on rendered text
  cannot see a field the renderer never mentions.
- **`enable_utf8()` before printing domain text** — Thai + cp1252 kills the process (`B1`).
- **`event` is not usable as a structlog kwarg** (`B2`).
- **`time.monotonic()` is useless for stage timings on Windows** (`B3`) — use `perf_counter`.
- **`ManualClock()` defaults to New Year's Day**, so every `business` queue is CLOSED under
  a default test clock and a placed call returns `queue_closed:holiday`. Pass an explicit
  `start` when hours matter. The default is not changed: scenario golden output depends on it.
- **`zoneinfo` has no tz database on Windows** — `tzdata` is a declared dependency. Without
  it `Asia/Bangkok` raises *only* on the demo laptop, never on CI.
- **The client never sends `customer_id`** (`D4`) — **nor `agent_id`** on the staff side.
  Both come from their own (separate!) session cookie and store.
- **Routing must never depend on the AI** (`D37`); **never let AI delay the call** (`D12`);
  **never let the model produce coverage numbers** (`D16`).
- **An ANI match is probable, not verified** (`D20`). **Assurance goes UP and DOWN mid-call**
  (`D42`) — promotion is a **re-render, not a re-fetch**.
- **Keypad capture is UNTYPED** (`D44`). A lookup returns evidence; only the agent attests.
- **Saving the wrap-up is NOT "done"** (`D45`). ACW runs from media disconnect until the
  agent declares *any* next state. Nothing is auto-saved and nothing auto-readies.
- **A rating is NOT a call state** (`D46`).
- **Hard filters exclude, they do not down-rank** (`D22`). A failed filter is not a low score.
- **A call can go unplaced for two opposite reasons** (`D50`) — roster gap vs capacity. And
  `chosen_agent_id is None` covers a **third** case: a deliberate `DEFER`.
- **Don't make UI state optimistic where the value is read aloud.** The keypad panel did, and
  invented a digit the server did not have.
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `datetime.now()` or a raw random id** outside `clock.py`/`ids.py` (`D35`).
- **`docs/` is excluded from `ruff format`** — the explanations are verbatim records.
- Python is pinned **3.11**: PEP 695 generics are a syntax error; use `Generic[T]`.
- Windows: paths have spaces (quote them); heredocs with apostrophes fail — use the Write tool.

## Diagrams (visual walkthroughs)

`docs/diagrams/` — **50 diagrams** with explanations, in ten themed pages. Start at
`docs/diagrams/README.md`. Twelve are **generated from source**, so they cannot drift;
`tests/unit/test_diagrams.py` fails if a committed one falls behind.

```bash
uv run python scripts/gen_diagrams.py      # rebuild derived .mmd sources
uv run python scripts/render_diagrams.py   # render all .mmd -> .svg  (needs mermaid-cli)
uv run python scripts/render_diagrams.py --check   # content-hash staleness check
```

`mermaid-cli` is not installed globally: `npm i -g @mermaid-js/mermaid-cli`, or set `MMDC`.

## Explanations (plain-language walkthroughs)

`docs/explanations/` — teaching notes, one per phase. **Snapshots, not specifications**;
each has a "changes since" section. Write one per phase as it lands.

- `P0_foundations.md` · `P1_context.md` · `P1b_http_layer.md` · `P2a_matching.md`
- `P2b_workstation.md` — the two axes, the handshake, after-call work, the socket, queue
  hours, and **the two bugs found by running it** (the disclosure leak and the invented digit).

## Before any `/compact`

```bash
uv run python scripts/audit_docs.py
```

Mismatches in `NEXT_SESSION` / `PROJECT_STATE` / `diagrams/*.md` are real bugs. Mismatches
in `explanations/*.md` and `BUG_HISTORY` verification notes are **expected** — those are
dated snapshots, and the fix is a "changes since" entry, never an edit to the body.

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock p.7). Brief:
  `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
