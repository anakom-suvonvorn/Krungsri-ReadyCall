# PLAN

_The master build plan for the full system: what gets built, in what order, and what "done" means for each phase._
_Last updated: 2026-09-05._

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

## P1 — Context-Aware Calling (no audio at all) — ✅ **DONE** (incl. P1b, 2026-08-23)
**Goal:** the pitch's Step 1, complete — **for both the app path and the cold-call path**.

- `POST /v1/calls/intents` (session auth → `customer_id`, correlation token, expiry) and
  `POST /v1/app/context-events`.
- **Identity Resolver + assurance ladder L0–L3** (`D20`): ANI lookup, pending-intent window, IVR
  verification stub, `identity_resolutions` audit rows, and disclosure gating in the brief builder.
- **`config/dids.yaml`** — product-line numbers → `{product_line, default_queue, greeting_prompt}`
  (`D19`), so a cold call already has a product line before anyone speaks.
- **Customer simulator** (web) with a demo login / persona picker, talking to the same public `/v1/…`
  API the real app would use.
- `CoreDataProvider`: `MockPostgresProvider` + `FixtureFileProvider` + `mapping.py` (YAML field mapper)
  + `CachingProvider` (TTL, stale-while-revalidate, circuit breaker) + `NullProvider`.
- `ContextAssembler` → `Customer360` (identity, active policies + coverage, holdings, recent
  interactions, life events, prior ReadyCall history) with **per-field provenance and freshness**;
  frozen into `context_snapshots`.
- Prefetch fires on `intent.created`, off the request path.
- ~~Agent screen v1 (static page, no realtime yet)~~ → **moved to P2.** Building a static
  version now means building it twice; it belongs with the workstation and its websocket.
- Contract tests for all core-data adapters; latency instrumentation on the assembler.

**Exit criteria** — met, except where noted
- ✅ Tap-to-snapshot: measured **~1.8 ms** against fixtures (p95 against `mock_postgres`
  awaits the DB layer at P2). Note `B3` — stage timings read `0.0` until `SystemClock`
  moved to `perf_counter`, so any earlier measurement was meaningless.
- Swapping `CORE_DATA_PROVIDER=mock_postgres → fixtures` changes nothing but the env var, and both
  pass the same contract suite.
- Every field on the screen can name its source and its age.
- **A scenario with no app and no intent** (DID + ANI only) produces a usable brief at the correct
  assurance level, with policy details correctly withheld at L1.

---

## P2 — Matching, queues, agents, and the desktop
_Split in practice: **P2a the engine (done, 2026-08-24)** and **P2b the workstation
(done, 2026-08-24)**. The database layer slipped again — see the note under the exit criteria._
**Goal:** the right agent gets the call, for the right reason, and the screen is live.

- `skills.yaml` / `queues.yaml` / `queue_hours.yaml`; `queues` + `queue_entries`; SLA + overflow.
- **Agent state model** (`D9` of the architecture): system state (auto) × agent intent (manual,
  incl. `LAST_CALL` and `DRAINING`), capacity, schedules, Redis heartbeat presence with TTL,
  `agent_state_log`.
- **Matching Engine** (`D22`): single waiting pool, fit scoring, urgency (wait / SLA / priority /
  situational), **global optimal assignment (Hungarian)**, the `MAX_WAIT_BEFORE_ANY_AGENT_S` override,
  and the anti-hot-spot checks (startup validation that no skill has a single holder; concentration
  metric). Deferral is **stubbed off** here and switched on in P6 when call-progress data exists.
- **Full breakdown persisted** to `matching_decisions` — candidates, every term, solver used.
- **Matching simulator** (`scripts/simulate_matching.py`): replay a day of arrivals against a synthetic
  pool to tune weights offline in seconds.
- **Offer/accept handshake** (`D33`): offer card + timeout + decline/RONA, `AFTER_CALL_WORK` timer with
  a Done button, `manual_accept` / `auto_accept` modes, all measured into `assignments`.
- Agent WebSocket (auth, presence, brief push, acks, reconnect-with-replay).
- **Agent workstation shell** in React (`D32`): offer card, the panel layout from pitch p.7, assurance
  badge, "why this agent", queue strip, status control, transcript pane (empty for now), and **the
  call-control bar wired to a stubbed softphone** — the real WebRTC audio arrives in P5, but the UI,
  the states and the handshake are all real here and driven by `SimulatedTelephonyAdapter`.

