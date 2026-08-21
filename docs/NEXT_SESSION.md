# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-19 (second pass)._

---

## Where things stand right now

**P0 foundations are built and verified.** The spine runs: a full call lifecycle executes end to end
on fake adapters with no telephony, no GPU, no database and no API key.

Verified 2026-08-19 — **84 tests pass** (~0.5 s), `ruff check` + `ruff format --check` clean,
`mypy --strict` clean over 41 files, and all three scenarios replay with byte-identical output twice.

```bash
uv sync
uv run pytest -q
uv run python scripts/run_scenario.py tests/scenarios/pattheera_ipd.yaml --quiet
```

**Since P0 landed** (`D37`, `D38`): the call flow was reworked so a **keypad menu routes the call
before any AI runs** — see below. `config/menus.yaml` is new, every product line gained a
`<line>.other` catch-all intent, and language (Thai/English, CEFR-graded, a hard matching filter) is
now modelled in `domain/` while behaviour stays Thai-only.

**One project now, not two** (`D34`): the `DemoProject/` split is dropped — each phase already
produces a demonstrable slice, so the demo is the current state plus a chosen scenario. The folder is
left on disk untouched (it never had a commit); deleting it is the user's call.

## The most important recent change: the menu leads (`D37`)

Worst case under the old design — general hotline (usually the *only* published number in Thailand),
no app, unrecognised caller, declines the recording — the system knew **nothing**. That is worse than
the keypad menu every call centre already has, and adding AI is not worth much if the floor sits below
the status quo.

So the order is now: **greeting → product-line menu → reason menu → queue → *then* the AI intake
offer.** Routing is settled by keypresses before a word is transcribed.

| Layer | Gives | Needs | If it fails |
|---|---|---|---|
| **Keypad menu** (base) | Line + reason → the queue | Nothing — no AI, consent, speech or network | `0` always reaches a human |
| **AI intake** (delta) | The detail: which hospital, which plate, how urgent | Consent, audio, STT, LLM | Routing unaffected — it was never the AI's job |

The base is **parity with what already exists**; the AI is the delta that makes the agent's screen
useful. Menus are skipped whenever the app or a DID already answered the question, and recognised
callers get their likely options read out first (assurance L1 is enough — reordering a menu discloses
nothing).

## What exists

- **Foundation** — `config.py` (whole documented env surface + startup coherence checks that fail
  loudly on contradictory settings), structured logging with `call_session_id` bound and secrets
  redacted, injected `Clock` and swappable id generator (`D35`), UTF-8 console (`B1`).
- **Domain** — 16 call states, 19 event types, ~30 models. Pure, no I/O.
- **Ports** — all 7 defined with the contract in the docstring: telephony, stt, llm, tts, core_data,
  event_bus, blob_storage.
- **Adapters** — a fake or null for every port: in-memory bus (deterministic, replayable),
  `FixtureFileProvider` (JSON/YAML, Thai phone normalisation both ways, **Buddhist-era dates**),
  `NullCoreDataProvider`, `SimulatedTelephonyProvider` (holding bridges, per-leg forks, DTMF,
  barge-in, injectable media-fork failure), `ScriptedSttEngine`, `RuleBasedLlm` (Thai keyword intent +
  template summary — the real degradation rung, not a stub), `NullTtsEngine`, in-memory blob storage.
- **Call state machine + orchestrator** — one explicit transition table, self-validating; the
  orchestrator is the single writer of `state` and appends every transition with time + reason.
- **Tests** — 117: state machine (including tests that assert the *decisions*, so a later "tidy-up"
  that breaks `D12`/`D21`/`D33` fails loudly), orchestrator, and contract suites for `CoreDataProvider`
  and `EventBus`, plus a domain-pack suite that keeps the config files consistent with each other and
  a language suite for the CEFR filter.
- **Domain pack** — `intents.yaml` (**28** intents across 5 lines, each line with an explicit
  `<line>.other` catch-all), `skills.yaml` (13 skills, 9 queues), `dids.yaml` (5 numbers),
  **`menus.yaml`** (the keypad tree, personalisation rules, and a disabled language menu).
  All cross-references are validated by tests — dangling skill/queue/intent names fail the build.
- **Mock core** — 3 customers matching the brief's 3 personas, 4 policies across 4 product lines.
- **3 scenarios** — in-app happy path (the pitch's Pattheera/IPD), the roadside motor claim (cold
  call, no app, ANI-only identity), and the fully degraded path (anonymous + declined intake).
- **CI** — lint, format, mypy, tests, and all scenario replays.

## Next steps (in order)

