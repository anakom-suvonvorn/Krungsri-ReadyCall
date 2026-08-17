# PLAN

_The master build plan for the full system: what gets built, in what order, and what "done" means for each phase._
_Last updated: 2026-08-17._

---

## 0. Sequencing principle

Build **inward-out along the value path**, and make every phase independently demonstrable:

```
P0 foundations ─▶ P1 context (no audio) ─▶ P2 routing + agent screen ─▶ P3 intake/STT
                                    │                                        │
                                    └──────────────▶ P4 analysis + brief ◀───┘
                                                              │
                                        P5 real telephony ────┼──── P6 wrap-up + metrics
                                                              │
                                                        P7 PDPA hardening
                                                              │
                                                        P8 future (AI caller, live assist)
```

Two rules that shape the order:

1. **Telephony is deferred to P5.** The `SimulatedTelephonyAdapter` + scenario runner exist from P0,
   so nothing is ever blocked on SIP, NAT, or a phone number. This is the single biggest schedule risk
   removed.
2. **P1 alone is already a product.** Context-aware calling with a context-only brief delivers real
   value with zero AI risk. Everything after it is additive. If time collapses, the demo still works.

---

## P0 — Foundations
**Goal:** an empty but *correct* skeleton that can already run a fake call end-to-end.

- Rename package `fullproject` → `readycall`; restructure `src/` per `PROJECT_STATE.md` §4; fix
  `pyproject.toml` (name, scripts, `[tool.uv.build-backend] module-name`), pin Python 3.11.
- `config.py` with `pydantic-settings` covering the whole config surface (`INTEGRATIONS.md` §7);
  `.env.example`.
- `structlog` + OpenTelemetry; **`call_session_id` and `trace_id` on every log line and span.**
- `domain/models.py`, `enums.py`, `events.py` — pure, no I/O.
- **All ports defined** (`telephony`, `stt`, `llm`, `tts`, `core_data`, `event_bus`, `blob_storage`,
  `agent_directory`, `notifier`) with docstrings that state the contract.
- Fake + null adapters for every port. `InMemoryBus`. `SimulatedTelephonyAdapter`. `ScriptedSttAdapter`.
  `RuleBasedAdapter` for LLM.
- Postgres: `readycall` tables + Alembic baseline; `core` schema DDL + `mock/bank_core/generate.py`
  (seeded, ~2,000 customers across the three personas) + `personas.yaml` + the first 5 `scenarios/*.yaml`.
- `tests/contracts/` harness; `scripts/run_scenario.py` skeleton; `docker-compose.yml`
  (postgres, redis, minio); GitHub Actions running `ruff`, `mypy`, `pytest`.

**Exit criteria**
- `uv run python scripts/run_scenario.py scenarios/pattheera_ipd.yaml` drives a fake call from
  intent → queued → assigned → closed using only fake adapters, and prints the state timeline.
- `pytest tests/contracts` passes for every fake adapter.
- `docker compose up` + `alembic upgrade head` + seed works from a clean clone.

---

## P1 — Context-Aware Calling (no audio at all)
**Goal:** the pitch's Step 1, complete.

- `POST /v1/calls/intents` (session auth → `customer_id`, correlation token, expiry) and
  `POST /v1/app/context-events`.
- `CoreDataProvider`: `MockPostgresProvider` + `FixtureFileProvider` + `mapping.py` (YAML field mapper)
  + `CachingProvider` (TTL, stale-while-revalidate, circuit breaker) + `NullProvider`.
- `ContextAssembler` → `Customer360` (identity, active policies + coverage, holdings, recent
  interactions, life events, prior ReadyCall history) with **per-field provenance and freshness**;
  frozen into `context_snapshots`.
- Prefetch fires on `intent.created`, off the request path.
- Agent screen v1 (static page, no realtime yet) rendering a **context-only brief**.
- Contract tests for all core-data adapters; latency instrumentation on the assembler.

**Exit criteria**
- Tap-to-snapshot **p95 < 1.5 s** against the mock, measured and recorded.
- Swapping `CORE_DATA_PROVIDER=mock_postgres → fixtures` changes nothing but the env var, and both
  pass the same contract suite.
- Every field on the screen can name its source and its age.

---

## P2 — Routing, queues, and the agent desktop
**Goal:** the right agent gets the call, and the screen is live.

