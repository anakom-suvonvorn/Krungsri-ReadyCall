# ARCHITECTURE

_How the full ReadyCall system works, end to end. Read this to understand the machine._
_Status: **design only** — nothing here is implemented yet (see `PLAN.md` for the build order)._
_Last updated: 2026-08-18._

---

## 1. Big picture

**Krungsri ReadyCall** turns hold time into preparation time. It is an **AI context layer that sits on
top of** the existing app, the existing contact centre, and the existing core insurance systems — it
changes none of them.

"Insurance" here means **every line** — motor, health, life, travel, personal accident, savings — not
just health. The health/IPD example in the pitch is one scenario among many; the motor-claim scenario
(roadside, after an accident, dialling the number off the windscreen sticker) is arguably the
strongest demonstration of the idea, and it is the one that arrives with no app at all.

```
   entry channels                ┌───────────────────── ReadyCall ──────────────────────┐
 ┌──────────────────┐            │                                                      │
 │ in-app: tap      │            │  ┌────────┐  ┌─────────┐  ┌────────┐  ┌───────────┐  │
 │ Contact on plan  │───────────▶│  │ Intent │─▶│ Context │─▶│Matching│─▶│  Agent    │  │
 │ hotline / website│            │  │  API   │  │Assembler│  │ Engine │  │ Delivery  │  │
 │ sticker on car   │─┐          │  └────────┘  └────┬────┘  └───▲────┘  └─────┬─────┘  │
 │ callback / IVR   │ │          │                   │           │             │        │
 └──────────────────┘ │          │            ┌──────▼───────────┴─┐           │        │
                      ├─────────▶│  Call Orchestrator (state machine, per call)│        │
   ┌──────────┐       │   media  │            └──────┬─────────────┘           │        │
   │ Telephony│───────┘          │                   │                         │        │
   │  (VoIP)  │──────────────────┼─▶┌────────┐  ┌────▼─────────┐  ┌─────────┐  │        │
   └──────────┘                  │  │ Media  │─▶│Transcription │─▶│ Analysis│──┘        │
                                 │  │Gateway │  │  (Thai STT)  │  │  (LLM)  │           │
                                 │  └────────┘  └──────────────┘  └─────────┘           │
                                 └───────────┬─────────────────────────┬─────────────---┘
                                             │                         │
                                  ┌──────────▼─────────┐    ┌──────────▼──────────┐
                                  │  bank core  (RO)   │    │  readycall  (RW)    │
                                  │ customers, KYC,    │    │ calls, transcripts, │
                                  │ policies, claims,  │    │ briefs, matching,   │
                                  │ interactions       │    │ agents, consents    │
                                  └────────────────────┘    └─────────────────────┘
```

Three hard architectural rules follow from that picture:

1. **A plain phone call is the base case.** Everything works with no app, no intent, and no
   transcript. App origin, pre-call intake and AI analysis are *enrichments*, each of which can be
   absent without breaking anything. (`D19`)
2. **The bank's data is read-only to us.** Everything ReadyCall produces lands in *our* store. (`D5`)
3. **Every external system is behind a port.** Telephony, STT, LLM, TTS, the bank's data, blob
   storage, the event bus. Business logic imports `ports/`, never a vendor SDK. (`D3`)

### The three things the product does (from the pitch)

| Pitch step | What it means technically |
|---|---|
| **1. Context-Aware Calling** | The call carries identity + product context. Best case: the tap on *Contact* binds them cryptographically. Worst case: caller-ID lookup plus an IVR menu. Context is assembled *before the phone rings* either way. |
| **2. AI Pre-Call Intake** | While queued, the customer *may* describe the issue. Audio → Thai STT → LLM → structured case brief. Modular, so it can later become a talking AI agent (`D10`). |
| **3. Agent gets a ready screen** | Who, which policy, what they want, what to say, what to do — plus *why* the system thinks so, and how sure it is. |

---

## 2. Components (what each service owns)

