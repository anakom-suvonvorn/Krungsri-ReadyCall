# PROJECT_STATE

_What this project is, what exists, what doesn't, and where everything lives._
_Last updated: 2026-08-17._

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
3. **A ready agent screen** — on assignment the broker already has who / which policy / what they
   want / what to say / what to do, plus the routing rationale and a confidence signal.

Targets the brief's *"Broker เวลาจํากัด"* and *"เข้าไม่ถึงข้อมูล"* leaks, in the in-scope areas
(lead prioritisation, customer engagement, **broker productivity — "สรุปลูกค้าให้ broker ก่อนคุย"**).
Deliberately outside: underwriting, policy issuance, premium pricing, legal/tax advice, core-system changes.

**This repo is the FULL system.** The hackathon demo is a separate, later, deliberately smaller
project in `../DemoProject/`.

---

## 2. Status: PLANNING

Nothing is implemented. The current artifacts are the docs in this folder. `src/` still contains the
`uv init` placeholder (`src/fullproject/__init__.py`) and `pyproject.toml` still says `fullproject` —
both get replaced in Phase P0 (`PLAN.md`).

---

## 3. Tech stack (decided)

| Layer | Choice |
|---|---|
| Language / tooling | Python 3.11, **`uv`** for envs, deps, and running |
| API / services | FastAPI + Uvicorn, async-first; **modular monolith, multiple entrypoints** (`D2`) |
| Data | Postgres 16 (two logical stores: `core` read-only, `readycall` read-write), SQLAlchemy 2.0 + Alembic |
| State / bus | Redis 7 (presence, queues, cache) + Redis Streams as the event bus (Kafka adapter for scale) |
| Telephony | Asterisk 20 + ARI + AudioSocket by default, behind a `TelephonyProvider` port (Twilio / LiveKit / simulated adapters) |
| STT | Thonburian Whisper (`biodatlab/whisper-th-medium-combined`), re-implemented streaming-first (`D9`) |
| LLM | Claude (`claude-sonnet-5` / `claude-opus-5`) by default, behind an `LlmClient` port (Typhoon / Gemini / Ollama / rule-based) |
| Object storage | MinIO (S3 API) for recordings |
| Agent desktop | React 18 + TypeScript + Vite, WebSocket push |
| Customer side | React Native app (production) + a web **customer simulator** for dev/demo |
| Observability | OpenTelemetry traces keyed by `call_session_id`, Prometheus + Grafana + Loki |

Full adapter catalogue and library list: `INTEGRATIONS.md`.

---

## 4. Planned folder structure

```
FullProject/
├─ pyproject.toml            # package renamed fullproject → readycall in P0
├─ uv.lock  .python-version  .env.example  .gitignore
├─ README.md
├─ docs/                     # ← this documentation system
├─ config/
│  ├─ core_mapping.yaml      # bank-data field mapping (swap target, DATA_MODEL §4)
│  ├─ routing_weights.yaml   # scoring weights, tunable at runtime
│  ├─ intents.yaml           # closed intent taxonomy + required slots per intent
│  ├─ skills.yaml            # skill codes, queues, intent→skill mapping
│  └─ playbooks/             # per-intent recommended-action playbooks
├─ prompts/                  # versioned prompt files (never inline in code)
│  └─ th/ intent_classify.v1.md  summarize_intake.v1.md  suggested_opening.v1.md  ...
├─ src/readycall/
│  ├─ config.py  logging.py  errors.py
│  ├─ domain/                # pure models, no I/O
│  │  ├─ models.py           # Customer, Policy, CallSession, TranscriptTurn, CaseBrief, RoutingDecision…
│  │  ├─ enums.py            # CallState, IntentCode, ConsentScope, FinalizeReason…
│  │  └─ events.py           # event schemas (versioned)
│  ├─ ports/                 # Protocols only — THE seams
│  │  ├─ telephony.py  stt.py  llm.py  tts.py  core_data.py
│  │  └─ event_bus.py  blob_storage.py  agent_directory.py  notifier.py
│  ├─ adapters/
│  │  ├─ telephony/  asterisk_ari.py  twilio.py  livekit.py  simulated.py
│  │  ├─ stt/        thonburian_hf.py  faster_whisper.py  cloud.py  scripted.py
│  │  ├─ llm/        anthropic.py  typhoon.py  gemini.py  ollama.py  rulebased.py
│  │  ├─ tts/        prerecorded.py  azure.py  null.py
│  │  ├─ core_data/  mock_postgres.py  fixtures.py  http_api.py  sql_passthrough.py
│  │  │               caching.py  null.py  mapping.py   # YAML-driven field mapper
│  │  ├─ event_bus/  redis_streams.py  kafka.py  memory.py
│  │  └─ storage/    minio.py  s3.py  localfs.py
│  ├─ services/
│  │  ├─ call_orchestrator/  machine.py  handlers.py     # single writer of call state
│  │  ├─ context/            assembler.py  snapshot.py   # Customer360 + provenance
│  │  ├─ intake/             base.py  passive.py  guided.py  conversational.py  slots.py
│  │  ├─ transcription/      stream_manager.py  vad.py  turns.py  worker.py
│  │  ├─ analysis/           intent.py  entities.py  summary.py  brief.py
│  │  │                      nba.py  opening.py  confidence.py  pii.py
│  │  ├─ routing/            engine.py  scoring.py  queues.py  presence.py
│  │  ├─ consent/            service.py  policy.py  retention.py
│  │  ├─ wrapup/             service.py
│  │  └─ metrics/            rollups.py
│  ├─ media/                 # the media gateway (audio I/O, resampling, framing, recording)
│  │  ├─ gateway.py  audiosocket.py  ws_media.py  resample.py  recorder.py
│  ├─ api/
│  │  ├─ app.py  deps.py  security.py
│  │  ├─ routers/  mobile.py  agent.py  telephony_webhooks.py  admin.py  health.py
│  │  ├─ ws/       agent_ws.py  customer_ws.py
│  │  └─ schemas/  # request/response DTOs (never leak ORM models)
│  ├─ db/
│  │  ├─ session.py  base.py
│  │  ├─ models/    readycall/*.py        # our writable tables
│  │  └─ migrations/                      # alembic
│  ├─ workers/     orchestrator_worker.py  analysis_worker.py  stt_worker.py  jobs.py
│  ├─ observability/  tracing.py  metrics.py  timing.py
│  └─ entrypoints/  api.py  worker.py  media.py  stt.py    # the runnable processes
├─ mock/bank_core/           # the simulated read-only bank data
│  ├─ schema.sql  generate.py  personas.yaml  scenarios/*.yaml
├─ apps/
│  ├─ agent_desktop/         # React + Vite
│  └─ customer_sim/          # web page that fakes the mobile app (tap Contact, speak, hold)
├─ infra/
│  ├─ docker-compose.yml  asterisk/  grafana/  k8s/
├─ scripts/                  # seed_db, run_scenario, convert_ct2_model, eval_golden_set…
└─ tests/
   ├─ unit/  integration/  contracts/   # contracts/ = the port suites every adapter must pass
   ├─ scenarios/                        # end-to-end scripted calls, no telephony
   └─ golden/                           # labelled AI evaluation set
```