- `queues.yaml`/`skills.yaml`, `queues` + `queue_entries`, priority + SLA + overflow.
- `agent_presence` (Redis-backed heartbeat), capacity, states.
- `RoutingEngine`: hard filters + weighted score, weights from `config/routing_weights.yaml`,
  **full candidate breakdown persisted** to `routing_decisions`.
- Assignment + offer/accept/reject/no-answer re-route (keeping queue position and brief).
- Agent WebSocket (auth, presence, push of the brief bundle, acks, reconnection with replay).
- Agent desktop shell in React: incoming-call card, the panel layout from pitch p.7, "why this agent",
  transcript pane (empty for now).

**Exit criteria**
- 20 simulated concurrent calls + 5 agents route deterministically; replaying the same scenario twice
  yields identical decisions.
- The rationale panel explains a real assignment in a sentence a judge understands.
- Queue pop → brief rendered **< 1 s**.

---

## P3 — AI Pre-Call Intake v1 (passive)
**Goal:** the pitch's Step 2 — audio in, transcript out, live.

- Media Gateway: AudioSocket + WebSocket media servers, resampling to 16 kHz mono float32, framing,
  encrypted recording to MinIO, per-recording key refs.
- Consent gate (IVR keypress + in-app toggle) writing `consents` before a single frame is analysed.
- `transcription/`: rolling buffer, Silero VAD endpointing (threshold 0.65 / 500 ms / 100 ms +
  120 ms·60 ms padding, per `D9`), utterance dispatch, repetition guard.
- `stt_worker`: long-lived, model loaded once, GPU-pinned, batched, health-checked;
  `ThonburianHfAdapter` first, `ThonburianFasterWhisperAdapter` benchmarked against it.
- `TranscriptTurn` events + incremental DB writes; live transcript in the agent desktop.
- `IntakeStrategy` seam with `PassiveRecordIntake`; `finalize(reason)` incl. `queue_pop` → partial.

**Exit criteria**
- Utterance end → turn visible **p95 < 1.5 s** on the target hardware (record the number and the hardware).
- Killing the STT worker mid-call degrades to recording-only; the call is unaffected.
- Thai WER measured on the scenario audio set and written into `PROJECT_STATE.md`.
- No audio ever written to local disk unencrypted.

---

## P4 — Analysis & the case brief
**Goal:** the agent screen from pitch p.7, fully populated and trustworthy.

- `config/intents.yaml`: closed taxonomy + required slots per intent + intent→skill mapping.
- Prompts in `prompts/th/`, versioned, each with a Pydantic output schema.
- `intent.py`, `entities.py`, `summary.py`, `nba.py` (playbook-driven), `opening.py`, `pii.py`.
- `confidence.py` — the blend from `ARCHITECTURE.md` §9 + calibration against the golden set + the floor.
- `brief.py` — versioned brief assembly merging context + speech, `is_partial` handling, `sources_json`.
- Re-routing when the speech-derived intent disagrees with the tapped product.
- `tests/golden/` + `scripts/eval_golden_set.py` reporting intent accuracy / entity F1 / summary
  faithfulness; wired into CI as a gate on prompt changes.
- `brief_feedback` 👍/👎 in the desktop.

**Exit criteria**
- ≥ 85% intent accuracy on the golden set (or a documented reason why not, with the actual number).
- **Zero** coverage figures generated by the model — a CI check asserts every numeric traces to a field (`D16`).
- LLM outage → rule-based brief, verified by killing the provider.
- Customer stops speaking → final brief **≤ 3 s**.

---

## P5 — Real telephony
**Goal:** an actual phone call, not a simulation.

- Asterisk 20 in compose: `chan_pjsip`, WSS/WebRTC endpoint for the app, queues, ARI enabled.
- `AsteriskAriAdapter`: originate/answer/play/bridge/hangup + AudioSocket fork; ARI event stream → bus.
- WebRTC path from the customer simulator (correlation token in a SIP header), then the RN app.
- PSTN fallback: ANI → `customer_phones`, pending-intent disambiguation, IVR code, anonymous path.
- Hold music/prompts, transfer (brief travels with the call), reconnect handling.
- `TwilioAdapter` as the alternate, behind the same contract tests.