| Component | Owns | Never does |
|---|---|---|
| **Intent API** (`api/routers/mobile.py`) | Creating a `CallIntent` from an authenticated app session; issuing the correlation token. | Trust a client-supplied `customer_id`. |
| **Identity Resolver** (`services/identity/`) | Turning whatever the call carries (token / ANI / IVR input) into a customer **plus an assurance level**. | Treat an ANI match as verified. |
| **Context Assembler** (`services/context/`) | Building `Customer360` from the read-only core + our history; freezing it as a `ContextSnapshot` with provenance + freshness. | Write to the bank core. |
| **Call Orchestrator** (`services/call_orchestrator/`) | The per-call state machine; the single writer of `call_sessions.state`; emitting lifecycle events. | Talk to a vendor SDK directly; do AI work inline. |
| **IVR / Voice** (`services/ivr/`) | The spoken menu, consent capture, DTMF handling, queue announcements, post-call rating. Plays **pre-rendered TTS** clips. | Synthesise speech at call time (`D24`). |
| **Media Gateway** (`services/media_gateway/`) | Receiving forked audio **per call leg**, normalising to 16 kHz mono PCM, persisting the encrypted recording, fanning frames out. | Interpret content. |
| **Transcription** (`services/transcription/`) | VAD endpointing, streaming Thai STT, emitting `TranscriptTurn`s — for both the intake and the live agent call. | Decide meaning. |
| **Analysis** (`services/analysis/`) | Intent, entities, urgency/sentiment, summary, next-best-action, suggested opening, confidence calibration, PII spans, **call-progress estimation**. | Choose the agent. |
| **Matching Engine** (`services/matching/`) | The waiting pool, agent presence/capacity, fit scoring, the global match, deferral decisions, and the full rationale. | Use an LLM to pick a human (`D8`). |
| **Intake** (`services/intake/`) | The pre-call experience via a swappable `IntakeStrategy`; produces one `IntakeResult` shape regardless of strategy. | Assume a strategy. |
| **Agent Delivery** (`api/ws/agent_ws.py`) | Agent presence, push of the `CaseBrief` bundle, live updates, acknowledgements. | Hold business state. |
| **Consent/PDPA** (`services/consent/`) | Consent capture, scope (incl. separate health-data consent), redaction policy, retention, audit. | Be optional. |
| **Wrap-up** (`services/wrapup/`) | Post-call summary + disposition + follow-ups, human-confirmed, written to our store. | Auto-write unverified content. |

---

## 3. Entry channels and the identity assurance ladder

**The system must work for a call that arrives with nothing.** A driver standing next to a dented car
dials the number printed on the windscreen sticker; they are not going to open an app first. That path
is the base case; the in-app path is the enriched one. (`D19`)

### Entry channels

| Channel | How it arrives | What it gives us for free |
|---|---|---|
| **In-app tap Contact** | WebRTC from the app (or app-initiated PSTN) carrying a `correlation_token` | Verified identity, exact plan viewed, screen context, consent already collected in-app |
| **Product-line DID** | A distinct phone number printed per product (motor sticker, health card, travel policy) | **The product line, with no app and no menu.** A dedicated motor-claims number *is* an intent signal |
| **General hotline** | The main number, from the website or a document | Only the caller's number |
| **Callback** | We ring them (after-hours voicemail, or an in-app "call me back") | Full context — we chose to place the call |
| **Transfer** | Another agent hands the call over | The existing brief travels with it |

DIDs are mapped in `config/dids.yaml` → `{product_line, default_queue, greeting_prompt}`. Adding a new
printed number is a config line, not code.

### Identity assurance ladder

Identity is not binary. Each level unlocks more of the screen, and the level is **shown to the agent**:

| Level | How reached | Agent screen shows |
|---|---|---|
| **L0 anonymous** | No ANI match, or withheld number | Nothing personal. IVR offers identification. |
| **L1 probable** | ANI matches `customer_phones` | Name + masked details, banner *"identity not verified — please confirm"*. **No policy numbers, no coverage figures.** |
| **L2 strong** | ANI match **and** a pending app intent from that customer within N minutes | Full context, flagged as inferred |
| **L3 verified** | In-app WebRTC token, or IVR verification (DOB / last 4 of citizen ID / policy number) | Everything |

A phone can be borrowed, shared or spoofed, so ANI alone never unlocks policy details. This is both a
security position and a good answer to a judge asking "what if it isn't really them?" (`D20`)

Unresolved identity never blocks the call — it degrades the brief and adds an IVR identification step,
and the agent verifies the old-fashioned way.

---

## 4. The call lifecycle (state machine)

`call_sessions.state`, single-writer = Call Orchestrator. Every transition is appended to
`call_state_transitions` with timestamp + reason, which is what makes the timeline demonstrable.