Rationale for the shape: `domain` has no I/O, `ports` has no implementations, `adapters` has no
business rules, `services` imports only `domain` + `ports`. Any file that breaks that is a bug.

---

## 5. Feature status

Nothing is built. Legend: ☐ planned · ◐ in progress · ☑ done.

**Phase P0 — foundations**
☐ package rename + layout · ☐ config/settings · ☐ structured logging + tracing · ☐ `readycall` schema
+ migrations · ☐ mock `core` schema + generator + personas/scenarios · ☐ all ports defined ·
☐ fake/null adapters · ☐ contract test harness · ☐ scenario runner skeleton · ☐ CI

**P1 — context-aware calling** ☐ intent API · ☐ session auth · ☐ app context events · ☐ Customer360
assembler + snapshot + provenance · ☐ caching/circuit breaker · ☐ agent screen v1 (context-only brief)

**P2 — routing & agent delivery** ☐ queues · ☐ presence · ☐ scoring engine + persisted rationale ·
☐ assignment · ☐ agent WebSocket · ☐ agent desktop shell

**P3 — intake v1 (passive)** ☐ media gateway · ☐ recording + encryption · ☐ VAD endpointing ·
☐ streaming Thonburian STT worker · ☐ incremental transcript turns · ☐ consent gate

**P4 — analysis & case brief** ☐ intent taxonomy + classifier · ☐ entity extraction · ☐ rolling
summary · ☐ brief versioning · ☐ confidence calibration · ☐ NBA playbooks · ☐ suggested opening ·
☐ golden-set evaluation

**P5 — real telephony** ☐ Asterisk + ARI adapter · ☐ WebRTC path · ☐ PSTN/ANI identification ·
☐ media fork · ☐ bridge/transfer · ☐ Twilio adapter

**P6 — wrap-up & metrics** ☐ post-call summary · ☐ dispositions · ☐ follow-ups · ☐ feedback loop ·
☐ metrics rollups + dashboard

**P7 — PDPA hardening** ☐ consent flows · ☐ PII masking · ☐ retention/erasure · ☐ RBAC · ☐ audit log

**P8 — future** ☐ guided intake (TTS slot-filling) · ☐ conversational AI intake · ☐ live in-call
assist · ☐ proactive outbound · ☐ product recommendation on top of the same context layer

---

## 6. How to run (once P0 exists)

```bash
uv sync
docker compose -f infra/docker-compose.yml up -d      # postgres, redis, minio, asterisk
uv run alembic upgrade head
uv run python scripts/seed_mock_core.py --seed 42
uv run python -m readycall.entrypoints.api            # API + agent WS
uv run python -m readycall.entrypoints.worker         # orchestrator + analysis consumers
uv run python scripts/run_scenario.py scenarios/pattheera_ipd.yaml   # full call, no phone
```

---

## 7. Known constraints & open risks

- **GPU.** Thonburian medium wants a GPU for the latency budget. CPU-only → distilled model, higher
  WER, longer turns. Decide the target machine before P3.
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

## 8. Relationship to the demo project

`../DemoProject/` will implement a **slice**: most likely the customer simulator + simulated
telephony + scripted-or-live STT + one persona's full journey + the agent screen. It gets its own
docs and its own git history. Design decisions still belong here; demo-only shortcuts belong there.
