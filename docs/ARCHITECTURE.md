# ARCHITECTURE

_How the full ReadyCall system works, end to end. Read this to understand the machine._
_Status: **design only** — nothing here is implemented yet (see `PLAN.md` for the build order)._
_Last updated: 2026-08-19._

---

## 1. Big picture

**Krungsri ReadyCall** turns hold time into preparation time. It is an **AI context layer that sits on
top of** the existing app, the existing contact centre, and the existing core insurance systems — it
changes none of them.

"Insurance" here means **every line** — motor, health, life, travel, personal accident, savings — not
just health. The health/IPD example in the pitch is one scenario among many; the motor-claim scenario
(roadside, after an accident, dialling the number off the policy documents kept in the car) is
arguably the strongest demonstration of the idea, and it is the one that arrives with no app at all.

```
   entry channels                ┌───────────────────── ReadyCall ──────────────────────┐
 ┌──────────────────┐            │                                                      │
 │ in-app: tap      │            │  ┌────────┐  ┌─────────┐  ┌────────┐  ┌───────────┐  │
 │ Contact on plan  │───────────▶│  │ Intent │─▶│ Context │─▶│Matching│─▶│  Agent    │  │
 │ hotline / website│            │  │  API   │  │Assembler│  │ Engine │  │ Delivery  │  │
 │ printed number   │─┐          │  └────────┘  └────┬────┘  └───▲────┘  └─────┬─────┘  │
 │ callback / menu  │ │          │                   │           │             │        │
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
| **Agent Delivery** (`api/ws/agent_ws.py`) | Agent presence, the offer/accept handshake, push of the `CaseBrief` bundle, live updates, acknowledgements. | Hold business state. |
| **Agent Workstation** (`apps/agent_desktop/`) | **The agent's entire job in one browser tab: the softphone itself (WebRTC audio through their headset), the brief, the queue, their status.** There is no separate desk phone. | Be "just a screen". |
| **Consent/PDPA** (`services/consent/`) | Consent capture, scope (incl. separate health-data consent), redaction policy, retention, audit. | Be optional. |
| **Wrap-up** (`services/wrapup/`) | Post-call summary + disposition + follow-ups, human-confirmed, written to our store. | Auto-write unverified content. |

---

## 3. Entry channels and the identity assurance ladder

**The system must work for a call that arrives with nothing.** A driver standing next to a dented car
digs the policy documents out of the glovebox and dials the number on them; they are not going to open
an app first. That path is the base case; the in-app path is the enriched one. (`D19`)

In practice that printed number is **usually the general hotline**, not a per-product line — so the
system must not depend on knowing the product from the number. It asks instead, with a keypad menu
(§6, `D37`). A product-line DID, where one exists, is an *optimisation* that lets us skip a question.

### Entry channels

| Channel | How it arrives | What it gives us for free |
|---|---|---|
| **In-app tap Contact** | WebRTC from the app (or app-initiated PSTN) carrying a `correlation_token` | Verified identity, exact plan viewed, screen context, consent already collected in-app |
| **Product-line DID** | A distinct number printed per product (in the car's document folder, on the health card, on the travel schedule) | **The product line, with no app and no menu** — a dedicated motor-claims number *is* an intent signal. A nice-to-have, never assumed |
| **General hotline** | The main number, from the website or any document. **The common case in Thailand today.** | Only the caller's number — so the keypad menu does the work (`D37`) |
| **Callback** | We ring them (after-hours voicemail, or an in-app "call me back") | Full context — we chose to place the call |
| **Transfer** | Another agent hands the call over | The existing brief travels with it |

DIDs are mapped in `config/dids.yaml` → `{product_line, default_queue, greeting_prompt}`. Adding a
newly printed number is a config line, not code. An entry with `skip_product_menu: true` must name a
real product line — skipping the question while not knowing the answer is how a caller ends up
silently in the wrong queue, and a test enforces it.

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
   OFFERED ──────► IN_CALL ──► WRAP_UP ──► CLOSED
      │                          (no rating state: the customer rates in the IVR within
      │                           seconds of hanging up, while the agent may still be
      │                           writing the wrap-up. The rating is an event that
      │                           attaches to the record whenever it lands — D46)
      │  (offer card + ringtone     │
      │   in the agent's browser;    └──► TRANSFERRED (brief travels with the call)
      │   they press Accept)
      │
      └── declined / offer timeout ──► back to MATCHED (re-match to someone else;
                                        keeps waiting credit + brief; the agent that
                                        missed it is auto-flipped out of READY)
```