```
 INTENT_CREATED  (app path only — a cold call starts at CONNECTING)
      │
      ▼
   CONNECTING ──────────► FAILED           (no media, bad correlation, hang-up before answer)
      │
      ▼
      IVR   ─────────────► ABANDONED       (hangs up during the menu)
      │   (identify → product menu → consent)
      ▼
    QUEUED  ─────────────► ABANDONED       (hangs up while waiting)
      │   \               ─► VOICEMAIL     (after hours, or queue closed → callback task)
      │    └── consent ──► INTAKE_ACTIVE ──► INTAKE_COMPLETE ──┐
      │                          │  (agent freed early)        │
      ▼                          └─────────────────────────────┤
   MATCHED ◄────────────────────────────────────────────────---┘
      │
      ▼
   ASSIGNED ──► RINGING_AGENT ──► IN_CALL ──► WRAP_UP ──► RATING ──► CLOSED
                     │                │
                     └── no answer ───┘ (re-match; keeps waiting credit + brief)
                                      └──► TRANSFERRED (brief travels with the call)
```

**Invariant:** leaving `QUEUED` is driven by *agent availability*, never by AI completeness. If intake
is still running when an agent frees up, intake is finalised as **partial** and the call proceeds.
Waiting on AI to connect a human would invert the entire value proposition. (`D12`)

---

## 5. Data flow A — before the call connects

### App path (best case)

1. **In-app telemetry.** While the customer browses, the app posts screen-context events
   (`product_code`, section, dwell) to `POST /v1/app/context-events`. Short TTL, consent-gated. This is
   what produces *"Recent Activity: Viewed hospitalization coverage"* on the agent screen.
2. **Tap Contact** on a plan → `POST /v1/calls/intents` `{product_code, plan_id, entry_screen,
   preferred_channel, consent_flags}`. The customer is identified from the **app session token,
   server-side**; the client never asserts identity. (`D4`)
3. A `CallIntent` is created (`PENDING`, `expires_at` ≈ 15 min) returning
   `{intent_id, correlation_token, dial_target}`.
4. **Context prefetch fires immediately**, asynchronously, off the request path:
   `ContextAssembler.build(customer_id, product_code)` reads the core RO (KYC, active policies +
   coverage, holdings, recent interactions, life-event signals) and our store (previous ReadyCall
   briefs, last agent, open follow-ups), and freezes a `ContextSnapshot` with per-field provenance.
   By the time the phone rings, the non-speech half of the brief already exists. (`D6`)
5. **The app places the call** carrying the token — WebRTC preferred (token in the SIP header / room
   metadata, so identity is bound to the media session), PSTN as fallback.

### Cold-call path (base case)

1. Call arrives on a DID. `config/dids.yaml` gives the product line and default queue immediately.
2. **Identity Resolver** runs the ladder (§3): ANI → `customer_phones`; check for a pending intent;
   otherwise L0/L1.
3. **Context prefetch fires the moment a customer id is guessed** — even at L1, so the data is warm.
   What is *displayed* is gated by assurance level; what is *fetched* is not.