**Exit criteria**
- 20 simulated concurrent callers + 20 agents (3 real browser sessions) match deterministically;
  replaying the same scenario twice yields identical decisions.
- The rationale panel explains a real assignment in a sentence a judge understands.
- ⚠️ No starvation: with a skewed skill mix, the wait-time p99 stays under the configured ceiling.
  **This was recorded as met on 2026-08-24 and was not** (`B12`, found 2026-09-01). The pool fed
  the matcher a `waiting_s` frozen at admit time, so `wait_pressure` stayed at 0 and the ceiling
  was unreachable — the mechanism was written, correct, and driven by a constant. Fixed; the
  criterion now needs re-measuring under load, which is the part that was never run.
  **And a second reason it was not met** (`B13`, same day): the ceiling that the criterion
  names sat inside a guard that only ran for callers the solver had already placed, so it
  could never rescue the starved caller. Both are fixed (`D93`); the load measurement is
  still outstanding.
- Match → brief rendered **< 1 s**.

**Status 2026-08-24.** Met, except the multi-browser load test: the handshake, presence, the
workstation and the disclosure gate are all verified end to end in a real browser, and brief
render is **0.2 ms** (a re-render of a frozen snapshot, not a fetch — `D42`), but the
20-caller × 3-session run has not been done. The matching engine's own determinism is covered
by `scripts/run_matching.py --seed`.

**P2c — done, 2026-08-25.** Nine tables, one storage factory, and a restart that is
proved rather than asserted.

- SQLAlchemy 2.0 + Alembic, URL from `Settings`; `call_sessions`,
  `call_state_transitions`, `agent_state_log`, `assignments`, `identity_attestations`,
  `keypad_captures`, `matching_decisions`, `context_snapshots`, `call_wrapups`.
- **Write-through with an in-memory projection** (`D78`). Services keep the working set
  they already had, write durably on every mutation, and rebuild at startup. Reads never
  touch the database, because `excluded_agents()` runs inside the matcher tick and `D39`'s
  trigger — two processes needing one call — has not fired.
- **Half the state is derived on purpose**: current presence from `agent_state_log`, the
  waiting pool from `call_sessions`, the live identity from `CallSession.identity`.
- One contract suite across in-memory, SQLite and Postgres (`D75`), plus
  `tests/integration/test_restart.py`, which ends a process and starts another on the same
  storage. Re-verified outside pytest with two real uvicorn processes.

**Exit criteria, met:** a declared *lunch* survives a restart; a waiting caller is still
waiting with their accrued wait intact; an attested `L3` identity and its disclosure log
come back; the brief still renders; and an agent who declined is still excluded from
re-matching (`D52`) rather than being offered the same caller again.

**The database (`D39`) did NOT land with P2b.** Presence, assignments and the state log are
still in memory. The reason is not oversight: `D39`'s trigger was "two processes need to see
the same call", and P2b is a single process. Landing Postgres would have doubled the size of an
already-large phase and made every test need a container. It is now the first item of P2c, and
the seams it plugs into (`CallSessionRepository`, `PresenceService`, `AssignmentService`) are
already Protocol-shaped or dict-backed behind one class each.

---

## P3 — Voice, IVR, and AI Pre-Call Intake v1 (passive) — 🔶 **steps 1–3 done (2026-08-25)**
**Goal:** the pitch's Step 2 — the line talks, audio goes in, transcript comes out, live.

The phase was ordered lowest-risk-first, and that order held. Steps 1–3 needed no model, no
audio hardware and no network; step 4 is where the RTX 3050 risk actually lives and is not
started. `explanations/P3_voice.md` covers the built half; `diagrams/12_the_menu.md` draws it.

### ✅ Done

- **Voice prompts** (`D24`): `config/voice_prompts.yaml` with **32 prompts**, declared slots,
  and a `flow:` table mapping **19 roles** to ids so `services/` carries no prompt literals
  (`D28`). Cross-validated against `menus.yaml` / `dids.yaml` **in both directions**, as a
  startup gate *and* a test — a dangling id is a silent gap in a call.
