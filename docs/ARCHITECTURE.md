# ARCHITECTURE

_How the full ReadyCall system works, end to end. Read this to understand the machine._
_Status: **design only** — nothing here is implemented yet (see `PLAN.md` for the build order)._
_Last updated: 2026-08-17._

---

## 1. Big picture

**Krungsri ReadyCall** turns hold time into preparation time. It is an **AI context layer that sits on
top of** the existing app, the existing contact centre, and the existing core insurance systems — it
changes none of them.

```
                       ┌───────────────────────── ReadyCall ──────────────────────────┐
 Krungsri Mobile App   │                                                              │
  (customer, logged    │  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐  │
   in, viewing a plan) ├─▶│  Intent  │──▶│  Context  │──▶│ Routing  │──▶│  Agent   │  │
        │              │  │   API    │   │ Assembler │   │  Engine  │   │ Delivery │  │
        │ tap Contact  │  └──────────┘   └─────┬─────┘   └────▲─────┘   └────┬─────┘  │
        ▼              │                       │              │              │        │
   ┌──────────┐        │                 ┌─────▼─────┐        │              │        │
   │ Telephony│───────▶│  Call Orchestrator (state machine, one per call)    │        │
   │  (VoIP)  │  media │                 └─────┬─────┘        │              │        │
   └──────────┘───┐    │                       │              │              │        │
                  │    │  ┌────────┐   ┌───────▼──────┐   ┌───┴──────┐       │        │
                  └───▶│  │ Media  │──▶│ Transcription│──▶│ Analysis │───────┘        │
                       │  │Gateway │   │  (Thai STT)  │   │  (LLM)   │                │
                       │  └────────┘   └──────────────┘   └──────────┘                │
                       └───────────┬──────────────────────────┬───────────────────────┘
                                   │                          │
                        ┌──────────▼─────────┐     ┌──────────▼──────────┐
                        │  bank core  (RO)   │     │  readycall  (RW)    │
                        │ customers, KYC,    │     │ calls, transcripts, │
                        │ policies, claims,  │     │ briefs, routing,    │
                        │ interactions       │     │ agents, consents    │
                        └────────────────────┘     └─────────────────────┘
                                   ▲                          │
                                   │                          ▼
                             (read-only port)          Agent Desktop (WS)
```

Two hard architectural rules follow from that picture:

1. **The bank's data is read-only to us.** Everything ReadyCall produces lands in *our* store. See `D5`.
2. **Every external system is behind a port.** Telephony, STT, LLM, TTS, the bank's data, blob
   storage, the event bus. Business logic imports `ports/`, never a vendor SDK. See `D3`.

### The three things the product does (from the pitch)

| Pitch step | What it means technically |
|---|---|
| **1. Context-Aware Calling** | The tap on *Contact* carries verified identity + the exact plan being viewed into the call. Context is assembled *before the phone even rings*. |
| **2. AI Pre-Call Intake** | While queued, the customer may describe the issue. Audio → Thai STT → LLM → structured case brief. Modular, so it can later become a talking AI agent (`D10`). |
| **3. Agent gets a ready screen** | On assignment: who, which policy, what they want, what to say, what to do — plus *why* the system thinks so. |

---

## 2. Components (what each service owns)

| Component | Owns | Never does |
|---|---|---|
| **Intent API** (`api/routers/mobile.py`) | Creating a `CallIntent` from an authenticated app session; issuing the correlation token. | Trust a client-supplied `customer_id`. |
| **Context Assembler** (`services/context/`) | Building `Customer360` from the read-only core + our history; freezing it as a `ContextSnapshot` with provenance + freshness. | Write to the bank core. |
| **Call Orchestrator** (`services/call_orchestrator/`) | The per-call state machine; the single writer of `call_sessions.state`; emitting lifecycle events. | Talk to a vendor SDK directly; do AI work inline. |
| **Media Gateway** (`services/media_gateway/`) | Receiving forked call audio, normalising it to 16 kHz mono PCM, persisting the recording, fanning frames out. | Interpret content. |
| **Transcription** (`services/transcription/`) | VAD endpointing, streaming Thai STT, emitting `TranscriptTurn`s with timestamps + confidence. | Decide meaning. |
| **Analysis** (`services/analysis/`) | Intent classification, entity extraction, urgency/sentiment, summary, next-best-action, suggested opening, confidence calibration, PII spans. | Choose the agent. |
| **Routing Engine** (`services/routing/`) | Queues, agent presence/capacity, deterministic scoring, the assignment decision + its full rationale. | Use an LLM to pick a human. (`D8`) |
| **Intake** (`services/intake/`) | The pre-call experience via a swappable `IntakeStrategy`; produces one `IntakeResult` shape regardless of strategy. | Assume a strategy. |
| **Agent Delivery** (`api/ws/agent_ws.py`) | Agent presence, push of the `CaseBrief` bundle, live updates, acknowledgements. | Hold business state. |
| **Consent/PDPA** (`services/consent/`) | Consent capture, scope (incl. separate health-data consent), redaction policy, retention, audit. | Be optional. |
| **Wrap-up** (`services/wrapup/`) | Post-call summary + disposition + follow-ups, human-confirmed, written to our store. | Auto-write unverified content. |