4. IVR runs the menu (§6) to collect product line (if the DID didn't imply it), optional
   identification, and consent.

Either way: `call.initiated` → the Orchestrator creates the `CallSession` and binds intent (if any),
customer (if any), product, snapshot and queue.

---

## 6. Data flow B — the line, the IVR, and pre-call intake

### The spoken flow

Everything the caller hears is a **pre-rendered TTS clip** keyed by a prompt id (`D24`), so the wording
can be edited without a studio and the audio is deterministic and offline-safe.

```
[greeting + recording notice]
  "สวัสดีค่ะ ... สายนี้อาจถูกบันทึกเพื่อพัฒนาคุณภาพบริการ"
        │
        ├─ (if DID didn't imply it) product menu:  "กด 1 ประกันรถ  กด 2 ประกันสุขภาพ  กด 3 ..."
        │
        ├─ (if identity is L0/L1 and the caller wants full service) identify:
        │     "กรุณากดเลขบัตรประชาชน 4 หลักสุดท้าย" → assurance L3
        │
        ▼
[queue position + estimated wait]  "ขณะนี้ท่านอยู่ลำดับที่ 3 ..."
        │
        ▼
[intake offer]
  "ระหว่างรอสาย ท่านสามารถเล่าเรื่องที่ต้องการติดต่อไว้ล่วงหน้าได้
   เจ้าหน้าที่จะเห็นข้อมูลทันทีที่รับสาย
   กด 1 เพื่อบันทึกข้อความ   กด 2 เพื่อรอสายตามปกติ"
        │
   ┌────┴─────────────────────────┐
 press 1                        press 2
   │                              │
[beep] record…                 hold music, context-only brief
   │
   └─ stops on: press 1 again  ·  silence > INTAKE_SILENCE_TIMEOUT_S (default 6s)
                ·  max duration (default 180s)  ·  agent answers
```

Design points worth arguing about (all runtime-tunable):

- **The recording notice comes first**, before any menu, because it has to.
- **Press 2 is a first-class outcome, not a failure.** The call proceeds with a context-only brief and
  the agent screen says so. (`D19`)
- **Re-offer once.** If the caller pressed 2 and the wait exceeds `INTAKE_REOFFER_AFTER_S` (default
  90 s), offer once more, then never again.
- **Stopping on "agent available" does not cut the caller off mid-sentence.** The agent's *ring time
  is the grace period*: the moment a match happens the agent's phone starts ringing (typically
  5–15 s), and intake keeps recording and transcribing until the agent actually answers. The brief is
  finalised during the ring and updates on screen in the first seconds of the call. Nobody waits
  longer, and no sentence is lost. (`D21`)
- **Barge-in** on every prompt (a DTMF press interrupts playback) — otherwise the menu feels slow.
- **A caller who says nothing after pressing 1** gets one re-prompt, then falls back to hold.

### While recording