- **`scripts/build_prompts.py`**: renders through the `TtsEngine` port, cached by
  `hash(text, voice, engine)`, deduped by rendered text to **63 clips**. Committed manifest,
  asserted fresh by a test. Verified: one edited line re-renders one clip.
- **Two decisions that only appeared once it was built.** `D80` — a menu is a lead-in plus
  one line per option, because personalised ordering makes a single baked clip impossible.
  `D81` — the key pressed is not the key stored; every press resolves to canonical, so
  `menu_path` means the same thing on every call.
- **`services/ivr/`**: greeting + recording notice → product-line menu (skipped when the DID
  or the app already said) → reason menu → queue. `0` repeats without spending an attempt,
  a wrong key is never a strike (`D82`), two silences route rather than hang up, and a
  product-line number with no keypress still reaches that line's queue.
  *(As written this said "`0` reaches a human from any depth" and "three wrong keys …
  route"; `D82` removed the attempt limit and `D86` removed the operator key. The way out
  of a menu is its own spoken "เรื่องอื่นๆ".)*
  Personalised ordering with the evidence attached. No I/O in the machine, so a timeout is a
  method call (`B7`).
- **Handover complete.** `run_scenario.py`'s `# P1:` IVR marker and `demo.py`'s `# P2b:` are
  both retired; the only thing still faked is the number dialled and the keys pressed.

### ☐ Remaining — step 4, the GPU half

- A **real TTS voice**: `TTS_ENGINE=null` synthesises nothing today, so the pack is a manifest.
  Choose on a listening test of the actual 63 lines, not a spec sheet. Plus the checked-in
  audio pack and the admin **prompt studio** page.
- ✅ **The press-1/press-2 intake offer, with its re-offer** — `services/intake/` (`D88`).
  The identify step is **gone**, not pending (`D84`). The **post-call rating keypress** still
  has a prompt and a role and nothing calling it.
- Media Gateway: AudioSocket + WebSocket media servers, **per-leg forking**, resampling to
  16 kHz mono float32, framing, encrypted recording to MinIO, per-recording key refs.
- Consent gate (IVR keypress + in-app toggle) writing `consents` before a single frame is analysed.
- `transcription/`: rolling buffer, Silero VAD endpointing (threshold 0.65 / 500 ms / 100 ms +
  120 ms·60 ms padding, per `D9`), utterance dispatch, repetition guard.
- `stt_worker`: long-lived, model loaded once, GPU-pinned, batched, health-checked.
  **The `ml` extra is still commented out in `pyproject.toml`** — declaring `torch` /
  `transformers` / `faster-whisper` / `onnxruntime` / `silero-vad` is step zero and it is the
  slow install.
- ✅ **STT bake-off on the real hardware**: Thonburian-HF vs Thonburian-CT2 vs distilled vs
  Typhoon ASR — CER (never WER, `B18`), p95 utterance latency, VRAM — recorded in
  `PROJECT_STATE.md` §8 and decided in `D104` (`D30`, `INTEGRATIONS.md` §2.1).
- ✅ `TranscriptTurn` events (`transcript.turn`, published per turn). ☐ incremental DB writes —
  `transcript_turns` has no table and no ORM model yet. ☐ live transcript in the agent desktop.
- ✅ `IntakeStrategy` seam with `PassiveRecordIntake`; `finalize(reason)`; **ring-time
  grace** (`D21`) — the accept endpoint finalises a live intake as partial, proved on a
  running server. Turns arrive through `IntakeService.on_turn`, **fed for real since
  `D96`** by `services/transcription/`, and the strategy publishes each one on the bus as
  `transcript.turn`. What is missing is a **subscriber**: nothing takes those events off
  the bus and pushes them to the workstation, which is why the screen has no live
  transcript yet.