1. **Finish P0's remaining slice:** Postgres schema + Alembic, the mock-core *generator* (~2,000
   customers; hand-authored fixtures exist and stay as the scenario set), `docker-compose.yml`
   (postgres, redis, minio, pgweb).
2. **P1 — Context-Aware Calling** (`PLAN.md`): intent API + session auth, **identity resolver +
   assurance ladder L0–L3**, `dids.yaml` wired in, the context assembler with per-field provenance and
   freezing, caching/circuit breaker, the customer simulator with a persona picker, and the
   workstation showing a context-only brief.
3. Then P2 (matching + workstation), P3 (voice/IVR/intake), P4 (analysis/brief).

`grep -rn "# P0:" scripts/` is the honest to-do list: every lifecycle step the scenario runner still
performs by hand, which the next services take over (`D36`).

## Still open

| # | Question | Current default |
|---|---|---|
| Q7 | **The intent taxonomy and the menu wording** — `config/intents.yaml` (28 intents) and `config/menus.yaml` (the spoken options). Needs the team's domain review: are these the reasons people actually call, is the Thai wording natural, and is the menu order sensible? | Straw-man in place; P3 needs the menu wording, P4 needs the taxonomy |
| Q8 | Typhoon model ids / licence / pricing | **Verify against live docs when writing the adapter** — not from memory or these docs |
| Q9 | `OFFER_TIMEOUT_S=20`, `ACW_TIMER_S=45` | Guesses. Tune against how a real agent works |
| Q10 | Mock-core generator scale (~2,000 customers) and whether the 3 hand-authored personas stay the demo set | Assume yes: generated data for load, hand-authored for demos and the golden set |
| Q11 | **Language menu wording**, when English lands. `preferred` vs `acceptable` is modelled (`D38`); the open question is how to ask without a clumsy four-option menu | One keypress sets `preferred`; `acceptable` defaults to just that and is widened from the customer profile |

Resolved: single project (`D34`) · telephony = Asterisk · GPU = RTX 3050 laptop · LLM = Claude and
Typhoon, compared · workstation = React with the softphone in it · customer side = web simulator ·
nothing known about their data, so `FixtureFileProvider` was built first.

## Things to be careful about (live landmines)

- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`) — read only.
- **`enable_utf8()` before printing domain text.** Thai + the Windows cp1252 console kills the
  process, and it bit within an hour of the first line of code (`B1`).
- **`event` is not usable as a structlog kwarg** — it collides with the message (`B2`).
- **Never let AI delay the call.** Leaving the queue is agent-availability only (`D12`).
- **Never let the model produce coverage numbers, eligibility, or prices** — data only (`D16`).
- **Never trust a client-supplied `customer_id`** (`D4`); an ANI match is *probable*, not verified (`D20`).
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `datetime.now()` or a raw random id** outside `clock.py`/`ids.py` — determinism depends on
  it (`D35`).
- **Never `torch.hub.load` at call time** — bundle Silero VAD (`D9`).
- **Never run a local LLM and Whisper on the same 4–6 GB GPU.**
- **Never depend on the venue's network or their API during a stage demo.**
- **The agent takes the call in the browser** — never design as if there were a separate phone (`D32`).
- **Browsers need a secure context for microphone access**; SIP-over-WSS needs a cert Asterisk serves.
  `localhost` is fine for one machine; other LAN machines need `mkcert`. P5 landmine.
- Python is pinned **3.11**, so PEP 695 generics (`class Foo[T]`) are a syntax error — use
  `Generic[T]`. Cost one debugging round already.
- **Routing must never depend on the AI** (`D37`). The menu settles the queue; speech only refines it.
- **`docs/` is excluded from `ruff format`** — the explanation files are verbatim records, and
  reformatting their code samples silently rewrites them.
- Windows: paths have spaces (quote them).

## Explanations (plain-language walkthroughs)

`docs/explanations/` holds teaching notes written for someone building understanding a layer at a
time, rather than reading the full design at once. They are **snapshots, not specifications** — if one
disagrees with `ARCHITECTURE.md` or the code, the other is right and the explanation is stale.
Each file carries a "changes since this was written" section at the bottom.

- `P0_foundations.md` — the whole P0 skeleton, bottom-up: utilities, domain, ports, adapters, the
  state machine, the scenario runner, the tests.

Write one per phase as it lands.

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock is p.7 — build to that).
- Competition brief: `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf`
  (personas, journey leaks, available data, PDPA, in/out of scope, judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`
  (`main.py`, `utils.py::perform_vad`) and `…/ProjectCode/thonburian-whisper/README.md`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