- Telephony forks the media (Asterisk AudioSocket / `externalMedia`, Twilio Media Streams, or the
  app's mic over WebSocket). **Each call leg is forked separately** — which means speaker identity is
  free and correct, with no diarisation needed (§10).
- **Media Gateway** normalises to 16 kHz mono float32 frames, writes the encrypted recording to
  object storage, and fans frames to the transcriber.
- **Transcription** runs a ring buffer + Silero VAD endpointing; each completed utterance goes to the
  Thai STT engine and is emitted as a `TranscriptTurn` (text, `t_start_ms`, `t_end_ms`,
  `asr_confidence`, speaker role, engine version). Turns are persisted **incrementally** — a dropped
  call still leaves a usable transcript.
- **Incremental Analysis** runs debounced (every ~5 s of new speech, and on utterance end): intent →
  entities (hospital, admission date, plate number, accident location, policy no, claim id, amounts,
  dates) → urgency/sentiment → rolling summary. Each pass writes a **new version** of the
  `case_brief`; the agent screen always shows the newest. (`D7`)

---

## 7. The `IntakeStrategy` seam

All strategies consume audio and produce **the same `IntakeResult`**, so nothing downstream ever knows
which one ran:

```python
class IntakeStrategy(Protocol):
    async def start(self, session: CallSession, media: MediaStream) -> None: ...
    async def on_turn(self, turn: TranscriptTurn) -> None: ...
    async def finalize(self, reason: FinalizeReason) -> IntakeResult: ...
```

| Strategy | Behaviour | Status |
|---|---|---|
| `PassiveRecordIntake` | Listens. Records, transcribes, summarises. The pitched v1. | Phase P3 |
| `GuidedPromptIntake` | Pre-rendered TTS asks for the specific slots the detected intent still needs; the caller answers; slots fill. | P8 |
| `ConversationalAgentIntake` | Full-duplex voice agent with barge-in, streaming TTS, and read-only tool access to the caller's own data. Can genuinely help while waiting — and is the natural after-hours agent. | Future |

Whichever runs, `finalize()` returns transcript turns + audio refs + filled slots + summary +
confidence, and the same `case_brief` is built from it. **This is why intake must stay a strategy and
never be inlined into the orchestrator.** (`D10`)

---

## 8. Data flow C — matching customers to agents

This is the most decision-heavy part of the system, so it is specified precisely.

### The model: one pool, global matching, not per-agent queues

Callers wait in a **single pool per queue group** (not a line per agent). A **matcher** runs whenever
anything changes: an agent becomes available, a caller arrives, a caller's fit changes because new
speech arrived, or a periodic tick (default 1 s).

Per-agent queues were rejected: they cause head-of-line blocking (caller stuck behind a long call in
agent A's line while agent B sits free) and they make waiting-time fairness incoherent. (`D22`)

### Fit

`fit(caller, agent) ∈ [0,1]` — hard filters first (required skill, language, licence/authority,
capacity, not blocked), then:

```
fit = w_skill      * skill_match          # agent_skills vs the intent's required skill
    + w_continuity * continuity           # same agent handled this customer/case recently
    + w_fitness    * historical_fit       # this agent's outcomes on this intent
    - w_load       * current_load
```

**Fit is confidence-weighted.** Early in a call the intent is a guess, so:

```
effective_intent = blend(app_or_DID_intent, speech_intent, weight = intent_confidence)
```

A low-confidence partial transcript nudges fit; it does not yank someone into a specialist queue. Fit
is recomputed continuously as turns arrive, and again at the moment of matching. (`D23`)

### Urgency — what stops starvation

```
urgency = w_wait     * min(1, wait_s / TARGET_WAIT_S) ** WAIT_CURVE
        + w_sla      * sla_breach_risk
        + w_priority * customer_priority      # tier, vulnerability flags
        + w_situation* situational_urgency    # "at an accident scene", "admitted tomorrow"
```

`situational_urgency` comes from the intake analysis and is one of the most demonstrable wins: a caller
at a crash site should not queue behind a routine renewal question.

Above `MAX_WAIT_BEFORE_ANY_AGENT_S` (default 180 s) **fit is ignored entirely** — connect to anyone
qualified. That is the hard anti-starvation guarantee.

### The match itself

```
score(caller, agent) = fit(caller, agent) * (1 + urgency(caller))
```

With *n* waiting callers and *m* free agents, run a **global optimal assignment** (Hungarian /
min-cost max-flow) maximising total score, rather than greedily handing each freed agent its own
favourite. At contact-centre scale (tens of each) this is microseconds, and it is strictly better:
it avoids the case where agent A takes the caller that agent B was uniquely suited to.

This is the direct answer to *"choose customer/agent pairs that maximise the most top fits"*.

### Deferral — holding briefly for a soon-free specialist

The Analysis service maintains a **call-progress estimate** for every in-progress call:

```
progress(call) = f(elapsed / expected_AHT(intent),
                   live-transcript wrap-up cues,      # closing phrases detected
                   the agent's own "wrapping up" button)
→ expected_free_in(agent)
```

The matcher may **defer** a caller — leave them waiting a few more seconds for a much better agent who
is about to free up — only when *all* of these hold:

- the caller's wait is below `DEFER_MAX_WAIT_S` (default 60 s),
- `expected_free_in(better_agent) ≤ DEFER_MAX_HOLD_S` (default 25 s),
- the fit gap exceeds `DEFER_MIN_FIT_GAP` (default 0.25),
- and **no agent is left idle as a result** — a freed agent always takes its best available caller;
  deferral only ever chooses *between* waiting callers.

Every deferral (and every rejected deferral) is written to `matching_decisions` with its reasoning, so
"why did this person wait 12 seconds longer?" always has an answer. If the prediction is wrong, the
deferral times out and normal matching resumes. (`D22`)

### Anti-hot-spotting

Three mechanisms, because "everyone is the best fit for one star agent" is a real failure mode:

1. `- w_load * current_load` and a fairness term inside `historical_fit` normalisation.
2. Skills are defined so that **at least two agents share every skill** — enforced by a config
   validation check that fails startup if a skill has a single holder.
3. A monitored **assignment-concentration metric** per skill (share of assignments taken by the top
   agent); an alert fires past a threshold, and the weights are the knob.

Everything — every candidate, every term, the chosen pair, and any deferral — lands in
`matching_decisions`. That row is what renders *"Assigned to: Health Insurance Specialist"* plus its
rationale on the agent screen.

---

## 9. Agents: state, presence, and the after-hours path

### Two layers of state

**System state** (automatic, set by the platform): `OFFLINE` · `AVAILABLE` · `RINGING` · `ON_CALL` ·
`WRAP_UP` (auto on hangup, timed) · `AFTER_CALL_WORK`.

**Agent intent** (manual, set by the person): `READY` · `BREAK` · `LUNCH` · `TRAINING` · `ADMIN` ·
**`LAST_CALL`** (finish the current call, then stop taking new ones) · **`DRAINING`** (take no new
callers, but stay logged in for anything already committed to me).

```
effective_availability = system_state == AVAILABLE
                         and agent_intent in {READY}
                         and current_load < max_concurrent
                         and within_schedule(agent, now)
```

Each agent is a **separate browser session** on their own machine, authenticated as themselves,
holding a WebSocket that publishes a heartbeat. Presence lives in Redis with a TTL, so a closed laptop
drops out automatically; the DB keeps the durable record.

### Queue hours and after-hours

Queues have schedules (`config/queue_hours.yaml`: business hours, holidays, per-line overrides —
motor claims is plausibly 24/7 while policy servicing is not).

When a queue is closed, or nobody is logged in, the caller is offered:

1. **Leave a message** — this runs **the same intake pipeline**: recorded, transcribed, analysed,
   turned into a brief. It becomes a `callback_task` that lands in the morning queue **already briefed**,
   so the agent calls back knowing everything. (`D25`)
2. **Request a callback slot** at a chosen time.
3. Self-service pointers (claim status, documents) for things that don't need a human.

This is a genuinely strong feature that costs almost nothing: the intake machinery already exists.
It is also exactly the slot the future `ConversationalAgentIntake` fills — after hours, the AI *is*
the first responder, and the human gets a briefed callback.

---

## 10. Data flow D — the live call, both sides transcribed

On answer, media is bridged and **both legs keep being forked**. Because each leg is a separate audio
stream, speaker labels are exact — no diarisation model, no speaker-confusion bugs. (`D26`)

Live-call transcription pays for itself three times over:

| Consumer | Why it needs it |
|---|---|
| **Wrap-up** | A summary written from the actual conversation beats one reconstructed after the fact |
| **Call-progress estimation** | Closing cues in the transcript feed the deferral logic in §8 |
| **Live assist** (P8) | Real-time suggestions, policy lookups, compliance nudges |

It roughly doubles STT load, so it is a feature flag (`LIVE_CALL_TRANSCRIPTION=on|off`) with its own
model setting — a smaller/faster model is acceptable here, since latency matters less than for intake.

The recording notice at the start of the call covers this; the agent screen shows a live "recording &
transcribing" indicator to both the agent and (in-app) the customer.

---

## 11. Data flow E — the agent screen

Agent desktops hold an authenticated WebSocket and publish presence. On assignment the desktop receives
the **CaseBrief bundle** *with or before the ring*, so it is on screen when they answer:

| Panel (matches pitch p.7) | Source |
|---|---|
| Customer identity, ID, **assurance level badge** | `ContextSnapshot` + Identity Resolver (§3) |
| Reason for Contact + AI summary | `case_briefs` (latest) ← Analysis |
| Recent Context (last contact, previous inquiry) | core RO `interactions` + our `call_wrapups` |
| Relevant Policy (status, coverage, room & board, expiry) | core RO `policies.coverage_json` — **data, never model output** (`D16`) |
| AI Confidence — intent match % | Analysis, calibrated (§13) |
| Smart Routing — assigned to + why (incl. any deferral) | `matching_decisions` |
| Next Best Action + Recommended Actions | Analysis, from a per-intent playbook |
| AI Suggested Opening | Analysis (Thai, polite register, editable) |
| Live transcript (intake + call) + audio player | `transcript_turns` + recording ref |
| PDPA badges (what's consented / what's masked) | `consents` |

Every AI panel is labelled as AI-generated and is editable. Agents rate the brief (1–5 + which fields
were wrong) — that feedback is the evaluation signal for prompt and model iteration.

---

## 12. Data flow F — wrap-up, rating, and the loop back

1. Call ends → `WRAP_UP`. Analysis drafts a summary, disposition, and follow-up tasks **from the live
   transcript**. The agent **confirms or edits** — nothing is written as fact from unverified model
   output.
2. Saved to `call_wrapups` + `follow_up_tasks` in **our** store (the core is read-only). This is what
   makes the *next* call's "Recent Context" richer than the bank's own interaction log.
3. **Ratings, both sides** (`D27`):
   - **Customer** — in-app prompt if the call originated in-app (higher response rate), otherwise a
     post-call IVR "กด 1 ถึง 5". Stored as CSAT 1–5 + optional NPS 0–10 + optional voice comment
     (which goes through the same transcription pipeline).
   - **Agent** — rates the *brief* 1–5 with wrong-field tags, plus a call-difficulty flag.
   Together these are the measurement substrate for the pitch's "↑ NPS" and "brief accuracy" claims.
4. Metrics roll up: AHT, first-contact resolution, time-to-context, brief-readiness rate, intent
   accuracy, abandonment, deferral hit rate, CSAT/NPS.

---

## 13. How the confidence number is computed

The pitch shows *"Intent Match: 96%"*. A number on an agent's screen must mean something, so:

```
confidence = calibrate( w1*classifier_prob            # posterior for the top intent
                      + w2*context_agreement          # speech intent vs tapped plan / DID / DTMF menu
                      + w3*entity_completeness        # are the slots this intent needs filled?
                      + w4*asr_quality )              # mean ASR confidence over the decisive turns
```

Calibrated (isotonic/Platt) against the labelled scenario set so "96%" is empirically ~96% right.
Below `CONFIDENCE_FLOOR` the screen shows **"intent unclear — please confirm"** instead of a number,
and the recommended actions collapse to generic ones. A confidently wrong brief is worse than no
brief. (`D13`)

---

## 14. Event backbone

Services communicate through an `EventBus` port (Redis Streams by default, Kafka adapter for
production scale). All events carry `call_session_id`, `trace_id`, `occurred_at`, `schema_version`.

| Event | Emitted by | Consumed by |
|---|---|---|
| `intent.created` | Intent API | Context Assembler |
| `identity.resolved` | Identity Resolver | Context Assembler, Agent Delivery |
| `context.snapshot.ready` | Context Assembler | Orchestrator, Agent Delivery |
| `call.initiated` / `call.queued` / `call.state.changed` | Orchestrator | everything |
| `consent.recorded` | Consent | Intake, audit |
| `intake.started` / `intake.finalized` | Intake | Analysis, Orchestrator |
| `transcript.turn` | Transcription | Analysis, Agent Delivery, call-progress |
| `analysis.brief.updated` | Analysis | Agent Delivery, Matching (fit changed) |
| `agent.presence.changed` | Agent Delivery | Matching |
| `matching.decided` / `call.assigned` / `matching.deferred` | Matching | Agent Delivery, Orchestrator |
| `call.ended` / `wrapup.saved` / `rating.received` | Orchestrator / Wrap-up / IVR | Metrics |

Consumers are **idempotent** (dedupe on event id) and **replayable** — replaying a call's event stream
must reproduce its final state. That property is what lets the scenario runner (§17) exercise the whole
system without a telephone.

---

## 15. Latency budget

| Stage | Target | Notes |
|---|---|---|
| Intent/ANI → context snapshot ready | **< 1.5 s** | Parallel fan-out to core RO; cached per customer 60 s |
| Call connect → media forked | < 300 ms | Fork at answer, not after the greeting |
| Utterance end → transcript turn | **< 1.5 s** | See `INTEGRATIONS.md` §2 for the hardware reality |
| New turns → brief version updated | < 2 s | Debounced; streaming LLM output |
| Matcher tick | < 50 ms | Hungarian over tens×tens is trivial |
| Match → brief on agent screen | **< 1 s** | Pre-built and pushed, not fetched |
| Customer stops speaking → final brief | **≤ 3 s** | The headline number |

Each stage's actual timing is written to `call_sessions.stage_timings_json`. A stage that blows its
budget degrades (§16) rather than delaying.

---

## 16. Failure modes & the degradation ladder

| Failure | Behaviour |
|---|---|
| No app / no intent (cold call) | DID + ANI + IVR menu; context-only brief at the assurance level reached |
| Caller presses 2 (no recording) | Context-only brief; agent screen says intake was declined |
| No consent | Same as above; nothing is analysed |
| STT down / low confidence | Recording kept + context brief; transcript marked unavailable; agent gets audio playback |
| LLM down / times out | Rule-based brief: intent from the DID/menu/tapped plan, entities by regex, template summary |
| Core RO unavailable | Last cached snapshot with a staleness badge; else intent-only brief |
| Matching unavailable | Default queue, FIFO — i.e. exactly today's behaviour |
| Agent desktop offline | Brief emailed/queued to the agent; call still connects |
| Media fork fails | Call proceeds normally, intake silently skipped, incident logged |
| Queue closed / nobody logged in | Voicemail intake → briefed `callback_task` (§9) |

**Nothing in this list ever drops or delays the customer's call.** (`D12`)

---

## 17. Testing & the scenario runner

- A `SimulatedTelephonyAdapter` + `ScenarioRunner` replay a scripted call from YAML: persona, entry
  channel, DTMF presses, an audio file (or pre-written transcript turns with timings), queue depth,
  agent pool with schedules. It drives the real orchestrator, real matching, real analysis, real agent
  WebSocket.
- **Contract tests** per port: every adapter — including ones written on hackathon day against the real
  data — must pass the same suite.
- **Golden-set evaluation** for AI stages: labelled Thai intake recordings → expected intent, entities,
  routing target; tracked as a CI score so prompt/model changes are measurable. The same harness runs
  the **provider comparison** (`INTEGRATIONS.md` §4).
- **Matching simulation**: replay a day of arrivals against a synthetic agent pool to tune weights and
  measure wait-time distribution, fit quality, and assignment concentration — offline, in seconds.
- Load: the queue + matching path at N concurrent calls with fake media.

---

## 18. Security, privacy, PDPA

- **Consent-first.** Explicit consent before any analysis; **health data requires a separate consent**;
  every use is tied to a recorded lawful basis.
- **Assurance-gated disclosure.** Policy numbers and coverage figures are not rendered below L2/L3 (§3).
- **Data minimisation.** The Context Assembler pulls only the fields the current product/intent needs;
  raw transactions are never surfaced — only derived, non-sensitive signals.
- **PII handling.** Detected PII spans in transcripts are masked in the UI by default; reveal is an
  audited action. National ID / card numbers are never rendered in full.
- **Encryption.** TLS in transit (mTLS between services), AES-256 at rest for recordings and
  transcripts, keys in a vault, per-recording key refs.
- **Retention.** Configurable per artifact (recordings ≪ transcripts ≪ briefs); an erasure job honours
  deletion requests across both stores and object storage.
- **RBAC + audit.** Agents see only their assigned customers; every read of a customer record is
  written to `audit_log` (who, what, when, why).
- **No discriminatory decisioning.** Matching uses skills, availability, stated intent and waiting
  time — never protected attributes. Weights are inspectable and every decision is persisted.
- **Cross-org data sharing** (bank ↔ insurer) is an explicit port with its own consent scope.

---

## 19. Deployment shape

- **Dev:** Docker Compose — Postgres, Redis, MinIO, Asterisk, a DB browser (`pgweb`), the ReadyCall
  app, a mock-core seeder, the agent desktop dev server, the customer simulator, and optionally a GPU
  STT worker.
- **Runtime processes:** `api` (FastAPI/Uvicorn), `orchestrator+workers` (event consumers),
  `media-gateway` (async audio I/O), `stt-worker` (GPU-pinned, batched), all sharing one codebase — a
  **modular monolith with separate entrypoints** (`D2`).
- **Prod:** Kubernetes; STT workers on GPU nodes with a queue; scaling driven by concurrent-call
  count; blue/green for prompt/model changes with the golden-set gate in CI.

---

## 20. Reuse beyond insurance

The system is deliberately split into a **generic contact-centre AI layer** and a thin **domain pack**,
because almost none of this is insurance-specific. (`D28`)

| Generic (reusable as-is) | Insurance-specific (the domain pack) |
|---|---|
| Telephony ports + adapters, media gateway, per-leg forking | `CoreDataProvider` adapters + `core_mapping.yaml` |
| Streaming STT, VAD, transcript turns | `config/intents.yaml` (the taxonomy) |
| `IntakeStrategy` framework, IVR + pre-rendered prompts | `config/skills.yaml`, `config/dids.yaml` |
| Call state machine, event bus, degradation ladder | `config/playbooks/` (next-best-actions) |
| Matching engine, presence, deferral, fairness | `prompts/th/*` (the wording) |
| Agent desktop shell, brief versioning, feedback | The `Customer360` field set |
| Consent/PDPA, retention, audit, RBAC | Scenario + persona fixtures |

Swapping those config files and one adapter turns ReadyCall into a context-aware call layer for a
hospital, a government service line, a telco, or an e-commerce support desk. Keeping that boundary
clean is a standing rule, not an aspiration: **nothing insurance-specific may be hardcoded in
`services/`.**