**Invariant:** leaving `QUEUED` is driven by *agent availability*, never by AI completeness. If intake
is still running when an agent frees up, intake is finalised as **partial** and the call proceeds.
Waiting on AI to connect a human would invert the entire value proposition. (`D12`)

Note the state is `OFFERED`, not "ringing a phone". There is no phone — the offer arrives in the
agent's browser tab and the ringtone plays through their headset (§9, `D32`).

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

**The menu comes first, and it — not the AI — is what routes the call** (`D37`).

```
[greeting + recording notice]
  "สวัสดีค่ะ ... สายนี้อาจถูกบันทึกเพื่อพัฒนาคุณภาพบริการ"
        │
        ├── STEP 1: which product line?    ← skipped if the app or the DID already said
        │     "กด 1 ประกันรถยนต์  กด 2 ประกันสุขภาพ  กด 3 ประกันเดินทาง ..."
        │
        ├── STEP 2: why are you calling?   ← skipped if the app gave a specific plan
        │     "กด 1 แจ้งอุบัติเหตุ  กด 2 ขอความช่วยเหลือฉุกเฉิน  ...  กด 6 เรื่องอื่นๆ"
        │
        ├── (optional) identify, for full service at L0/L1:
        │     "กรุณากดเลขบัตรประชาชน 4 หลักสุดท้าย" → assurance L3
        ▼
  ►► QUEUE IS NOW KNOWN. Nothing after this point is required for routing. ◄◄
        │
        ▼
[queue position + estimated wait]  "ขณะนี้ท่านอยู่ลำดับที่ 3 ..."
        │
        ▼
[intake offer]  ← the ENRICHMENT layer
  "ระหว่างรอสาย ท่านสามารถเล่าเรื่องที่ต้องการติดต่อไว้ล่วงหน้าได้
   เจ้าหน้าที่จะเห็นข้อมูลทันทีที่รับสาย
   กด 1 เพื่อบันทึกข้อความ   กด 2 เพื่อรอสายตามปกติ"
        │
   ┌────┴─────────────────────────┐
 press 1                        press 2
   │                              │
[beep] record…                 hold music, menu-derived brief
   │
   └─ stops on: press 1 again  ·  silence > INTAKE_SILENCE_TIMEOUT_S (default 6s)
                ·  max duration (default 180s)  ·  agent accepts the offer
```

### Why the menu leads

Follow the worst case through the old design: general hotline (in Thailand usually the
*only* published number), no app, unrecognised caller, declines the recording. The system
knew **nothing** — worse than the keypad menu every call centre already has. Adding AI is
not worth much if the floor sits below the status quo.

So the split is now explicit:

| Layer | Gives | Needs | If it fails |
|---|---|---|---|
| **Keypad menu** (the base) | Product line + reason → **the queue** | Nothing. No AI, no consent, no speech, no network | It cannot really fail; `0` always reaches a human |
| **AI intake** (the delta) | The *detail*: which hospital, which plate, how urgent, what happened | Consent, audio, STT, LLM | Routing is unaffected — it was never the AI's job |

The base is **parity with what already exists**. The AI makes the agent's screen useful; it
does not make the routing possible. Every AI failure now degrades to "a normal, competent
call centre" rather than "a call centre that knows nothing."

### Menu rules (`config/menus.yaml`, enforced by tests)

- **Skip what we already know.** App tap → line and often intent; product DID → line.
  Asking a question we know the answer to is bad service.
- **Reserved keys are consistent everywhere:** `9` repeat, `0` operator. A caller who
  learns `0` in one menu must not be surprised in another.
