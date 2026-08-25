# PROJECT_STATE

_What this project is, what exists, what doesn't, and where everything lives._
_Last updated: 2026-08-25._

---

## 1. Purpose

**Krungsri ReadyCall** — an AI context layer for insurance service calls.

> Customers wait with no progress, while brokers start each conversation with limited context.
> ReadyCall turns waiting time into preparation time.

Three moves (from the team's pitch, `../Krungsri.pdf`):

1. **Context-Aware Calling** — tapping *Contact* on a specific plan inside the logged-in app carries
   verified identity, the selected product, active policies and recent in-app activity into the call.
2. **AI Pre-Call Intake** — while queued, the customer can describe the issue. It's recorded,
   transcribed (Thai), summarised and structured into a case brief before they reach the front.
3. **A ready agent workstation** — the broker takes the call *in the browser* (softphone included),
   and by the time they press Accept they already have who / which policy / what they want / what to
   say / what to do, plus the matching rationale and a confidence signal (`D32`).

Targets the brief's *"Broker เวลาจํากัด"* and *"เข้าไม่ถึงข้อมูล"* leaks, in the in-scope areas
(lead prioritisation, customer engagement, **broker productivity — "สรุปลูกค้าให้ broker ก่อนคุย"**).
Deliberately outside: underwriting, policy issuance, premium pricing, legal/tax advice, core-system changes.

**This is the whole project.** There is no separate demo repo — each build phase produces a
demonstrable slice, so "the demo" is the current state plus a chosen scenario (`D34`).

---

## 2. Status: **P0 · P1 · P1b · P2a · P2b · P2c complete**

The spine runs. A full call lifecycle - arrival, IVR, consent, queue, intake, matching, the offer
handshake, the live call, wrap-up, rating, closed - executes end to end on fake adapters with no
telephony, no GPU, no database and no API key.

As of P2b the identity ladder, the menu walk, the context assembler, the brief builder, the public
API, the matching engine, agent presence, the offer handshake and the React workstation are all real
services doing real work - only the *edges* (phone, speech, AI, the bank's data, the agent roster)
are still fakes. An agent signs in at `/workstation`, a caller arrives, the desk rings, the brief is
already there, and the disclosure gate moves when the agent attests.

Verified on 2026-08-25: **441 tests pass** (438 + 3 skipped — the FK cases the in-memory
backend cannot have), `ruff check` and `ruff format --check` clean over 128 files,
`mypy --strict` clean over **100** source files, and all three scenarios replay
byte-identically. The database suites ran against a **live Postgres**, and a restart was
verified outside pytest with two real uvicorn processes.

```
$ uv run python scripts/run_scenario.py tests/scenarios/pattheera_ipd.yaml --quiet
TIMELINE
  +   0.00s  (start) intent_created                       intent_created
  +   0.00s  intent_created -> connecting                 app_placed_call
  +   1.00s  connecting -> ivr                            ivr_started
  +  10.00s  ivr -> queued                                queued
  +  10.00s  queued -> intake_active                      consent_given_press_1
  +  23.50s  intake_active -> intake_complete             silence_timeout
  +  45.50s  intake_complete -> matched                   agent_available
  +  45.50s  matched -> offered                           offered_to:A006
  +  52.50s  offered -> in_call                           agent_accepted
  + 292.50s  in_call -> wrap_up                           caller_hung_up
  + 327.50s  wrap_up -> closed                            wrapup_saved

RATING
  csat=4/5  via customer_ivr  at +297.5s
  (call closed at +327.5s - the rating landed 30.0s earlier)
```

The last two lines are the point of `D46`: the rating arrived **30 seconds before** the call
closed, while the agent was still writing up. A `RATING` state after `WRAP_UP` asserted an
ordering that does not hold.

**One project, not two** (`D34`): the `DemoProject/` split is dropped. Each phase already produces a
demonstrable slice, so the demo is simply the current state of the system with a chosen scenario.

---

## 3. Tech stack (decided)

| Layer | Choice |
|---|---|
| Language / tooling | Python 3.11, **`uv`** for envs, deps, and running |
| API / services | FastAPI + Uvicorn, async-first; **modular monolith, multiple entrypoints** (`D2`) |
| Data | Postgres 16 (two logical stores: `core` read-only, `readycall` read-write), SQLAlchemy 2.0 + Alembic |
| State / bus | Redis 7 (presence, queues, cache) + Redis Streams as the event bus (Kafka adapter for scale) |
| Telephony | Asterisk 20 + ARI + AudioSocket by default, behind a `TelephonyProvider` port (Twilio / LiveKit / simulated adapters). Demo trick: a softphone on a real mobile pointed at the laptop over local Wi-Fi = a genuine VoIP call with no internet |
| STT | Thonburian Whisper (`biodatlab/whisper-th-medium-combined`) default, re-implemented streaming-first (`D9`); CTranslate2 build and **Typhoon ASR** benchmarked against it (`D30`) |
| TTS | Pre-rendered prompt clips built from `voice_prompts.yaml` (`D24`); streaming only for future conversational intake |
| LLM | `AnthropicAdapter` + `OpenAiCompatibleAdapter` both implemented (`D29`) — the latter covers Typhoon API, OpenAI, vLLM and Ollama by base URL. No LLM framework (`D31`) |
| Object storage | MinIO (S3 API) for recordings |
| Agent workstation | React 18 + TypeScript + Vite. **A full contact-centre workstation in one browser tab — the softphone is in it** (SIP.js over WSS to Asterisk, WebRTC/Opus through the agent's headset), plus the brief, the queue and status control. No desk phone, no install (`D32`) |
| Customer side | Responsive **web customer simulator** with a demo persona picker, calling the same public `/v1/…` API the real Krungsri app would |
| DB inspection | `pgweb` in compose + our own **Call Explorer** admin page |
| Observability | OpenTelemetry traces keyed by `call_session_id`, Prometheus + Grafana + Loki |

Full adapter catalogue and library list: `INTEGRATIONS.md`.

---

## 4. Folder structure

**`*` = exists on disk today. Everything without one is planned and NOT THERE.** The markers
were inconsistent for several phases, which is the general form of `Q19` — a folder map that
lists a path reads as though the path is real, and somebody eventually writes an import
against it. They were audited against disk on 2026-08-25; re-audit rather than trust if this
date is old.

```
FullProject/
├─ pyproject.toml*           # package renamed fullproject → readycall in P0
├─ uv.lock  .python-version  .env.example  .gitignore
├─ README.md*
├─ docs/*                    # ← this documentation system
│  └─ diagrams/*           # 56 diagrams + 11 explanation pages; a quarter generated from source
├─ config/*                  # ← the entire insurance-specific "domain pack" (D28)
│  ├─ core_mapping.yaml      # bank-data field mapping (swap target, DATA_MODEL §4)
│  ├─ matching_weights.yaml* # fit + urgency weights, tunable at runtime
│  ├─ intents.yaml*          # closed intent taxonomy + required slots per intent
│  ├─ skills.yaml*           # skill codes, queues, intent→skill mapping
│  ├─ queue_hours.yaml*      # opening hours + holidays per queue
│  ├─ dids.yaml*             # printed phone numbers → product line + queue (D19)
│  ├─ menus.yaml*            # the IVR tree. ALSO served to the app (D48) - one menu, two surfaces
│  ├─ challenges.yaml*       # how an agent may verify a caller. Served, never hardcoded twice (D72)
│  ├─ demo_personas.yaml*    # DEMO: ids only, everything displayed is read live (D47)
│  ├─ voice_prompts.yaml     # every spoken line, as Thai text (D24)
│  └─ playbooks/             # per-intent recommended-action playbooks. NOT YET REAL (Q19):
│                            #   the steps live in _PLAYBOOKS in services/brief/builder.py
│                            #   until P4. The chain is intent -> playbook name -> ordered
│                            #   (Thai text, required assurance) -> filtered by level ->
│                            #   verify-identity inserted at 0 below L2 (D56).
├─ prompts/                  # versioned prompt files (never inline in code)
│  └─ th/ intent_classify.v1.md  summarize_intake.v1.md  suggested_opening.v1.md  ...
├─ src/readycall/
│  ├─ config.py* logging.py* errors.py*
│  ├─ domain/*               # pure models, no I/O
│  │  ├─ models.py           # Customer, Policy, CallSession, TranscriptTurn, CaseBrief, MatchingDecision…
│  │  ├─ enums.py            # CallState, IntentCode, ConsentScope, FinalizeReason…
│  │  └─ events.py           # event schemas (versioned)
│  ├─ ports/*                # Protocols only — THE seams
│  │  ├─ telephony.py  stt.py  llm.py  tts.py  core_data.py
│  │  └─ event_bus.py  blob_storage.py  agent_directory.py  notifier.py
│  ├─ adapters/*
│  │  ├─ telephony/  asterisk_ari.py  twilio.py  livekit.py  simulated.py
│  │  ├─ stt/        thonburian_hf.py  faster_whisper.py  cloud.py  scripted.py
│  │  ├─ llm/        anthropic.py  openai_compatible.py  gemini.py  rulebased.py
│  │  ├─ tts/        prerendered.py  azure.py  null.py   # build-time render, not live
│  │  ├─ core_data/  mock_postgres.py  fixtures.py  http_api.py  sql_passthrough.py
│  │  │               caching.py  null.py  mapping.py   # YAML-driven field mapper
│  │  ├─ event_bus/  redis_streams.py  kafka.py  memory.py
│  │  └─ storage/    minio.py  s3.py  localfs.py
│  ├─ services/*             # NO insurance-specific literals may live here (D28)
│  │  ├─ call_orchestrator/  machine.py  handlers.py     # single writer of call state
│  │  ├─ identity/           resolver.py  assurance.py   # L0–L3 ladder (D20)
│  │  ├─ context/            assembler.py  snapshot.py   # Customer360 + provenance
│  │  ├─ ivr/                flow.py  prompts.py  dtmf.py  rating.py
│  │  ├─ intake/             base.py  passive.py  guided.py  conversational.py  slots.py
│  │  ├─ transcription/      stream_manager.py  vad.py  turns.py  worker.py
│  │  ├─ analysis/           intent.py  entities.py  summary.py  brief.py
│  │  │                      nba.py  opening.py  confidence.py  pii.py  progress.py
│  │  ├─ matching/*          engine.py  scoring.py  solver.py  weights.py
│  │  ├─ agents/*            presence.py  assignment.py  dispatch.py   # P2b
│  │  ├─ queues/*            hours.py                                  # P2b
│  │  ├─ capture/*           keypad.py            # untyped DTMF capture (D44)
│  │  ├─ consent/            service.py  policy.py  retention.py
│  │  ├─ wrapup/             service.py  callbacks.py
│  │  └─ metrics/            rollups.py
│  ├─ media/                 # the media gateway (audio I/O, resampling, framing, recording)
│  │  ├─ gateway.py  audiosocket.py  ws_media.py  resample.py  recorder.py
│  ├─ api/*
│  │  ├─ app.py*  deps.py*  security.py*  realtime.py*   # realtime = the agent hub
│  │  ├─ routers/  mobile.py*  agent.py*  demo.py*  health.py*  telephony_webhooks.py  admin.py
│  │  └─ schemas.py*   # request/response DTOs. NEVER serialise a domain model where a
│  │                   #   permission boundary exists - that shipped a leak (B5, D53)
│  ├─ db/*                   # P2c. Repositories return DOMAIN models, never ORM rows (D77)
│  │  ├─ base.py*            #   declarative base, naming convention, Json/Utc types
│  │  ├─ session.py*         #   engine + session factory. SQLite gets foreign keys ON (D75)
│  │  ├─ repositories.py*    #   Postgres impls of the seams P0 already had
│  ├─ stores.py*          #   the other six, one page (D78)
│  ├─ storage.py*         #   STORAGE_BACKEND -> a set of stores. THE factory line
│  │  ├─ models/*            #   calls.py, agents.py. Presence is NOT a table (D76)
│  │  └─ migrations/*        #   alembic; URL from Settings, never alembic.ini
│  ├─ workers/     orchestrator_worker.py  analysis_worker.py  stt_worker.py  jobs.py
│  ├─ observability/  tracing.py  metrics.py  timing.py
│  └─ entrypoints/  api.py  worker.py  media.py  stt.py    # the runnable processes
├─ mock/*                    # the simulated read-only bank data + the agent roster
│  ├─ bank_core/generate.py*  bank_core/fixtures/*  agents/agents.json*
│  │                        # the `core` schema DDL lives in infra/postgres/init/*, not here.
│  │                        # personas.yaml is NOT written; demo personas are config/demo_personas.yaml*
├─ apps/*
│  ├─ workstation/*          # React 18 + TS + Vite — the agent desktop (D32). dist/ is
│  │                         #   gitignored and mounted at /workstation when it exists,
│  │                         #   so the API runs with no node installed.
│  └─ customer_sim/*         # one static HTML page, no build step (D47)
├─ infra/*
│  ├─ docker-compose.yml  asterisk/  grafana/  k8s/
├─ scripts/*                 # audit_docs, gen_diagrams, render_diagrams, run_matching, run_scenario
└─ tests/*
   ├─ unit/*  integration/*  contracts/*  # contracts/ = the suites every impl must pass
   ├─ scenarios/*                       # end-to-end scripted calls, no telephony
   └─ golden/                           # labelled AI evaluation set
```

Rationale for the shape: `domain` has no I/O, `ports` has no implementations, `adapters` has no
business rules, `services` imports only `domain` + `ports`. Any file that breaks that is a bug.

---

## 5. Feature status

P0 and the P1 core are built; everything from P1b onward is not. Legend: ☐ planned · ◐ in progress · ☑ done.

**Phase P0 — foundations** (mostly done)
☑ package rename + layout · ☑ config/settings (`config.py`, startup coherence checks) ·
☑ structured logging with `call_session_id` bound + secret redaction · ☑ injected clock and ids
(`D35`) · ☑ UTF-8 console (`B1`) · ☑ domain models, enums, event schemas · ☑ all 7 ports defined ·
☑ fake/null adapters for every port · ☑ call state machine + orchestrator + transition log ·
☑ contract test suites (core data, event bus) · ☑ scenario runner + 3 scenarios · ☑ CI ·
☑ domain-pack config (`intents.yaml`, `skills.yaml`, `dids.yaml`) · ☑ core fixtures (3 personas,
4 policies across 4 lines) · ☐ Postgres schema + Alembic · ☐ mock-core *generator* (~2,000 customers;
hand-authored fixtures exist) · ☐ docker-compose

**P1 — context-aware calling** (core done; the HTTP layer is P1b)
☑ identity resolver + assurance ladder L0–L3 · ☑ `dids.yaml` + `menus.yaml` wired in ·
☑ typed, cross-validated domain pack · ☑ Customer360 assembler + frozen snapshot + per-field
provenance · ☑ caching / stale-serving / circuit breaker · ☑ **context-only brief with
assurance-gated disclosure** · ☑ mock-core generator · ☑ docker-compose + schema SQL ·
☑ **intent API + session auth** (`POST /v1/calls/intents`, `SessionResolver` seam) ·
☑ **app context events** · ☑ **customer simulator + demo persona picker** (`D47`) ·
☐ agent screen v1 (moves to P2 with the workstation)

**P2a — the matching engine** (done)
☑ agent directory port + 15-agent roster (every skill held by 2+, `D22`) · ☑ tunable
`matching_weights.yaml` with startup validation · ☑ hard filters (skill, **graded language**,
capacity) · ☑ fit + urgency scoring with full breakdowns · ☑ **Hungarian solver, ours**
(`D49`) + greedy for comparison · ☑ guard rails: wait ceiling, anti-hot-spot, guarded
deferral · ☑ persisted rationale on every decision incl. non-assignments · ☑ matching
simulator with `--compare` · ☑ unplaced callers say **which** of the two reasons applies —
roster gap vs capacity (`D50`, `B4`)

**P2b — the workstation** (done, except the DB)
☑ queues + hours (`queue_hours.yaml`, holidays, next-open time) · ☑ presence with both axes +
heartbeat sweep (`D51`) · ☑ **offer/accept + RONA + ACW** (`D45`), with re-offer exclusion
(`D52`) · ☑ agent WebSocket: per-agent sequencing, replay-on-reconnect, backoff · ☑ **React
workstation** incl. call-control bar (stubbed softphone) · ☑ identity control (`D42`) ·
☑ keypad capture panel (`D44`) · ☑ **the disclosure gate moved to a wire DTO** after it
leaked (`B5`, `D53`) · ☑ **the background sweeper** — RONA, re-matching and heartbeat
expiry finally have a driver (`B7`) · ☑ tiered keypad lookups reporting which rung matched,
both eras (`D66`, `D67`) · ☑ gated brief preview on the offer card (`D69`) · ☑ queue strip
split into mine/all (`D70`) · ☐ **Postgres/SQLAlchemy/Alembic** (`D39`) — deferred again;
see `NEXT_SESSION`

**P2c — persistence** (done)
☑ SQLAlchemy 2.0 async + Alembic, URL from `Settings` (`D75`) · ☑ **9 tables**:
`call_sessions`, `call_state_transitions`, `agent_state_log`, `assignments`,
`identity_attestations`, `keypad_captures`, `matching_decisions`, `context_snapshots`,
`call_wrapups` · ☑ Postgres stores returning **domain models** (`D77`) · ☑ **one contract
suite across three backends**, SQLite with foreign keys enforced · ☑ **write-through with
an in-memory projection** (`D78`): services keep their working set, write durably, and
restore at startup · ☑ presence, the waiting pool and the live identity are **derived, not
stored** (`D76`, `D78`) · ☑ `Container` wired to `STORAGE_BACKEND` · ☑ `D44`'s inverted
default at rest — unnamed digits are never persisted · ☑ **a restart proved by ending a
process**, twice: under `TestClient` and with two real uvicorn processes against live
Postgres · ☑ Alembic no longer churns foreign keys, and the suite has its own database
(`D79`)

**P3 — voice, IVR & intake v1** ☐ voice-prompt build pipeline + prompt studio · ☐ IVR flow (menu,
identify, consent, press-1/2, rating) · ☐ media gateway (per-leg fork) · ☐ recording + encryption ·
☐ VAD endpointing · ☐ streaming STT worker · ☐ **STT bake-off on the 3050** · ☐ incremental turns ·
☐ ring-time grace

**P4 — analysis & case brief** ☐ intent taxonomy + classifier · ☐ entity extraction · ☐ rolling
summary · ☐ brief versioning · ☐ confidence calibration · ☐ NBA playbooks · ☐ suggested opening ·
☐ Anthropic adapter · ☐ OpenAI-compatible adapter · ☐ golden-set evaluation · ☐ provider comparison

**P5 — real telephony** ☐ Asterisk + ARI adapter · ☐ TLS/WSS certs · ☐ **in-browser softphone
(SIP.js, devices, self-test, reconnect)** · ☐ customer WebRTC path · ☐ PSTN/ANI identification ·
☐ product-line DIDs · ☐ media fork · ☐ bridge-on-accept/transfer · ☐ Twilio adapter

**P6 — live transcription, wrap-up & metrics** ☐ both-leg live transcription · ☐ call-progress
estimation · ☐ deferral enabled · ☐ post-call summary · ☐ dispositions · ☐ follow-ups · ☐ ratings
(customer + agent) · ☐ after-hours voicemail → briefed callbacks · ☐ Call Explorer ·
☐ metrics rollups + dashboard

**P7 — PDPA hardening** ☐ consent flows · ☐ PII masking · ☐ retention/erasure · ☐ RBAC · ☐ audit log

**P8 — future** ☐ guided intake (TTS slot-filling) · ☐ conversational AI intake · ☐ live in-call
assist · ☐ proactive outbound · ☐ product recommendation on top of the same context layer

---

## 6. How to run

Works today, from a clean clone, with no services and no keys:

```bash
uv sync
uv run pytest -q
uv run python scripts/run_scenario.py tests/scenarios/pattheera_ipd.yaml --quiet
uv run python scripts/run_scenario.py tests/scenarios/roadside_motor_claim.yaml --quiet
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml --quiet
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Arrives with later phases:

```bash
docker compose -f infra/docker-compose.yml up -d   # postgres, redis, minio, pgweb, asterisk
uv run alembic upgrade head
uv run python scripts/seed_mock_core.py --seed 42
uv run python -m readycall.entrypoints.api         # API + agent WebSocket
uv run python -m readycall.entrypoints.worker      # orchestrator + analysis consumers
```

Handy: `grep -rn "# P1:" scripts/` lists every lifecycle step the scenario runner is still
performing by hand, i.e. what the next services take over (`D36`).

---

## 7. Known constraints & open risks

- **GPU is an RTX 3050 laptop (4–6 GB).** Enough — it already ran Thonburian medium for the scam
  project — but tight. Whisper pads every chunk to 30 s, so `faster-whisper`/CTranslate2 with
  `int8_float16` is probably required to hit the latency budget. **Do not plan to run a local LLM and
  Whisper on the same card**: the default split is STT local, LLM via API. Whoever has the strongest
  GPU should own the demo machine. See `INTEGRATIONS.md` §2.1.
- **Telephony is the highest-risk dependency** — self-hosted Asterisk is free but fiddly (NAT, codecs,
  WebRTC certs); Twilio is easy but costs money and needs public HTTPS. The simulated adapter exists
  so no other phase is ever blocked on this.
- **The hackathon's real data is unknown.** Mitigated by the `CoreDataProvider` port + YAML mapping +
  contract tests (`DATA_MODEL.md` §4).
- **Thai NLP quality** on domain jargon (IPD/OPD, ค่าห้อง, สินไหม) needs the golden set to measure —
  don't trust vibes.
- **PDPA** is a judged constraint; health data needs its own consent. Not optional.
- **Hallucination risk** in an insurance context is the single biggest product risk → coverage numbers
  are never model-generated, only data-read (`INTEGRATIONS.md` §3).
- Windows dev box: paths contain spaces, console is cp1252 — quote paths, write Thai to UTF-8 files.

---

## 8. Real numbers (as of 2026-08-25)

| | |
|---|---|
| Source files | 131 (`src/` 98 + `tests/` + `scripts/` + `mock/`) |
| Tests | 441, all passing, ~85 s (137 of them store contract + restart suites across 3 backends) |
| Ports defined | 8 (telephony, stt, llm, tts, core_data, event_bus, blob_storage, agent_directory) |
| Persisted tables | **9** + Alembic, verified on a live Postgres. Presence, the waiting pool and the live identity are deliberately **not** among them (`D78`) |
| Adapters | 9 fakes/nulls + a caching/circuit-breaking decorator; no real vendor adapter yet |
| Call states | 15, transition table self-validated (the rating is an event, not a state — `D46`) |
| Event types | 19 |
| Scenarios | 3 (in-app happy path, cold-call motor claim, fully degraded) |
| Mock core | 3 customers, 4 policies across 4 product lines, 5 products, 5 interactions, 2 claims |
| Intent taxonomy | 28 intents across 5 lines, each with a catch-all (revisit during the hackathon) |
| Generated mock data | 2,000 customers / 2,292 policies / 5,880 interactions (seeded, gitignored) |
| Diagrams | 56 (13 generated from source, 43 hand-drawn), across 11 explanation pages |

---

## 9. Reuse outside this hackathon

Deliberate design goal (`D28`, `ARCHITECTURE.md` §20): the machinery is a **generic context-aware
contact-centre AI layer**, and everything insurance-specific lives in `config/`, `prompts/`, the
`CoreDataProvider` adapter, and the fixtures. Retargeting it to a hospital line, a government service
desk, a telco or an e-commerce support desk means swapping those files — not touching `services/`.
The standing rule that makes this true: **no insurance literal may be hardcoded in `services/`**,
enforced by a lint check.

---

## 10. What comes next

**P3** — voice, IVR and intake: the menu prompts become real audio, VAD endpointing, streaming
Thai STT, and the bake-off on the RTX 3050. Then **P4** (analysis and the brief v2+, Claude vs
Typhoon measured rather than argued).

P2c is done: the system survives a restart, and what it restores is `D78`'s working set rather
than a re-read of the database on every tick. See `PLAN.md`, `explanations/P2c_persistence.md`
for the reasoning, `diagrams/11_persistence.md` for the picture, and `NEXT_SESSION.md` for the
live state.
