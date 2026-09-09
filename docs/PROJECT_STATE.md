# PROJECT_STATE

_What this project is, what exists, what doesn't, and where everything lives._
_Last updated: 2026-09-09._

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

## 2. Status: **P0 · P1 · P1b · P2a · P2b · P2c · P3 complete. P4 PARTLY DONE.**

⚠️ **The phase numbering stopped describing the work on 2026-09-07.** The hackathon
orientation redirected the project (`D115`), and everything since has been the **broker's
own job** rather than a phase: the broker domain (`D117`), playbooks in config (`D118`),
the LLM seam (`D119`), the customer's paired screen and its tool rail (`D120`–`D122`), the
app's new-business path (`D123`), handing a call to the insurer (`D124`), compare-and-best-fit
end to end (`D125`–`D127`), and a message the broker types themselves (`D128`). Only three
of those are P4 items; the rest are in no phase, because the plan was written before the
ideas existed. **Read `NEXT_SESSION.md` for what is actually done** rather than inferring it
from a phase letter.

The one-line version: a caller reaches the right broker queue, is transcribed while they
wait, and the broker answers with the brief already on screen — and can then **compare the
market against what this customer holds**, push that table or a single plan or their own
sentence to the customer's phone, and **hand the call to the insurer** with the carrier and
the reason recorded.

The spine runs. A full call lifecycle - arrival, IVR, consent, queue, intake, matching, the offer
handshake, the live call, wrap-up, rating, closed - executes end to end on fake adapters with no
telephony, no GPU, no database and no API key. So does everything the broker does on top of it:
the plan catalogue, the ranking, the pushes and the handoff all run on the fixture adapter with
nothing configured.