- **Every reason menu has a catch-all** ("เรื่องอื่นๆ" → `<line>.other`), so an unexpected
  reason lands with the right line's generalist instead of trapping the caller.
- **At most seven options**, because past that people stop listening and press `0`.
- Three unrecognised presses, or silence twice, → the general queue. Never a hang-up.

### Personalised menu ordering

When the caller is recognised (assurance **L1 is enough** — reordering a menu discloses
nothing), their likely options are read out **first** and take the low numbers. Signals:
an open claim in that line, an active policy, a product viewed in the app in the last day,
a renewal due soon. Everything else stays available, just later.

**Reordering only — the spoken line never carries customer detail.** Options are always
just the number and the plain label ("กด 1 ประกันรถยนต์"). Reading someone's plate or policy
number back at them through a menu is unsettling, and it lengthens every option, which is
the opposite of the point. The context we prefetched at §5 decides the *order*; it is
never read aloud.

### Language (`D38`) — modelled, not yet implemented

Thai only today. The shape is agreed so nothing built now blocks it: the call records
**`preferred_language`** (what we speak, what the TTS uses) separately from
**`acceptable_languages`** (the hard filter for matching). They differ because a keypress
says what someone *prefers*, not what they can understand — a bilingual caller who picks
English is still perfectly routable to a Thai speaker. Agents carry per-language **CEFR
levels**, and language is a **hard filter**: past the wait ceiling the matcher drops fit
entirely (`D22`), but "qualified" must still include understanding the caller.

Design points worth arguing about (all runtime-tunable):

- **The recording notice comes first**, before any menu, because it has to.
- **Press 2 is a first-class outcome, not a failure.** The call proceeds with a context-only brief and
  the agent screen says so. (`D19`)
- **Re-offer once.** If the caller pressed 2 and the wait exceeds `INTAKE_REOFFER_AFTER_S` (default
  90 s), offer once more, then never again.
- **Stopping on "agent available" does not cut the caller off mid-sentence.** The *offer window is the
  grace period*: the moment a match happens, an offer card + ringtone appears in the matched agent's
  browser, and intake keeps recording and transcribing until they press **Accept** (typically 5–15 s).
  The brief is finalised during that window and keeps updating in the first seconds of the live call.
  Nobody waits longer, and no sentence is lost. (`D21`)
  If the agent is in **auto-accept** mode there is no offer window, so the tail of the utterance is
  transcribed into the first seconds of the live call instead — same outcome, different timing.
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

`fit(caller, agent) ∈ [0,1]` — hard filters first (required skill, **language at the
required CEFR level** (`D38`), licence/authority, capacity, not blocked), then:

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

Since `D37`, the prior is a **keypress rather than a guess** — the caller told us the line
and the reason. Speech now *refines* the intent instead of having to establish it, which
is why a wrong or missing transcript can no longer send anyone to the wrong queue.

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

## 9. The agent workstation: one browser tab, softphone included

**The agent desktop is not an information screen next to a telephone. It is the whole workstation, and
the call happens inside it.** (`D32`)

The agent opens a browser tab, logs in, plugs in a headset, and from that one tab they:

- **take and hold the call itself** — WebRTC audio in/out through the PC's headset, with mute, hold,
  hangup, DTMF, transfer and conference,
- see the case brief and customer context for whoever they are talking to,
- see the queue and their own position in the rotation,
- set their own status,
- and (later phases) dial out, work a callback list, and look up past customers.

There is no desk phone, no softphone app to install, no second device. That matters practically —
bank agent desktops are locked down, so "just a URL" is the realistic deployment — and it matters for
the demo, because everything a judge needs to see is in one window.

**Must exist from the start:** the softphone, the customer brief, and status control.
**Later:** past-customer lookup, outbound dialling, callback list, wallboard, supervisor view.

### How the audio actually works