**Exit criteria**
- A real call from the simulator reaches a real agent desktop with a full brief.
- Provider swap (`asterisk` ↔ `twilio` ↔ `simulated`) is env-var only and passes the same contract suite.
- Media fork starts < 300 ms after answer.

---

## P6 — Wrap-up, feedback, metrics
- Post-call AI draft (disposition, summary, follow-ups) → **agent edits/confirms** → `call_wrapups`.
- Follow-up tasks; the loop back into the *next* call's "Recent Context".
- `metrics_rollups` + a Grafana dashboard: AHT, FCR, time-to-context, brief-ready rate, abandonment,
  intent accuracy, NPS.
- A **before/after view**: same scenario with ReadyCall on vs off, timings side by side. This is the
  Impact evidence.

**Exit criteria:** a measured before/after on the scenario set, with real numbers, not estimates.

---

## P7 — PDPA & security hardening
- Full consent lifecycle incl. separate health-data scope and withdrawal.
- PII masking by default in the UI; reveal is an audited action.
- Retention policies per artifact + erasure job across Postgres and object storage.
- RBAC (agents see only assigned customers), `audit_log` on every customer-data read.
- mTLS between services; secrets from a vault; encryption-at-rest verified.
- **Consent/masking badges on the agent screen** so the compliance work is visible in a demo.

**Exit criteria:** an erasure request provably removes recordings, transcripts, briefs and snapshots;
audit log reconstructs who saw what.

---

## P8 — Future
- `GuidedPromptIntake` (TTS slot-filling for the missing fields of the detected intent).
- `ConversationalAgentIntake` — full-duplex AI caller with barge-in and read-only tool access; **still
  emits the same turns and the same final brief** (`D10`).
- Live in-call assist (real-time suggestions, policy lookup, compliance nudges).
- Proactive outbound: the same context layer driving renewal/cross-sell calls — which reaches the
  brief's *other* journey leaks (steps 5–6, post-sale engagement).
- Product recommendation / coverage-gap detection on top of the same `Customer360`.

---

## Work breakdown for a five-person team

| Track | Scope | Phases |
|---|---|---|
| **A — Platform** | repo, config, DB, migrations, mock core + generator, CI, scenario runner | P0 → all |
| **B — Call & telephony** | orchestrator state machine, media gateway, Asterisk/WebRTC | P0, P3, P5 |
| **C — Speech** | VAD, STT worker, streaming turns, WER/latency benchmarking | P3 |
| **D — AI/brief** | taxonomy, prompts, analysis, confidence, golden set | P4 |
| **E — Product/UI** | agent desktop, customer simulator, routing UX, metrics dashboard | P1, P2, P6 |

Tracks are deliberately aligned to port boundaries so they can proceed in parallel against fakes.

---

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Telephony eats the schedule | High | Simulated adapter from P0; real telephony deferred to P5; Twilio as the escape hatch |
| No GPU on demo hardware | High | Distilled model + `faster-whisper` benchmarked in P3; `ScriptedSttAdapter` as the stage-safe path |
| Hackathon data ≠ our assumptions | High | `CoreDataProvider` + YAML mapping + contract tests (`DATA_MODEL.md` §4) |
| Thai ASR quality on insurance jargon | Medium | Golden set from P0; domain prompt hints; measure, don't guess |
| LLM hallucinating coverage | High | `D16`: numbers are data-only + a CI check |
| Confidence number is meaningless | Medium | `D13`: calibrate, or don't show it |
| Demo depends on venue Wi-Fi / their API | High | Everything runs offline against the mock; **never depend on their network on stage** |
| Scope creep into out-of-bounds features | Medium | The brief's out-of-scope list is quoted in `PROJECT_STATE.md` §1 |

---

## How the demo will be scoped (later, in `../DemoProject/`)

Not decided yet — but the intended shape, so P0–P4 are built to make it cheap:

- Customer simulator (web) + `SimulatedTelephonyAdapter` (or a real WebRTC call if P5 lands in time).
- One or two personas end-to-end from the scenario set, with a live-typed or live-spoken variant.
- The agent desktop as the hero screen, with the **stage timeline visible** ("context ready at 1.2 s,
  before the phone rang") — that's `D18` paying off on stage.
- A visible fallback path for every live component, and a fully pre-recorded run as insurance.
