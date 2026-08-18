# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-19._

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

**One project now, not two** (`D34`): the `DemoProject/` split is dropped — each phase already
produces a demonstrable slice, so the demo is the current state plus a chosen scenario. The folder is
left on disk untouched (it never had a commit); deleting it is the user's call.

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
- **Tests** — 84: state machine (including tests that assert the *decisions*, so a later "tidy-up"
  that breaks `D12`/`D21`/`D33` fails loudly), orchestrator, and contract suites for `CoreDataProvider`
  and `EventBus`.
- **Domain pack** — `intents.yaml` (22 straw-man intents across 5 lines), `skills.yaml` (13 skills,
  9 queues), `dids.yaml` (5 numbers incl. the motor sticker).
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
| Q7 | **The intent taxonomy** — `config/intents.yaml` now holds a **22-intent straw-man** across motor/health/travel/life/general, with skill, urgency, required slots and playbook per entry. Needs the team's domain review: are these the reasons people actually call, and are the labels ones an agent would recognise? | Straw-man in place; P4 needs the reviewed version |
| Q8 | Typhoon model ids / licence / pricing | **Verify against live docs when writing the adapter** — not from memory or these docs |
| Q9 | `OFFER_TIMEOUT_S=20`, `ACW_TIMER_S=45` | Guesses. Tune against how a real agent works |
| Q10 | Mock-core generator scale (~2,000 customers) and whether the 3 hand-authored personas stay the demo set | Assume yes: generated data for load, hand-authored for demos and the golden set |

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
- Windows: paths have spaces (quote them).

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock is p.7 — build to that).
- Competition brief: `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf`
  (personas, journey leaks, available data, PDPA, in/out of scope, judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`
  (`main.py`, `utils.py::perform_vad`) and `…/ProjectCode/thonburian-whisper/README.md`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