The workstation registers as a **WebRTC SIP endpoint** (SIP over WSS to Asterisk's `chan_pjsip`,
driven by SIP.js or JsSIP in the page). The agent's browser is a real SIP peer, so Asterisk bridges
the customer channel to it exactly as it would to a hardware phone. Codec: Opus, with the browser's
own echo cancellation and noise suppression. The workstation also carries a device picker
(mic/speaker), a mic level meter, and a pre-shift **audio self-test**, because "my headset wasn't
selected" is otherwise the classic five-minutes-before-demo failure.

Constraint worth knowing now: browsers only allow microphone access in a **secure context**, and SIP
over WSS needs a certificate Asterisk serves. `localhost` is fine for one machine; agents on other
machines on the LAN need real certs (`mkcert` in dev). This is a P5 landmine, flagged early.

### Two layers of agent state

**System state** (automatic, set by the platform): `OFFLINE` · `AVAILABLE` · `OFFERING` ·
`ON_CALL` · `AFTER_CALL_WORK`.

**Agent intent** (manual, set by the person): `READY` · `BREAK` · `LUNCH` · `TRAINING` · `ADMIN` ·
**`LAST_CALL`** (finish the current call, then stop taking new ones) · **`DRAINING`** (take no new
callers, but stay logged in for anything already committed to me).

```
effective_availability = system_state == AVAILABLE
                         and agent_intent == READY
                         and current_load < max_concurrent
                         and within_schedule(agent, now)
```

Each agent is a separate authenticated browser session holding a WebSocket that publishes a heartbeat.
Presence lives in Redis with a TTL, so a closed laptop drops out automatically; the DB keeps the
durable record in `agent_state_log`.

### The offer/accept handshake, and what happens after a call (`D33`)

```
 AVAILABLE ──match──► OFFERING ──Accept──► ON_CALL ──hangup──► AFTER_CALL_WORK ──► AVAILABLE
                 │        │                                          │
                 │        └── Decline / timeout (default 20 s)       └── "Done" button ends it early,
                 │            → re-match to someone else, and this       or the timer expires
                 │              agent is flipped out of READY so a
                 │              distracted agent cannot black-hole
                 │              the queue (RONA)
                 └── auto_accept mode: connect immediately with a short beep, no click
```

Three things this settles:

1. **Yes, after-call work is real** — dispositions, notes, follow-ups. So a call does **not** drop the
   agent straight back to `AVAILABLE`; it enters `AFTER_CALL_WORK` with a configurable timer
   (`ACW_TIMER_S`, default 45 s) that the agent can end early with **Done** or extend.
   *And this is one of the product's better numbers:* because the AI drafts the wrap-up, ACW should
   shrink measurably — that reduction is a headline metric, not a side effect (§12).
2. **Both of your models are supported, by config, because they suit different moments.**
   - `manual_accept` + ACW timer (**the default**): the agent is explicitly ready, and the Accept
     press is visible and demonstrable on stage.
   - `auto_accept` + `ACW_TIMER_S=0`: the call just connects with a beep and the agent goes straight
     back to available — the "no delay at all" mode busy centres actually use.
   These are per-agent and per-queue settings, so a demo can show both.
3. **The variant where the agent stays `READY` and simply doesn't press Accept is deliberately not the
   default.** It looks equivalent but it isn't: the customer sits on hold while a distracted agent
   decides, and the matcher can't tell "thinking" from "walked away". Explicit availability keeps the
   matcher honest — and the decline/timeout path (RONA) covers the same human situation without
   punishing the caller.

**On the transition itself:** while queued, the customer's channel sits in a holding bridge (hold
music / intake). On **Accept**, Asterisk bridges the customer channel to the agent's browser endpoint.
That is a bridge operation on an already-connected channel — effectively instantaneous — so there is
no dial-out delay between "agent free" and "talking".

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

## 11. Data flow E — what the workstation shows