---

## 3. The call lifecycle (state machine)

`call_sessions.state`, single-writer = Call Orchestrator. Every transition is appended to
`call_state_transitions` with timestamp + reason, which is what makes the timeline demonstrable.

```
 INTENT_CREATED
      │  (app tapped Contact; context prefetch starts NOW)
      ▼
   CONNECTING ──────────► FAILED            (no media, bad correlation, hang-up before answer)
      │
      ▼
    QUEUED ─────────────► ABANDONED         (customer hangs up while waiting)
      │  \
      │   └── consent given ──► INTAKE_ACTIVE ──► INTAKE_COMPLETE ──┐
      │                              │  (agent freed early)         │
      ▼                              └──────────────────────────────┤
   ROUTING ◄──────────────────────────────────────────────────────-─┘
      │
      ▼
   ASSIGNED ──► RINGING_AGENT ──► IN_CALL ──► WRAP_UP ──► CLOSED
                     │                │
                     └── no answer ───┘ (re-route, keeps position + brief)
                                      └──► TRANSFERRED (brief travels with the call)
```

**Invariant:** `QUEUED → ROUTING` is driven by *agent availability*, never by AI completeness.
If intake is still running when an agent frees up, intake is finalised as **partial** and the call
proceeds. Waiting on AI to connect a human would invert the entire value proposition. (`D12`)

---

## 4. Data flow A — pre-dial (Context-Aware Calling)

This is where "waiting time becomes preparation time" actually begins: **before the call connects.**

1. **In-app telemetry.** While the customer browses, the app posts lightweight screen-context events
   (`product_code` viewed, section, dwell) to `POST /v1/app/context-events`. Stored in
   `app_context_events` with a short TTL, gated on consent. This is what produces
   *"Recent Activity: Viewed hospitalization coverage"* on the agent screen.
2. **Tap Contact** on a specific plan → `POST /v1/calls/intents`
   `{ product_code, plan_id, entry_screen, preferred_channel, consent_flags }`.
   The customer is identified from the **app session token**, server-side. The client never asserts identity.
3. The API creates a `CallIntent` (status `PENDING`, `expires_at` ≈ 15 min) and returns
   `{ intent_id, correlation_token, dial_target }`.
4. **Context prefetch fires immediately** — asynchronously, not on the request path:
   `ContextAssembler.build(customer_id, product_code)` pulls from the read-only core (KYC, active
   policies + coverage, product holdings, recent interactions, life-event signals) and from our store
   (previous ReadyCall briefs, last agent, open follow-ups), and freezes a `ContextSnapshot`.
   By the time the phone rings, the non-speech half of the brief already exists.
5. **The app places the call** carrying the correlation token:
   - **WebRTC (preferred)** — in-app voice. The token rides in the signalling (SIP header /
     room metadata), so identity is *cryptographically bound to the call*. No phone-number guessing.
   - **PSTN fallback** — dials the hotline. Identity is resolved by ANI → `customer_phones`,
     disambiguated by "is there a pending intent from this customer in the last N minutes?", and
     failing that by a short IVR-entered code. Worst case: anonymous call, IVR identification,
     brief downgraded to intent-only.
6. Telephony adapter emits `call.initiated{correlation_token}` → Orchestrator creates the
   `CallSession`, binds it to the intent, snapshot, customer and product. State → `CONNECTING`.

---