As of P3 the identity ladder, **the IVR itself**, **the intake offer**, the context assembler,
the brief builder, the public API, the matching engine, agent presence, the offer handshake and
the React workstation are all real services doing real work - only the *edges* (phone, speech,
AI, the bank's data, the agent roster) are still fakes. An agent signs in at `/workstation`, a
caller keys their way through the real menu to the right queue, is offered
the recording and either takes it or does not, the desk rings, the brief is already there, and
the disclosure gate moves when the agent attests. **What they said while waiting is on that
screen** (`D106`), and if they consented, **their audio is in object storage encrypted**
(`D110`) with a key ref and a retention date. If they declined, it is nowhere.

Verified on 2026-09-09: **913 tests** — 901 pass + 12 skipped with Postgres and MinIO both
up (the 12 are foreign-key cases the in-memory backend cannot have, and the `ml`-extra ones).
Without those containers the count of skips rises and nothing fails.
`ruff check` and `ruff format --check` clean over **225** files, `mypy --strict`
clean over **157** source files, 69/69 diagrams current, the prompt pack fresh, and all three
scenarios replay byte-identically. The database suites ran against a **live Postgres** on
2026-09-02, and a restart was verified outside pytest with two real uvicorn processes.

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
| STT | **Typhoon ASR** (`scb10x/typhoon-asr-realtime`, a NeMo FastConformer transducer), re-implemented streaming-first (`D9`). Chosen on measurements against Thonburian Whisper fp16 and its CTranslate2 `int8_float16` build — it is the only one that meets the 1.5 s budget (`D30` closed by `D104`). The CT2 build is the documented fallback (`D103`) |
| TTS | Pre-rendered prompt clips built from `voice_prompts.yaml` (`D24`); streaming only for future conversational intake |
| LLM | **Built** (`D119`): `build_llm` is the factory, with `AnthropicLlm` (structured output via forced tool use), `OpenAiCompatibleLlm` (one adapter for Typhoon-hosted / OpenAI / vLLM / Ollama / LM Studio by base URL alone) and `RuleBasedLlm` as the shipped default and degradation rung. Prompts are versioned files in `prompts/th/`. Measured live: `claude-sonnet-5` summarises an intake in **4.5 s for $0.0085**. No LLM framework (`D31`) |
| Object storage | MinIO (S3 API) for recordings |
| Agent workstation | React 18 + TypeScript + Vite. **A full contact-centre workstation in one browser tab — the softphone is in it** (SIP.js over WSS to Asterisk, WebRTC/Opus through the agent's headset), plus the brief, the queue and status control. No desk phone, no install (`D32`) |
| Customer side | **Two static pages, no build step** (`D47`, `D120`). `/sim` is the customer simulator with a persona picker; `/assist/<token>` is the **paired screen** a broker pushes onto during a call — comparisons, forms, document requests. Both call the same public `/v1/…` API the real Krungsri app would |
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
│  ├─ diagrams/*           # 69 diagrams + 13 explanation pages; a fifth generated from source
│  └─ reading/*            # readable twins: the keypad, the restart, the workstation, the offer,
│                          # the audio path, and THE RECORDING (D110-D114, the newest)
│                          #   the audio path (which also carries the verification commands)
├─ config/*                  # ← the entire insurance-specific "domain pack" (D28)
│  ├─ core_mapping.yaml      # bank-data field mapping (swap target, DATA_MODEL §4)
│  ├─ matching_weights.yaml* # fit + urgency weights, tunable at runtime
│  ├─ intents.yaml*          # closed intent taxonomy + required slots per intent
│  ├─ skills.yaml*           # skill codes, queues, intent→skill mapping
│  ├─ queue_hours.yaml*      # opening hours + holidays per queue
│  ├─ dids.yaml*             # printed phone numbers → product line + queue (D19)
│  ├─ menus.yaml*            # the IVR tree. ALSO served to the app (D48) - one menu, two
│  │                         #   surfaces, but NOT the same list (D122): options declare
│  │                         #   `contexts: [plan, general]` so the app can filter to the
│  │                         #   situation the customer is in. The IVR is never filtered.
│  ├─ voice_prompts.yaml*    # every spoken line, Thai text + slots + flow roles (D24, D80)
│  ├─ challenges.yaml*       # how an agent may verify a caller. Served, never hardcoded twice (D72)
│  ├─ stt_vocabulary.yaml*   # jargon the ASR is nudged toward. READ B14 BEFORE EDITING
│  ├─ demo_personas.yaml*    # DEMO: ids only, everything displayed is read live (D47)
│  ├─ voice_prompts.yaml     # every spoken line, as Thai text (D24)
│  ├─ assist_tools.yaml*     # the tool rail: what a broker may push to the customer's
│  │                         #   screen (D120, D121, D127, D128). 4 groups, 14 tools.
│  │                         #   `personal` is the
│  │                         #   TIER GATE and is read from here, never from a request -
│  │                         #   a client able to declare its own push non-personal would
│  │                         #   be the gate's own bypass. Guarded both ways at startup,
│  │                         #   including: prefill on a non-personal tool refuses to boot.
│  ├─ comparison.yaml*       # which figures a comparison ranks on, per line, and which
│  │                         #   way is better (D126). The OTHER half of D125's split: the
│  │                         #   plan catalogue is live data behind CoreDataProvider, and
│  │                         #   this is our own knowledge about how to compare it. Test of
│  │                         #   the distinction - if the answer changes because an insurer
│  │                         #   launched a plan it is data; if it changes because we
│  │                         #   decided differently it is config. `better:` refuses to
│  │                         #   boot on anything but higher/lower: wrong is SILENT.
│  ├─ insurers.yaml*         # who a call may be handed TO, and why (D124). Real carriers
  │                         #   from MARKET_FACTS 8. A MENU, not a whitelist: the carrier
  │                         #   on the customer's own policy is offered whether or not it
  │                         #   is in here, because the real extract arrives carrying
  │                         #   carriers nobody typed. Guarded one way only, and that is
  │                         #   deliberate - see the file's own header.
  └─ playbooks.yaml*        # per-intent recommended actions (D118, closing Q19). The
│                            #   chain is intent -> playbook name -> ordered (Thai text,
│                            #   required assurance) -> filtered by level -> verify-identity
│                            #   inserted at 0 below L2 (D56). Guarded BOTH ways at startup:
│                            #   a missing playbook and an unreachable one both refuse boot.
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
│  │  ├─ stt/*       thonburian_hf.py*  faster_whisper.py*  scripted.py*  cloud.py
│  │  │               typhoon_asr.py*   # D104. The shipped engine
│  │  │               worker.py*  wire.py*   # D112. The engine in its own process
│  │  ├─ vad/*       energy.py*  silero.py*   # the 9th port (D96)
│  │  │               # stt/: + typhoon_asr.py* - NeMo, not Whisper (D99)
│  │  ├─ llm/        anthropic.py  openai_compatible.py  gemini.py  rulebased.py
│  │  ├─ tts/        prerendered.py  azure.py  null.py   # build-time render, not live
│  │  ├─ core_data/  mock_postgres.py  fixtures.py  http_api.py  sql_passthrough.py
│  │  │               caching.py  null.py  mapping.py   # YAML-driven field mapper
│  │  ├─ event_bus/  redis_streams.py  kafka.py  memory.py
│  │  ├─ blob_storage/*      # D110. Named for its port, which the diagram generator assumes
│  │  │  ├─ encrypting.py*   #   AES-256-GCM over ANY backend. The only crypto in the repo
│  │  │  ├─ memory.py*  localfs.py*  s3.py*   # s3.py covers MinIO too; needs the `s3` extra
│  │  │  └─ factory.py*      #   build_blob_storage - the ONLY place a store is constructed
│  │  └─ keyring/    local.py*        # D110. The master key. P7 replaces it with a vault
│  ├─ services/*             # NO insurance-specific literals may live here (D28)
│  │  ├─ call_orchestrator/  machine.py  handlers.py     # single writer of call state
│  │  ├─ identity/           resolver.py  assurance.py   # L0–L3 ladder (D20)
│  │  ├─ context/            assembler.py  snapshot.py   # Customer360 + provenance
│  │  ├─ ivr/                flow.py  prompts.py  dtmf.py  rating.py
│  │  ├─ intake/             base.py  passive.py  guided.py  conversational.py  slots.py
│  │  ├─ transcription/*     # P3 step 4b. Audio -> TranscriptTurn (D96)
│  │  │  ├─ delivery.py*     #   D106. Held while nobody owns the call, flushed on accept
│  │  │  ├─ store.py*        #   D114. The FOURTH subscriber: writes transcript_turns
│  │  │  ├─ endpointer.py*   #   WHERE an utterance starts/stops. No model, no I/O (D9)
│  │  │  ├─ stream.py*       #   one leg: ring buffer, ordered turns, the B14 guards
│  │  │  └─ service.py*      #   the driver. Feeds IntakeService.on_turn at last (D88)
│  │  ├─ analysis/           intent.py  entities.py  summary.py  brief.py
│  │  │                      nba.py  opening.py  confidence.py  pii.py  progress.py
│  │  ├─ matching/*          engine.py  scoring.py  solver.py  weights.py
│  │  ├─ agents/*            presence.py  assignment.py  dispatch.py   # P2b
│  │  ├─ queues/*            hours.py                                  # P2b
│  │  ├─ capture/*           keypad.py            # untyped DTMF capture (D44)
│  │  ├─ consent/            service.py  policy.py  retention.py
│  │  ├─ ivr/*               # P3. The keypad walk - THE thing that routes the call (D37)
│  │  │  ├─ machine.py*      #   the walk, with NO I/O. A timeout is a method call (B7)
│  │  │  ├─ presentation.py* #   a menu as THIS caller hears it, + the mapping back (D80/D81)
│  │  │  ├─ personalise.py*  #   which options come first, and the evidence for each (D37)
│  │  │  └─ service.py*      #   the async driver. Plays lines, holds no rules
│  │  ├─ intake/*            # P3 step 4a. Everything BELOW "the queue is known" (D88)
│  │  │  ├─ hold.py*         #   the offer, re-offer, recording. NO I/O either (B7)
│  │  │  ├─ strategy.py*     #   the IntakeStrategy seam. Turns in, not frames in (D10/D88)
│  │  │  ├─ passive.py*      #   PassiveRecordIntake: listen, keep every word, say nothing
│  │  │  └─ service.py*      #   the driver + the LIVE holds an accept has to end (D21)
│  │  ├─ recording/*         # D110. The consented leg's audio -> one encrypted object
│  │  │  ├─ service.py*      #   a SINK on the gateway. Seals on accept, uploads on the sweep
│  │  │  └─ store.py*        #   audio_recordings. The only store with no memory projection
│  │  ├─ wrapup/             service.py  callbacks.py
│  │  └─ metrics/            rollups.py
│  ├─ media/*                # the media gateway (D96)
│  │  ├─ audio.py*           #   G.711 both laws, resample, mono. PURE PYTHON on purpose
│  │  ├─ gateway.py*         #   per-LEG fan-out, so speaker id is structural (D26)
│  │  ├─ sources.py*         #   replay a WAV as if it were a phone line
│  │  └─ audiosocket.py  ws_media.py   # P5. NOT THERE
│  │                        # (the recorder is services/recording/, not here - D110)
│  ├─ entrypoints/*          # D2. One codebase, several processes
│  │  ├─ api.py*             #   the HTTP server and both front-ends
│  │  └─ stt.py*             #   D112. The STT worker - it exists to be KILLABLE
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
│  ├─ customer_sim/*         # one static HTML page, no build step (D47)
│  └─ customer_assist/*      # D120. The customer's PAIRED screen, opened from a link the
│                            #   broker sends. One static page, polls 1s. Separate from the
│                            #   sim on purpose: a stranger opens this from a phone.
├─ infra/*
│  ├─ docker-compose.yml  asterisk/  grafana/  k8s/
├─ scripts/*                 # audit_docs, gen_diagrams, render_diagrams, run_matching, run_scenario,
│                            #   bake_off (D30, --dump prints the text behind a CER),
│                            #   score_endpointer (B20 - scores the DETECTOR, which a CER
│                            #   cannot see), show_audio_path, make_test_audio,
│                            #   prepare_dataset (D97), convert_ct2 (B17)
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
`matching_weights.yaml` with startup validation · ☑ hard filters in two groups —
**capability** (skill, **graded language**) and **availability** (`offline`, `not_ready`,
`busy`, `at_capacity`), the second added by `B25` after the matcher was found ringing
agents who had never pressed ready · ☑ fit + urgency scoring with full breakdowns ·
☑ **Hungarian solver, ours** (`D49`) + greedy for comparison · ☑ guard rails: wait ceiling,
anti-hot-spot, guarded deferral · ☑ persisted rationale on every decision incl.
non-assignments · ☑ unplaced callers say **which of four** reasons applies — contention,
staffing, everyone-declined, roster gap (`D50`, `D108`, `B4`) · ☑ **and the
everyone-declined one is now acted on** (`D113`): the exclusions are cleared, the caller
goes round again, and the offer card says which round it is and whether this agent is the
last one who could take it · ☑ matching simulator with
`--compare` · ☑ **a floor-level stress suite** driving the real API with several agents and
callers at once, asserting invariants (`tests/integration/test_floor_under_load.py`)

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
☑ SQLAlchemy 2.0 async + Alembic, URL from `Settings` (`D75`) · ☑ **10 tables**:
`call_sessions`, `call_state_transitions`, `agent_state_log`, `assignments`,
`identity_attestations`, `keypad_captures`, `matching_decisions`, `context_snapshots`,
`call_wrapups`, plus `audio_recordings` (`D110`) and `transcript_turns` (`D114`) · ☑ Postgres stores returning **domain models** (`D77`) · ☑ **one contract
suite across three backends**, SQLite with foreign keys enforced · ☑ **write-through with
an in-memory projection** (`D78`): services keep their working set, write durably, and
restore at startup · ☑ presence, the waiting pool and the live identity are **derived, not
stored** (`D76`, `D78`) · ☑ `Container` wired to `STORAGE_BACKEND` · ☑ `D44`'s inverted
default at rest — unnamed digits are never persisted · ☑ **a restart proved by ending a
process**, twice: under `TestClient` and with two real uvicorn processes against live
Postgres · ☑ Alembic no longer churns foreign keys, and the suite has its own database
(`D79`)

**P3 — voice, IVR & intake v1** (steps 1-3 and step 4a done; the audio is what remains)
☑ `config/voice_prompts.yaml` — **28 prompts**, 15 flow roles, declared slots · ☑ the guard that
every referenced prompt id resolves, **in both directions**, as a startup gate and a test · ☑
`scripts/build_prompts.py` — hash-cached by (text, voice, engine), deduped to **59 clips**,
committed manifest asserted fresh (`D24`) · ☑ **`services/ivr/`**: greeting + notice, menu-first
routing, one reserved key, unlimited wrong presses (`D82`), silence bounded, personalised ordering
with its evidence (`D37`) · ☑ a menu is composed, not one clip (`D80`) · ☑ `menu_path` is canonical
whatever was pressed (`D81`) · ☑ no operator key (`D86`) · ☑ **`services/intake/`**:
the press-1/press-2 offer, the re-offer driven by the sweep, consent granted and refusals recorded,
the `IntakeStrategy` seam with `PassiveRecordIntake`, and **ring-time grace** — the accept endpoint
finalises a live intake as partial (`D21`, `D88`) · ☑ **no queue position spoken at all** (`D91`,
reversing `D89`) — there is no line to have a position in ·
☑ the scenario runner and the demo endpoint hand both walks over — no faked IVR or intake anywhere
☐ prompt studio · ☐ real TTS voice (the null engine renders no audio) · ☐ post-call rating keypress ·
☑ **media gateway (per-leg fork)** · ☑ **VAD endpointing** (`D9`'s constants, in a machine with
no model in it) · ☑ **the VAD port + two adapters** · ☑ **the STT worker seam + three real engines**
(Typhoon NeMo, faster-whisper CT2, Thonburian HF) · ☑ **the bake-off harness**, with its own instruments fixed
twice (`B14`) · ☑ **`IntakeService.on_turn` is finally fed**, and both recording timeouts are driven
by the sweep (`B7`) · ☑ **the bake-off TABLE, and the engine chosen on it** — 20 real Thai
calls, `D30` closed by `D104` · ☑ **a recording is actually opened when the caller consents**
and a WAV can be played down it with no telephony (`D107`) · ☑ **the live transcript on the
workstation** — held while nobody owns the call, flushed on accept (`D105`, `D106`) ·
☐ recording + encryption (needs P7's keys) · ☐ incremental turns persisted
*(The identify step is not pending — it was designed and removed, `D84`.)*

**P4 — analysis & case brief** (partly done, 2026-09-07)
☑ **`build_llm` + `AnthropicLlm` + `OpenAiCompatibleLlm`** (`D119`) — the factory that did not
exist for six phases while `Settings` accepted both names · ☑ **versioned prompt files** in
`prompts/th/`, refusing a missing slot AND an undeclared one · ☑ **the intake summary**,
fire-and-forget from Accept, measured at 4.5 s on `claude-sonnet-5` (⚠️ **the $0.0085 figure is withdrawn** until the cost table in `adapters/llm/anthropic.py` is corrected — it carries pre-2026 rates, so the real number is ≈ $0.0057; do not quote it) · ☑ **NBA
playbooks in config** (`D118`, closing `Q19`) · ☑ **the broker intent taxonomy** (`D117`,
closing `Q7`) · ☐ intent classifier wired to the blend · ☐ entity extraction (**blocked on
`Q24`**) · ☐ rolling summary · ☐ brief versioning · ☐ confidence calibration ·
☐ suggested opening · ☐ golden-set evaluation · ☐ provider comparison table

**Not in any phase — the customer's paired screen** (`D120`, 2026-09-07)
☑ pairing a call to a screen, three entry paths · ☑ the guest/verified tier gate ·
☑ push comparison / info / navigate · ☑ **push a form and get the answer back**, end to end ·
☑ `apps/customer_assist/`, one static page · ☑ **the tool rail on the workstation**, with
the gate read per TOOL from `config/assist_tools.yaml` rather than per kind (`D121`) ·
☑ **the app places a real call and becomes the paired screen** (`D122`, `B36`) ·
☑ **contact menus filtered per context**, and the *"something else"* branch asking which
kind of cover first so new business is reachable at all (`D122`, `D123`) ·
☑ **compare & best-fit** (`D125`, `D126`) — the catalogue behind `CoreDataProvider`,
ranked on facts with weights in `config/comparison.yaml`, a panel on the workstation, and
a table **composed on the server** and pushed to the customer's phone. The customer's own
column is gated on the screen's tier, not the tool's `personal` flag ·
☑ **handing the call to the insurer** (`D124`) — `insurers.yaml` with the real carriers,
the customer's own carrier offered first and never missing, closed reasons, the wrap-up
prefilled, a `call.handed_off` event, and no new call state ·
☐ sending the link (P5's `NotifierPort`) · ☐ signature, OCR, document upload ·
☐ merging with `customer_sim` · ☐ **internal transfer** (`D63`'s consulted handover) —
a labelled stub on screen, and a scope call awaiting the user (`D124`) ·
☐ **agent → customer files and images** (`D128`) — the text half is built

**Not in any phase — the broker's own job** (`D123`–`D129`, 2026-09-08/09)
☑ **the app can ask about cover the customer does not hold** — the keypad's own step 1 in
front of the *"something else"* branch (`D123`) · ☑ **handing the call to the insurer** as an
action, with `config/insurers.yaml`, closed reasons and the wrap-up prefilled (`D124`) ·
☑ **the plan catalogue behind `CoreDataProvider`**, not in `config/` — `list_products` on the
port and every adapter, `insurer` + typed `Coverage` rows on `Product`, 19 plans across 6 real
carriers (`D125`) · ☑ **compare & best-fit ranked on arithmetic** over weights in
`config/comparison.yaml`, with the model reserved for the reason sentence alone (`D126`) ·
☑ **the plan information panel** — rail preview, dialog, line selector, ranking, catalogue
plan by plan, push one plan (`D127`) · ☑ **a message the broker types themselves**, labelled
as human-written on the customer's screen (`D128`) · ☑ one-of-many as pills (`D129`) ·
☑ **the AI summary is on the offer card BEFORE Accept** (`D131`) — a fast preview during
the offer window, the careful pass over the whole transcript after · ☑ **`scripts/compare_llm.py`**,
the P4 comparison table, with both cost tables corrected against live pricing (`D130`) ·
☐ the model writing the reason sentence — `Candidate.reason_th` is the seam ·
☐ gap analysis across a customer's whole portfolio rather than one policy per line

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

## 8. Real numbers (as of 2026-09-06)

| | |
|---|---|
| Source files | 203 Python files (`src/` 143 + `tests/` + `scripts/` + `mock/`) |
| Tests | **812**, all passing, ~123 s with every backend up (**15 on the durable transcript**, `D114` - including one that publishes through a store which raises and asserts the agent's screen still got the turn; **6 on `Q31`'s circle-back and the offer card's two new sentences**, `D113`; **13 on the loss reporting and the killable STT worker**, `D111`/`D112` - the timeout one asserts the child PROCESS is gone, not that an exception was raised; **54 on the recording and the blob port**, `D110`: 15 on the service, 27 on the store contract across memory/localfs/MinIO, 12 on `audio_recordings` across memory/SQLite/Postgres; **20 on agent availability and the wait on screen**, `B25`/`B26`; 149 store contract + restart across 3 backends; 56 on the prompt pack and the IVR; 40 on the hold and the intake seam; 41 on matching, 12 of them on the wait ceiling under contention; **61 on the audio path**; **21 on the transcript reaching the screen**) |
| Ports defined | **10** (telephony, stt, **vad**, llm, tts, core_data, event_bus, blob_storage, agent_directory, **keyring**) - `vad` added by `D96`, `keyring` by `D110`. Plus three **capability** protocols, one adapter each: `BatchSttEngine` (`D101`), `ReplayableSttEngine` (`D107`) and `ProvisionableBlobStorage` (`D110`) |
| Process entrypoints | **2** of `D2`'s four: `api.py` and `stt.py` (`D112`). `worker.py` and `media.py` are still one process with the API |
| Persisted tables | **11** + Alembic, verified on a live Postgres - `audio_recordings` (`D110`) and `transcript_turns` (`D114`) added 2026-09-06. Presence, the waiting pool and the live identity are deliberately **not** among them (`D78`) |
| Adapters | 9 fakes/nulls + two decorators (`CachingCoreDataProvider`, `EncryptingBlobStorage`), **plus seven real ones**: `SileroVad`, `EnergyVad`, `TyphoonAsrEngine`, `FasterWhisperEngine`, `ThonburianHfEngine`, `LocalFsBlobStorage`, `S3BlobStorage` |
| Spoken lines | 28 prompts + 15 flow roles -> **59 distinct clips** after dedupe (`D80`); rendered by the null engine, so a manifest rather than audio |
| Call states | 15, transition table self-validated (the rating is an event, not a state — `D46`) |
| Event types | **20** — `call.handed_off` added by `D124`, its own event rather than a flavour of `CallEnded`: *"the media stopped"* and *"this went to Muang Thai because they have to rule on the coverage"* answer different questions, and counting handoffs per carrier is one a broker actually has |
| Scenarios | 3 (in-app happy path, cold-call motor claim, fully degraded) |
| Mock core | 3 customers, **6 policies across 5 carriers** and a **19-plan catalogue across 6 carriers** (`D125`) — the policies are one customer's real broker portfolio (employer group health, a second health policy, and motor, from three insurers); the catalogue is what they are compared against, and it carries a **withdrawn** plan so `active_only` has something to exclude (`B30`). ⚠️ The affiliated carrier is deliberately not the best row, and a test asserts it |
| Intent taxonomy | **33 intents** across 5 lines, each with a catch-all — **broker-shaped** since `D117`: advice/compare and renewal are first-class, claims are handoffs (`handoff_to_insurer`), and `health.ipd.preauth` is gone because pre-authorisation is the insurer's decision |
| Skills / queues | **11 / 11** (`D117`). Advice and service per line, plus `renewal.retention`, `claims.assist`, `general.service`, `general.escalation`. Every skill held by 2+ agents, enforced at startup (`D22`) |
| Playbooks | **24**, in `config/playbooks.yaml` (`D118`) |
| Tool rail | **14 tools in 4 groups**, in `config/assist_tools.yaml` (`D121`, `D127`, `D128`). 5 personal, 2 visibly stubbed, one free-text catch-all *from* the customer (`D122`) and one **to** them — `note.agent_message`, the only tool whose content the system does not compose, so the composer shows the screen's tier while the broker types (`D128`) |
| Generated mock data | 2,000 customers / 2,292 policies / 5,880 interactions (seeded, gitignored) |
| GPU, measured (`D95`) | RTX 3050 Laptop, sm_86, **4.00 GiB total / ~3.2 GiB free**, torch 2.11+cu128 |
| STT latency, measured (`B14`) | faster-whisper `tiny` int8_float16: **155 ms** per utterance with speech in it |
| **THE ENGINE** (`D30`, `D104`) | **Typhoon ASR** (`scb10x/typhoon-asr-realtime`, NeMo FastConformer transducer). `STT_ENGINE=typhoon`, needs the `asr` extra. **The only engine that meets `ARCHITECTURE` §15's 1.5 s budget: p95 0.19 s median, 0.28 s worst, 20 of 20 calls inside.** No 30 s window, which is the structural reason `D99` predicted before any of it was measured. Costs CER mean 0.133 against fp16's 0.109, and cannot be hinted |
| **THE FALLBACK** (`D103`) | **CT2 `int8_float16` + hint**, for a box where NeMo will not install. `STT_ENGINE=thonburian_ct2`, `STT_MODEL` unset (`B23`), needs only the `ml` extra. p95 1.68 s median / 2.53 s worst, 7 of 20 inside budget, CER mean 0.128 |
| **Real Thai accuracy** (`D103`) | Balanced 20-call set (`--mix --seed 7`), both engines hinted or both not: fp16 unhinted **CER mean 0.109**, CT2 hinted **0.128**, CT2 unhinted 0.171, fp16 hinted 0.119. **Rank on the mean** — the median is unstable at n=20 and moved 0.087 -> 0.124 between two runs of an identical config. Earlier figures (0.161/0.182) were measured on a digit-heavy set with only one engine hinted and are withdrawn |
| **The vocabulary hint is load-bearing** (`B19`, `Q27`) | Applied via `prompt_ids` since 2026-09-04. It is mildly *negative* on fp16 (+0.010) and strongly positive on int8 (**-0.043**), and it steadies the decoder enough to move CT2's worst `busy` from 0.46 to 0.11. `Q27` answered: an off-domain insurance vocabulary does not hurt. Changing `config/stt_vocabulary.yaml` now costs accuracy as well as risking `B14` |
| **Latency against the budget** (`ARCHITECTURE` §15) | Budget p95 **< 1.5 s**. CT2 paced on the balanced set: **1.68 s median, 2.53 s worst, 7 of 20 inside**. Missed by 1.1-1.7x, against fp16's 13-39x. What remains is the fixed per-utterance cost, not a queue - closing it needs fewer or cheaper 30 s windows |
| **Rejected: `distill-whisper-th-large-v3`** | Free to try (already in the HF cache) and worse than CT2 on every axis: CER 0.096 vs 0.087 median, `busy` worst 0.23 vs 0.11, VRAM 1942 vs ~1000 MB. A distilled *large* is still a large |
| **Throughput** | `busy` = model-seconds per second of audio; above 1.00 the transcriber never catches up. fp16 **0.25 median / 1.25 worst**; CT2 hinted **0.08 / 0.11**. `pad` = seconds Whisper encoded per second of call: median **2.7**, because it pads every clip to a fixed 30 s window |
| **Real Thai latency** (`D30`) | Paced over 12 calls: p95 **4.5 s - 58.7 s** against a **1.5 s** budget, and the spread tracks throughput — rtf <= 0.31 gives 4.5-8 s, rtf >= 0.65 gives 31-59 s. Once decode is slower than speech the backlog compounds and the last utterance lands a minute late; **half these calls are in that regime.** Invisible until now because every run used `--fast` (`B20`). No segment was abandoned, so these are honest end-to-end numbers. This is `Q29` and it is what `D30`'s table now decides |
| Diagrams | 69 (14 generated from source, 55 hand-drawn), across 13 explanation pages |

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

**P4** — analysis and the brief v2+, Claude vs Typhoon measured rather than argued. It is the
largest remaining phase and the one the pitch leans on hardest. Two things already point at it:
`OfferOut.summary_th` is rendered on the offer card today from the rule-based builder and is the
field an AI summary fills, so **no client change is needed**; and `D92` draws the line P4 must
not cross — speech may change WHO answers and HOW SOON, never WHICH QUEUE — which should be
written as a test *with* the blend rather than after it.

**The encrypted recording landed 2026-09-06** (`D110`), which closes P3's last exit criterion.
One wrapper does the crypto for every backend, `KeyRing` is the tenth port, `audio_recordings`
is the tenth table, and `scripts/purge_recordings.py` is `D14`'s erasure job for the audio half.
Verified against a real MinIO container: the bucket holds ciphertext, the right key returns the
original WAV, a wrong one refuses, and a caller who pressed 2 left nothing behind.

Two smaller things in the same area, both written down rather than left to be rediscovered:
**`IntakeService._degradation()` returns `NONE` unconditionally** even though
`TranscriptionService` now knows whether the engine failed — a *wait* until `D96` and a *gap*
since; and **the decode timeout** (`D98`'s missing half) still needs `D2`'s killable worker
process, and must not be faked with `asyncio.wait_for`, which does not kill the thread.

**The live transcript on the agent's screen has landed** (`D105`–`D107`, `B24`, 2026-09-05).
Turns are held while nobody owns the call — which is the whole of intake, and is the product
rather than an edge case — and flushed to whoever accepts. Getting there found two services
that were written, correct, tested and **called by nothing**: `TranscriptionService.open()`,
so the running system had never transcribed anything; and the event bus, which is drained on
`drain()` and had no periodic driver, so any subscriber would have been unreached.

**P3 step 4b — the GPU half — has landed** (`D96`, `D104`). `media/` normalises whatever
telephony delivers, `ports/vad.py` and `services/transcription/` endpoint it, and
`TranscriptionService` feeds `IntakeService.on_turn` for real. The engine was chosen on 20 real
Thai calls rather than argued about, and it is the first thing in this project to meet
`ARCHITECTURE` §15's latency budget. Everything above the audio was already done and none of it
needed a model or a sound card: every spoken line is text in one file rendered at build time, the
keypad menu that routes the call is a real service, and so is the offer that runs after it — a
caller is offered the recording, and either takes it, refuses it (recorded as a refusal) or
ignores it, and all three reach the same queue. See `explanations/P3_voice.md` for the reasoning,
`diagrams/12_the_menu.md` for the picture, and `NEXT_SESSION.md` for the live state.