Agent workstations hold an authenticated WebSocket and publish presence. **The brief arrives with the
offer**, so it is fully on screen while the agent is still deciding to press Accept — they take the
call already knowing who it is and what it is about:

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
| **Call controls** — accept/decline, mute, hold, hangup, DTMF, transfer, device picker | The in-page softphone (§9) |
| **Queue strip** — depth, longest wait, my status, my next-up position | Matching Engine, live |

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
4. Metrics roll up: AHT, **ACW time** (after-call work — the number the AI-drafted wrap-up should
   visibly shrink, §9), first-contact resolution, time-to-context, brief-readiness rate, intent
   accuracy, abandonment, offer-decline/timeout rate, deferral hit rate, CSAT/NPS.

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

### 13.1 The intent taxonomy — what it is and why everything hangs off it

An **intent** is *the reason someone is calling*. The **taxonomy** is the fixed, closed list of those
reasons, organised per product line, living in `config/intents.yaml`.

It has to be a closed list because five separate things key off the intent code, and none of them can
key off free text the model invented:

| Consumer | Uses the intent for |
|---|---|
| **Matching** | `intent → required skill` (`skills.yaml`), which decides who can take the call |
| **Guided intake / slots** | Each intent declares the facts it *needs*; that is what a future AI intake would ask for, and what "entity completeness" scores in §13 |
| **Playbooks** | The recommended-actions list and the next-best-action come from a per-intent playbook, not from the model improvising |
| **Urgency** | Each intent carries a default urgency (an accident-in-progress outranks a renewal question) |
| **Evaluation** | The golden set is labelled with intent codes — that is how "85% intent accuracy" is even measurable |

Each entry looks roughly like:

```yaml
motor.claim.accident:
  label_th: "แจ้งอุบัติเหตุรถยนต์"
  label_en: "Report a motor accident"
  skill: motor.claim
  default_urgency: high
  required_slots: [location, plate_number, injuries, other_party, drivable]
  playbook: playbooks/motor_accident.yaml
```

A rough straw-man of the shape — **this needs the team's domain input, it is a product decision more
than a technical one**, and it is the main input P4 needs:

- **motor** — `claim.accident` · `claim.status` · `roadside_assist` · `policy.coverage` ·
  `policy.renew` · `document.request`
- **health** — `ipd.preauth` (the pitch's own scenario) · `claim.submit` · `claim.status` ·
  `coverage.query` · `network.hospital`
- **travel** — `claim.submit` · `coverage.query` · `policy.extend`
- **life** — `policy.value` · `beneficiary.change` · `premium.payment` · `surrender.query`
- **cross-cutting** — `general.billing` · `general.renewal` · `general.complaint` ·
  `general.update_details` · `general.new_product` · **`unknown`**

Around 20–40 entries is the right size: fine enough that a skill and a playbook are meaningful,
coarse enough that a classifier can be accurate and an agent recognises every label. `unknown` is a
first-class outcome, not a failure — it routes to the product-line generalist.

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
| `matching.decided` / `matching.deferred` | Matching | Agent Delivery, Orchestrator |
| `call.offered` / `offer.accepted` / `offer.declined` / `offer.timeout` | Agent Delivery | Orchestrator, Matching |
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
| No app / no intent (cold call) | Keypad menu routes it (`D37`); ANI gives probable identity; brief built from menu + context at the assurance level reached |
| Caller presses 2 (no recording) | Menu-derived brief (line + reason are still known); agent screen says intake was declined |
| No consent | Same as above; nothing is analysed |
| STT down / low confidence | Recording kept + context brief; transcript marked unavailable; agent gets audio playback |
| LLM down / times out | Rule-based brief: intent from the **menu** (reliable, not a guess), entities by regex, template summary |
| Core RO unavailable | Last cached snapshot with a staleness badge; else intent-only brief |
| Matching unavailable | Default queue, FIFO — i.e. exactly today's behaviour |
| One agent's workstation drops (tab closed, network, laptop asleep) | Presence TTL expires → that agent is simply not available; the matcher routes elsewhere. If it happens mid-offer, the offer times out and re-matches. **Mid-call the audio is a separate WebRTC session, so a UI reload does not drop the call** — the workstation re-attaches to the in-progress call on reconnect |
| The agent's brief/data panel fails but audio is fine | The call still works; the panel shows a retry and the agent works the old way. Audio and data are independent paths on purpose |
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