**Exit criteria**
- ✅ Utterance end → turn visible **p95 < 1.5 s** on the RTX 3050, with the chosen engine named and
  the bake-off table recorded. **MET 2026-09-04** (`D104`), paced over a balanced 20-call set of
  real Thai call-centre audio:

  | | Thonburian fp16 | CT2 int8 + hint | **Typhoon** |
  |---|---|---|---|
  | p95 median / worst | 19.5 s / 58.7 s | 1.68 s / 2.53 s | **0.19 s / 0.28 s** |
  | inside the budget | 0 of 12 | 7 of 20 | **20 of 20** |
  | CER mean | **0.109** | 0.128 | 0.133 |
  | VRAM | 2716 MB | ~1000 MB | 1068 MB |

  **Typhoon ships; CT2 int8 is the fallback** for a box where NeMo will not install. The reason
  Typhoon wins is structural and was written down in `D99` before it was measured: a transducer
  has **no 30 s window**, so it does not pay a full encode for a two-second utterance.

  **The road here is worth more than the table**, and every step of it is a bug entry: the
  earlier figures of CER 0.47–0.76 were `B20` (our buffer dropping audio), then 0.161 was a
  digit-heavy test set plus `B21` (the guard deleting phone numbers), then the engine comparison
  itself was unfair until `B19` was fixed. **Do not quote an accuracy number from before
  2026-09-04**; and rank on the CER **mean**, because the median is unstable at this sample size
  and cost `D103` a self-correction.

- 🔶 The live transcript on the agent's screen. Turns exist, are ordered, reach
  `IntakeService.on_turn` and are **published on the bus**; nothing subscribes, so
  `api/realtime.py` never sees one. **This is the next slice** and `NEXT_SESSION` breaks it
  into two pieces — a subscriber and a panel.
- 🔶 The encrypted recording to object storage — P7's key management.
- ✅ A caller who presses 2, and a caller who consents to nothing, both still reach **the correct
  queue** with a menu-derived brief — because routing never depended on the AI (`D37`).
  Proved end to end: `test_every_answer_leaves_the_queue_exactly_where_the_menu_put_it`,
  and three live calls (record / decline / ignore) all landing in `q_health_policy`.
- ✅ A caller on the general hotline with an unrecognised number reaches the right specialist purely by
  keypad. That is the floor, and it must be at least as good as an ordinary call centre.
  *(`anonymous_declined` replays it: keys 2/4 → `q_health_policy`, no identity, no consent.)*
- 🔶 Killing the STT worker mid-call degrades to recording-only; the call is unaffected. The
  swallow-and-log path exists in `TranscriptionStream._consume` (`D12`) and is asserted by test;
  killing a *real* worker mid-call waits on the worker being a separate process (P5).
- ✅ No audio ever written to local disk unencrypted — because **no audio is written to disk at
  all** yet. Everything is per-utterance and in memory (`D9`). The encrypted recording is the
  remaining piece and it wants P7's key management.
- 🔶 Changing a line of Thai in `voice_prompts.yaml` changes what the caller hears after one re-render.
  *(The re-render is proved; "what the caller hears" waits on a real voice.)*

---

## P4 — Analysis & the case brief
**Goal:** the agent screen from pitch p.7, fully populated and trustworthy.

- `config/intents.yaml`: closed taxonomy + required slots per intent + intent→skill mapping.
- Prompts in `prompts/th/`, versioned, each with a Pydantic output schema.
- `intent.py`, `entities.py`, `summary.py`, `nba.py` (playbook-driven), `opening.py`, `pii.py`.
- `confidence.py` — the blend from `ARCHITECTURE.md` §9 + calibration against the golden set + the floor.
- `brief.py` — versioned brief assembly merging context + speech, `is_partial` handling, `sources_json`.
- Re-routing when the speech-derived intent disagrees with the tapped product.
- **Two LLM adapters implemented** (`D29`): `AnthropicAdapter` and `OpenAiCompatibleAdapter` (which
  covers Typhoon-hosted, OpenAI, vLLM and Ollama by base URL alone). `GeminiAdapter` defined only.
- `tests/golden/` + `scripts/eval_golden_set.py` reporting intent accuracy / entity F1 / summary
  faithfulness; wired into CI as a gate on prompt changes.
- **`scripts/compare_llm.py`** — the same golden set through every configured provider, printing
  accuracy / latency / cost side by side, plus a runtime provider switch for live demos.
- Brief rating (1–5 + wrong-field tags) in the desktop (`D27`).