## 5. Data flow B — queue + AI pre-call intake

7. Orchestrator asks Routing for a **target queue** from what it knows so far (product line + app
   context; no speech yet). State → `QUEUED`; position + ETA computed and pushed back to the app.
8. **Consent gate.** The customer is told what happens and consents to (a) recording, (b) AI
   processing, and — separately — (c) health-related content if the intent implies it. Consent is
   recorded in `consents` with scope, basis, timestamp and channel. **No consent → no intake**, and
   the call still proceeds with a context-only brief. (`D14`)
9. **Intake starts** (state → `INTAKE_ACTIVE`) through the configured `IntakeStrategy`:
   - Telephony adapter **forks the media** (Asterisk `AudioSocket`/`externalMedia`, Twilio Media
     Streams, or the app's own mic stream over WebSocket).
   - **Media Gateway** normalises to 16 kHz mono PCM float32 frames, writes the encrypted recording
     to object storage, and fans frames to the transcriber.
   - **Transcription** runs a ring buffer + Silero VAD endpointing; each completed utterance goes to
     the Thai STT engine and is emitted as a `TranscriptTurn` (text, t_start, t_end, confidence,
     speaker role). Turns are persisted incrementally — a dropped call still leaves a usable transcript.
   - **Incremental Analysis** runs debounced (every ~5 s of new speech, and on every utterance end):
     intent classify → entities (hospital, admission date, policy no, claim id, amounts, dates) →
     urgency/sentiment → rolling summary. Each pass writes a **new version** of the `case_brief`;
     the agent screen always shows the newest complete one.
10. **Intake ends** on whichever comes first: customer signals done (silence timeout / phrase / DTMF),
    max duration, or **queue pop**. On queue pop mid-intake, finalise as `partial` immediately.

### The `IntakeStrategy` seam (this is the future-proofing the pitch needs)

All strategies consume audio and produce **the same `IntakeResult`**, so nothing downstream ever
knows which one ran:

```python
class IntakeStrategy(Protocol):
    async def start(self, session: CallSession, media: MediaStream) -> None: ...
    async def on_turn(self, turn: TranscriptTurn) -> None: ...
    async def finalize(self, reason: FinalizeReason) -> IntakeResult: ...
```

| Strategy | Behaviour | Status |
|---|---|---|
| `PassiveRecordIntake` | Listens. Records, transcribes, summarises. The pitched v1. | Phase P3 |
| `GuidedPromptIntake` | TTS asks for the specific slots the detected intent still needs; customer answers; slots fill. | P8 (optional) |
| `ConversationalAgentIntake` | Full-duplex voice agent with barge-in and read-only tool access to the customer's own data. Can genuinely help while waiting. | Future |

Whichever runs, `finalize()` returns transcript turns + audio refs + filled slots + summary +
confidence, and the same `case_brief` is built from it. **This is why intake must stay a strategy and
never be inlined into the orchestrator.** (`D10`)

---

## 6. Data flow C — routing

11. At queue pop (and re-evaluated whenever the intent label changes materially), the **Routing
    Engine** scores candidate agents:
    - **Hard filters:** required skill (product line × intent category), language, licence/authority
      level, `presence = AVAILABLE`, spare capacity, not the customer's blocked-agent list.
    - **Score** (deterministic, weights in config, every term persisted):

      ```
      score = w_skill      * skill_match          # 0–1, from agent_skills vs required skill
            + w_continuity * continuity           # same agent handled this customer/case recently
            + w_priority   * customer_priority    # tier / SLA / vulnerability flags
            + w_fitness    * historical_fit       # this agent's outcomes on this intent
            + w_fairness   * idle_normalised      # longest-idle tie-break, prevents hot-spotting
            - w_load       * current_load
      ```
    - The **LLM never picks the human.** It supplies the *intent label and its confidence*, which feed
      a deterministic, auditable scorer. This is both an explainability requirement and a regulatory
      one — the brief explicitly forbids decisioning that could become discriminatory. (`D8`)
    - The full candidate list with per-term breakdown is written to `routing_decisions`. That row is
      the source of the *"Assigned to: Health Insurance Specialist"* + rationale on the agent screen.
12. Assignment created → state `ASSIGNED` → `RINGING_AGENT`.

---

## 7. Data flow D — the agent screen

13. Agent Desktops hold an authenticated WebSocket and publish presence. On assignment, the desktop
    receives the **CaseBrief bundle** *with or before the ring*, so it is on screen when they answer:

    | Panel (matches pitch p.7) | Source |
    |---|---|
    | Customer identity, ID, verified status | `ContextSnapshot` ← core RO |
    | Reason for Contact + AI summary | `case_briefs` (latest) ← Analysis |
    | Recent Context (last contact, previous inquiry) | core RO `interactions` + our `call_wrapups` |
    | Relevant Policy (status, coverage, room & board, expiry) | core RO `policies.coverage_json` |
    | AI Confidence — intent match % | Analysis calibrated confidence (§9) |
    | Smart Routing — assigned to + why | `routing_decisions` |
    | Next Best Action + Recommended Actions | Analysis, from intent + policy state + playbook |
    | AI Suggested Opening | Analysis (Thai, tone-matched, always editable) |
    | Full transcript + audio player | `transcript_turns` + recording ref |
    | PDPA badges (what's consented / what's masked) | `consents` |

14. Agent answers → media bridged → `IN_CALL`. Live in-call transcription and assist reuse the same
    transcription service with a different session role (roadmap, P8).
15. **Every AI panel is labelled as AI-generated and is editable.** Agents rate brief accuracy
    (👍/👎 + reason) — that feedback is the evaluation signal for prompt/model iteration.

---

## 8. Data flow E — wrap-up and the loop back

16. Call ends → `WRAP_UP`. Analysis drafts a call summary, disposition, and follow-up tasks.
    The agent **confirms or edits** — nothing is written as fact from an unverified model output.
17. Saved to `call_wrapups` + `follow_up_tasks` in **our** store (the core is read-only). This is
    what makes the *next* call's "Recent Context" richer than the bank's own interaction log.
    An optional outbound port can push a summary back to the real CRM when the bank permits it.
18. Metrics roll up: AHT, first-contact resolution, time-to-context, brief-readiness rate,
    intent accuracy (from agent feedback), abandonment, NPS survey hook. These are exactly the
    business outcomes the brief lists, so they are first-class, not an afterthought.

---

## 9. How the confidence number is computed

The pitch shows *"Intent Match: 96%"*. A number on an agent's screen must mean something, so:

```
confidence = calibrate( w1*classifier_prob            # LLM/classifier posterior for the top intent
                      + w2*app_context_agreement      # does speech intent agree with the tapped plan?
                      + w3*entity_completeness        # are the slots this intent needs filled?
                      + w4*asr_quality )              # mean ASR confidence over the decisive turns
```

- Calibrated against a labelled scenario set (isotonic/Platt) so "96%" is *empirically* ~96% right.
- Below a configurable floor the screen shows **"Intent unclear — please confirm"** instead of a
  number, and the recommended actions collapse to generic ones. A confidently wrong brief is worse
  than no brief. (`D13`)

---

## 10. Event backbone

Services communicate through an `EventBus` port (Redis Streams by default, Kafka adapter for
production scale). All events carry `call_session_id`, `trace_id`, `occurred_at`, `schema_version`.

| Event | Emitted by | Consumed by |
|---|---|---|
| `intent.created` | Intent API | Context Assembler |
| `context.snapshot.ready` | Context Assembler | Orchestrator, Agent Delivery |
| `call.initiated` / `call.queued` / `call.state.changed` | Orchestrator | everything |
| `consent.recorded` | Consent | Intake, audit |
| `intake.started` / `intake.finalized` | Intake | Analysis, Orchestrator |
| `transcript.turn` | Transcription | Analysis, Agent Delivery (live view) |
| `analysis.brief.updated` | Analysis | Agent Delivery, Routing (intent changed) |
| `routing.decided` / `call.assigned` | Routing | Agent Delivery, Orchestrator |
| `call.ended` / `wrapup.saved` | Orchestrator / Wrap-up | Metrics |

Consumers are **idempotent** (dedupe on event id) and **replayable** — replaying a call's event
stream must reproduce its final state. That property is what makes the scenario runner (§13) able to
exercise the whole system without a telephone.

---

## 11. Latency budget

The whole product is a race against the queue. Targets, measured per stage and stored on the session:

| Stage | Target | Notes |
|---|---|---|
| Intent → context snapshot ready | **< 1.5 s** | Parallel fan-out to core RO; cached per customer for 60 s. |
| Call connect → media forked | < 300 ms | Fork at answer, not after greeting. |
| Utterance end → transcript turn | **< 1.5 s** | Thonburian medium on GPU; small/distilled if CPU-only. |
| New turns → brief version updated | < 2 s | Debounced; streaming LLM output. |
| Queue pop → brief on agent screen | **< 1 s** | Brief is pre-built and pushed, not fetched on assignment. |
| Customer stops speaking → final brief | **≤ 3 s** | The headline number. |

If a stage blows its budget, it degrades (§12) rather than delaying.

---

## 12. Failure modes & the degradation ladder

Every rung still delivers *something* better than today's blank screen:

| Failure | Behaviour |
|---|---|
| No consent | Context-only brief (identity, policy, recent activity). Intake skipped. |
| STT down / low confidence | Recording kept + context brief; transcript marked unavailable; agent gets audio playback. |
| LLM down / times out | Rule-based brief: intent from the tapped plan, entities from regex/keyword extraction, template summary. |
| Core RO unavailable | Last cached snapshot with a staleness badge; else intent-only brief. |
| Routing unavailable | Default queue, FIFO — i.e. exactly today's behaviour. |
| Agent desktop offline | Brief is emailed/queued to the agent's inbox; call still connects. |
| Media fork fails | Call proceeds normally, intake silently skipped, incident logged. |

**Nothing in this list ever drops or delays the customer's call.** (`D12`)

---

## 13. Testing & the scenario runner

Telephony is the hardest thing to test, so the system is designed to be driven **without it**:

- A `SimulatedTelephonyAdapter` + `ScenarioRunner` replay a scripted call from a YAML file:
  persona, tapped plan, an audio file (or pre-written transcript turns with timings), queue depth,
  agent pool. It drives the real orchestrator, real routing, real analysis, real agent WebSocket.
- **Contract tests** per port: every adapter (including the ones written on hackathon day against
  the real data) must pass the same suite. This is what makes swapping safe.
- **Golden-set evaluation** for AI stages: a labelled set of Thai intake recordings → expected
  intent, entities, and routing target; tracked as a score in CI so prompt changes are measurable.
- Load: the queue + routing path is tested at N concurrent calls with fake media.

---

## 14. Security, privacy, PDPA

The brief makes this a judged constraint, not a nice-to-have.

- **Consent-first.** Explicit consent before any analysis; **health data requires a separate consent**;
  every use of data is tied to a recorded lawful basis.
- **Data minimisation.** The Context Assembler pulls only the fields the current product/intent needs;
  raw transactions are never surfaced — only derived, non-sensitive signals.
- **PII handling.** Detected PII spans in transcripts are masked in the UI by default (reveal is an
  audited action). National ID / card numbers are never rendered in full.
- **Encryption.** TLS in transit (mTLS between services), AES-256 at rest for recordings and
  transcripts, keys in a vault, per-recording key refs.
- **Retention.** Configurable per artifact (recordings ≪ transcripts ≪ briefs); an erasure job honours
  data-subject deletion requests across both stores and object storage.
- **RBAC + audit.** Agents see only their assigned customers; every read of a customer record is
  written to `audit_log` (who, what, when, why).
- **No discriminatory decisioning.** Routing uses skills, availability and stated intent — never
  protected attributes. The weights are inspectable and the rationale is persisted per decision.
- **Cross-org data sharing** (bank ↔ insurer) is modelled as an explicit port with its own consent
  scope, because in reality it must pass a formal data-sharing process.

---

## 15. Deployment shape

- **Dev:** Docker Compose — Postgres, Redis, MinIO, Asterisk, the ReadyCall app, a mock-core seeder,
  the agent desktop dev server, and (optionally) a GPU STT worker.
- **Runtime processes:** `api` (FastAPI/Uvicorn), `orchestrator+workers` (event consumers),
  `media-gateway` (async audio I/O), `stt-worker` (GPU-pinned, batched), all sharing one codebase.
  A **modular monolith with separate entrypoints** — split into real services only when a boundary
  actually hurts. (`D2`)
- **Prod:** Kubernetes; STT workers on GPU nodes with a queue; horizontal scaling driven by
  concurrent-call count; blue/green for prompt/model changes with the golden-set gate in CI.
