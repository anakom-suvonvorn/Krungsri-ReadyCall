# PROJECT_STATE

_What this project is, what exists, what doesn't, and where everything lives._
_Last updated: 2026-08-18._

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
| Telephony | Asterisk 20 + ARI + AudioSocket by default, behind a `TelephonyProvider` port (Twilio / LiveKit / simulated adapters). Demo trick: a softphone on a real mobile pointed at the laptop over local Wi-Fi = a genuine VoIP call with no internet |
| STT | Thonburian Whisper (`biodatlab/whisper-th-medium-combined`) default, re-implemented streaming-first (`D9`); CTranslate2 build and **Typhoon ASR** benchmarked against it (`D30`) |
| TTS | Pre-rendered prompt clips built from `voice_prompts.yaml` (`D24`); streaming only for future conversational intake |
| LLM | `AnthropicAdapter` + `OpenAiCompatibleAdapter` both implemented (`D29`) — the latter covers Typhoon API, OpenAI, vLLM and Ollama by base URL. No LLM framework (`D31`) |
| Object storage | MinIO (S3 API) for recordings |
| Agent desktop | React 18 + TypeScript + Vite, WebSocket push, **in the browser** — no install on the agent's machine |
| Customer side | Responsive **web customer simulator** with a demo persona picker, calling the same public `/v1/…` API the real Krungsri app would |
| DB inspection | `pgweb` in compose + our own **Call Explorer** admin page |
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
├─ config/                   # ← the entire insurance-specific "domain pack" (D28)
│  ├─ core_mapping.yaml      # bank-data field mapping (swap target, DATA_MODEL §4)
│  ├─ matching_weights.yaml  # fit + urgency weights, tunable at runtime
│  ├─ intents.yaml           # closed intent taxonomy + required slots per intent
│  ├─ skills.yaml            # skill codes, queues, intent→skill mapping
│  ├─ queue_hours.yaml       # opening hours + holidays per queue
│  ├─ dids.yaml              # printed phone numbers → product line + queue (D19)
│  ├─ voice_prompts.yaml     # every spoken line, as Thai text (D24)
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
│  ├─ services/              # NO insurance-specific literals may live here (D28)
│  │  ├─ call_orchestrator/  machine.py  handlers.py     # single writer of call state
│  │  ├─ identity/           resolver.py  assurance.py   # L0–L3 ladder (D20)
│  │  ├─ context/            assembler.py  snapshot.py   # Customer360 + provenance
│  │  ├─ ivr/                flow.py  prompts.py  dtmf.py  rating.py
│  │  ├─ intake/             base.py  passive.py  guided.py  conversational.py  slots.py
│  │  ├─ transcription/      stream_manager.py  vad.py  turns.py  worker.py
│  │  ├─ analysis/           intent.py  entities.py  summary.py  brief.py
│  │  │                      nba.py  opening.py  confidence.py  pii.py  progress.py
│  │  ├─ matching/           engine.py  fit.py  urgency.py  solver.py
│  │  │                      queues.py  presence.py  defer.py
│  │  ├─ consent/            service.py  policy.py  retention.py
│  │  ├─ wrapup/             service.py  callbacks.py
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

**P1 — context-aware calling** ☐ intent API · ☐ session auth · ☐ app context events · ☐ identity
resolver + assurance ladder · ☐ `dids.yaml` · ☐ Customer360 assembler + snapshot + provenance ·
☐ caching/circuit breaker · ☐ customer simulator + demo login · ☐ agent screen v1 (context-only brief)

**P2 — matching & agent delivery** ☐ queues + hours · ☐ agent state model (auto × manual) ·
☐ presence heartbeat · ☐ fit + urgency scoring · ☐ Hungarian solver · ☐ anti-hot-spot checks ·
☐ persisted rationale · ☐ matching simulator · ☐ assignment/re-match · ☐ agent WebSocket ·
☐ agent desktop shell

**P3 — voice, IVR & intake v1** ☐ voice-prompt build pipeline + prompt studio · ☐ IVR flow (menu,
identify, consent, press-1/2, rating) · ☐ media gateway (per-leg fork) · ☐ recording + encryption ·
☐ VAD endpointing · ☐ streaming STT worker · ☐ **STT bake-off on the 3050** · ☐ incremental turns ·
☐ ring-time grace

**P4 — analysis & case brief** ☐ intent taxonomy + classifier · ☐ entity extraction · ☐ rolling
summary · ☐ brief versioning · ☐ confidence calibration · ☐ NBA playbooks · ☐ suggested opening ·
☐ Anthropic adapter · ☐ OpenAI-compatible adapter · ☐ golden-set evaluation · ☐ provider comparison

**P5 — real telephony** ☐ Asterisk + ARI adapter · ☐ WebRTC path · ☐ PSTN/ANI identification ·
☐ product-line DIDs · ☐ media fork · ☐ bridge/transfer · ☐ softphone demo path · ☐ Twilio adapter

**P6 — live transcription, wrap-up & metrics** ☐ both-leg live transcription · ☐ call-progress
estimation · ☐ deferral enabled · ☐ post-call summary · ☐ dispositions · ☐ follow-ups · ☐ ratings
(customer + agent) · ☐ after-hours voicemail → briefed callbacks · ☐ Call Explorer ·
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

## 8. Reuse outside this hackathon

Deliberate design goal (`D28`, `ARCHITECTURE.md` §20): the machinery is a **generic context-aware
contact-centre AI layer**, and everything insurance-specific lives in `config/`, `prompts/`, the
`CoreDataProvider` adapter, and the fixtures. Retargeting it to a hospital line, a government service
desk, a telco or an e-commerce support desk means swapping those files — not touching `services/`.
The standing rule that makes this true: **no insurance literal may be hardcoded in `services/`**,
enforced by a lint check.

---

## 9. Relationship to the demo project

`../DemoProject/` will implement a **slice**: most likely the customer simulator + simulated
telephony + scripted-or-live STT + one persona's full journey + the agent screen. It gets its own
docs and its own git history. Design decisions still belong here; demo-only shortcuts belong there.