**Exit criteria**
- ≥ 85% intent accuracy on the golden set (or a documented reason why not, with the actual number).
- **Zero** coverage figures generated by the model — a CI check asserts every numeric traces to a field (`D16`).
- LLM outage → rule-based brief, verified by killing the provider.
- Customer stops speaking → final brief **≤ 3 s**.
- **A Claude-vs-Typhoon comparison table exists**, produced by the harness, not by opinion.

---

## P5 — Real telephony
**Goal:** an actual phone call, not a simulation.

- Asterisk 20 in compose: `chan_pjsip`, WSS/WebRTC transport, holding bridges, ARI enabled, TLS certs
  (`mkcert` for LAN machines).
- `AsteriskAriAdapter`: originate/answer/play/**bridge**/hangup/transfer + AudioSocket fork; ARI event
  stream → bus.
- **The agent-side softphone becomes real** (`D32`): SIP.js over WSS registering the workstation as a
  SIP endpoint, Opus, device picker + level meter + pre-shift audio self-test, ringtone autoplay
  unlock, mute/hold/DTMF/transfer, and reconnect-without-dropping-the-call.
- Customer-side WebRTC from the simulator (correlation token in a SIP header).
- Cold-call path: DID mapping, ANI → `customer_phones`, pending-intent disambiguation, IVR
  verification, anonymous path.
- Hold music/prompts, **bridge on Accept** (customer sits in a holding bridge until then), transfer
  with the brief travelling.
- `TwilioAdapter` as the alternate, behind the same contract tests. *(Chosen path: Asterisk first,
  precisely because a real SIP trunk / DID can be attached later without changing the adapter — the
  same code that runs the demo runs against a real number.)*

**Exit criteria**
- A real call from a **softphone on a phone over local Wi-Fi** reaches an agent who accepts it **in the
  browser** and talks through their headset, with the full brief already on screen.
- Agent presses Accept → audio bridged in < 500 ms (it is a bridge, not a dial-out).
- Reloading the workstation mid-call does not drop the call.
- Provider swap (`asterisk` ↔ `twilio` ↔ `simulated`) is env-var only and passes the same contract suite.
- Media fork starts < 300 ms after answer.

---

## P6 — Live-call transcription, wrap-up, ratings, metrics
- **Live-call transcription** of both legs (`D26`) behind `LIVE_CALL_TRANSCRIPTION`, feeding:
- **Call-progress estimation** (elapsed vs expected AHT + transcript wrap-up cues + the agent's own
  "wrapping up" button) → `call_progress_estimates` → **deferral switched on** in the matcher (`D22`).
- Post-call AI draft from the real conversation (disposition, summary, follow-ups) → **agent
  edits/confirms** → `call_wrapups`. Follow-up tasks; the loop back into the next call's Recent Context.
- **Ratings both sides** (`D27`): customer CSAT/NPS via in-app prompt or IVR keypress (voice comments
  go through the same transcription pipeline); agent brief rating.
- **After-hours path** (`D25`): queue hours, voicemail through the full intake pipeline,
  `callback_tasks` landing pre-briefed in the morning queue.
- **Call Explorer** admin page: one call's whole story — timeline, identity, provenance, every brief
  version, the matching breakdown, the wrap-up.
- `metrics_rollups` + Grafana: AHT, FCR, time-to-context, brief-ready rate, abandonment, intent
  accuracy, CSAT/NPS, **defer hit rate**, **assignment concentration**.
- A **before/after view**: same scenario with ReadyCall on vs off, timings side by side.

**Exit criteria:** a measured before/after on the scenario set with real numbers; deferral demonstrably
improves fit without increasing p95 wait; an after-hours call produces a briefed callback task.

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

## How the demo will be scoped

There is no separate demo project (`D34`) — the demo is this system at whatever phase it has
reached, driven through a chosen scenario. Not decided yet, but the intended shape, so P0–P4
are built to make it cheap:

- Customer simulator (web) + `SimulatedTelephonyAdapter` (or a real WebRTC call if P5 lands in time).
- One or two personas end-to-end from the scenario set, with a live-typed or live-spoken variant.
- The agent desktop as the hero screen, with the **stage timeline visible** ("context ready at 1.2 s,
  before the phone rang") — that's `D18` paying off on stage.
- A visible fallback path for every live component, and a fully pre-recorded run as insurance.
