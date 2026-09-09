# DECISIONS

_Significant engineering decisions and their rationale. Append new ones at the bottom; never silently reverse one without a new entry explaining why._
_Last updated: 2026-09-09._

Format per entry: **Problem → Decision → Reasoning → Alternatives → Tradeoffs → Future.**

`D1–D18` were made during the initial planning session (2026-08-17), before any code existed.

---

## D1. Full system and hackathon demo are two separate projects
- **Problem:** Demo pressure corrupts architecture — shortcuts taken to fit a stage become permanent.
- **Decision:** `FullProject/` designs and builds the real system. `DemoProject/` is a separate repo
  with separate docs that *selects a slice* of it. Design decisions live here; demo-only shortcuts
  live there and must name the full-system piece they stand in for.
- **Reasoning:** The judging criteria include **Feasibility** — being able to show "here's the demo,
  here's the real architecture behind it" is worth more than a demo alone.
- **Tradeoffs:** Some duplicated setup; two git histories to keep tidy.
- **Future:** Demo code that turns out to be genuinely good gets promoted *upward* with a decision entry.

## D2. Modular monolith with multiple entrypoints, not microservices
- **Problem:** The system has genuinely separate runtime concerns (HTTP API, event consumers,
  real-time audio, GPU inference) but a team of five and a hackathon timeline.
- **Decision:** One codebase, one dependency set, several process entrypoints
  (`entrypoints/api.py`, `worker.py`, `media.py`, `stt.py`) sharing `domain` + `ports`.
- **Reasoning:** The hard boundaries that matter (ports/adapters, service modules) are enforced by
  module structure, not by network hops. GPU and audio processes get their own lifecycle without
  paying for distributed tracing, service discovery, and N deploy pipelines.
- **Alternatives:** True microservices — rejected as premature; a single process — rejected because
  GPU inference and async audio must not share an event loop with the API.
- **Tradeoffs:** Shared deps mean the STT worker installs the web stack too.
- **Future:** Any module can be lifted out at a port boundary later; the event bus is already there.

## D3. Ports & adapters for every external dependency
- **Problem:** Telephony, STT, LLM, and *especially the bank's data* are all unknown or likely to
  change — the last one changes on the morning of the hackathon.
- **Decision:** A `Protocol` per external concern in `ports/`; implementations in `adapters/`;
  selection by env var. Business logic never imports a vendor SDK. Every port ships a real, a fake,
  and a null adapter, and a **contract test suite** all adapters must pass.
- **Reasoning:** This is the single highest-leverage structural choice for this competition: it turns
  "the data isn't what we assumed" from a rewrite into a config change.
- **Tradeoffs:** More indirection; a little boilerplate per adapter.
- **Future:** Any real bank system (Genesys, a policy admin API, an on-prem LLM) plugs in the same way.

## D4. Identity is bound server-side at intent creation, never asserted by the client
- **Problem:** The whole value proposition is "the agent already knows who this is". Getting that
  wrong is a security incident, not a UX bug.
- **Decision:** `POST /v1/calls/intents` derives `customer_id` from the authenticated app session. The
  response carries an opaque, short-lived `correlation_token` that the call must present.
- **Reasoning:** Phone numbers are spoofable and shared; app sessions are not. WebRTC carries the
  token in signalling, so identity is cryptographically bound to the media session.
- **Alternatives:** ANI-only identification — never treated as verified; it is one rung of the
  assurance ladder (`D20`).
- **Tradeoffs:** Non-app callers get a weaker identity signal — but **not** a weaker product.
  *(Amended by `D19`: the original wording said non-app callers were "outside the pitched journey".
  That was wrong — the cold call is the base case.)*

## D5. The bank's data is READ-ONLY; everything we produce goes in our own store
- **Problem:** A hackathon will hand us an extract or a read endpoint, not write access to core systems.
  The pitch also promises we *don't change the core insurance system*.
- **Decision:** Two stores. `core` is behind `CoreDataProvider` with a `SELECT`-only DB role.
  Everything ReadyCall generates (transcripts, briefs, routing decisions, wrap-ups) lands in `readycall`
  and is joined by key at read time.
- **Reasoning:** Matches reality and the pitch's own feasibility claim ("an AI context layer on top of
  existing workflows"). Also means we can never corrupt their data.
- **Tradeoffs:** "Recent context" is assembled from two places at read time.
- **Future:** If write-back is ever permitted, it's an outbound port — not a schema change.

## D6. Context assembly starts at *tap*, not at *answer*
- **Problem:** If context loads when the agent picks up, we've saved nothing.
- **Decision:** `intent.created` immediately triggers an async `ContextAssembler.build(...)`, frozen
  into a `context_snapshots` row with per-field provenance and freshness.
- **Reasoning:** The non-speech half of the brief is then ready before the phone even rings; the
  latency budget for the visible path collapses to "push a prepared object".
- **Tradeoffs:** Work done for calls that never connect (mitigated: intents expire, snapshots are cheap).
- **Future:** Prefetch could start on *plan view* rather than *tap* — measure abandonment first.

## D7. Briefs are versioned and immutable, never mutated in place
- **Problem:** The brief changes as speech arrives; agents and auditors need to know what was known when.
- **Decision:** Each analysis pass writes a new `case_briefs` row (`v1` context-only, `v2…` speech-informed,
  final flagged). The screen renders the latest; history is retained.
- **Reasoning:** Makes "how early did we get it right?" measurable, and makes demos reproducible.
- **Tradeoffs:** More rows. Trivial at this scale.

## D8. Routing is deterministic and explainable; the LLM only labels intent
- **Problem:** Which human gets which customer is an auditable, regulated decision. The brief
  explicitly forbids decisioning that could become discriminatory.
- **Decision:** A weighted deterministic scorer over skills, availability, continuity, priority,
  fitness and fairness. The LLM contributes only the *intent label + confidence*. Every candidate's
  full score breakdown is persisted in `matching_decisions` (table renamed with `D22`).
- **Reasoning:** Explainability, reproducibility, testability, and regulatory defensibility. Also a
  strong answer to a judge asking "why did it choose that agent?"
- **Alternatives:** LLM-as-router — rejected: unexplainable, non-deterministic, unauditable.
- **Tradeoffs:** Weights need tuning; edge cases need explicit rules.
- **Future:** Learn the weights from outcome data — but keep the function linear and inspectable.

## D9. STT is re-implemented streaming-first; the reference project is reference only
- **Problem:** The team's earlier scam-detection STT works, but it's a batch file-in/file-out CLI:
  VAD the whole file → write chunks to disk → HF pipeline → CSV. ReadyCall needs text *while the
  customer is still talking*.
- **Decision:** New implementation behind an `SttEngine` port: rolling in-memory buffer, VAD
  **endpointing**, per-utterance transcription in a long-lived GPU-pinned worker, `TranscriptTurn`
  events. Same model family (`biodatlab/whisper-th-medium-combined`). The reference folder is read-only.
- **Reasoning:** Latency and privacy (no PII chunks on local disk) both demand it; the old shape can't
  be adapted into it.
- **Kept from the reference:** VAD parameters (threshold 0.65, min speech 500 ms, min silence 100 ms),
  the ~120 ms/60 ms segment padding, and a repetition guard for Whisper's silence-loop failure mode.
- **Changed:** `silero-vad` as a pip/ONNX dependency instead of a runtime `torch.hub.load` (a network
  fetch during a live call is unacceptable).
- **Future:** CTranslate2/`faster-whisper` conversion of the same checkpoint for latency, once verified.

## D10. Pre-call intake is a swappable STRATEGY, not inline orchestrator code
- **Problem:** v1 records and transcribes, but the intended evolution is a full AI voice agent that
  *talks* to the customer while they wait — and still hands the agent a transcript + summary.
- **Decision:** An `IntakeStrategy` protocol (`start` / `on_turn` / `finalize`) with
  `PassiveRecordIntake` (v1), `GuidedPromptIntake` (TTS slot-filling), and `ConversationalAgentIntake`
  (future). **All produce the same `IntakeResult` and emit the same events**, so nothing downstream
  knows or cares which ran. A `TtsEngine` port exists from day one even though v1 only needs
  pre-recorded prompts.
- **Reasoning:** The user explicitly wants this future open. Building the seam now costs one protocol;
  retrofitting it later costs a redesign of the orchestrator, the brief builder, and the agent screen.
- **Tradeoffs:** A small amount of indirection in v1 that isn't strictly needed yet.
- **Future:** The conversational strategy also needs turn-taking/barge-in, a tool layer with
  **read-only** access to the customer's own data, and much tighter guardrails — all inside the
  strategy, invisible to everything else.

## D11. No LLM framework (no LangChain / LlamaIndex / agent framework)
- **Problem:** The AI work here is a fixed, short pipeline: classify → extract → summarise → assemble.
- **Decision:** Plain Python + the provider SDK + versioned prompt files + Pydantic output schemas.
- **Reasoning:** Full control over what's recorded is what makes the brief traceable and the confidence
  number honest. Frameworks obscure exactly the steps we need to show a judge. (Same lesson as the
  user's other project: a hand-rolled traced pipeline beat a framework.)
- **Alternatives:** LangGraph — reconsider only if the *conversational* intake needs dynamic control flow.
- **Tradeoffs:** We write our own retry/timeout/tracing. Small and worth it.

## D12. The call is never blocked on AI
- **Problem:** Any AI stage can be slow, wrong, or down. A customer waiting longer *because of our AI*
  would invert the entire product thesis.
- **Decision:** Queue pop is driven solely by agent availability. Mid-flight intake is finalised as
  `partial`. Every stage has a timeout and a documented degradation rung (`ARCHITECTURE.md` §12).
- **Reasoning:** It's the product's core promise, and it's the honest engineering answer.
- **Tradeoffs:** More code paths; each needs a test.

## D13. Show a confidence number only when it's calibrated and above a floor
- **Problem:** The pitch shows "Intent Match: 96%". An uncalibrated number on an agent's screen is
  worse than none — a confidently wrong brief costs more time than a blank one.
- **Decision:** Confidence blends classifier posterior, agreement with the tapped plan, entity
  completeness and ASR quality, then is calibrated against the labelled scenario set. Below
  `CONFIDENCE_FLOOR` the UI shows "intent unclear — please confirm" and collapses to generic actions.
- **Reasoning:** Trust is the whole reason an agent looks at the panel at all.
- **Future:** Track agent 👍/👎 to re-calibrate continuously.

## D14. Consent-gated, PDPA-first, health data separately consented
- **Problem:** The brief makes PDPA an explicit constraint; health data is sensitive data requiring
  separate consent; cross-org (bank ↔ insurer) sharing needs a formal process.
- **Decision:** No consent → no intake, no analysis, context-only brief (and the call still works).
  Consent scopes are separate rows with basis + evidence. PII spans masked by default with audited
  reveal. Retention per artifact class + an erasure job across both stores and object storage.
- **Reasoning:** It's judged, it's law, and "we thought about PDPA" is a differentiator in this brief.
- **Tradeoffs:** Real work in P7 that doesn't demo visibly — so it gets *badges on the agent screen*
  to make it visible.

## D15. Redis Streams as the event backbone (Kafka behind the same port)
- **Problem:** Need ordered, replayable, at-least-once fan-out between orchestrator, transcription,
  analysis, routing and the agent socket — without operating Kafka during a hackathon.
- **Decision:** `EventBus` port; Redis Streams default (consumer groups + replay), `KafkaAdapter` for
  production scale, `InMemoryBus` for tests. Consumers are idempotent; replaying a call's stream must
  reproduce its final state.
- **Reasoning:** Redis is already needed for presence/queues/cache. Replayability is what lets the
  scenario runner drive the whole system without a telephone.
- **Tradeoffs:** Redis Streams is weaker on retention/partitioning than Kafka. Fine at this scale.

## D16. Coverage figures, eligibility and prices are DATA, never model output
- **Problem:** The highest-consequence hallucination in insurance is a wrong number about someone's
  cover. The brief also puts underwriting and pricing explicitly out of scope.
- **Decision:** The LLM may reference and phrase policy facts but never produce them. Numbers on the
  agent screen are rendered from `policies.coverage_json`; prompts receive them as given context and
  are instructed to cite, not compute. Closed taxonomies for intents, skills and next-best-actions.
- **Reasoning:** Product safety, regulatory scope, and judge credibility all point the same way.
- **Future:** Automated check in CI: any numeric in a generated brief must trace to a data field.

## D17. Documentation-as-memory (this docs/ folder + root CLAUDE.md)
- **Problem:** Multi-session AI-assisted work loses context; a five-person team loses it too.
- **Decision:** Adopt the structure proven in the user's `music-backlog-adder` project:
  `NEXT_SESSION` / `PROJECT_STATE` / `ARCHITECTURE` / `DECISIONS` / `BUG_HISTORY`, plus `PLAN`,
  `DATA_MODEL` and `INTEGRATIONS` for this project's extra surface area. One `CLAUDE.md` at the
  `krungsri/` root routes to whichever project is being worked on.
- **Tradeoffs:** `CLAUDE.md` lives outside both git repos (the root isn't a repo) — noted in the file.

## D18. Everything is demonstrable: timings, provenance, and rationale are persisted
- **Problem:** Judges score **Impact, Feasibility, Creativity, User insight**. Claims are cheap.
- **Decision:** Every stage writes its timing to `call_sessions.stage_timings_json`; every context
  field carries provenance; every routing decision carries its full score breakdown; every brief
  carries its sources and model/prompt version.
- **Reasoning:** It makes the demo *show* "context was ready in 1.2 s, before the phone rang" instead
  of asserting it — and it's genuinely how you'd debug this in production.
- **Tradeoffs:** More writes per call. Negligible, and it doubles as the metrics substrate.

---

_`D19`–`D31` added 2026-08-18 after the first design review with the user._

## D19. A plain phone call is the base case; app origin is an enrichment
- **Problem:** The original design treated the in-app tap as the entry point. But the most compelling
  insurance call — a motor claim from the roadside, dialled off the policy documents in the car — has
  no app, no intent record, and possibly no identified customer. A design that assumes the app fails
  exactly when it matters most.
- **Decision:** The `CallSession` is primary and `intent_id` is optional. Every stage must work with
  the enrichments absent: no intent, no identity, no consent, no transcript. Entry channels are
  in-app, **product-line DID**, general hotline, callback, and transfer.
- **Reasoning:** Coverage of real behaviour, and it makes the value proposition robust: even a cold
  call gets caller-ID context, a DID-derived product line, and an optional recorded intake.
- **Bonus, where it exists:** a **dedicated number per product line** supplies the line for free, with
  no app and no menu. But the common Thai case is a single general hotline printed on everything, so
  this is an optimisation the system may use and must never depend on — the keypad menu (`D37`) is
  what actually carries the base case.
- **Tradeoffs:** More paths to build and test; identity becomes a spectrum (`D20`).

## D20. Identity is an assurance ladder (L0–L3), and disclosure is gated by it
- **Problem:** Caller ID is not proof — phones are borrowed, shared, and spoofable. But refusing all
  context without hard verification throws away the product.
- **Decision:** Four levels — L0 anonymous, L1 ANI match (probable), L2 ANI + pending app intent
  (strong), L3 app token or IVR verification (verified). Context is *fetched* as soon as a customer is
  guessed; what is *displayed* is gated by level. Below L2, no policy numbers and no coverage figures.
  The level is shown to the agent as a badge.
- **Reasoning:** Security without losing the "warm start". Also a clean answer to "what if it isn't
  really them?"
- **Tradeoffs:** The agent screen needs several disclosure states, and the IVR needs a verification step.

## D21. The offer window is the intake grace period
- **Problem:** If an agent frees up mid-sentence, cutting the caller off loses the sentence; holding
  the agent back to let them finish violates `D12`.
- **Decision:** Match immediately and send the offer to the agent's workstation. Intake keeps recording
  and transcribing until the agent presses **Accept** (typically 5–15 s). The brief is finalised during
  that window and keeps updating in the first seconds of the live call.
- **Reasoning:** Nobody waits longer, no sentence is lost, and the dead time already inherent in the
  handshake is put to work. Removes an ugly either/or.
- **Tradeoffs:** The brief may update while the agent is already greeting, so the UI must animate
  changes rather than swap silently. Under `auto_accept` there is no offer window at all, and the tail
  of the utterance lands in the first seconds of the live call instead — same outcome, different timing.
- *(Amended 2026-08-18: originally worded as "the agent's phone rings". There is no phone — see `D32`.)*

## D22. One waiting pool + global optimal matching, with guarded deferral
- **Problem:** Pure FIFO wastes fit; pure best-fit starves callers and hot-spots one popular agent;
  per-agent queues cause head-of-line blocking.
- **Decision:** A single pool per queue group. A matcher runs on every change (and a 1 s tick) and
  solves a **global optimal assignment** (Hungarian / min-cost max-flow) maximising
  `fit × (1 + urgency)`, where urgency grows with wait time, SLA risk, customer priority and
  *situational* urgency from the intake. Past `MAX_WAIT_BEFORE_ANY_AGENT_S` fit is ignored entirely.
  **Deferral** (holding a caller a few seconds for a soon-free specialist) is allowed only under four
  simultaneous guards, never leaves an agent idle, and is always logged with its reasoning.
- **Reasoning:** Global matching beats greedy per-agent picking; the urgency multiplier is the
  anti-starvation guarantee; deferral captures the user's insight that a nearly-finished call is a
  cheap way to reach a much better agent.
- **Alternatives:** Per-agent queues with re-assignment — rejected (fragmentation, blocking, unclear
  fairness). Pure FIFO — rejected (throws away the whole point). Pure best-fit — rejected (starvation).
- **Anti-hot-spotting:** load penalty + fairness normalisation, a startup check that **no skill has
  only one holder**, and a monitored assignment-concentration metric per skill.
- **Tradeoffs:** More complex than FIFO and needs a simulation harness to tune — which is built (§17).

## D23. Fit is intent-confidence-weighted and continuously recomputed
- **Problem:** Should a partial transcript change routing? Acting on a half-heard sentence can send
  someone to the wrong specialist; ignoring speech until the end wastes the whole waiting period.
- **Decision:** The effective intent is a confidence-weighted blend of the app/DID/DTMF intent and the
  speech-derived intent. Fit is recomputed on every `analysis.brief.updated` and again at match time.
  Low confidence keeps the caller pointed at the product-line generalist.
- **Reasoning:** Continuous improvement without whiplash; the confidence number already exists (`D13`)
  so this costs nothing extra.
- **Tradeoffs:** Fit changing under a waiting caller must not reset their waiting credit — it does not.

## D24. IVR prompts are pre-rendered TTS, not recorded audio and not live synthesis
- **Problem:** Hand-recording Thai prompts makes wording changes expensive; synthesising live adds
  latency, cost, and a network dependency during a stage demo.
- **Decision:** `config/voice_prompts.yaml` holds prompt id → Thai text + voice. A build step
  (`scripts/build_prompts.py`) renders each to a WAV, cached by hash of (text, voice, engine), and
  regenerates only what changed. The call plays cached files. Dynamic sentences (menu option lines,
  the reserved-key hint, and one day the caller's name) are rendered on first use and cached by their
  rendered text, so they are warm within minutes. *(This originally said "queue position, name";
  `D91` deleted the position line.)*
  A checked-in prompt pack is the offline fallback.
- **Reasoning:** Edit wording in a YAML file, hear it a second later, zero call-time latency, works
  with no internet. An admin "prompt studio" page makes on-the-day tuning trivial.
- **Future:** Streaming/live TTS is only needed for `GuidedPromptIntake` and
  `ConversationalAgentIntake` — the same `TtsEngine` port serves both.

## D25. After-hours voicemail runs the full intake pipeline into a briefed callback
- **Problem:** Out of hours, or with nobody logged in, the call is currently just lost.
- **Decision:** Offer to leave a message; run it through the *same* recording → STT → analysis →
  brief pipeline; create a `callback_task` that appears in the morning queue **already briefed**.
- **Reasoning:** The machinery already exists, so the feature is nearly free, and it converts a lost
  call into a prepared one. It is also precisely the slot the future conversational AI agent fills.

## D26. Both call legs are forked separately — no diarisation needed
- **Problem:** The live agent call has two speakers; separating them with a diarisation model is
  error-prone and adds latency.
- **Decision:** Fork each call leg as its own audio stream at the telephony layer; speaker identity is
  then structural, not inferred.
- **Reasoning:** Exact labels, no extra model, no speaker-swap bugs. Live-call transcription then feeds
  wrap-up quality, call-progress estimation (`D22`) and future live assist.
- **Tradeoffs:** Roughly doubles STT load → a feature flag with its own (smaller) model setting.

## D27. Ratings are collected from both sides
- **Decision:** Customer CSAT 1–5 (+ optional NPS and a voice comment that goes through the same
  transcription pipeline) — in-app if the call originated in-app, otherwise post-call IVR keypress.
  Agent rates the **brief** 1–5 with wrong-field tags, plus a call-difficulty flag.
- **Reasoning:** The pitch claims "↑ NPS" and brief accuracy; both need a measurement substrate.
  The agent rating doubles as labelled training/eval data for the AI stages.

## D28. Generic core vs. insurance domain pack — nothing domain-specific in `services/`
- **Problem:** The user wants to reuse this machinery for other projects.
- **Decision:** Everything insurance-specific lives in config and prompts (`intents.yaml`,
  `skills.yaml`, `dids.yaml`, `playbooks/`, `prompts/`, `core_mapping.yaml`, the `Customer360` field
  set, fixtures). `services/` may not hardcode a product line, an intent, or a policy concept.
- **Reasoning:** Turns the system into a general "context-aware contact-centre AI layer" reusable for a
  hospital, a government line, a telco, or an e-commerce desk — at the cost of discipline only.
- **Enforcement:** A lint rule / test that greps `services/` for domain literals.

## D29. Two LLM adapters implemented from day one (Anthropic + OpenAI-compatible); others defined only
- **Problem:** The user wants to compare a frontier model against a Thai-native model directly, and
  not be locked in.
- **Decision:** Implement `AnthropicAdapter` and **`OpenAiCompatibleAdapter`** (a single adapter
  parameterised by base URL + key, which covers Typhoon's hosted API, OpenAI, self-hosted vLLM, Ollama
  and LM Studio at once). Define but leave unimplemented: `GeminiAdapter`. Add
  `scripts/compare_llm.py` to run the golden set through every configured provider and print
  accuracy / latency / cost side by side, plus a runtime switch so a live demo can flip providers.
- **Reasoning:** Two implementations buy five back-ends because so much of the ecosystem speaks the
  OpenAI wire format. The comparison harness turns "which model?" from an argument into a table.

## D30. Thai STT: Thonburian stays default; Typhoon ASR is a first-class alternative
- **Problem:** Typhoon also publishes Thai ASR, with both open weights and a hosted API.
- **Decision:** Keep `ThonburianHfAdapter` as the default (known-good, already proven on this team's
  hardware). Add `TyphoonAsrAdapter` with `mode = api | local` behind the same `SttEngine` port, and
  benchmark both on the same audio in P3 — WER, latency, VRAM — recording the numbers in
  `PROJECT_STATE.md`.
- **Reasoning:** Same seam, so comparing is cheap; picking on measurements beats picking on reputation.
- **Note:** Model names, licences and API pricing must be re-verified at implementation time rather
  than trusted from memory.

## D31. Deliberately no LLM framework — with an explicit trigger to revisit
- **Problem:** "Why not LangChain?" deserves a real answer rather than a preference.
- **Decision:** Plain Python + provider SDK + versioned prompt files + Pydantic output schemas. Adopt a
  *small focused* library (`instructor` / `pydantic-ai`) only if structured-output retries become
  tedious. Optionally `LiteLLM` if provider count grows beyond what `OpenAiCompatibleAdapter` covers.
- **Reasoning:** Our AI stage is four short, independent, individually-timed-out, structured calls —
  roughly 150 lines of asyncio, not a chain. We must persist the exact prompt, exact output, latency,
  tokens and cost per call (`D18`), which means fighting any layer that hides the request. Frameworks
  also bring a heavy, fast-moving dependency tree — a breaking upgrade mid-competition is a lost day.
- **Revisit if:** (a) `ConversationalAgentIntake` needs dynamic branching multi-turn tool control flow
  → **LangGraph** becomes a genuine fit; (b) we build retrieval over policy-wording documents →
  **LlamaIndex** for ingestion/retrieval only, behind our own port. Either change gets a new decision
  entry.
- **Tradeoffs:** We write our own retry, timeout, fallback and tracing. Small, and it is exactly the
  code whose behaviour we need to be able to explain.

---

_`D32`–`D33` added 2026-08-18, correcting a misread of the product._

## D32. The agent workstation IS the phone — one browser tab, softphone included
- **Problem:** Earlier documents described an "agent info screen" and talked about the agent's *phone*
  ringing, as though the call happened on a separate device and our product merely displayed context
  beside it. **That was a misreading of the product.** Splitting the call from the work surface would
  be absurd in practice: the agent would be juggling a handset and a browser, and the system could not
  control hold, transfer, or the moment of connection.
- **Decision:** The agent desktop is a **full contact-centre workstation in one browser tab**, and the
  call happens inside it. The page registers as a **WebRTC SIP endpoint** (SIP over WSS to Asterisk's
  `chan_pjsip`, via SIP.js or JsSIP); audio goes in and out through the PC headset. From that one tab
  the agent takes calls, holds, mutes, transfers, hangs up, sends DTMF, sees the brief and customer
  context, sees the queue, and sets their own status. No desk phone, no installed softphone, no second
  device.
- **Scope:** softphone + brief + status control are **required from day one**. Past-customer lookup,
  outbound dialling, callback list, wallboard and supervisor views come later.
- **Reasoning:** It is how a real contact centre works; it is the only way we can control the
  connection moment (`D33`); "just a URL" is the realistic deployment on locked-down bank desktops;
  and for a demo, everything a judge needs to see is in one window.
- **Consequences:** Browsers require a **secure context** for microphone access, and SIP-over-WSS needs
  a certificate Asterisk serves — `localhost` is fine for one machine, other machines on the LAN need
  real certs (`mkcert`). Codec is Opus with the browser's own echo cancellation. A device picker, mic
  level meter, and a pre-shift **audio self-test** are part of the workstation, not nice-to-haves.
- **Resilience:** the audio session and the data session are independent. A UI reload does not drop a
  live call — the workstation re-attaches to the in-progress call on reconnect; and if the data panel
  fails, the agent still has the call.

## D33. Offer/accept handshake, with after-call work as a real state
- **Problem:** How does a matched call actually reach the agent, and what happens in the seconds after
  a call ends? Two plausible models: (a) auto-enter after-call work, agent manually returns to ready;
  (b) stay ready and let the agent press Accept or "not yet".
- **Decision:** `AVAILABLE → OFFERING → ON_CALL → AFTER_CALL_WORK → AVAILABLE`.
  - The offer is a card + ringtone **in the browser**, with Accept/Decline and a timeout
    (`OFFER_TIMEOUT_S`, default 20 s). Decline or timeout re-matches to someone else and flips the
    missing agent out of `READY` (RONA), so a distracted agent cannot black-hole the queue.
  - After a call, the agent enters `AFTER_CALL_WORK` on a timer (`ACW_TIMER_S`, default 45 s), endable
    early with **Done** or extendable.
    ⚠️ **AMENDED BY `D45`:** "endable with Done" conflated finishing *our* wrap-up form with being
    ready for another call. Saving the form closes the call record; only the person's **Ready**
    click ends `AFTER_CALL_WORK`.
  - **Both of the user's models are supported by config**, because they suit different moments:
    `manual_accept` + ACW timer is the default (explicit, visible, demo-friendly); `auto_accept` +
    `ACW_TIMER_S=0` gives the "connect instantly with a beep" mode busy centres actually use. Per-agent
    and per-queue.
  - On Accept, Asterisk **bridges** the customer channel (already connected, sitting in a holding
    bridge) to the agent's browser endpoint. No dial-out, so the connection is effectively instant.
- **Reasoning:** After-call work is real — and because the AI drafts the wrap-up, *shrinking ACW is one
  of the product's most credible metrics*, so it must be measured, which means it must be a state.
  Model (b) as the sole design was rejected: it looks equivalent but leaves the customer on hold while
  a distracted agent decides, and the matcher cannot distinguish "thinking" from "walked away".
  Explicit availability keeps matching honest; RONA covers the same human situation without punishing
  the caller.
- **Tradeoffs:** More states to test; the ACW timer needs tuning against real behaviour.

---

_`D34`–`D36` added 2026-08-19, at the start of implementation (P0)._

## D34. One project, not two — the DemoProject split is dropped
- **Problem:** `D1` split the work into `FullProject/` (the real system) and `DemoProject/`
  (the stage slice). In practice the build plan already produces a demonstrable slice at
  the end of every phase: P1 is context-aware calling, P2 adds matching and the
  workstation, P3 adds voice. The demo *is* the current state of the system.
- **Decision:** Work only in `FullProject/`. `DemoProject/` is abandoned (it never had a
  commit) and its `docs/` will not be created. What would have been "demo scoping"
  becomes "choose which phase to show, and which scenario to run".
- **Reasoning:** The reason for `D1` was to stop demo pressure corrupting the
  architecture. An iterative build with a scenario runner and a degradation ladder gets
  that protection for free — there is no place for a shortcut to hide, because every
  phase has exit criteria and CI runs the scenarios. A second repo would now just be
  duplicated setup and a second history to keep tidy.
- **Reverses:** `D1`. Kept in place rather than deleted so the reasoning stays visible.
- **Tradeoffs:** Demo-only shortcuts, if any are ever needed, now need marking *in place*
  — a `# DEMO:` comment and a decision entry, rather than living in a separate repo.
  The `# P0:` markers in `scripts/run_scenario.py` are the same idea already in use.
- **Note:** The folder is left on disk untouched; deleting it is the user's call.

## D35. Time and ids are injected, never read from the wall
- **Problem:** Every stage is judged on timing and every scenario replay has to be
  reproducible (`D18`), which is impossible if code calls `datetime.now()` or generates a
  random id wherever it likes.
- **Decision:** A `Clock` protocol (`SystemClock` / `ManualClock`) and a swappable id
  generator (`random_ids` / `DeterministicIds`). Nothing outside `readycall/clock.py`
  imports `time` or `datetime.now`. Ids are prefixed and time-sortable
  (`call_01JQK7M2R4X8ZB3N`) so a log line says what it is at a glance.
- **Reasoning:** It makes a full call lifecycle run in milliseconds with a *plausible*
  timeline, and it makes two runs of the same scenario byte-identical — which is the
  precondition for golden-output comparison of briefs and matching decisions later.
- **Verified:** `test_scenario_output_is_deterministic` renders each scenario twice and
  compares.

## D36. The P0 scenario runner performs lifecycle steps itself, and hands them over
- **Problem:** The P0 exit criterion is a full call from arrival to `closed`, but the
  services that own most of those steps (identity, matching, IVR, intake, analysis) land
  in P1–P4.
- **Decision:** `scripts/run_scenario.py` drives the transitions directly for now, with
  every stand-in marked `# P0:`. As each service lands it takes its step over and the
  marker is deleted. The *real* orchestrator, state machine, event bus, fixtures provider
  and transition log are used throughout — only the edges are fakes.
- **Reasoning:** It proves the spine end to end immediately, and the `# P0:` markers are
  an honest, greppable to-do list rather than hidden scaffolding.
- **Tradeoffs:** The runner temporarily contains logic that belongs in services. Guarded
  by the markers and by this entry; `grep -rn "# P0:" scripts/` is the checklist.

---

_`D37`–`D38` added 2026-08-19 after a design review of the call flow._

## D37. The keypad menu runs FIRST; AI intake is the layer on top
- **Problem:** The design so far leaned on the app tap, the DID, and the AI intake to work
  out why someone was calling. Follow the worst case through: a caller on the general
  hotline (which in Thailand is usually the *only* published number), no app, unrecognised
  number, declines the recording. The system knew **nothing at all** — worse than the
  keypad menu every call centre in the country already has. Adding AI is not worth much if
  the floor sits below the status quo.
- **Decision:** A **DTMF menu runs before the queue**, in two short steps — product line,
  then reason — and the caller is queued to the right place before a word is transcribed.
  The AI pre-call intake then runs *while they wait*, adding the detail a keypad cannot
  capture (which hospital, which plate, how urgent, what actually happened).
  - The menu is **skipped when we already know**: the app tap gives line and often intent;
    a product-line DID gives the line. Asking a question we know the answer to is bad
    service.
  - `config/menus.yaml` holds the tree. Reserved keys are consistent everywhere (`0`
    repeat — **and it is the only one**: `D86` removed the operator key, `D90` moved repeat
    onto it), every reason menu has a catch-all option, and no menu exceeds seven spoken
    options — all enforced by tests.
- **Reasoning:** This re-frames the product honestly. **The base is parity with what
  already exists** — reliable keypad routing that needs no AI, no consent and no speech.
  **The AI is the delta on top**: it makes the agent's screen useful rather than making
  the routing possible. Every failure of the AI layer now degrades to "a normal, competent
  call centre" instead of "a call centre that knows nothing".
- **Bonus, and cheap:** when the caller is recognised (assurance L1 is enough — reordering
  a menu discloses nothing), their likely options are **read out first**: an open claim, an
  active policy in that line, a product viewed in the app yesterday, a renewal due. Same
  menu, fewer options to sit through, and it is the cheapest possible use of context we
  already prefetched.
  **Reordering only.** The spoken line stays just the number and the plain label; customer
  detail is never read aloud in a menu. Hearing your own plate number recited back at you
  is unsettling, and it makes every option longer — which defeats the purpose.
- **Alternatives:** AI-first with no menu — rejected, it makes the floor worse than the
  status quo and bets routing on the least reliable component. Menu-only with no AI —
  that is just today's call centre.
- **Tradeoffs:** Two extra keypresses for callers who would rather just talk. Mitigated by
  skipping the menu whenever we already know, by personalised ordering, and by every menu
  ending in a spoken "เรื่องอื่นๆ" that reaches a generalist. *(Originally "by `0` always
  reaching a human" — `D86` removed that key, and the catch-all does the job.)*
- **Consequence for matching:** the queue is known at `QUEUED` rather than after intake, so
  `D23`'s confidence-weighted intent blend now has a *reliable* prior (the keypress) rather
  than a guess. Speech refines it; it no longer has to establish it.

## D38. Language is modelled now, implemented later; it is a hard filter, and graded
- **Problem:** The service needs Thai and English eventually. Retrofitting language into
  matching and the agent roster later would touch the schema, the matcher and the IVR at
  once — but building it now would slow down the thing that actually matters for the
  hackathon, which is Thai.
- **Decision:** Model it now, ship Thai only.
  - `Language` (th/en) and `CefrLevel` (none/A1…C2/native) in `domain/enums.py`.
  - `AgentLanguage` on the agent roster, with `Agent.speaks(lang, at_least=B1)`.
  - `CallSession.preferred_language` **and** `acceptable_languages`, deliberately separate.
  - `config/menus.yaml` carries a `language_menu` block with `enabled: false`.
- **Reasoning for graded rather than a yes/no flag:** an agent with A1 English cannot hold
  a claim conversation in English, and pretending otherwise produces a worse call than a
  longer wait. So the bar is a *level*, and it can differ per intent — small talk needs
  less than explaining an exclusion clause.
- **Reasoning for preferred vs acceptable:** a keypress tells us what someone **prefers**,
  not what they can understand. A bilingual caller who picks English is still perfectly
  routable to a Thai speaker. Recording only "English" would shrink the eligible agent pool
  for no reason. So `preferred` drives what we speak and what the TTS uses; `acceptable` is
  the matching filter, defaulting to `{preferred}` (the safe read of one keypress) and
  widened from the customer profile or a previous call.
  - This also avoids the clumsy four-option menu ("1 Thai only, 2 English only, 3 both…")
    that asking the question directly would require.
- **Hard filter, not a score:** past `MAX_WAIT_BEFORE_ANY_AGENT_S` the matcher drops fit
  entirely and connects to anyone qualified (`D22`) — but "qualified" must still include
  understanding the caller. A long wait is recoverable; a conversation neither party can
  hold is not.
- **Status:** Thai-only in behaviour. The hackathon will very likely stay Thai-only. The
  standing rule is simply that nothing built in the meantime may assume a single language.

---

_`D39`–`D41` added 2026-08-21, during P1._

## D39. The database layer is deferred until something actually needs persistence
- **Problem:** P0 listed "Postgres schema + Alembic" as a deliverable, but nothing in P0 or
  P1 persists anything across a process. Writing ~15 SQLAlchemy models and a migration
  chain now would mean writing them against a schema still moving under active design.
- **Decision:** Ship the **compose file and the schema/role SQL** (so the boundary in `D5`
  is enforced by grants the day we connect), keep the in-memory repositories behind their
  Protocols, and write the ORM models at **P2**, when agent presence and matching decisions
  genuinely need to outlive a process.
- **Reasoning:** The seams already exist — `CallSessionRepository` and `CallIntentStore`
  are Protocols with dict-backed implementations, so adding Postgres changes one factory
  line. Writing the models before the schema settles would mean writing the migrations
  twice.
- **Tradeoffs:** Nothing survives a restart yet. Acceptable while the only entrypoint is a
  scenario replay that runs in half a second.
- **Trigger to revisit:** the moment two processes need to see the same call, or a demo
  needs to survive a restart. That is P2.

## D40. The queue falls back to the product line before it falls back to "general"
- **Problem:** An in-app caller whose plan we know, but whose specific reason we do not,
  was landing in `q_general` — throwing away the product line we already had.
- **Decision:** Queue selection walks the best evidence available, in order: an explicit
  intent (menu or app screen context) → the **line's catch-all intent** → the DID default →
  `q_general`.
- **Reasoning:** Knowing the line should always beat not knowing it. `health.other` routes
  to the health generalist, who opens with the right context; `q_general` does not.
- **Consequence:** `<line>.other` catch-alls are load-bearing, not decoration — they are
  what makes this fallback land somewhere useful.

## D41. The app's screen context supplies the intent, not just the product
- **Problem:** The pitch's own scenario has Khun Pattheera reading the *hospitalisation
  coverage* page and tapping Contact. Carrying only "Health Plan A" into the call wastes
  the more specific thing we already knew.
- **Decision:** The intent API accepts an intent hint derived from the screen the tap came
  from (`entry.app_intent` in scenarios). The app path therefore answers **both** menu
  questions before the call is even placed, and the menu is skipped entirely.
- **Reasoning:** It is the same "skip what we already know" rule as the DID (`D37`), applied
  one level deeper. It is also what makes the app path visibly better than the keypad path,
  which is the product argument the pitch is making.
- **Guard:** it is a *hint*, weighted like a menu answer rather than treated as certainty,
  and speech can still override it once P4 lands (`D23`).

---

## D42. Assurance is raised mid-call, and the agent's identity control has three outcomes
- **Problem:** `D20` built the assurance ladder as a one-shot decision taken at call arrival.
  But identity is exactly the thing that gets *resolved during the conversation* — the agent
  asks, the caller answers. With no way to raise the level mid-call, an ANI-matched caller
  stayed at L1 forever and the agent could never unlock the policy details they had just
  verbally verified. The ladder had no upward staircase.
- **Decision:** Assurance is mutable during a call, through an explicit control on the
  workstation with **three** outcomes, not two:
  - **Confirmed** — the agent verified by challenge (DOB, last 4 of citizen id, policy no).
    The agent records *which* challenge was used; promotion is to L3.
  - **Not this person** — the ANI guess was wrong. Assurance drops to L0, and the rejected
    `customer_id` is recorded on the session so nothing re-proposes them.
  - **Third party acting for them** — see below; the most common real case after the first two.
- **Reasoning for three:** a daughter calling about her father's claim is neither "confirmed"
  nor "wrong". Forcing that case into a binary makes the agent press *Confirmed*, and the
  audit log then falsely records that the policyholder was verified. The third button exists
  to keep the disclosure log honest, which is the whole point of having one under PDPA.
  Third party = the case context attaches (the agent sees which policy it is about) but
  disclosure stays locked and a playbook step appears to check authority to act.
- **Rejection is not plain L0:** "this is not C000002" is information, not the absence of it.
  It suppresses the guess for the rest of the call and feeds a data-quality signal — a
  rejected ANI match usually means a recycled mobile number sitting stale in the core data.
- **Cost is a re-render, not a re-fetch.** Verified on 2026-08-23: at L1 the frozen snapshot
  already holds the full policy numbers and every `Coverage` figure; only `BriefBuilder`'s
  rendering is gated. Promotion therefore rebuilds a brief from data already in memory. No
  bank-core round trip, no spinner. This is why the assembler was never gated by assurance
  and must not become gated.
- **The gate is server-side, at the wire.** The workstation must receive only what the
  current assurance permits. Sending the full brief and hiding fields in React would put
  someone's coverage one devtools panel away. Promotion = the client re-requests and the
  server renders more.
- **Versioned, not mutated:** promotion produces brief **v2** (`D7`), so the record shows
  what the agent saw before and after, and when it changed.

## D43. The keypad stays live during the call: digits are typed, not spoken
- **Problem:** Mid-call, agents constantly need digit strings — policy number, claim number,
  citizen id, plate, hospital code. Spoken digits over a mobile connection are the single
  worst case for accuracy: short, phonetically confusable, and with no linguistic context for
  either a human or an STT model to constrain them. The result is "ขอโทษค่ะ ทวนอีกครั้งได้ไหมคะ"
  — dead air, which is the exact thing this product exists to remove.
- **Decision:** DTMF is captured on the caller's leg for the **whole** call, not just in the
  IVR. The agent clicks a labelled request ("policy number"), a field appears on both the
  workstation and the caller's prompt, and the digits land as they are typed.
- **Reasoning:** this is `D37` applied a second time. The keypad is the reliable channel and
  the microphone is the lossy one, so the keypad gets the job speech is worst at. It needs no
  AI, so it cannot degrade with one.
- **It doubles as identity verification.** When we already hold the customer's data, a typed
  policy number can be *checked against their actual policies*. A match is strong evidence.
  ⚠️ **AMENDED BY `D44`:** this entry originally said a match "promotes assurance
  automatically ... no subjective confirmation involved". That was wrong, and wrong for the
  exact reason `D42` exists — a match cannot tell the policyholder apart from a daughter
  holding their documents. Promotion is always the agent's attestation; the lookup only
  supplies evidence. `D44` also drops the assumption that the captured digits are a
  known kind of thing.
- **Never capture a full secret.** Last 4 of a citizen id, never the whole number; never a
  card number. For secret-ish challenges the system stores **the outcome only** (matched /
  did not match), never the entered digits. A policy number is not a secret and is stored.
- **Logged as disclosure evidence:** every challenge issued, its type, its outcome and its
  timestamp. This is the record that proves policy details were not handed to a stranger.
- **Implementation note (P5):** the caller's channel must stay in the Stasis app and be
  bridged from within it, or Asterisk stops emitting `ChannelDtmfReceived` once bridged.
  Always-on passive capture, no mode switch — a "digit entry mode" that interrupts the
  conversation would be worse than asking.

---

## D44. Keypad capture is generic; the agent interprets it, and only the agent attests identity
- **Problem:** `D43` described capture as "the agent clicks *policy number*, the customer types
  it". Two assumptions were baked in, and both are unsafe:
  1. **that the customer has the thing we asked for.** They may have a citizen ID card in
     their wallet, a claim SMS on the screen they are calling from, a renewal letter, or
     nothing at all. Someone standing next to a crashed car has whatever was in the glovebox.
     Asking for one specific document and building the feature around it fails the moment
     they do not have it — which is often.
  2. **that a match proves who is holding the phone.** It does not. A daughter calling on her
     father's behalf may legitimately be holding his documents and type his policy number
     correctly. `D43` said a match promotes assurance *automatically* — which reintroduces the
     precise failure `D42` was written to prevent, one decision later.
- **Decision:**
  - **Capture is untyped.** The agent starts capture, the customer keys whatever they have,
    the agent stops it. Raw digits appear on the workstation. That is the whole primitive.
  - **Interpretation is a separate, optional step.** With digits on screen the agent may run a
    lookup — match against this customer's policy numbers, match the last N of a citizen id,
    look up a claim number — or may simply *use the digits themselves* and run nothing.
  - **"The agent handles it" is the default and the only mode we build first.** Named lookups
    are added afterwards, one at a time, as they prove worth automating.
  - **A lookup returns evidence, never an action.** It renders `matched` / `not matched` plus
    what it matched against. It never changes assurance, never unlocks a field, never writes
    to the identity record on its own.
  - **Promotion stays `D42`'s three-way agent control.** Confirmed / Not this person / Third
    party acting for them.
- **Reasoning:** separating *capture* from *interpretation* is what makes the feature survive
  reality. The hard, valuable part — getting digits across a lossy line accurately — works for
  any number the customer happens to have. Everything above it is convenience that can be
  added incrementally without redesigning the primitive.
- **The audit record gets better, not worse.** Instead of one machine assertion, it holds two
  independent facts: *the system matched policy MT-2025-004512* **and** *the agent attested
  third party acting for the policyholder*. A daughter with the right documents is now
  recorded as exactly that, rather than as a verified policyholder.
- **New consequence — the safe default inverts.** `D43` could say "a policy number is not a
  secret, so store it". With untyped capture **we do not know what the digits are**, so raw
  captures must be treated as potentially sensitive by default: masked in transcripts and
  logs, short retention, discardable with one click. Only once a lookup names the value may it
  be stored in the clear, and secret-ish challenges still store the outcome only.
- **Tradeoff:** one more click for the agent in the common case, and a capture whose meaning
  is not machine-known. Worth it — the alternative is a feature that only works when the
  caller happens to be holding the one document we guessed.

## D45. After-call work ends when the person says so, never when our form is saved
_Revised 2026-08-23, same day, after reading the diagrams back. Three clauses in the first
version were wrong; they are struck through below rather than deleted, because the reasoning
that replaced them is the useful part._

- **Problem:** `D33` ended `AFTER_CALL_WORK` on the agent pressing **Done** in the wrap-up
  form, or on a timer. Both assume the agent's remaining work lives *inside this system*. It
  does not — real agents have other tabs, other internal tools, paper forms, a colleague to
  ask, a note to write. "I finished your form" and "I am done with this call" are different
  statements, and only the second one is about availability.
- **Decision:**
  - Saving the wrap-up form **closes the call record**. That is our system's work finishing.
  - **`AFTER_CALL_WORK` is measured from the moment the media disconnects**, not from any form
    action. The clock starts when the customer hangs up, full stop.
  - **ACW ends when the agent declares what they are doing next — *any* next state**, not only
    Ready. Ready, Break, Lunch, Training, Admin all end it.
  - **Saving and declaring are independent, and may happen in either order.** An agent may
    finish their outside work first and then press **Save & Ready** in one go; or save the
    record immediately, do the outside work, and declare afterwards. Both are normal.
  - The workstation offers a combined **Save & Ready** button, so the common case is one click.
- **~~The ACW timer may auto-save the record so a call cannot hang open forever.~~ WRONG, removed.**
  Three things were confused here:
  1. It **flatly contradicted `D33`'s own rule** that the summary is *pre-filled, never
     auto-saved, the agent owns the record*. Both sentences appeared in the same diagram.
     An auto-save writes an unreviewed AI draft into a customer's file — the exact liability
     the pre-fill rule exists to prevent.
  2. **"The call cannot hang open" was a borrowed worry that does not apply.** The customer has
     already hung up; no media, no channel, no resource is held. The only thing still "open" is
     a row in our own database. Nobody is waiting on it.
  3. An unsaved wrap-up is **honest data**. It records that this call was never wrapped up,
     which is a true and useful fact. Auto-saving replaces it with a fabricated one.
  If stale rows genuinely need cleaning up later, the fix is a janitor that marks them
  *abandoned wrap-up* — recording that nothing was written, never inventing content.
- **~~Only the person clicking Ready ends ACW.~~ Too narrow, corrected above.** An agent who
  saves the record and then goes to lunch has finished their after-call work; they are simply
  not available. Ending ACW only on *Ready* would show them sitting in after-call work for an
  hour, which is both false and a metric nobody could use.
- **~~The timer must never auto-ready.~~ Still true, and now the timer does nothing else.**
  Any timer here is purely a *visibility* device: after a threshold it raises a long-ACW
  indicator for the agent and their supervisor. It writes nothing and changes no state.
- **This is exactly what the two axes are for.** When the agent declares:
  - `system_state`: `AFTER_CALL_WORK → AVAILABLE` — the platform has no work for them.
  - `agent_intent`: whatever they chose — `READY`, `BREAK`, `LUNCH`, `TRAINING`, `ADMIN`.
  - Offerable is still `AVAILABLE` **and** intent in (`ready`, `last_call`), which
    `AgentPresence.is_available` already enforces. Lunch ends ACW *and* stays un-offerable,
    with no new state and no special case.
- **Reasoning for the core rule:** auto-ready trades an honest metric for a customer-visible
  failure. If the platform marks an agent available while they are mid-task in another system,
  the next caller rings an empty desk for a full offer timeout and is then re-matched — RONA.
  A slightly worse utilisation number is much cheaper than a caller waiting for nobody.
- **It makes the headline metric truer.** Shrinking ACW is one of this product's most credible
  claims (the AI drafts the wrap-up). Measuring only "time in our form" would let us shrink the
  number without shrinking the agent's actual work. Disconnect-to-declaration captures the real
  thing, including the systems we do not own — which is what workforce planning wants anyway.
- **Known risk, deliberately not solved by automation:** an agent who walks away and declares
  nothing. ACW simply keeps running, which is the honest signal, surfaced as a long-ACW
  indicator. The platform never asserts a state the person did not choose.
- **Edge case:** declaring Ready *before* saving leaves an open draft while a new call may
  arrive. Drafts are per-call and persist, and the workstation warns rather than blocking —
  an agent who wants to wrap up two calls at once is doing something unusual, not something
  forbidden.
- **Scope:** this changes the **agent presence** model only. `CallState` is untouched — the
  call's own lifecycle is independent of whether the agent is ready for the next one.
  (Reviewing this decision also exposed that `WRAP_UP → RATING` was ordered backwards —
  raised as `Q14` and resolved by `D46`, which deletes the rating state entirely.)

## D46. A rating is an event attached to a call, not a state the call passes through
- **Problem:** the table said `WRAP_UP -> RATING -> CLOSED`, and `run_scenario.py` even
  transitioned into `RATING` with the reason `wrapup_saved`. That asserts an ordering which
  is false in wall-clock time: the customer rates in the IVR **within seconds** of hanging
  up, while the agent may still be writing the wrap-up three minutes later. The model had no
  honest answer for when `RATING` was entered, no place for a rating that arrives late, and
  no place at all for the agent's own rating (`D27`), which happens during or after wrap-up.
  Most callers also simply hang up without rating, which the ordering treated as an
  exception rather than the common case.
- **Decision:** `CallState.RATING` is deleted. `WRAP_UP` goes straight to `CLOSED`. A rating
  arrives as a `RatingReceived` event and attaches to the call record by `call_session_id`
  **whenever it lands — including after the call is closed.** A `Rating` domain model carries
  either side, distinguished by `RatingSource` (`customer_ivr` / `customer_app` / `agent`).
- **The rule this establishes:** *call state describes the call's progress; it never claims
  data completeness.* `CLOSED` means the call is over, not that every fact about it has
  arrived. That was already true for analysis output and finalised transcripts — the rating
  was the one place we pretended otherwise.
- **Alternatives considered:**
  - **A join: close only once wrap-up *and* rating have settled.** Buys a `CLOSED` that
    genuinely means "nothing more is coming". Rejected because it smears state across boolean
    fields *outside* the enum — exactly what a single transition table exists to prevent —
    so `assert_can(from, to)` would stop being sufficient to validate a transition. It also
    needs an arbitrary rating timeout, and nothing in this system actually requires that
    guarantee.
  - **Orthogonal regions** (true concurrent agent-side and customer-side sub-states). Most
    theoretically correct, and it would cost the flat dict plus the single `assert_can`
    lookup that make the machine self-validating and explainable. Overkill here.
  - **Fixing only the reason string** (`wrapup_saved` -> `rating_prompted`). One line, and it
    relabels the problem without fixing it.
  - **Also removing `WRAP_UP` from `CallState`**, on the grounds that `D45` put after-call
    work on the agent. Rejected as over-correction: the *call record* has a legitimate
    wrap-up phase (an interaction is not complete until dispositioned) and the *agent* has an
    ACW period. Two different entities, two clocks, both real.
- **Cost:** four lines and a `Rating` model. No test asserted on the state and no scenario
  referenced it, because the scenarios always asserted `state: closed`.
- **It improves the demo, which was a surprise.** The timeline used to print
  `rating -> closed` at a fictional moment. The event carries its real timestamp, so the
  scenario now prints `csat=4/5 via customer_ivr at +297.5s` alongside
  `call closed at +327.5s - the rating landed 30.0s earlier`. That is a truer story, and a
  better one: it shows the customer finishing while the agent is still working.
- **Guarded by `tests/unit/test_rating_is_not_a_state.py`**, which pins that the state is
  gone, that `WRAP_UP`'s targets are exactly `{CLOSED, FAILED}` so no waypoint can be slipped
  back in, and that a rating arriving two minutes after closure neither errors nor reopens
  the call.

## D47. The customer simulator is one static page, and its login is a marked shim
- **Problem:** the customer side of the demo needs to (a) authenticate as a customer and
  (b) be something a judge can watch. Neither is our product. Building a real identity
  provider is out of scope — the competition brief puts core-system changes out of scope
  and we are a context layer, not an auth vendor — and a React app for a three-screen
  simulator buys nothing but a build step that can fail.
- **Decision, two parts:**
  1. **`apps/customer_sim/index.html` is a single file with no build step.** No npm, no
     bundler, no `node_modules`. FastAPI serves it directly, so
     `uv run python -m readycall.entrypoints.api` is the entire setup. The React decision
     (`D32`) still stands for the **agent workstation** at P2, which genuinely needs
     component state, a websocket and a softphone; the simulator needs three screens and a
     fetch call.
  2. **The persona picker is a `DemoSessionStore`, behind `demo_login_enabled`**, and is
     marked `DEMO` in place rather than hidden in a separate repo (`D34`). It stands in for
     the bank's login and nothing else.
- **What is *not* faked, and this is the point:** the *shape* is real. A token is issued
  server-side, the mapping to a customer lives on the server, the cookie is HttpOnly, and
  `/v1/calls/intents` **cannot tell a demo session from a real one** because it only ever
  asks a `SessionResolver`. Swapping in Krungsri's OIDC changes one adapter.
- **The simulator talks only to the public `/v1` API.** No privileged back door, no
  simulator-only endpoint. That is what makes "replace this page with the real Krungsri
  app" a true statement rather than an aspiration — and the page shows its own request log
  so a judge can see there is nothing else.
- **Persona *ids* live in `config/demo_personas.yaml`; everything displayed is read live
  through `CoreDataProvider`.** Two consequences worth having: the picker cannot drift from
  the data an agent would actually see, and the file stays free of names and policy numbers,
  so it is safe to commit.
- **Rejected: adding `list_customers` to `CoreDataProvider`** so the picker could browse.
  The port models what the *system* needs — lookup by id and by phone — and a browse method
  would oblige every future adapter, including one written under time pressure on hackathon
  morning, to implement something only a demo uses.
- **Guard:** `demo_login_enabled=False` removes the router entirely (verified by test), so
  the endpoints 404 rather than merely refusing. Login also accepts *only* configured
  personas, so it can never become "log in as any customer id you can guess".
- **Tradeoff:** the simulator will not scale into a real app, and is not meant to. If it
  ever needs to, it gets rewritten in React alongside the workstation — by which point the
  API it calls is unchanged, which is the whole argument.

## D48. The app asks *why* before placing the call, so an app caller skips the IVR entirely
- **Problem:** `D41` said the screen a customer tapped Contact from supplies the intent. That
  works for the pitch's own scenario — reading the hospitalisation page implies an IPD
  question — but it only works when the screen happens to imply something. A customer sitting
  on a generic plan-detail page has told us the **product line** and nothing about the
  **reason**, so they still had to answer the second question on the keypad. Worse, the
  simulator was fudging it: each persona carried a hardcoded `app_intent`, which pretended the
  app knew the reason without ever asking.
- **Decision:** tapping *Contact* opens a **reason sheet** in the app. The customer picks
  from the same list the IVR would read out, and the chosen intent travels with the call.
  Both menu questions are then answered before the phone rings, and an app caller skips the
  IVR completely.
- **One menu, two surfaces.** `GET /v1/app/contact-reasons?product_line=…` reads
  **`menus.yaml`** — the exact file the IVR reads (`D28`), including the keypad digit for
  each option. The app shows `1 แจ้งอุบัติเหตุรถยนต์` because the phone would say
  *"กด 1 แจ้งอุบัติเหตุรถยนต์"*. If the app kept its own list, a customer would get different
  options depending which door they came through and the taxonomy would quietly fork in two.
  A test asserts the two lists are identical, in order, with the same keys.
- **Reasoning — it is strictly better UX, not just faster.** Five options take about a second
  to read on a screen and roughly thirty to hear in an earpiece, and a screen lets you scan
  and go back. This is the same instinct as `D37` inverted: the keypad beats speech *on the
  phone*, and a screen beats the keypad *when there is a screen*.
- **A free UX win we did not go looking for:** the sheet is a **deliberate barrier against
  an accidental call**. A single tap on *Contact* previously placed a call; now there is a
  second, cheap, cancellable step, and tapping outside the sheet dismisses it. Contact
  buttons sit next to other buttons in a banking app, and a mis-tap that dials a call centre
  is a bad minute for the customer and a wasted slot for an agent.
- **The customer may still have no reason to give**, and that is fine: "Contact us — something
  else" opens the general menu, and skipping the sheet entirely falls back to the keypad,
  which is exactly the cold-call path (`D19`). Nothing here is load-bearing.
- **Consequence for the plan list:** the simulator now lists the customer's *actual* policies
  through `CoreDataProvider`, with the count they really hold — two for the SME owner, none
  for the customer whose only policy has lapsed. Policy numbers are masked even in the
  customer's own list, because a policy number on screen is a disclosure regardless of who is
  looking. Previously it showed three fixed rows named *Documents*, *Claim history* and
  *Coverage details*, which are pages, not plans.

_`D49` added 2026-08-24, during P2a._

## D49. The Hungarian solver is ours, and the greedy gap is measured rather than asserted
- **Problem:** `D22` chose global optimal assignment over greedy best-first. Two things were
  left open: what implements it, and how much it actually buys.
- **Decision on the implementation:** written in `services/matching/solver.py`, ~90 lines,
  rather than adding `scipy` for `linear_sum_assignment`. Same reasoning as `D31` on LLM
  frameworks — a ~30MB dependency that would also land on the STT box, to avoid one
  well-understood O(n³) algorithm on a matrix of tens by tens, where constant factors are
  irrelevant. Owning it also means the rationale we persist is genuinely ours to explain.
- **Decision on the claim:** `scripts/run_matching.py --compare` scores Hungarian **and**
  greedy on the same matrix, so "greedy is worse" is a measurement, not a slogan.
- **What the measurement actually said**, across seven seeds at 25 waiting calls:

  | seed | greedy leaves on the table |
  |---|---|
  | 7, 42 | **0.0%** — identical assignment |
  | 13 | 0.1% |
  | 2024 | 0.5% |
  | 1 | 0.7% |
  | 99 | 5.1% |
  | 123 | **8.2%** |

  So the honest claim is **not** "greedy is bad". It is: *greedy is usually fine and
  occasionally meaningfully worse, and it is worst exactly when agents are scarce relative
  to skill diversity* — which is precisely when routing matters most. An 8% worse assignment
  during a staffing crunch is a real cost; the same algorithm costs nothing when the centre
  is quiet. Keeping the comparison in the tool means this can be re-checked against real
  volumes rather than trusted.
- **A bug worth recording even though it never shipped:** the first implementation returned
  an **empty matching on every input** — a Python tuple-assignment order bug in the
  augmenting-path backtrack (`j0, p[j0] = way[j0], p[way[j0]]` rebinds `j0` before `p[j0]`
  is resolved). No exception; every caller simply came back `no_candidates`, which is
  indistinguishable from "nobody was available". Only known-optimal small cases catch that
  class of failure, so `test_matching.py` now pins several, including one asserting that a
  solvable matrix produces *some* assignment.
- **Tradeoff:** we own an algorithm we must maintain. Mitigated by the property test —
  Hungarian must never score below greedy, over pseudo-random matrices — which is cheap and
  catches almost any regression.

## D50. "Unplaced" is two outcomes, not one, because they demand opposite responses
- **Problem:** the matcher had a single `no_candidates` outcome for every caller it could not
  place. Two genuinely different situations collapsed into it: *nobody online holds the required
  skill or language*, and *qualified agents exist but every one of them was won by a
  higher-scoring call this tick*. Shipped, this was actively misleading — see `B4`, where twelve
  of seventeen callers were told no qualified agent existed while the decision record listed one.
- **Decision:** `MatchKind.NO_CANDIDATES` is replaced by **`NO_QUALIFIED_AGENT`** and
  **`ALL_QUALIFIED_BUSY`**, selected by whether any candidate in that row passed every hard
  filter. Each carries its own Thai rationale; the busy one states how many qualified agents are
  occupied. An empty floor is reported as `NO_QUALIFIED_AGENT` with its own wording, since the
  response there is "get anyone online", not "get this skill online".
- **Reasoning:** the whole justification for storing a decision per call, including the ones we
  chose not to assign (`D22`, `D18`), is that someone can ask *"why is this caller still
  waiting?"* and get a true answer. A roster gap and a capacity shortfall lead a supervisor to do
  opposite things — retrain versus staff up — so an outcome that cannot tell them apart is not
  an explanation, it is noise wearing an explanation's clothes.
- **Consequence beyond the label:** the same distinction now drives the starvation summary in
  `scripts/run_matching.py`, which previously *asserted* that every caller past the wait ceiling
  had failed a hard filter. That claim is false on `--seed 123`. Reported facts are now read off
  the stored decisions rather than reasoned about in the printer.
- **Alternatives:** keep one kind and put the nuance only in the rationale string — rejected,
  because a free-text Thai sentence cannot be aggregated, alerted on, or charted, and the whole
  point is that a supervisor sees "eleven callers waiting on a skill gap" at a glance. Add a
  boolean `had_qualified_candidates` flag beside the existing kind — rejected as a second source
  of truth for one fact.
- **Tradeoffs:** one more enum member for consumers to handle, and any future dashboard must
  treat both as "unplaced" when counting. Cheap next to the misdirection it removes.
- **Future:** `ALL_QUALIFIED_BUSY` is the natural trigger for a "we are short-staffed **right
  now**" signal, and the count it carries is already the number that matters. A roster gap that
  persists across ticks is a different alert on a much slower clock.

## D51. Signing in is not being ready, and the platform writes the person's axis exactly twice
- **Problem:** `D33` gives every agent two axes — `system_state` (the platform's) and
  `agent_intent` (the person's) — but never said what happens at the two moments where the
  platform *needs* the person's axis to change and the person is not there to change it:
  the instant they sign in, and the instant an offer times out with nobody answering (RONA).
  Left unspecified, the tempting answers are "sign in as READY" and "set them to BREAK",
  and both put words in the agent's mouth.
- **Decision:**
  - A new intent value **`NOT_READY`**, which an agent may **not** declare for themselves.
  - **Sign-in sets `AVAILABLE` + `NOT_READY`.** The workstation shows a prominent Ready
    button; declaring is one click and is the person's own statement.
  - **RONA sets `NOT_READY`** too, and both writes are recorded in `agent_state_log` with
    `set_by="platform"` and a distinct `reason` (`signed_in`, `rona_missed_offer`).
  - The *reason* lives in the log, not in a second enum value. "Not taking calls" is one
    state however it was arrived at; splitting it into `NOT_READY` and `NOT_RESPONDING`
    would put the same fact in two places and force every consumer to know both.
- **Reasoning:** this is `D45`'s rule — *the platform never asserts a state the person did
  not choose* — applied to the two gaps it did not cover. Starting an agent READY hands a
  call to someone who just opened the tab and is making coffee; the caller pays for that
  with a full offer timeout. Setting them to BREAK after a missed offer fabricates a reason
  and quietly corrupts the one dataset workforce planning actually uses.
- **Why `set_by` earns its column:** "they chose break" and "we stopped offering because
  nobody picked up" are very different facts about the same person, and a supervisor
  looking at a shift needs to tell them apart. Without the field they are indistinguishable.
- **An agent still cannot declare `NOT_READY` for themselves** — the service rejects it. A
  person saying "I am not ready" is really saying break, lunch, training or admin, and
  collapsing those into one value throws away the only thing the log is good for.
- **Correction to `D45` while implementing this:** its text says offerable is "`AVAILABLE`
  and intent in (`ready`, `last_call`)". That is a slip in the prose. `LAST_CALL` means
  *finish the one I am on, then stop*, so it must **not** be offered a new caller — which is
  what `AgentPresence.accepts_new_callers` has always done. The code was right; the
  sentence was wrong. Asserted now by a test rather than left to be re-derived.
- **Tradeoffs:** one more enum value, and an agent who signs in and starts working without
  pressing Ready sits idle. That is visible on their own screen and is the safe direction to
  fail in.

## D52. An agent who declined or missed a call is excluded from re-matching it
- **Problem:** the matcher solves the whole waiting pool globally every tick and has no
  memory. On the tick after a decline or a RONA timeout it re-computes the same matrix,
  reaches the same optimum, and offers the same caller to the same desk. The caller watches
  one agent not answer, forever, and every individual decision is defensible.
- **Decision:** `AssignmentService` keeps the set of agents who have already been offered
  and rejected each call, and `WaitingCall.excluded_agent_ids` feeds it into the matcher as
  a **hard filter** returning `"already_offered"`. Both a decline and a timeout exclude;
  a *cancel* (the caller hung up) does not, because nobody did anything wrong.
- **Reasoning for a hard filter rather than a penalty:** it is the same argument as `D22`.
  A penalty large enough to work is a hard filter with extra steps, and a penalty small
  enough to be a penalty re-offers the call as soon as everyone else is busy. More
  importantly the filter *names itself* in the decision record — "already offered to A001"
  is the answer to "why is this caller still waiting", and a score cannot say it.
- **Consequence:** with `D50` in place the two failure shapes stay legible — a call whose
  only qualified agent has already declined now reports `no_qualified_agent` with an
  `already_offered` exclusion visible in its candidate list, rather than looking like a
  skill gap.
- **Future:** the exclusion is per-call and lives as long as the call does. If a shift ever
  runs long enough that re-offering a declined call becomes reasonable, that is a timed
  expiry on this set — not a softening of the filter.

## D53. Nothing crosses the wire as a domain model where a permission boundary exists
- **Problem:** `B5`. `render_brief` returned `CaseBrief.model_dump()`, and `CaseBrief`
  embeds the frozen `ContextSnapshot`, which embeds the whole `Customer360`. A call at
  `L1_PROBABLE` therefore shipped the policy number, the sum insured, every coverage
  figure and the customer's date of birth — in the same body whose
  `may_disclose_policy_details` field said `false`. Every individual component was
  behaving correctly; the leak lived between them.
- **Decision:** the agent API serialises **wire DTOs**, never domain models, anywhere
  assurance gates what may be seen. `BriefOut` and its nested models have **no field** for
  a policy number until `_brief_out` is allowed to fill one, and the raw snapshot is not
  representable at all — only field-level provenance survives.
- **Reasoning:** `D42` already said the gate must be server-side at the wire. It was not
  wrong, it was *unenforceable*: a rule that says "do not send too much" loses to
  `model_dump()`, which sends everything by construction and does so silently. A DTO
  converts the rule into a property of the type — it can only leak what it has a field
  for — and that is the difference between a gate and a promise.
- **Scope, deliberately narrow:** this is not "DTOs everywhere". Internal seams keep
  passing domain objects, which is what makes them pleasant. It applies where bytes leave
  the process **and** something must be withheld: the brief today, transcripts and
  recordings later, anything a supervisor view exposes.
- **Consequence for testing:** the assertion has to be on the **bytes**. Every test we had
  checked rendered Thai lines, which were correctly gated, and none could see a field the
  renderer never mentions. The new tests serialise the whole response and search the raw
  string for the policy number.
- **Alternatives:** a `model_dump(exclude=...)` allow-list — rejected, because the default
  is still "include", so a field added to `Customer360` next month leaks until somebody
  remembers to exclude it. A response-model filter in FastAPI — same objection, and it
  cannot express "this field, but only above L2".
- **Tradeoffs:** two shapes to keep in step, and a mapping function to maintain. Cheap: the
  mapping is where the rule is written down, and the alternative already shipped a leak.

## D54. The demo may bypass queue hours, and says so in the request
- **Problem:** most queues run on `business` hours. Rehearsals happen at 2 a.m. and the
  laptop's clock is real, so half the system is unreachable exactly when it is being built
  and practised on — while the *correct* behaviour (a closed queue offering a briefed
  callback, `D25`) is a feature we want to show, not one we want to fight.
- **Decision:** `PlaceCallRequest.ignore_hours`, default `false`, marked `DEMO:` in the
  schema and in the caller. Leaving it off exercises the real closed-queue path; turning it
  on places the call anyway.
- **Reasoning:** the honest alternatives are worse. Faking the clock would desynchronise
  every timestamp in the call record. Making every queue 24/7 would delete a real feature
  from the config. A flag on the *demo* endpoint changes nothing about production, which
  has no such endpoint at all.
- **What it stands in for:** nothing. Unlike most `DEMO:` markers this is not a placeholder
  for a service that has not landed — it is a rehearsal convenience, and the real system's
  behaviour is what happens with the flag absent.
- **Related landmine:** `ManualClock()` defaults to 09:00 UTC on 1 January = 16:00 Bangkok
  on **New Year's Day**, so every `business` queue is closed under a default test clock.
  Documented on the class rather than changed, because scenario replays are byte-compared
  against golden output built on that epoch.

## D55. The agent never speaks a name they have not verified
- **Problem:** `BriefBuilder._opening()` produced *"สวัสดีค่ะ คุณภัทธีรา ทราบว่าติดต่อเรื่อง…"*
  at **every** assurance level, including `L1_PROBABLE`, where all we have is a caller-ID
  match. The rest of the brief was carefully gated; the one line the agent reads *out loud*
  was not.
- **Decision:** below `L2_STRONG` the suggested opening contains **no name and no detail** —
  it is an open question: *"สวัสดีค่ะ ยินดีให้บริการเรื่อง{intent} ขอทราบชื่อผู้ติดต่อด้วยค่ะ"*.
  The screen still shows who we think it is. Only the spoken sentence changes.
- **Reasoning, and the second one is the stronger:**
  1. Greeting someone by name **confirms to whoever is holding that phone** that the number
     belongs to that person. Small, and free to avoid.
  2. **A leading question is weaker verification.** *"ใช่คุณภัทธีราไหมคะ"* can be answered
     *"ใช่ครับ"* by anybody. *"ขอทราบชื่อผู้ติดต่อด้วยค่ะ"* has to be **produced**. That is
     the difference between recognition and recall, and only one of them is evidence.
- **This was already in the docs.** `diagrams/src/identity_promotion.mmd` — handwritten and
  marked "checked against D42" — contains both the open question and a note saying a leading
  question "both leaks that the number belongs to them AND is weaker verification". The
  diagram was right and the code did not match it, which is the failure mode `CLAUDE.md`'s
  read-the-docs-first rule exists to prevent.
- **Scope:** the *opening line* only. The summary keeps the name, the customer panel keeps
  the name, the matcher keeps everything. None of that is spoken.
- **Future:** when speech lands (P3), the same rule governs any AI-suggested phrasing — a
  model must not be able to put a name into a sentence the agent reads before L2.

## D56. Where the recommended actions come from, written down because it keeps being asked
- **Problem:** "what source are you determining the recommended actions from" is a fair
  question that the code answered only by being read. Worth stating once, plainly.
- **The chain:** `config/intents.yaml` gives each intent a **playbook name** → the playbook
  is an ordered list of `(Thai text, required assurance)` → the list is filtered against the
  caller's current level → **if below L2, a verify-identity step is inserted at position 0**.
- **Where it physically lives today:** `_PLAYBOOKS` in `services/brief/builder.py`, a
  hand-written dict. `config/playbooks/` is the P4 destination and does not exist yet; the
  docs have listed it in the folder map for a while, which reads as though it were there.
- **Hand-written, static, deterministic, no AI** — because this is the version that must
  never fail: it is what an agent gets when the caller declines recording, when STT is down,
  and when the LLM times out. Even at P4 the rule holds: **the model may rank and select
  from the playbook; it may never write a step.** An invented instruction in an insurance
  call is a compliance incident, not a bad suggestion (`D16`).
- **Action 0 is not decoration.** It is the on-screen half of `D42`'s identity control and
  the reason the agent is never *blocked* below L2 — they are told what to do first.

## D57. "Other" is a first-class verification method, and a third party must be named
- **Problem:** two gaps in `D42`'s control, both of which force an agent to record something
  untrue.
  1. The challenge list was closed: DOB, last 4 of citizen id, policy number, recent claim
     amount. Real verification does not fit that. The caller was recognised by voice from
     last week; they read a claim reference off an SMS; they were transferred from a branch
     that already checked ID; they answered a question about a recent transaction. Every one
     of those forces the nearest lie from a four-item dropdown.
  2. *Third party acting for them* recorded a **relationship** but not a **name** — so the
     disclosure log said "somebody who is not the policyholder called", which nobody can act
     on later.
- **Decision:** add `other` to the challenge list with a **required free-text note**; require
  **both** `caller_name` and `relationship` for a third-party attestation. All four are
  rejected server-side when missing, not merely disabled in the UI.
- **`D44` said this first and it was implemented backwards.** *"'The agent handles it' is the
  default and the only mode we build first. Named lookups are added afterwards, one at a
  time, as they prove worth automating."* The named challenges shipped and the escape hatch
  did not — exactly inverted.
- **Why the note is required:** an `other` with nothing written in it is the unfalsifiable
  audit row `D42` exists to prevent. The list is a convenience; the note is the evidence.
- **Tradeoff:** free text cannot be aggregated. Accepted — a true sentence nobody can chart
  beats a false category everybody can.

## D58. Masking protects the log, not the agent
- **Problem:** `D44` says a raw keypad capture is *"masked in transcripts and logs, short
  retention, discardable with one click"*. The implementation read that as "masked
  everywhere" and returned only `••••••••11` to the workstation — **including to the agent
  who had just asked the caller to key it.**
- **Why that is not a small mistake:** it deletes the feature and keeps none of the
  protection. The whole valuable part of the primitive is *getting digits across a lossy line
  accurately*; the agent has to read them back, compare them to a letter, or type them into
  another system. And `••••••••11` is not a policy number anybody can misuse, so hiding it
  from the one person entitled to see it buys nothing.
- **Decision:** `Capture.digits` goes to the agent's own panel. `Capture.masked` is what
  goes **everywhere else** — log lines, transcripts, analytics, anything persisted. The
  service already logs only `length` and `masked`; the API now sends both and the panel
  renders the real one.
- **This is a different question from the disclosure gate** (`D53`). That governs what *we*
  reveal from the bank's records to someone whose identity is unproven. These digits are the
  caller's own input, typed seconds ago, to the person they are speaking to. Conflating the
  two produced a screen that hid the caller's own keystrokes from the agent while the same
  response carried the customer's date of birth (`B5`, before it was fixed).
- **Unchanged:** untyped capture, lookups as evidence only, one-click discard, and the
  inverted storage default. Only the audience for `mask()` is corrected.

## D59. `agent_intent` is a standing instruction, not a momentary status
- **Problem:** the status control behaved confusingly in a way that produced a page of
  half-formed questions — should the selection be deselected when a call starts? re-selected
  after the wrap-up is saved? saved and restored around a call? What is `LAST_CALL` supposed
  to *do* when the last call ends? The confusion was real and the model had a genuine hole.
- **Decision — one framing settles all of it.** `agent_intent` is a **standing instruction**:
  *keep sending me calls* / *this one, then stop* / *no new ones* / *I am away*. It is not
  "what I am doing this second". Read that way:
  - **It is never deselected.** Not when a call starts, not when a wrap-up is saved. It
    persists because the instruction persists.
  - **`offerable` stays `AVAILABLE` + `READY`.** Unchanged.
  - **Mid-call, only the forward-looking three may change**: `READY`, `LAST_CALL`,
    `DRAINING`. An agent may well decide halfway through a conversation that this is their
    last. They cannot be at lunch, because what they are doing right now is talking to a
    customer. Enforced server-side (`DECLARABLE_ON_CALL`), and the screen greys the rest.
  - **`LAST_CALL` is the one instruction with a built-in end condition**, so it is **spent**
    when that call disconnects. It becomes `NOT_READY` — never a concrete state, because the
    platform still may not assert what the person is doing (`D45`) — logged as
    `set_by=platform, reason=last_call_fulfilled`.
  - **`DRAINING` has no end condition** and simply persists.
- **`awaiting_declaration` fixes the thing that actually looked broken.** In after-call work
  the standing instruction is unchanged underneath, but the agent owes a declaration (`D45`).
  The screen therefore stops rendering the old choice as *active* and asks for the next one.
  That is why "I'm marked พร้อมรับสาย but I'm not getting calls" happened: ready was what
  they said **before** the call. No deselect-and-restore machinery is needed — only an
  honest presentation of a state that was already correct.
- **`intent_reason` earns its place immediately.** `NOT_READY` is reached three ways —
  `signed_in`, `rona_missed_offer`, `last_call_fulfilled` — and they want three different
  screens. Without it the wrap-up panel offers **Save & Ready** as the primary button to an
  agent who has just told us they are finishing, making the fastest click the one that undoes
  what they said. With it: `LAST_CALL` spent → only **Save**; `DRAINING` → *Save & stay
  draining*; otherwise → *Save & Ready*.
- **Alternatives rejected:** *deselect on call start and restore afterwards* — needs a
  shadow copy of the instruction, and every bug in it silently changes an agent's
  availability. *A separate "next state" field* — two fields meaning almost the same thing,
  and the log then has to explain which one was true. *Freeze the control during a call* —
  loses the legitimate mid-call "this is my last one".
- **The screen renders permissions, it does not compute them.** `presence.declarable` comes
  from the server, same reason `offerable` does. A client that decides what is legal will
  eventually disagree with the server, and the client's copy will be the wrong one.

## D60. An attestation locks the control, and correcting it is a separate, recorded act
- **Problem:** after pressing *Confirmed*, every identity button stayed live. Pressing again
  silently rewrote a disclosure record, and nothing on screen said the question was settled.
- **Decision:** once anything has been attested on a call, all three outcomes **lock**, the
  panel shows what was recorded, and a distinct **แก้ไขการยืนยัน** reopens it. Re-attesting
  requires an explicit `amend` flag; without it the server answers **409**.
- **Reasoning:** an attestation is a signed statement in a disclosure log, not a toggle. But
  it must not be a *trap* either — an agent who confirmed and then realised they were talking
  to the policyholder's daughter has to be able to correct it. Reopening **appends**; both
  statements survive, which is a better record than either a silent overwrite or a locked
  mistake.
- **Enforced server-side.** A disabled button is a courtesy to the agent; the 409 is the
  rule. Anything that can be clicked twice will be.
- **Known one-way door (`Q18`):** *Not this person* clears the customer — exactly what `D42`
  asks, so nothing re-proposes a wrong ANI match — but it leaves the agent with nobody to
  attach the call to, and customer search does not exist yet (`D32` puts lookup in a later
  phase). A rejected call therefore stays anonymous for its duration. Asserted by a test, so
  the day search lands, that test fails and points at the gap.

## D61. The identity control is a cycle, not a latch: locked after every attestation, always reopenable
- **Problem:** `D60` locked the control after an attestation and added an amend path. Two
  things were wrong with how that landed, both found by an agent clicking around:
  1. **`reopened` was client-local state that nothing ever reset.** One press of
     *แก้ไขการยืนยัน* unlocked the three outcomes for the rest of the call, so an agent could
     cycle Confirmed → Third party → Confirmed freely, silently appending a row to the
     disclosure log on every click. The lock was a latch that opened once and stayed open.
  2. **After *ไม่ใช่บุคคลนี้* the two other buttons still looked pressable** and answered
     `400` on every click, because there is no longer a proposed customer to confirm or to
     act for. A live-looking control that always errors reads as a broken screen.
- **Decision — one shape for all three outcomes, with no dead ends:**
  - Any attestation — **including a rejection** — locks all three outcomes and greys them.
  - **แก้ไขการยืนยัน is always available** once anything has been attested. An agent who
    pressed the wrong button must never be trapped by it.
  - Reopening unlocks all three; the next attestation **re-locks** them. The client learns
    that a new attestation landed from **`attestation_count`**, a server field that only ever
    grows, since amending appends (`D60`) rather than overwriting.
  - Where an outcome is genuinely impossible — confirm or third-party with no customer —
    the button is **disabled with the reason in its tooltip**, not left live to fail.
- **Reasoning:** the lock exists so a signed statement is not a toggle, and the amend path
  exists so a mistake is not permanent. Those two only coexist if the lock **re-arms**. A
  latch that opens once gives the appearance of an audit control while providing none — which
  is worse than no lock, because the screen says the question is settled.
- **Why the count rather than the outcome:** amending *confirmed → confirmed with a different
  challenge* is a legitimate correction that changes no other field. Watching
  `attested_outcome` would miss it, and the control would stay unlocked exactly when an agent
  is fixing something.
- **`Q18` is now visible instead of silent.** Rejection remains a one-way door for *this call*
  — the two forward outcomes stay disabled until customer search exists — but the door is now
  labelled. The test that pins the limitation is unchanged; the screen simply stopped lying
  about it.
- **Enforced server-side.** The `409` on a stray click is the rule; the disabled button is the
  courtesy. Verified by driving the live API: attest → `409` → amend → `409` again, which is
  the re-arming the first implementation lacked.

## D62. Third party does not promote, and the screen now says so out loud
- **Problem:** reported as a bug — *"confirmation via third party doesn't move up to L3"*. It
  is not a bug; it is `D42`, and `attestation.py` carries the comment *"Deliberately NOT
  promoted"* with `test_a_third_party_does_not_unlock_disclosure` pinning it. But a rule that
  an experienced user of the screen reads as a malfunction is a **presentation** failure, and
  worth fixing as one.
- **Why it must not promote:** the third button exists precisely because a daughter calling
  about her father is neither *confirmed* nor *wrong*. Promoting her to `L3_VERIFIED` would
  write into the disclosure log that **the policyholder was verified**, which is the false
  record the third outcome was invented to prevent. Holding the documents is not being the
  person. What third party buys is the **case context** — the agent can see which policy this
  is about — plus an authority-check step; disclosure stays locked.
- **Decision:** behaviour unchanged; the panel states the consequence *before* the button is
  pressed — *"บันทึกว่าเป็นผู้ดำเนินการแทน — ยังไม่เปิดเผยรายละเอียดกรมธรรม์ และจะมีขั้นตอนตรวจสอบสิทธิ์"* —
  rather than leaving the agent to infer it from a badge that did not move.
- **The general rule this is an instance of:** where the system deliberately does *less* than
  a user expects, the screen has to say so at the point of action. Silence is indistinguishable
  from a fault, and the cost is that someone eventually "fixes" the safeguard.
- **Future:** if Krungsri's process defines a verified-representative status (a recorded power
  of attorney, a registered representative on the policy), that is a **fourth** outcome with
  its own evidence requirements — not a promotion of this one. `Q13` already tracks that
  `Policy` has no `representatives` field.

## D63. Call transfer is one filtered menu, a consulted handshake, and the caller moves last
_Designed 2026-08-25 from the user's specification. **Not built** — it lands with P6, when
live-call transcription and the wrap-up loop make a warm handover meaningful. Written down now
because the shape is decided and the pieces it needs are being built before then._

- **Problem:** `CallState.TRANSFERRED` exists and `ARCHITECTURE.md` §9 lists transfer among the
  call controls, but nothing says *how a transfer is chosen, offered, accepted, or aborted*.
  The obvious three features — transfer to a **named agent**, to a **department**, or **up to a
  senior** — look like three screens, and building them as three would triple the state machine
  for one underlying operation.
- **Decision, and the collapse is the point: one menu.** The agent opens a transfer panel
  showing the roster **filtered** by product line / department, seniority, and skill. From that
  one filtered list they may either:
  - **pick a specific agent**, or
  - press **"let the system choose"**, which walks the same filtered list in fit order.

  Transfer-to-department and transfer-to-a-senior are then not separate features at all — they
  are *this menu with a filter set and the system choosing*. Three requirements, one surface,
  one state machine.

### What the transferring agent sees
- **Live presence per candidate**, because choosing blind is how a call lands on an empty desk:
  `system_state`, `agent_intent`, current load, and — for someone on a call — a rough
  *expected free* (`D22`'s call-progress estimate, which P6 introduces anyway).
- **A required reason for the transfer.** The receiving agent decides on the reason **plus the
  existing brief** (`D7`) — which is exactly the pitch's own thesis applied internally: the
  second agent should not have to re-interview the customer either.

### The handshake, and the two rules that make it humane
1. **The caller does not move when the transfer is *offered*.** They stay in the original
   agent's call, talking, unaware. Only an acceptance changes anything. This is `D21`'s
   offer-window insight reused: the seconds spent deciding are seconds the conversation
   continues, not dead air on hold.
2. **The caller does not move when the transfer is *accepted* either.** Acceptance notifies the
   **original** agent, who then wraps up with the customer — *"I'm putting you through to
   คุณสุดา in claims now"* — and presses **Release** to actually move them. Transferring on the
   receiver's click would cut the first agent off mid-sentence, which is precisely the jarring
   experience a warm transfer exists to avoid.

So the caller's leg moves exactly once, on a deliberate press by the person currently talking
to them.

### Receiving an offer
- **Free** → *Accept* and take the call when released.
- **On a call** → *Accept and queue at the front*. They finish their current conversation; the
  transferred caller is the next thing they get, ahead of the pool. That is a legitimate
  priority: this caller has already spoken to someone and been told they are being handed over.
- **Decline**, which notifies the sender and names them.

### When the system chooses
It walks the filtered list **most-appropriate first** (the existing fit function, `D22`),
offering to one agent at a time. A decline moves to the next. **The hard filters still apply** —
language at the required CEFR level (`D38`), skill, licence — because "the system picked them"
must never mean a conversation neither party can hold. If the whole filtered list declines, the
sender is told **"every transfer attempt was declined"** and keeps the call. The customer never
learns any of this happened.

### Why not the obvious alternatives
- **Blind transfer** (push the caller into another queue and hang up) — this is what call
  centres do today and the reason people hate being transferred: the context dies, the caller
  re-explains, and nobody owns the outcome. It stays available as an escape hatch, never a
  default.
- **Transfer to a queue rather than a person** — a special case of "system chooses" with the
  filter set to the queue's skill, so it needs no separate mechanism.
- **Auto-accept on the receiving side** — rejected for the same reason `D33` rejected it for
  inbound offers: it hands a call to a desk that may be empty, and the caller pays the timeout.
- **Letting the receiver's Accept move the caller immediately** — the jarring cut described
  above, and it also removes the only moment where the customer can be *told* what is happening.

### What it will reuse rather than invent
Almost all of it exists. The offer/accept handshake and its timeout (`D33`), RONA (`D51`),
exclusion of an agent who declined (`D52` — a declined transfer must not be re-offered to the
same person by the auto-walk), presence with both axes, the socket with per-agent sequencing,
and the brief itself. The genuinely new pieces are: a **transfer offer** distinct from a queue
offer, the **front-of-queue** commitment for a busy receiver, the **filtered roster view** with
live presence, and `CallState.TRANSFERRED` finally being entered by something.

- **Open questions to settle when it is built:** does a transfer offer time out, and if so does
  the auto-walk treat a timeout like a decline (probably yes, `D52`'s reasoning)? Can the
  sender cancel a pending offer? Does the receiver see the *full* brief before accepting, or
  the reduced offer-card view (`D53`'s gate applies to them too — they are a different agent,
  and assurance is a property of the call, so it travels)? Does a front-of-queue commitment
  survive the receiver going to lunch?

## D64. A live matching board is the debug surface the matcher has earned
_Idea captured 2026-08-25 from the user. **Not scheduled** — P8 or a spare afternoon. Recorded
because it is cheap, and because it would have caught `B4` on sight._

- **Problem:** the matcher already persists everything about every decision (`D18`, `D22`) —
  every candidate, every term, the chosen pair, the exclusions, the rejected deferrals — and
  the only way to look at it is `scripts/run_matching.py`, a wall of text you have to read
  carefully to spot a contradiction. `B4` sat in that output for a while: twelve callers told
  *"no qualified agent"* directly above a listed qualified agent.
- **The idea:** a bipartite board. **Callers as nodes on the left, agents on the right**, edges
  drawn between them, live.
  - Click a node for its detail — the caller's queue, intent, wait, urgency terms; the agent's
    skills, load, presence, current call.
  - **Edges coloured by fit**, so a good match and a desperate one look different at a glance.
  - **Distinguish the two kinds of pairing**: currently *in call* versus *matched and still
    waiting*. They are different facts and the picture should not merge them.
  - Show the **unplaced** callers with which of the two reasons applies (`D50`), and the
    **excluded** edges (`already_offered`, skill, language) as something visibly different from
    a low score — because `D22` says a hard filter is not a bad score, and a picture that draws
    them the same way teaches the opposite.
- **Why it is worth building:** this system's product claim is *explainability* — "why did it
  choose that agent?" is a question a judge will ask. A board that answers it in one frame is
  worth more than a paragraph, and it is the same data already in `matching_decisions`, so
  there is nothing to instrument. It is also a genuine **debugging** tool: contradictions
  between a label and its evidence are visible spatially in a way they are not in a log.
- **Constraint carried over:** it is a **debug/supervisor** view, so it shows customer identity
  at whatever the disclosure gate permits (`D53`) — a board showing every caller's name to
  anyone who opens it would be a second `B5` with a nicer layout.

## D65. A verified third party IS verified — the outcome keeps the record honest, not the level
_Reverses the level set by `D42` and restated in `D62`. Both are left in place so the
reasoning stays visible; this entry explains why the earlier one was wrong._

- **Problem:** `D42` held a third party at `L1_PROBABLE`, on the reasoning that a daughter
  holding her father's documents is not her father. `D62` then defended that and improved
  the wording. The user overruled both, and was right, for two reasons the earlier entries
  never addressed:
  1. **There was no way to finish.** The screen told the agent to *ตรวจสอบสิทธิ์ในการดำเนินการก่อน*
     and then offered no control to record having done so. No second button, no
     *ยืนยันสิทธิ์*. The level was therefore stuck at whatever it had been **for the rest of
     the call** — a dead end shipped as a safeguard.
  2. **It fights what the ladder is for.** The levels exist to decide *how much context we
     may put in front of the agent so the call goes well* (`D20`). Holding a caller whose
     authority the agent has just checked at L1 withholds exactly the information needed to
     help them — while the agent, who has more evidence than the system does, sits looking
     at a locked panel.
- **Decision:** *ยืนยันว่ามีสิทธิ์ดำเนินการแทน* promotes to **`L3_VERIFIED`**, exactly like
  *Confirmed*. The `AttestationOutcome` stays `THIRD_PARTY` forever, with the caller's name
  and relationship, and `authority_check_required` is retained as a standing flag on the call.
- **The honest-log argument survives intact, and this is the crux.** What `D42` was really
  protecting was the *record*, and the record was never the level — it is the **outcome**.
  The disclosure log says *"an authorised representative was verified, named สุดา ใจดี,
  relationship ลูกสาว"*. It has never said, and still does not say, that the policyholder was
  verified. Those are different facts about different people and they remain distinguishable.
  The earlier reading conflated "keep the log truthful" with "keep the level low", and only
  the first of those was ever the requirement.
- **One button, not two.** A separate authority-confirmation step was considered and
  rejected: in practice an agent would press both in immediate succession, so it buys a
  second click and no additional truth. The obligation is carried by the **label** instead —
  the button says the agent has checked, and the panel says so again above it. That is the
  same principle as the challenge dropdown on *Confirmed*: the system records what the agent
  says they did, and the agent is accountable for it.
- **Not a promotion of the same thing.** If Krungsri later defines a *registered*
  representative — a recorded power of attorney, a named representative on the policy — that
  is a **fourth outcome** with its own evidence requirements, not a variant of this one.
  `Q13` already tracks that `Policy` has no `representatives` field.

## D66. A lookup tries every way of matching and reports which one landed
- **Problem:** each lookup asked exactly one question — *does a policy number **end** with
  these digits?* — which silently assumed the agent had asked for the whole number, or at
  least the tail. Real calls are not like that: *"ขอ 4 ตัวท้ายค่ะ"*, *"ขอ 4 ตัวแรกค่ะ"*,
  *"ขอปีเกิดค่ะ"*. One fixed comparison answers **no match** to most of those, which is
  worse than useless — it tells the agent the caller failed a check nobody asked them to pass.
- **Decision:** every lookup walks a **ladder** from strongest to weakest and returns the
  strongest hit, carrying the rung it landed on.
  - digits: `exact` → `suffix` → `prefix` → `contains`, with digit count breaking ties;
  - dates: `full` → `day_month` → `year`.
  The screen then says *"เลขกรมธรรม์ตรง 4 ตัวท้าย"* rather than a bare tick.
- **Reporting the rung is the point, not a nicety.** The rungs are not equally good
  evidence. Four trailing digits of a policy number is a far weaker claim than the whole
  number, and an agent deciding whether to attest an identity (`D42`) has to know which one
  they got. A boolean would flatten that distinction at exactly the moment it matters.
- **Three digits is the floor.** A ten-digit number has only a hundred possible two-digit
  endings, so a two-digit "match" happens by chance constantly. A coincidence an agent may
  reasonably read as confirmation is worse than no answer at all, so the matcher refuses
  rather than returning something weak.
- **It lives in `services/capture/matching.py` and knows nothing about insurance** (`D28`):
  it matches digit strings against digit strings and a date against a date. Which *fields*
  to feed it stays in the API layer, where the policy and claim concepts already live.
- **Still evidence, never an action** (`D44`). A stronger rung does not promote anything.
  The ladder makes the evidence more useful and more precisely described; the agent still
  attests.

## D67. Both eras are accepted wherever a year is compared
- **Problem:** the date lookup accepted `ddmmYYYY` in CE and one BE variant. Thai documents
  disagree with each other — an ID card shows พ.ศ. 2530, the app shows 1987 — and a caller
  reads whichever is in front of them.
- **Decision:** every year comparison accepts CE and BE (`BE = CE + 543`), across all
  orderings, and the result records **which** era was keyed.
- **Reasoning:** rejecting a correct answer because the caller read their own ID card is a
  failure we would blame on the caller. And the era they used is a small, free signal about
  what they are holding.
- **Deliberately permissive about ordering** (`ddmmyyyy`, `mmddyyyy`, `yyyymmdd`), because
  insisting on one layout fails honest callers and the ambiguity is cheap to resolve: a
  string that parses as a valid date under one reading and not the other is not ambiguous.

## D68. Anything the server already knows, the server says
- **Problem:** three faults with one shape, found by auditing the client against the server.
  In each case the browser was holding, deriving, or guessing something the server knew:
  1. **`savedCalls`** — a local `Set` of wrap-ups saved in this tab. It was keyed on
     `active_call_session_id`, which becomes `null` the instant saving closes the record, so
     the confirmation badge **never rendered at all**: the agent pressed Save, the form
     vanished, and nothing acknowledged it. A refresh lost it too.
  2. **`server_time`** — present in every snapshot, read by nothing. Timers computed
     `Date.now() − server_timestamp`, silently mixing two clocks. Invisible on one machine;
     on a laptop whose clock has drifted, every duration on screen is wrong by the offset
     and nothing points at the clock.
  3. **`long_acw`** — computed server-side from `acw_long_after_s`, then OR-ed client-side
     with a hardcoded `45`. `Q9` explicitly expects that threshold to be tuned, at which
     point the screen would keep warning at the old one.
- **Decision:** the server sends `wrapup_saved` and `wrapup_call_session_id`; the client
  applies a `server − browser` skew to every timer; the client's copy of the ACW threshold
  is deleted. `savedCalls` and the dead `previousState` ref are gone.
- **The generalisable rule, which is why this is one entry and not three:** *if the server
  knows it, the server says it.* A client that re-derives a server fact is not saving a round
  trip — it is creating a second source of truth that will eventually disagree, and the
  client's copy is always the wrong one. This is the same argument as `D53` (the gate is the
  shape of the payload) and `D59` (`declarable` is a server decision), applied to plain data.
- **Why `wrapup_call_session_id` is a separate field from `active_call_session_id`:** they
  answer different questions. *Which call can I still act on* stops at `WRAP_UP`; *which call
  am I wrapping up* has to outlive the record closing, because ACW runs to the agent's
  declaration (`D45`). Conflating them is what produced the missing badge.

## D69. The offer card says what the call is about, not only why it was routed here
- **Problem:** the card carried routing metadata only — queue, intent label, urgency,
  accrued wait, assurance, the matcher's rationale. The agent therefore pressed **Accept**
  knowing why the call had reached *them* and nothing about what it was *for*, and only saw
  the brief afterwards.
- **Why that inverts the product:** the entire pitch is that the agent is ready before they
  speak. `D21` set aside the offer window precisely as preparation time — *"those ~7 seconds
  while they read who and why"* — and there was nothing to read. `ARCHITECTURE.md` §11 even
  says the brief arrives *with* the offer; the implementation carried a deliberately reduced
  card, and the reduction went too far.
- **Decision:** the card carries a preview — `summary_th`, `customer_name_th`, and the
  **first playbook action** — built from the same gated `BriefOut` the panel renders.
- **The gate is the reason this is safe, and it must stay that way.** The preview is not
  assembled from the raw brief; it is read off the DTO that has no field for a policy number
  until assurance permits one (`D53`). Reaching into `CaseBrief` here would reintroduce `B5`
  in a new place. A test asserts the raw bytes of an `L1` offer contain no policy number.
- **The first action is deliberately included.** Below L2 that is the verify-identity step
  (`D56`), which is exactly the right first thing for an agent to see before answering.
- **What it still is not:** the full brief. Coverage tables and provenance stay behind the
  Accept, because the card is a decision aid, not the workspace.

## D70. The queue strip distinguishes queues this agent can take from ones they cannot
- **Problem:** the strip listed all nine queues identically. A health agent watched motor
  and life fill up with no way to tell which numbers were theirs to act on — and, reasonably,
  read the panel as a list of *callers* rather than a list of *queue depths*.
- **Decision:** every queue carries `mine` — whether this agent holds its required skill —
  computed server-side. The strip defaults to the agent's own queues and offers a
  **ทั้งหมด** view, which greys the ones they cannot take and still shows the depth. When
  hidden, a one-line footer says how many callers are waiting elsewhere.
- **Server-computed, for the usual reason:** the client would otherwise need its own copy of
  the skill-to-queue mapping, and a second copy is a second thing that can disagree with the
  matcher (`D59`'s rule about permissions, applied to relevance).
- **Deliberately not built yet — the fuller design, and why it is deferred:** the user
  proposed three tabs (mine / my department's sub-categories / the whole floor) and a
  clickable caller list. Two of the three tabs collapse into what shipped: "mine" is skills,
  "the floor" is `ทั้งหมด`. The middle tab — intent-level breakdown within a line — is
  genuinely different and genuinely useful to a supervisor, and belongs with the wallboard
  work, not here.
- **The caller list carries a disclosure question that must be answered first.** Letting any
  agent open a waiting caller's details shows one customer's information to someone the call
  was never assigned to — a `B5`-shaped risk with a nicer layout. The likely answer is that
  a queue list may show *non-identifying* facts (intent, wait, urgency) to anyone, and
  identity only to the agent it is offered to. That needs deciding before it is built, so it
  is not built.

## D71. What the identity control offers depends on how the caller was identified
_Corrects the over-broad lock added in `D61`._

- **Problem:** `D61` disabled all three outcomes whenever no customer was attached. That is
  right for one case and wrong for another, and the two had been collapsed:
  - a caller nobody recognised (`L0` from the first second) — correctly inert;
  - a caller whose match the agent **rejected** — wrongly permanent. `ไม่ใช่บุคคลนี้`
    clears the customer, so the control locked itself out and the call sat at `L0` for its
    whole duration.
- **Why the second one matters more than it looks.** The cases are ordinary, not exotic: a
  mis-press; an agent who concluded they were talking to a stranger and then heard "I am
  his daughter, I am calling about his claim"; a caller who only produced the right
  document on the second attempt. Punishing those with an unrecoverable `L0` makes the
  safest button the one nobody dares press, which is the opposite of what a disclosure
  control should encourage.
- **Decision — the server publishes `attestable`, and the answer depends on the ORIGINAL
  identification, not the current state:**

  | How the call was identified | Offered |
  |---|---|
  | Nobody proposed (`L0` from the start) | **nothing** — no one to confirm, reject, or act for |
  | App token (`APP_TOKEN`) | **third party only** |
  | Anything else — ANI, pending intent, IVR — **including after a rejection** | all three |

- **A rejection is explicitly reversible.** The service keeps the resolution the resolver
  originally produced, so amending away from `not_this_person` **restores that proposal**
  and un-suppresses the customer id. Nothing is invented: it is the same match the system
  made, offered again. The rejection stays in the log, because amending appends (`D60`) —
  the record shows the agent rejected the match and then withdrew that, which is more
  truthful than either version alone.
- **Why an app-token caller is different, and this is the interesting one.** They
  authenticated against the bank's own login before the call existed. The agent re-asserting
  that adds nothing — they have *less* evidence than the auth system did. But one question
  is still open, and it is a real one: **is the person holding the phone the account holder,
  or somebody helping them?** A daughter can perfectly well be handed her father's unlocked
  app. So *third party* stays live and becomes the only control, relabelled: not
  *"ยืนยันว่ามีสิทธิ์ดำเนินการแทน"* (which claims the agent checked something) but
  *"บันทึกว่ามีผู้ดำเนินการแทน"* — recording a fact, not vouching for one.
- **`IVR_VERIFY` deliberately does NOT count as system-verified.** Keying the last four of a
  citizen id is a *knowledge* check, and a family member in the same room knows those
  digits. App auth is possession of an authenticated session. Collapsing the two would let
  the weaker one silently disable the agent's control, so the distinction is enforced in
  code and commented there.
- **Amending an outcome now clears the evidence the previous one set.** Confirming after a
  third-party attestation used to leave the banner saying *"ผู้ติดต่อดำเนินการแทนเจ้าของ
  กรมธรรม์"* on a call the agent had just confirmed **was** the policyholder — because
  evidence is carried forward wholesale on each amendment. Anything an outcome asserts, the
  next outcome has to be able to retire.
- **`Q18` is narrowed, not closed.** Customer *search* still does not exist, so a caller who
  was never identified stays unidentifiable for the call. What is fixed is the far more
  common case: undoing an attestation about a caller the system had already matched.

## D72. The challenge list lives in config and is served, never hardcoded twice
- **Problem:** the list of ways an agent may verify a caller existed in two hand-kept
  places — a `frozenset` in `services/identity/attestation.py` and an array in the React
  panel. The service **refuses** any code not in its copy, so a drift does not degrade
  gracefully: the screen offers an option, the agent picks it, the submit fails, and there
  is nothing on screen explaining why. `Q12` records that this list is unconfirmed and will
  change once Krungsri weigh in, so the drift was scheduled rather than hypothetical.
- **Decision:** `config/challenges.yaml` is the single source of truth. The domain pack
  loads it, `AttestationService` is constructed with it, and the workstation **renders the
  list the server sends** in its snapshot. The panel can no longer offer anything the server
  would refuse, because it does not know any codes of its own.
- **Same shape as `menus.yaml` serving both the IVR and the app** (`D48`), and for the same
  reason: one menu, several surfaces. Where a list is *enforced* on one side and *displayed*
  on another, the displaying side has to be told, not told twice.
- **`requires_note` travels with the option** rather than the client special-casing
  `other`. The rule "this choice needs an explanation" is policy about evidence quality
  (`D57`), so it belongs beside the option it governs — and adding a second free-text
  challenge later is then a config line, not a code change on both sides.
- **`promotes` is carried but not yet enforced.** Every current challenge reaches `L3`. The
  field exists because the honest answer to `Q12` may well be that some checks are weaker
  than others, and the schema should not have to change on the day that is decided.
- **Guarded by a test that is the whole point:** every challenge the server *offers* must be
  one the server *accepts*. It fails the day somebody edits the YAML without checking, which
  is exactly when nobody is looking.
- **Not moved to config:** the `other` code itself, which is special-cased in one place (the
  note requirement). A constant naming the one branch that exists is clearer than a lookup.

## D73. Call-progress prediction is really about after-call work, not the end of the call
_Raised by the user. Design correction to `D22`; deferral is still stubbed off until P6._

- **Problem:** `D22` allows the matcher to **defer** a caller for a few seconds when a much
  better-fitting agent is about to free up, and defines `expected_free_in(agent)` from a
  *call-progress* estimate: closing phrases in the live transcript, elapsed time against
  expected handling time, and an agent's own "wrapping up" button. All of that was designed
  **before `D45` existed**, when the model implicitly assumed an agent becomes available as
  the call ends.
- **They do not.** `D45` put a whole phase between hanging up and being offerable:
  after-call work runs from media disconnect until the agent *declares* a next state, and
  nothing ends it automatically. So "this call is nearly over" answers the wrong question.
  The agent finishing their sentence is not about to take a caller; the agent finishing
  their **wrap-up** is.
- **Decision:** `expected_free_in(agent)` = remaining call time **+ expected ACW**, and the
  second term is the larger and far less predictable one. Concretely:
  - an agent in `AFTER_CALL_WORK` **who has saved their wrap-up** is the strongest
    "about to be free" signal the system has — a discrete, already-observed event, not a
    prediction;
  - an agent in ACW who has not saved is weaker, and gets whatever ACW distribution the
    metrics show;
  - an agent mid-call is weakest of all, because their entire ACW is still ahead of them.
- **This makes the feature both simpler and more honest.** The original design leaned on
  transcript cues, which need P6's live transcription and a model that can be wrong.
  Wrap-up-saved is a fact we already have, needs no AI, and cannot hallucinate — the same
  instinct as `D37` preferring the keypad to speech for the thing the keypad does reliably.
- **The agent's "wrapping up" affordance moves too.** It made sense as a mid-call button
  under the old model. Under this one it is redundant: saving the wrap-up already says it,
  and asking an agent to *also* press "nearly done" is asking them to maintain a second,
  weaker copy of a fact they have already given us.
- **Consequence for the metric:** the same reasoning strengthens the product claim. ACW is
  the shrinkable part (`D45`, `D33`), so predicting availability from ACW rather than from
  call progress means the deferral logic improves as the AI-drafted wrap-up improves.
- **Not implemented.** Deferral stays off until P6 brings the data. Recorded now because
  `D22`'s wording would otherwise be built from as written.

## D74. Assurance gates what the agent may SAY and DO, not what they may SEE
_Reverses the disclosure gating in `D20` and narrows `D42`/`D53`. Both stay in place so the
reasoning remains visible. This is the largest reversal in the project so far, so the
argument is written out fully._

- **Problem, as put by the user, and it is three separate points:**
  1. **`เลขกรมธรรม์` is itself one of the verification challenges.** The system was hiding
     from the agent the very number it offered them a button to check a caller against.
  2. **Hiding it changes nothing about the outcome.** If the caller turns out to be the
     wrong person the data is not used, and the caller never learns what we knew, because
     the agent never says it.
  3. **The ladder was being asked the wrong question.** Its purpose is *how sure are we
     that this is who we think it is* — a confidence signal — and it had been overloaded
     into *what may appear on an employee's screen*.
- **Decision:** the agent sees the whole record from **`L1_PROBABLE`** upward: name, policy
  number, sum insured, coverage table, recent contact. `L0` shows nothing — **not as a
  restriction but because at `L0` there is genuinely nobody to show**. What assurance gates
  is what the agent may *say and do*, which was always the real control surface:
  - `may_act_on_policy` (renamed from `may_disclose_policy_details`) still requires `L2`;
  - the playbook still omits steps whose `requires_assurance` is unmet (`D56`) — *"read the
    policy number back to them"* is not offered at `L1`;
  - **`D55` is untouched**: the suggested opening still contains no name below `L2`.
- **The distinction that makes this correct rather than merely convenient.** Under PDPA,
  *disclosure* means revealing personal data to the data subject or a third party. Showing
  the record to the bank's own agent, who was routed this call in order to serve it, is
  **internal processing under the controller's existing lawful basis** — it is not a
  disclosure event at all. The disclosure event is the sentence the agent speaks. We had
  been applying a disclosure control to a processing step, which cost the agent the
  information they needed and protected nobody.
- **It is also how verification actually works.** An agent asks *"ขอทราบเลขกรมธรรม์ 4 ตัวท้ายค่ะ"*
  and compares the answer to the record. With the record hidden they cannot compare — so
  the previous design made the *keypad lookup* (`D44`, `D66`) the only route to a check the
  agent should have been able to do by eye, and made a system with less information than
  the human sitting in front of it.
- **What the previous design was actually defending against, and the better answer.** The
  real risk is an agent reading details to someone who has not proved who they are — a
  social-engineering call. Hiding the screen is defence-in-depth against *agent error*, and
  it is a weak one: it does not stop an agent reading aloud what they can see at `L3`, and
  it does stop a careful agent verifying efficiently at `L1`. The controls that address the
  actual risk are the ones now doing the work: **an open-question opening with no name
  (`D55`), a verify-identity step at position 0 (`D56`), and actions withheld until `L2`.**
  Those constrain behaviour at the point where data leaves the building.
- **Alternatives considered:**
  - *Show it masked (`HL-****-**0811`).* Rejected: enough to compare a suffix against, not
    enough to compare a full number, and it produces the `D58` mistake again — a mask that
    protects nothing while deleting the feature, this time from the person the record was
    fetched for.
  - *Show it behind a "reveal" click with an audit entry.* Genuinely tempting, and the
    right answer if the agent were ever a plausible adversary. Rejected for now because it
    adds a click to every single call to defend against an employee who could equally read
    it after clicking, and because the read is **already logged** either way.
  - *Keep the gate and add "verify by eye" as a separate permitted action at L1.* This is
    the previous design with an exception carved out, which is the same thing with more
    rules.
- **What did NOT change, and this matters — `B5` must stay impossible:**
  - The **wire DTO stays** (`D53`). It was never the policy; it is the *mechanism*. `BriefOut`
    still cannot carry the raw `ContextSnapshot`, so the class of bug where a domain model
    quietly serialises everything hanging off it remains structurally unavailable. What
    changed is which fields it fills at which level.
  - **`L0` carries nothing**, verified on the raw bytes, including after an agent rejects a
    match — at which point the frozen snapshot still holds the old policy and the *rendered
    Thai sentence* must therefore be gated on the identity rather than the payload. That
    was a live leak introduced while making this change and caught by the existing test:
    the structured fields were correctly `null` while `summary_th` still carried the policy
    number. `B5` with the two halves swapped.
  - **Every read is still audited** (`ARCHITECTURE.md` §18). With display opened up, the log
    is now the primary control rather than a secondary one, and it should be treated as
    load-bearing when `P7` implements it.
  - **Genuinely secret values stay masked regardless of level**: a full citizen id is never
    rendered (`D43`), and raw keypad captures stay masked in transcripts and logs (`D44`,
    `D58`).
- **Consequence for the pitch, which improves.** "We hide data until verification" is a
  weaker story than the true one: **"the agent sees what the bank already knows, every read
  is logged, and what the agent may *say* and *do* is gated by how certain we are."** The
  first sounds cautious and inconveniences the wrong person; the second describes a control
  that binds at the point where harm actually occurs.
- **`Q20` (new):** should a reveal-on-click with a per-field audit entry come back at `P7`,
  for the most sensitive fields only? The answer depends on Krungsri's own agent-desktop
  policy, which we do not have. Recorded rather than guessed.

## D75. The store is a seam with a contract suite, and the fast path is SQLite
- **Problem:** `D39` deferred the database until something genuinely needed to outlive a
  process, and P2b made that true — presence, assignments and `agent_state_log` were in
  memory, so a restart lost a shift. The open question was not *whether* to add Postgres
  but **how to keep it honest**: a database path exercised only when somebody runs a
  container is a database path nobody runs, which is exactly the shape that produced `B7`.
- **Decision, three parts:**
  1. **The store keeps the interface P0 already had.** `CallSessionRepository` was written
     as a Protocol with a dict implementation precisely so this day would be a factory
     line. It was, and nothing above `db/` changed.
  2. **One contract suite, run against every backend** — in-memory, SQLite and Postgres —
     the same rule `D3` applies to ports. The in-memory store is what every other test in
     the project runs against, so if the two ever disagree the suite is green while
     production is broken.
  3. **SQLite is the fast path, not a deployment target.** It needs no container, so the
     database tests run on every commit rather than when somebody remembers. Postgres runs
     too and *skips* if unreachable, so `docker compose up` is the difference between
     "tested" and "tested harder" rather than between "tested" and "not".
- **The divergence this immediately caught, and the reason part 3 needs care.** SQLite
  **ignores foreign keys unless asked**. An `agent_state_log` row pointing at a call that
  did not exist was refused by Postgres and accepted by SQLite — the same test passing on
  one backend and failing on the other. A fast path that enforces *less* than production is
  worse than no fast path, because it converts a real constraint into one that only appears
  in production. `PRAGMA foreign_keys=ON` is now set on every SQLite connection, and there
  is a test asserting the FK bites.
- **Alembic takes its URL from `Settings`**, not from `alembic.ini`, because a migration
  run against a different database from the one the app opens fails as *"the table does not
  exist"* and costs an hour every time. The ini value is left empty with a comment saying
  so.
- **`include_object` refuses to emit DDL against the `core` schema.** The bank's data is
  read-only to us (`D5`) and grants already enforce that — but a migration is exactly the
  kind of thing that runs as a superuser and defeats a grant, so this is the second lock.
  It also excludes `alembic_version`, which autogenerate will otherwise propose dropping.
- **The version table lives in the `readycall` schema.** Left in `public` it would be the
  one piece of our state sitting outside the boundary `D5` draws.
- **Verified against a real container**, not asserted: schemas and both roles created by the
  init SQL, `alembic upgrade head` → four tables, `downgrade base` → clean, `upgrade` again,
  a call written through the repository and read back with `psql`, then read again through a
  **fresh engine** to prove it survives a restart rather than a cache.

## D76. Presence is a projection of the state log, not a table of its own
- **Problem:** the obvious schema has an `agent_presence` table holding current state and an
  `agent_state_log` holding history. Two places recording the same fact.
- **Decision:** only the log is stored. Current presence is `latest_per_agent()` — the newest
  row per agent — computed at startup and held in memory thereafter.
- **Reasoning:**
  - **Two facts that can disagree will.** A crash between the two writes, or one path that
    updates presence without logging, and the shift report and the screen tell different
    stories. There is no reconciliation to write because there is nothing to reconcile.
  - **The log is the one that answers the real question.** A supervisor asks *"what was true
    at 14:03"*, and a current-state row cannot answer it. If only one survives, it is this.
  - **It costs nothing.** The rebuild is one ordered SELECT over a shift's worth of rows,
    run once per process start.
- **What this fixes, concretely:** a restart used to leave every agent silently `NOT_READY`
  — the sign-in default (`D51`) — regardless of what they had actually declared. Now a
  restart costs a socket reconnect, and the agent who said *"lunch"* is still at lunch.
- **Deliberately a plain SELECT ordered by time, not a window function.** It is the same on
  every backend including the SQLite the tests use, and clever SQL that only works on one
  of them would undo `D75`'s point.
- **Not stored, and correctly so:** the heartbeat. Liveness is a property of a live socket,
  not a durable fact — an agent whose laptop is shut is not present no matter what the last
  row says, which is what the sweep already handles (`B7`).

## D77. Repositories return domain models, never ORM rows
- **Problem:** the cheapest thing to do is hand a `CallSessionRow` back to the caller.
- **Decision:** every method in `db/repositories.py` maps to and from the domain model. No
  SQLAlchemy type crosses the package boundary, and the mapping is written by hand.
- **Reasoning:**
  - **An ORM row carries a session lifetime with it.** The first place that breaks is a
    background sweep (`B7`) whose session has closed, producing a lazy-load error far from
    the code that caused it.
  - **It is the same rule as the port boundary** (`D3`): adapters return `Customer` and
    `Policy`, never vendor rows. The store is an internal seam rather than a port, but the
    argument does not change — swapping the backend must not ripple.
  - **Hand-written, because this is the one place the two shapes are allowed to differ**, and
    a mapping worth having is a mapping worth reading. Generated mapping hides exactly the
    decisions that matter: what is a column and what is JSON.
- **The rule for column vs JSON, since it will be asked again:** anything the matcher, a
  report, or a query **filters on** is a real column; anything only ever read back whole is
  JSON. `state`, `queue_id` and the timestamps are columns with indexes. Consents, stage
  timings and the identity resolution are JSON.
- **`expire_on_commit=False` follows from this.** Nothing outside the package holds an
  attached instance, so the default would only buy re-SELECTs on objects we have finished
  with.
- **Tradeoff:** the mapping has to be kept in step with the domain model by hand, and a new
  field added to `CallSession` and not to the mapper is silently dropped. The contract suite
  covers it the only way that works — it round-trips a **whole object and compares
  equality**, so a field the mapper forgets fails the test rather than passing field-by-field
  assertions that were themselves written from the mapper.

## D78. Durability is write-through with an in-memory projection, and half the state is derived
_Completes P2c. Generalises `D76` from presence to every store, and settles what gets a table._

- **Problem:** `D75` landed `call_sessions` and `agent_state_log` and left five things in
  memory — assignments, attestations, captures, the waiting pool, `matching_decisions` —
  with a note saying each was "the same pattern repeated". Finishing it exposed a question
  that pattern did not answer: **does a service read from the database, or from memory?**
  Every one of these services has synchronous readers on the hot path. `excluded_agents()`
  runs inside the matcher tick, `history()` renders on every workstation snapshot,
  `waiting()` is read once a second. Making those `await` a query would put a round trip
  inside a 50 ms budget (`ARCHITECTURE` §15) and ripple `async` through the routers for it.
- **Decision — write-through, read from memory, restore at startup.** Each service keeps
  the working set it already had, gains a store it writes to on every mutation, and gains a
  `restore()` that rebuilds the working set when the process starts. Reads never touch the
  database.
- **Why that is honest here and not merely convenient:**
  - **There is one writer per store and one process** (`D2`). The classic reason to read
    through to the database is that somebody else might have written; nobody else can.
  - **`D76` already made this choice** for presence, and gave the reasoning. Applying it to
    one store and not the rest would leave two durability models in one codebase.
  - **The trigger to revisit is `D39`'s, unchanged:** the moment two processes need to see
    the same call. At that point the working set becomes a cache with an invalidation
    problem, and the answer is Redis or read-through — not a patch on this.
- **What gets a table, and what is derived.** This is the half of the decision that will be
  re-asked, so it is written as a rule: **a table records something a person or a service
  DID; anything computable from those records is derived.**

  | Stored — somebody did this | Derived, deliberately |
  |---|---|
  | `assignments` — an offer was made and resolved | **current presence** ← newest `agent_state_log` row per agent (`D76`) |
  | `identity_attestations` — an agent stated something | **the waiting pool** ← `call_sessions` in `queued`/`matched` |
  | `keypad_captures` — a caller keyed digits | **`identity_for_call`** ← `CallSession.identity` |
  | `matching_decisions` — the matcher decided | **`snapshot_for_call`** ← `CallSession.snapshot_id` |
  | `context_snapshots` — the assembler froze a payload | **accrued wait** ← recomputed from `queued_at` |
  | `call_wrapups` — an agent wrote up a call | |

  A waiting-pool table would be a second answer to *"is this caller waiting?"*, and the
  call's own `state` is the first. Two answers to one question is the `D76` argument again,
  and it does not get weaker for being about calls instead of agents.
- **Restore is ordered, and bounded to live calls.** `list_in_states(everything not
  finished)` first; every other store is then loaded *for those call ids*. That is why no
  store here needs a "load everything" method: a shift of closed calls is history, history
  is answered with SQL, and pulling it into a process buys nothing. It is also why the
  finished states are listed rather than the live ones — a state added later is live until
  somebody says otherwise, and reloading one call too many is the safe direction to fail.
- **What deliberately does not come back:**
  - **`system_state`.** Every agent returns `OFFLINE`. It describes what the *platform* has
    given the person to do, and after a restart it has given them nothing — no socket, no
    offer, no call. Restoring `ON_CALL` would assert a conversation that is not happening
    and let the matcher count a desk that is not there, which is the failure the heartbeat
    sweep exists to prevent (`B7`) arriving through the back door.
  - **`READY` and `LAST_CALL`.** Sign-in after a restart carries the standing instruction
    forward — *lunch* is still lunch — **except** the two that invite a call. This does not
    weaken `D51`: the platform is not *writing* the person's axis, it is declining to
    overwrite it, and the write it makes is `OFFLINE → AVAILABLE` on its own axis. What
    `D51` forbids is inventing a state the person did not choose, and lunch is exactly what
    they chose. An explicit sign-out ends the shift and the carry-forward with it.
  - **Unnamed keypad digits**, which were never stored at all (see below).
  - **Liveness.** A heartbeat is a property of a live socket, not a durable fact.
- **`D44`'s inverted default becomes a column decision.** Capture is untyped, so an unnamed
  run of digits could be a citizen id or a card number. The row therefore **always** stores
  the mask and the digit count — the audit fact that a capture of this length happened —
  and stores the digits themselves **only once something has named them**: a lookup matched,
  or the agent labelled them. The visible consequence is correct rather than a limitation:
  a restart mid-capture returns the fact, not the value. `D44` asks for short retention and
  one-click discard, and a durable copy of an unknown number is the opposite of both.
- **The in-memory stores are not stubs, and this is the `B7` lesson applied.** The `memory`
  backend builds real in-memory implementations rather than skipping the write, so the
  write-through path runs on the default configuration and in every test. A persistence path
  exercised only when somebody starts a container is a path nobody runs — the exact shape
  that produced `B7`, and `D75`'s argument for keeping SQLite in the suite, one level up.
- **Proved by ending a process, not by asserting.** `tests/integration/test_restart.py`
  boots an app, works through the real HTTP API, throws the app away, boots a second one on
  the same storage, and asks it what it knows. **The only thing crossing that boundary is
  the database** — no shared container, no shared service, nothing that could answer from a
  cache it happens to still hold. A per-store contract suite would have passed just as
  happily on `B7`'s three undriven services; this shape is what catches that class.
  Re-verified outside pytest with two real uvicorn processes against a live Postgres.
- **Two things this exposed that were not persistence bugs at all:**
  1. **`CallSession.identity` had existed since P0 and nothing ever wrote it.** The live
     resolution lived only in a container dict, so a restored call had no identity, so
     `render_brief` returned `None`, so the agent's screen was blank on a call they were in
     the middle of. Every write now goes through `Container.set_identity`.
  2. **Declaring READY does not tick the matcher.** Only placing a call, declining an offer
     and the sweeper do. Harmless in production, where the sweep runs every second — but any
     test that turns the sweeper off must drive `sweep_once` itself, or a restored caller
     sits in a restored pool that nothing ever looks at.
- **Tradeoff, stated plainly:** the working set and the database can diverge if a write
  throws after the in-memory mutation. The blast radius is one process lifetime, and the
  next restore corrects it because the durable record is what gets rebuilt from. The
  alternative — one transaction spanning both — is what read-through would buy, and it is
  the thing to build when `D39`'s trigger actually fires.

## D79. Alembic never compares foreign keys, and the test suite gets its own database
_Two migration papercuts, fixed permanently rather than worked around each time._

- **Problem 1: every autogenerate run proposed churning every existing foreign key.** The
  model spells a target unqualified (`call_sessions.call_session_id`) and the reflected
  database spells it qualified (`readycall.call_sessions...`), so the comparison always
  differs. Two rows of `drop_constraint` + `create_foreign_key` appeared in every migration,
  changing nothing — and the generated `drop_constraint` **omitted `schema=`**, so it would
  have looked for the table in `public` and failed. Broken DDL that changes nothing is worse
  than churn, because it only fails when somebody finally runs it.
- **Decision:** `include_object` returns `False` for `foreign_key_constraint`. This
  suppresses only *alterations* — a foreign key on a new table is still emitted inline by
  `create_table`, which is where they actually get made. Verified rather than assumed: the
  migration adding P2c's six tables carries all five of its foreign keys, and a second
  autogenerate run against the migrated database produces an **empty** migration, which is
  the real test that the models and the schema agree.
- **Rejected: qualifying the models** (`ForeignKey("readycall.call_sessions...")`). SQLite
  cannot reference a table in an `ATTACH`ed database, so it would break the container-free
  test path — and losing that is exactly the trade `D75` refuses to make.
- **Problem 2: running the suite silently destroyed the dev database.** The Postgres contract
  tests build their tables with `create_all` and drop them on teardown. Pointed at the
  database the app uses, that deletes everything **and** leaves `alembic_version` behind,
  still stamped at head, with no tables under it. `alembic upgrade head` is then a silent
  no-op and the app dies at startup with *"relation readycall.call_sessions does not
  exist"*. It cost two debugging detours in one session before the pattern was visible.
- **Decision:** the suite has its own database, `readycall_test`, created by
  `infra/postgres/init/02-test-database.sql` and used by default. The dev database is never
  touched by a test run. Recovery, if it ever happens again:
  `uv run alembic stamp base && uv run alembic upgrade head`.
- **Why a whole database rather than a naming convention:** the failure is silent, it looks
  like a code bug, and it is discovered at startup rather than at the moment of damage. A
  rule ("do not point the tests at the dev database") is exactly the kind of thing that
  holds until somebody sets an env var. Separation makes it structurally impossible.

---

_`D80`–`D81` added 2026-08-25, during P3. Both came out of building `D37`'s personalised
menu ordering, which turned out to have consequences the design had not spelled out._

## D80. A menu is a lead-in plus one line per option, never a single clip
- **Problem:** `D24` says every spoken line is pre-rendered at build time, cached by
  `hash(text, voice, engine)`, and played as a file. The obvious reading is one clip per
  prompt id — `menu.product_line` is a single recording that says *"กด 1 ประกันรถยนต์ กด 2
  ประกันสุขภาพ …"*. But `D37` reads a recognised caller's likely options **first**, so the
  order differs per caller. Those two cannot both be true of one baked clip.
- **Decision:** a menu is **composed** at call time from clips that were all pre-rendered:
  a static lead-in (`menu.product_line` = *"กรุณาเลือกประเภทประกัน…"*), one `menu.option`
  line per option rendered from the `label_th` that already lives in `menus.yaml`, then the
  reserved-key hint. Composition is playback, not synthesis — nothing is generated during
  the call, so `D24` holds exactly as written.
- **Reasoning:** it is the only shape that supports reordering at all, and it comes with
  two properties worth having on their own:
  - **the label exists in one file.** `voice_prompts.yaml` contains no menu options; the
    build reads them from the domain pack. There is no second copy of "ประกันสุขภาพ" to
    drift out of step with the one that does the routing.
  - **the same words are one clip.** "ติดตามสถานะเคลม" is key `3` in both the motor and the
    health menu, so it renders once. 32 prompts and 32 menu options come to **63 distinct
    clips**, and that dedupe is the same mechanism that makes a dynamic line like the queue
    position cacheable at all.
- **Alternatives:** *one clip per menu per ordering* — rejected: the product-line menu alone
  has 5 options and therefore 120 orderings, and every wording edit multiplies. *Speak the
  options in a changed order but keep their configured numbers* ("กด 3 … กด 1 … กด 2") —
  rejected under `D81`. *Give up personalisation* — it is `D37`'s cheapest win and costs
  one template.
- **Tradeoffs:** a menu is several playbacks rather than one, so a real telephony provider
  must chain them with barge-in still live between clips. Renumbered option lines are not in
  the built pack, and render on first use — which is exactly the mechanism `D24` §5 already
  describes, and it warms up after one call.

## D81. What the caller pressed is not what gets stored: `menu_path` is always canonical
- **Problem:** `D37` says promoted options "take the low numbers". So when health is
  promoted, `1` means health — **for that call only**. `CallSession.menu_path` records
  keypresses, and a stored `("1", "3")` would then mean different things on different calls.
  Every downstream reading of it — the brief, the intent source, a replayed scenario, an
  auditor asking what the caller chose — would be quietly wrong, and nothing would fail.
- **Decision:** the presentation carries the mapping back, and **every press is resolved to
  its canonical option the instant it arrives**. `menu_path` therefore always holds the keys
  as `menus.yaml` spells them, personalised or not. The raw presses are kept separately, on
  the outcome, where they are a UX signal rather than routing evidence.
  - `ScriptedChoices` — used by every scenario file and test — takes **canonical** keys and
    looks up the key that is actually under that option. A scenario keeps meaning what it
    says when a menu is reordered, instead of silently pressing the wrong thing.
  - Renumbering happens only when something was actually promoted. With nothing promoted the
    spoken key **is** the canonical key, which keeps the common case identical to the config
    and keeps the built prompt pack complete for it.
- **Reasoning for renumbering rather than reordering:** reading numbers out of sequence
  ("กด 3 … กด 1 … กด 2") makes the caller do the sorting, and is worse than not personalising
  at all. If the order changes, the numbers change with it.
- **Alternatives:** *store the presented order alongside the path* — a `CallSession` column
  and a migration, to preserve a fact nothing downstream wants. *Store raw presses and
  re-derive* — the derivation depends on the snapshot, the weights and the config all being
  identical months later, which is exactly the assumption that rots. *Never renumber* —
  see above.
- **Tradeoffs:** `menu_path` no longer literally means "what they pressed", so the model's
  field carries a comment saying which of the two it is. The distinction is invisible until
  personalisation fires, which is precisely why it is written down here.
- **Found by building it.** Two tests were wrong before they were right: one promoted
  options that were *already* in position one and two, so it asserted a reordering that
  never happened; and entering the second menu reset the record of the first having been
  personalised, so the call reported itself as ordinary. Both would have passed review.

---

_`D82`–`D84` added 2026-08-26. All three came from the user reviewing the built IVR and
pushing back on rules I had carried over from `ARCHITECTURE.md` without re-examining them._

## D82. A wrong keypress is never a strike; only silence is bounded
- **Problem:** the menu ended after three unrecognised presses and routed the caller to a
  queue. That came straight from `D37`'s "three unrecognised presses, or silence twice, →
  the general queue", written to guarantee a caller is never *trapped*. Symmetrical, tidy,
  and — as the user pointed out — wrong on the keypress half.
- **The distinction the old rule missed:** *a wrong key proves somebody is there.* Silence
  does not. Those are opposite evidence and they deserve opposite handling.
  - **Silence** may mean the handset is on a table. Looping forever leaves them in limbo,
    so one re-prompt and then a human is right. **Unchanged.**
  - **A wrong key** means an awake caller pressing buttons. Ending the menu there trades
    the *good* answer we were seconds from getting for a *guaranteed mediocre* one — and
    the whole of `D37` rests on those two keypresses being the most reliable routing signal
    in the system. Giving up on them to hit a tidy limit throws away the product's floor to
    enforce a rule that was protecting against something else.
- **Decision:** **no attempt limit.** An unrecognised press says sorry, names the two keys
  that always work, and offers the menu again — for as long as the caller keeps pressing.
- **What replaces the ceiling.** `menu.invalid` now *speaks the way out*
  («กด 9 เพื่อฟังตัวเลือกอีกครั้ง หรือกด 0 เพื่อติดต่อเจ้าหน้าที่»), which the old wording did not.
  With a limit, "try again" was survivable advice because the system eventually rescued
  you; without one, the escape has to be said out loud every time. A test asserts both keys
  appear in that line.
- **The remaining number is not a limit.** `runaway_press_guard: 40` exists for a **stuck
  DTMF sender** repeating one digit forever — a machine failure, not a person — and sits far
  past any human behaviour. A test asserts it is at least 20, on the grounds that a guard a
  caller could plausibly reach is an attempt limit wearing a different name.
- **Tradeoffs:** a caller *can* now stay in the menu indefinitely by actively pressing wrong
  keys. They can leave at any moment with one key, the silence rules still catch them the
  moment they stop, and "trapped by their own continued input" is a materially different
  situation from "trapped by the system".

## D83. `0` is a shortcut through the evidence ladder, not a separate route
> **REVERSED the next day by `D86`.** The queue-ladder fix below was right and stands;
> the *conclusion* that the key was worth keeping did not survive being questioned.
- **Problem:** the user challenged whether an operator key is needed at all: the menus
  already have catch-alls on both layers, so `0` looked like a second way to do the same
  thing — and worse, *"a path where we know nothing about the person at all."*
- **They were right about the implementation.** `queue_for` special-cased `OPERATOR` and
  jumped to the DID's default queue, **discarding a product line the caller had already
  chosen**. Someone who pressed `2` for health and then `0` landed in `q_general`. That is
  exactly the information loss they described, and it was the only real argument against
  the key.
- **Decision:** delete the special case. Pressing `0` ends the walk and the queue is chosen
  by the **same ladder as every other outcome** — explicit intent, then the line's catch-all,
  then the DID's assumption, then general. `0` is therefore not a new path: it is *"stop
  asking and put me through with whatever you already know."*
- **Why keep the key at all**, rather than relying on the catch-alls:
  - it is **the** universal convention. Callers press `0` whether or not we implement it;
    the choice is between honouring it and answering "invalid";
  - reaching the catch-all through the menu costs two more keypresses and a full listen,
    which is the wrong ask for someone distressed, elderly, or on a bad line;
  - the spoken hint «กด 0 เพื่อติดต่อเจ้าหน้าที่» stays **true** — they do reach a person.
- **The outcome kind stays distinct.** `OPERATOR` still differs from `EXHAUSTED` in the
  record, because *"gave up on the menu"* and *"kept mis-pressing"* are different facts and
  the first is a UX signal worth watching. Distinct **measurement**, identical **routing**.
- **Rejected: moving repeat onto `0`.** It only becomes free if the operator key goes, and
  `9`-repeats / `0`-operator is the pairing callers already have muscle memory for. Keeping
  both conventional costs one key and buys familiarity.

## D84. No pre-call identity check in the IVR; the agent's keypad does that job
- **Problem:** `ARCHITECTURE.md` §6 and `D20` both describe an optional IVR step — key the
  last four of your citizen id — that promotes the caller to **L3_VERIFIED**. Prompts for it
  were written during P3. The user objected to anything that has the customer keying
  identity *before* reaching an agent.
- **Decision:** **removed.** The `identify.*` prompts and their roles are deleted, and the
  orphan check would flag them if they came back.
- **Reasoning, and it is a consistency argument rather than a preference:** an automated
  check that promotes to L3 with **no human in the loop** is precisely what `D44` refuses —
  *a lookup returns evidence, never an action; only the agent attests.* The attestation
  service already spells out why `IVR_VERIFY` does not count as system-verified: keying four
  digits is a knowledge check, and a family member in the same room knows those digits. One
  rung of the ladder was contradicting the rule the rest of it is built on.
- **Nothing is lost**, because the replacement is strictly better and already built: the
  agent starts a keypad capture during the call (`D43`, `D44`), the lookup ladder says
  **which rung matched** (`D66`), and a person weighs it and attests. Judgement attached.
- **It also removes a PDPA-awkward collection.** Asking for a citizen id before anyone has
  said hello is a sensitive ask with weak justification, and it violates the menu's own
  principle of not asking for what is not needed (`D37`, `D48`).
- **The resolver rung is kept, and documented as reserved.** `IdentityResolver` still accepts
  `ivr_verified_customer_id`; nothing supplies it. It survives deletion for one reason:
  `D25`'s after-hours voicemail path has **no agent at all**, and is the one place a
  self-service check would have to stand alone. Anything wiring it up owes a decision entry
  saying why the missing human is acceptable there.

## D85. How close an agent is to free is a *conditional* prediction, and the curve is emergent
_Proposed by the user as a shaped preference over elapsed ACW. Adopted, with the shape
derived rather than tuned. Implemented as a projection; **not yet wired into matching.**_

- **Problem:** `D73` fixed `expected_free_in(agent)` as *remaining call time + expected ACW*
  and left the second term as "whatever ACW distribution the metrics show". That is the term
  that actually decides things, and nothing computed it.
- **The user's proposal**, in their words: record each agent's average ACW; treat an agent as
  more and more appropriate to match as elapsed time approaches that average; keep the
  preference *rising* past the average up to a peak around one standard deviation; and then
  **decline**, because an agent far past their usual wrap-up evidently has more work than
  usual on this one.
- **That last instinct is right, and it is not obvious.** The naive model — "they average two
  minutes, so at 1:30 they are 30 seconds away" — is wrong for task durations, which are
  **right-skewed**: many short wrap-ups, a few very long ones. For such a distribution the
  *expected remaining* time falls and then **rises**, because having already run long is
  evidence of being a long one. It is the same reason the wait for a bus that is already
  late gets longer, not shorter.
- **Decision:** model ACW as **log-normal per agent** and score on
  `E[T − t | T > t]` — the expected remaining time, *conditioned* on how long this wrap-up
  has already run. The preference curve the user drew then **falls out of the model** instead
  of being tuned:

  ```
  elapsed    0s     60s    120s   180s   300s   600s   1200s
  remaining  140s   83s    59s    54s    55s    69s    98s      <- falls, then rises
  readiness  0.10   0.25   0.37   0.41   0.40   0.32   0.20     <- peaks, then declines
  ```

  (measured, from a twelve-sample history with a 128 s median). The peak lands past the
  median without anybody placing it there, and it **moves with the data** — which a fixed
  "mean + 1σ" constant could not do.
- **Why not the literal `mean + k·σ` peak:** it needs a `k` nobody can justify, it assumes
  symmetry that duration data does not have, and it would have to be re-tuned per queue and
  per phase. The conditional expectation needs no `k` and is a quantity you can state in a
  sentence: *how much longer this is likely to take.*
- **Shrinkage is not optional.** On demo day an agent has three completed wrap-ups, and a
  mean and standard deviation from three samples is noise dressed as a measurement. Every
  estimate is blended toward the population with a prior weight, so an agent **earns** their
  own curve and borrows everyone else's until then. `is_mostly_borrowed` is exposed so any
  screen showing this can say which it is.
- **It is a SCORE, never a filter** (`D22`: hard filters exclude, they do not down-rank).
  This directly answers the user's own caveat about load: when every other agent is on a
  call, the one in a long wrap-up must still be reachable. Preferring someone less is not
  refusing them, and only the first is safe. It also inherits `D22`'s standing guard that
  deferral **never leaves an agent idle**.
- **Where it plugs in:** as the ACW term of `expected_free_in(agent)`, feeding **deferral**.
  It is deliberately *not* part of `fit` — fit is about whether this agent suits this caller,
  and availability is a different question that must not be able to out-vote skill.
- **The ladder `D73` already defined still ranks above it**, because an observed event beats
  any prediction: wrap-up **saved** is the strongest signal, ACW-not-saved gets this curve,
  and mid-call is weakest because the entire ACW is still ahead of them.
- **Status: implemented and parked.** `services/agents/acw_stats.py`, covered by its own suite including
  an assertion that the curve *has* the falling-then-rising shape — if that ever inverts, the
  matcher would start preferring the agents least likely to be free while every individual
  decision still looked defensible. Nothing calls it yet: `D73` keeps deferral off until P6
  brings real ACW data, and turning it on against invented numbers would be the same mistake
  in a new place. It is **derived, not stored** (`D78`) — a completed `Assignment` already
  carries both ends of ACW, so no table was added.
- **Future:** the same profile is the honest input to a "likely free in ~2 min" indicator on
  a supervisor board, and it improves on its own as the AI-drafted wrap-up shortens ACW —
  which is the product claim `D45` and `D73` are both built around.

## D86. There is no operator key; the way out of a menu is the menu's own catch-all
_Reverses `D83`, one day later, after the user pushed back on the justification rather
than on the mechanism._

- **What `D83` claimed, and what was wrong with it.** `D83` kept `0` on the grounds that it
  is "the universal convention" and that callers have "muscle memory" for it. Pressed on
  that, the claim does not survive:
  - **most people rarely call their insurer.** Once or twice a year is not enough repetition
    to build a reflex. The honest version is weaker: the convention exists *across services*
    (banks, telcos, utilities), so people meet it more often than they call any one company.
    That is a reason it might be *recognised*, not a reason it is *reached for*.
  - and recognition only matters if the key buys something. Under `D83` it did not.
- **The three points that settled it**, all the user's:
  1. **the menu already leads with the urgent options.** An urgent caller finds their reason
     in the first few entries, so "get me through now" is not solving an ordering problem.
  2. **an operator press tells us nothing.** We would either treat it as an urgency signal
     with no evidence — which `D13`'s "no uncalibrated confidence" instinct forbids — or
     treat it as neutral, in which case it carried no information at all.
  3. **there is no operator pool.** `0` landed in the general or line queue, which is exactly
     where the catch-all lands. So it was a *shortcut to a destination the menu already
     offers*, saving two keypresses in exchange for a reserved key, a prompt, an outcome
     kind, and a clause in every menu hint.
- **Decision:** remove it. `repeat_key` is now the only reserved key. `0` is simply an
  unrecognised digit, which under `D82` costs the caller nothing — sorry, here are the
  options again.
  _(**Amended by `D90`, 2026-08-31:** repeat then MOVED onto the freed `0`. The bullet below
  about "a caller who does press `0` out of habit hears an apology" is therefore no longer
  true, and that is the whole reason for the move.)_
- **What replaces it is not "nothing".** Every menu ends in **"เรื่องอื่นๆ"**, spoken aloud as
  a numbered option like any other, routing to that line's catch-all intent. The escape is
  now *part of the menu* rather than a convention the caller has to already know — which is
  strictly more accessible, not less. `test_every_reason_menu_still_ends_in_a_catch_all` was
  a nicety before and is load-bearing now.
- **What this costs:** a caller who does press `0` out of habit hears an apology instead of
  being transferred. With no attempt limit (`D82`) that costs them one re-listen, and the
  menu they then hear contains the option they wanted.
- **The lesson worth keeping.** `D83`'s *implementation* fix was right — `0` had been
  short-circuiting the queue ladder and discarding a chosen product line. But having fixed
  that, `0` became provably identical to an option the menu already spoke, and a shortcut
  that duplicates a path is a maintenance cost, not a feature. **A convention is only worth
  honouring if it buys the caller something the alternative does not.**

## D87. An unfiled wrap-up goes to a backlog, not to the bin
_Proposed by the user, and it resolves a tension `B10`'s fix had left half-open._

- **Problem.** `D45` says after-call work ends when the **person** says so — they may walk
  away from a half-written form for a bathroom break, an escalation, the end of a shift, or
  a mis-click on a status button sitting inches from the notes field. `B10` then closed the
  call so it could not haunt the screen. Correct as far as it went, and **it left no way
  back**: the customer's record simply had a hole in it, permanently. The user's question
  was the one that exposed it — *"when they get back, how would they finish what they had
  to leave early for?"* There was no answer.
- **The two options that were actually on the table, and why both were worse:**
  - **Block the state buttons until the form is saved.** Re-couples exactly what `D45`
    separated, traps an agent who genuinely needs to leave, and — the real damage —
    **incentivises garbage**: forced to file before they can go, somebody types "." and
    saves. The record now exists, looks filed, and is worthless. An honest gap beats a
    dishonest entry.
  - **Close and forget** (what `B10` shipped). Tidy, and it loses the note.
- **Decision:** the call still closes, and the wrap-up becomes a **backlog item**. The agent
  files it whenever they like — after the break, between calls, at the end of the shift —
  and the entry disappears when they do.
- **Derived, not stored** (`D78`). "This assignment's ACW has ended **and** no `call_wrapups`
  row exists" *is* the backlog. Nothing marks a call as owing one, so nothing can disagree
  about whether it does, and filing clears it by construction — there is no second flag to
  forget to reset.
- **Details that matter:**
  - the list is **oldest first**, and each row carries the intent label, the customer name,
    the time, and **how long they spent in ACW before leaving** — four seconds means they
    left immediately, which is useful context when coming back to it cold;
  - the name is gated on **that call's** identity resolution (`D74`), not the current one. A
    backlog row renders long after the call and is a disclosure surface like any other;
  - filing late **never touches presence.** The agent may be on another call while they do
    it, and `D45` keeps the two statements separate anyway;
  - the event carries **`filed_late`**. A wrap-up written twenty minutes afterwards is still
    real, but it is not the same as one written while the call was fresh — and an ACW metric
    that cannot tell them apart would report the product's headline claim as better than it
    is (`D45`, `D73`).
- **Not a modal and not a blocker**, deliberately. It is a standing offer in the left column
  with a count. Interrupting an agent to demand paperwork is the same mistake as blocking
  the buttons, wearing a friendlier face.
- **Why this is better than either alternative:** the agent keeps the freedom `D45` gave
  them, the customer keeps their record, and an accidental keypress becomes a two-click
  recovery instead of permanent data loss.
- **Future:** the same list is where an AI-drafted wrap-up would surface for approval, and
  the count is a supervisor signal on its own — an agent with six unfiled records is telling
  you something about their day.


## D88. The intake offer is its own machine, and a strategy consumes turns rather than frames
_P3 step 4a. The first thing built below `ARCHITECTURE.md` §6's "queue is now known" line._

Four choices, all made while building the offer, all of which could reasonably have gone
the other way.

### 1. A second machine, not two more states on the end of the IVR

`services/ivr/machine.py` decides **where the call goes**; `services/intake/hold.py` decides
**how much the agent will know when it gets there**. Those are the two halves of `D37` — the
keypad is the floor, the AI is the delta — and the architecture already draws a line between
them in the flow diagram. Adding `OFFERING` and `RECORDING` to `IvrMachine` would have put
routing rules and enrichment rules in one file with one outcome type, and the first person to
add "if the caller declined, route them differently" would have found nothing in the way.

The cost is a second machine with a similar shape. The benefit is that
`test_every_answer_leaves_the_queue_exactly_where_the_menu_put_it` is a statement about
module boundaries and not just about today's code.

### 2. `run_offer` stops at the answer; the recording outlives it

The first version looped until the intake finished, and it was wrong in a way that only
showed up when a scripted caller pressed `1` and the loop immediately "heard" them fall
silent. The three things that actually end a recording are a **VAD silence**, the **maximum
duration**, and **an agent pressing Accept** — and the last one arrives on a different
connection, seconds later, from a different person (`D21`).

So the driver returns as soon as the offer is answered, the hold stays live in
`IntakeService._live`, and `on_silence` / `on_max_duration` / `on_agent_accepted` /
`on_hangup` are entry points the media layer will drive when it exists. Keeping the loop
would have meant this file owning a media loop that does not exist and **inventing what it
reports** — which is the family of mistake `B3`, `B4`, `B7` and `B8` all belong to.

`api/routers/agent.py` calls `on_agent_accepted` **before** `assignments.accept`, so the last
of the transcript is attached to the call before the agent's screen renders it.

### 3. `IntakeStrategy` takes `TranscriptTurn`, not a media stream

`ARCHITECTURE.md` §7 drew `start(session, media: MediaStream)`. It takes turns instead.

- **It is the seam in the right place.** The media gateway, the resampler, the VAD and the
  STT worker are all *one* concern — turning audio into sentences — and every strategy wants
  the sentences. `PassiveRecordIntake` would have accepted a stream only to hand it straight
  to the transcriber.
- **It made the whole seam buildable today, with no GPU, no audio and no telephony.** That is
  not a convenience: it is the reason `D10`'s protocol now has a real implementation and a
  test suite three phases before the hardware risk lands.
- `ConversationalAgentIntake` will need more than turns, but what it actually needs is a
  **TTS output channel and barge-in**, not raw input frames — and those belong in its
  constructor, where they can be typed properly.

### 4. Pressing 1 grants both consent scopes; pressing 2 records the refusal

`session.may_run_intake` requires `recording` **and** `ai_processing` (`D14`). Two options:
ask twice, or grant both on one keypress.

- **One keypress.** The prompt says exactly what will happen — *"we will record what you say,
  and the agent will see it the moment they answer"* — so a single `1` is informed consent to
  both, and asking twice for one thing the caller already agreed to in one sentence is worse
  service without being better privacy. Both rows carry `basis="ivr_keypress_1"`, so the
  evidence is on the record either way.
- **`health_data` stays separate** and is *not* granted by the offer. It is sensitive data
  under the PDPA and it needs its own ask, which nothing does yet — see `Q24`.
- **Pressing 2 writes `granted=False`, not nothing.** *"They said no"* and *"we never asked"*
  are different facts, and a call whose consent list is simply empty cannot tell you which
  happened. The report distinguishes them too: `intake_declined` versus `no_consent`, so the
  agent looking at a thin brief gets the honest reason (`D14`).

### The smaller rules, and where each comes from

| Rule | Why |
|---|---|
| the offer is asked **at most twice** | enrichment is not worth nagging about; the re-offer at `INTAKE_REOFFER_AFTER_S` exists because a long wait genuinely changes the caller's mind |
| **a wrong key replays the offer, unlimited** | `D82` — a wrong key proves somebody is there. What bounds the loop is silence, not a strike count |
| **silence at the offer gets no re-prompt** | unlike the menu. The second chance already exists and is better placed; re-prompting a caller who ignored an offer is nagging |
| **silence during a recording gets one** | they pressed `1`, so they asked to be heard. Different evidence, different rule |
| the repeat key works here too | a caller who learned it in the menu must not find it means something else thirty seconds later |
| **keys do nothing while holding** | nobody asked a question, so replying would be answering something the caller never said |
| **other keys are ignored mid-recording** | interrupting a sentence to apologise for a mis-hit is worse than the mis-hit |
| the strategy **never guesses `degraded`** | no turns can mean silence *or* a dead transcriber, and only the driver knows which. A strategy inventing `stt_unavailable` puts a claim on the agent's screen that nothing checked |
| `reoffer_due()` runs in **`sweep_once`** | `B7` exactly: the only thing that happens at ninety seconds is that the wait got long, so nothing else is around to carry it |

### What this does not do

No audio, no VAD, no STT worker, no recording to storage, no live transcript on the
workstation. Turns arrive through `IntakeService.on_turn` and today nothing calls it except
tests and the scenario runner. That is the next slice, and it is the one with the GPU in it.

## D89. The caller hears their queue position; they hear a wait estimate only when there is one
> ⚠️ **REVERSED by `D91` (2026-09-01).** The position itself is gone too. This entry's
> reasoning — *"we can count a queue exactly"* — is true and irrelevant: counting a pool
> is not ranking it, and the matcher re-solves the whole matrix each tick, so nobody has
> a position. Kept as a record of the mistake, which is the interesting part.
_Split out of `D88` because it is a product rule, not a mechanism._

- **Problem.** `queue.position` says *"you are number {position}, about {wait_minutes}
  minutes"*. We can count a queue exactly. We cannot yet predict how long it takes to drain —
  there is no handle-time data, `estimated_wait_s` is passed `None` everywhere, and `D85`'s
  machinery for exactly this is implemented and deliberately parked until P6.
- **Decision:** three lines instead of one. `queue.position` when both are known,
  **`queue.position_only`** when only the position is, and `queue.hold` when neither is.
- **Reasoning.** A made-up "about three minutes" is a promise, and it becomes a visible lie
  at minute eight — to the one caller least inclined to forgive it. Saying only what we know
  is standard call-centre practice and costs nothing. This is `D18`'s "show, do not claim"
  applied to the caller rather than to the agent.
- **What switches the fuller line on:** real handle-time data at P6. Nothing else has to
  change — the prompt, its slots and its warm renders already exist.
- **Tradeoff:** one more clip in the pack (64, up from 63) and a branch in `_position_lines`.

## D90. Repeat moves onto `0`, the key the operator used to occupy
_Amends `D86`. Proposed by the user, one session after the operator key was removed._

- **Problem.** `D86` removed the operator key and admitted exactly one cost: *"a caller who
  does press `0` out of habit hears an apology instead of being transferred."* Meanwhile
  `repeat_key` sat on `9` — where it had been put in `D83` for a reason that no longer
  existed. `D83` explicitly rejected moving it:

  > *Rejected: moving repeat onto `0`. It only becomes free if the operator key goes, and
  > `9`-repeats / `0`-operator is the pairing callers already have muscle memory for.*

  Both halves of that are now void. The operator key **did** go, so `0` **is** free; and the
  muscle-memory claim is the one `D86` demolished — most people call their insurer once or
  twice a year, which is not enough repetition for a reflex.
- **Decision:** `repeat_key: "0"`. It remains the only reserved key.
- **Reasoning, and it is better than "the key was free":**
  - **`0` is where a lost caller's thumb already goes.** Not from muscle memory for *our*
    menu — from the cross-service convention that `0` means *"I need a person"*. That
    instinct is real even when the reflex is not, and it fires precisely when someone is
    confused. Confused is exactly when hearing the options again helps most.
  - **It deletes `D86`'s only admitted cost.** Pressing `0` out of habit used to earn an
    apology for a key that did nothing. It now replays the menu — the closest thing to help
    this layer can offer, and strictly more useful than *"sorry, I did not find that."*
  - **The keypad's shape argues for it.** `0` is bottom-centre, alone on its row, findable by
    touch. `9` is one of nine indistinguishable positions in the grid, and a caller holding a
    phone to their ear cannot see either.
- **What it does not change.** Still no operator, still no attempt limit (`D82`), still one
  reserved key, and the way out of a menu is still the menu's own spoken **"เรื่องอื่นๆ"**.
  This moves a key; it does not add a path.
- **The bug found while moving it.** `menu.invalid` spelled the digit into its Thai text —
  *"…กด 9 เพื่อฟังตัวเลือกอีกครั้ง"* — while `menus.yaml` owned the value the IVR actually
  honoured. Changing `repeat_key` would have left the apology naming a key that did nothing,
  **and nothing would have failed**: the prompt guard checks that ids resolve and that slots
  are declared, and a hardcoded digit is neither. It is a `{repeat_key}` slot now, rendered
  from `MenuSettings`, and `test_the_apology_names_the_repeat_key` asserts the *rendered*
  line rather than the template so the two cannot drift apart again.
  **The lesson is `D28`'s, in a place it had not been applied: if `config/` owns a value, no
  prompt may spell it out.** Worth grepping the pack for others.
- **Cost:** one re-rendered clip (`menu.invalid` becomes dynamic, warmed at `0`), and two
  tests replaced because the behaviour they described genuinely changed.

## D91. The caller is never told a queue position, because there is no queue to have one in
_Reverses `D89`, two days later. The user asked whether announcing a position made sense
given the matcher, and it does not._

- **What `D89` claimed, and the exact error.** `D89` split the position line in two so the
  caller heard *"you are number three"* without an invented wait, on the grounds that **"we
  can count a queue exactly; we cannot yet predict how long it drains."** The first clause is
  true and **irrelevant**. Counting a pool is not ranking it. I conflated *we can count* with
  *we can rank*, which is the same shape as `B3`, `B4` and `B7`: a confident, plausible,
  wrong result that every test agreed with because no test asked the question.
- **There is no line.** `MatchingEngine.match()` builds a full call x agent matrix and solves
  it with the Hungarian algorithm **every tick** (`D22`, `D49`). Arrival order is not an input
  anywhere in `WaitingCall`, `score_fit` or `score_urgency`. What `_queue_position()` actually
  computed was `len(pool on this queue) + 1` — *the size of the pool at the moment you
  arrived* — and that number was then spoken as **"ลำดับที่ 3"**, position three.
- **The number is wrong in both directions, and both are correct behaviour.** Measured against
  the real scoring code, one queue, one free agent:

  | caller | urgency | fit | score |
  |---|---|---|---|
  | arrived 1st, waited 200 s, routine | 3.000 | 0.650 | 1.950 |
  | arrived 2nd, waited 90 s, routine | 2.050 | 0.650 | 1.333 |
  | arrived 3rd, waited 0 s, **emergency**, reaches a specialist | 2.200 | 1.050 | **2.310** |

  The third caller is answered first. Give all three the same fit and the 200-second waiter
  wins instead — waiting drives `wait_pressure` to the urgency ceiling and beats CRITICAL on
  its own. **Both overtakes are the design working**, and both make an announced position a
  promise the system breaks.
- **The damage is asymmetric.** Overtaking lands hardest on low-urgency callers — who are
  exactly the patient ones most likely to have believed the number and least likely to
  complain about anything else.
- **Decision:** no position, no estimated wait. The caller hears **"กรุณาถือสายรอสักครู่ค่ะ"**
  and then the offer. `queue.position` and `queue.position_only` are **deleted** from the
  pack, and `QUEUE_POSITION` / `QUEUE_POSITION_ONLY` from `PromptRole`, so the line cannot be
  re-added by config alone. The pack drops from 64 clips to 54.
- **Alternatives considered, and why not:**
  - **speak the true count** — *"there are 3 calls waiting"*. Literally true and cheap, but a
    caller hears it as *"3 ahead of me"*, which is the same broken promise in a thinner
    disguise.
  - **state the policy** — *"calls are answered in order of urgency"*. Honest, and a better
    story than a fake position. Rejected **for now** only because it is a new Thai line that
    wants a native check, and because saying nothing is already correct. Worth revisiting
    with Krungsri: it pre-empts the *"why did they get through first"* complaint, which is
    the real operational cost of non-FIFO answering.
  - **compute a real rank and re-announce it** — it would go stale within a tick, and it
    would sometimes have to announce a number going **up**, which is worse than silence.
- **What this costs:** a caller in a long queue now gets no progress feedback at all. That is
  a genuine loss and it is the reason to revisit the policy line later. It is still better
  than a number we contradict.
- **The rule worth keeping.** *Never speak a number the system does not treat as binding.*
  `D18` says show, do not claim — this is that rule pointed at the caller instead of the agent.

## D92. Speech may change WHO answers and HOW SOON, never WHICH QUEUE
_The boundary `D23` implied and never stated. Written before P4 builds the intent blend,
because P4 is the first code that can cross it._

- **Problem.** `D23` says the effective intent is a confidence-weighted blend of the
  keypad/app/DID intent and the speech-derived one, and that fit is recomputed on every
  `analysis.brief.updated`. It never says what the blend is allowed to *move*. Since
  `WaitingCall.required_skill` is derived from the queue spec, a speech-revised intent could
  silently re-route the product line — and `D37`'s floor would stop being a floor.
- **Decision:** the keypad owns **`queue_id` and `required_skill`**, and speech may not
  change either. Speech may move **`intent_urgency`** and the **fit signals** — so it decides
  which agent within the queue, and how soon relative to other callers on it.
- **Reasoning.** The worst case has to stay survivable. Under this rule a mis-transcription
  costs the caller a slightly less ideal agent on the right queue; under the alternative it
  costs them the right queue entirely, decided by the least reliable component in the system
  (`D12`, `D37`). The keypress is also the one piece of intent evidence the caller
  *deliberately* gave us — overriding it with an inference is the wrong way round.
- **This is not a limitation, it is the product's shape.** "The AI makes the agent's screen
  useful; it does not make the routing possible" is only true if something enforces it.
- **Where it will be enforced:** whatever P4 writes must produce a *new urgency and new fit
  inputs*, never a new `queue_id`. A test should assert that a transcript cannot change
  `session.queue_id` after `QUEUED` — write it with the blend, not after.
- **The escape hatch that already exists.** A caller genuinely in the wrong queue is a
  **transfer** (`D63`), performed by a human who has spoken to them. That is the right place
  for it: re-routing is a judgement with a person attached, exactly like `D44`'s attestation.

## D93. The wait ceiling is a pre-pass, not a guard rail
_Fixes `B13`. The user asked how a caller past the ceiling is actually handled, and the
honest answer was: not at all._

- **Problem.** `config/matching_weights.yaml` has promised, since it was written, *"past this
  wait, drop to ANY qualified agent regardless of fit"*. The check lived in
  `MatchingEngine._guard()`. But `_guard` only runs for a call the **solver has already
  chosen an agent for** — a call the solver left unassigned hits `continue` several lines
  earlier. So the ceiling could never fire for the one caller it existed to rescue.
- **Measured before the fix**, on the real engine with one dual-skill agent:

  | caller | urgency | fit | score | outcome |
  |---|---|---|---|---|
  | health, waited **600 s** | 3.00 | 0.10 | 0.600 | `ALL_QUALIFIED_BUSY` → **nobody** |
  | motor, fresh, CRITICAL | 2.20 | 1.10 | 2.420 | `ASSIGN` |

  Ten minutes on hold, and the ceiling never ran.
- **Decision:** rescue **before** the optimiser. `MatchingEngine.match()` now begins with
  `_rescue()`, which walks every caller past `MAX_WAIT_BEFORE_ANY_AGENT_S` **longest wait
  first** and hands each one a qualified free agent. Those rows and columns are then knocked
  out of the matrix the solver sees, so nobody is assigned twice. The full matrix is still
  built for every pair, because `D18` needs the whole candidate list on the record.
- **The rescued caller gets the LOWEST-fit qualified agent, not the best.** `require_skill`
  already guarantees everyone in that pool can actually help — fit only says how well. Giving
  a starved caller the specialist buys them a little and moves the starvation onto whoever
  genuinely needed that specialist. The promise is *somebody competent, now*; spending the
  scarcest agent on it is the opposite of what the ceiling is for.
- **A rescued call never reaches `_guard`.** Deferral and the anti-hot-spot check both exist
  to *improve* a match, and neither may apply to someone already past the ceiling — that is
  what being past it means. The ceiling branch inside `_guard` is therefore **deleted**, not
  left as dead code that reads like a rule.
- **What it still cannot do.** If no qualified agent is online at all, the rescue finds
  nothing and the caller is correctly reported as `NO_QUALIFIED_AGENT` — a roster gap, which
  waiting cannot fix. `run_matching.py --calls 25` shows exactly one such caller, labelled as
  such. That is the honest floor.
- **Alternatives rejected:** *scoring* past-ceiling pairs at a large constant kept one code
  path but needed a magic number above every reachable `fit × urgency`, and made the decision
  record harder to read. *Accepting the behaviour and rewriting the promise* was on the table
  and is what the previous commit's doc edits assumed — but the promise is a good one and the
  hard filter already makes it safe.
- **The tradeoff, stated plainly.** A fresh **CRITICAL** caller can now wait behind a starved
  routine one when they are contesting the only qualified agent. See `Q25`: that is a real
  cost and it is the one thing about this change worth arguing over.

## D94. The wait ceiling is per urgency tier, and the most urgent starved caller goes first
_Proposed by the user, closing `Q25` — the cost `D93` introduced and deliberately left open._

- **Problem.** `D93` made the ceiling reachable but it was still **one number for
  everybody**. That says a caller at a crash scene and a caller asking about a renewal are
  owed the same guarantee at the same moment, which is plainly false — and it produced the
  case `Q25` recorded: a routine caller 181 s in could take the last qualified agent from a
  fresh emergency, because the routine caller was past the single ceiling and the emergency
  was not. Measured before this change: a health caller at 600 s took the shared agent from
  a fresh CRITICAL motor caller.
- **Decision, two parts:**
  1. **`guards.max_wait_before_any_agent_by_urgency` — a ceiling per tier.** Shipped as
     `critical: 60`, `high: 120`, `normal: 180` (unchanged from the old single value),
     `low: 270`. `max_wait_before_any_agent_s` survives as the default for any tier the
     table does not name, so the table is fully populated at load and `ceiling_for()` is a
     plain lookup rather than a lookup with a fallback branch only one tier ever takes.
  2. **When two callers are both past their own ceiling, the more urgent is rescued
     first**, with wait breaking ties inside a tier. Both are owed the guarantee and only
     one agent exists; urgency is the axis that says whose delay costs more.
- **Why separate numbers rather than one number scaled per tier.** This was the user's own
  question and they reached the right answer. A real contact centre states these as flat
  commitments — *"emergencies are answered within a minute"* — and a supervisor aligning
  the config to a company SLA wants to type that minute in, not derive it from a base and a
  multiplier. A multiplier also couples every tier to every other one: retuning `normal`
  would silently move `critical`, which is the tier you least want moving by accident.
- **Why this dissolves `Q25` rather than trading it away.** Under one ceiling the emergency
  and the routine caller were compared on a number written for neither of them. Now the
  emergency reaches *its* ceiling at 60 s while the routine caller is still 120 s from
  theirs, so in the ordinary case the contest never happens — the emergency is rescued
  before the routine caller is even eligible.
- **The table cuts both ways, and the second half is easy to miss.** A `low`-urgency caller
  at 200 s is past the *old* 180 s ceiling but not past their own 270 s one, so they are no
  longer yanked out of the matrix and handed the worst qualified agent. They stay where fit
  still counts, which is the right answer for somebody who is not in a hurry. `D93`'s
  lowest-fit rule was always a deliberate sacrifice; this stops us making it for people who
  were not actually starving.
- **A ceiling may never rise with urgency, and that is enforced at startup.** Getting the
  table backwards is silent — every call still routes, and the caller at the crash scene
  simply waits — so `MatchingWeights.validate()` walks the tiers in patience order and
  refuses to boot. An unknown key (`urgent:` for `high:`) is refused too, rather than
  leaving that tier quietly on the default.
- **The alternative ordering, recorded because it may be the better answer later.** Instead
  of strict urgency order, rank the starved by **overshoot ratio** — `wait / own_ceiling` —
  so a routine caller at 3.3× their ceiling outranks an emergency at 2× theirs. It is
  self-balancing and needs no second knob. It was **not** chosen because `defer_never_above_
  urgency: high` already establishes that this codebase treats CRITICAL as categorically
  special rather than as a large number, and because the ratio is much harder to explain to
  a supervisor than "emergencies first".
- **The cost, stated plainly.** Under **sustained** CRITICAL load a patient caller past
  their ceiling can be overtaken indefinitely, where the ratio ordering would eventually
  rescue them. The rescue pass only ranks callers who are *all* already past their ceilings,
  so this needs a genuine flood of emergencies to bite. **If it is ever observed, the fix is
  the ratio ordering above, not a new knob.** `Q25` is closed on that understanding.
- **What did not change.** The rescued caller still gets the **lowest**-fit qualified agent
  (`D93`), a rescued call still never reaches `_guard`, and the hard filters still run
  first — *"any qualified agent"* has never meant *"anyone"*.

## D95. `torch` comes from the CUDA index, because the PyPI wheel is the CPU build
_P3 step 4b, step zero. Recorded because the failure mode is invisible._

- **Problem.** `uv add torch` resolves from PyPI, and the PyPI `torch` wheel for Windows is
  **CPU-only**. Nothing about installing it fails: `import torch` works, every model loads,
  every test passes, and Whisper runs — roughly an order of magnitude too slowly, with
  `torch.cuda.is_available()` quietly returning `False`. It is precisely the shape this
  project keeps meeting (`B3`'s zero timings, `B12`'s frozen wait): a confident, plausible,
  wrong result that nothing raises about.
- **Decision:** pin `torch` to the PyTorch CUDA 12.8 index in `pyproject.toml`, with
  `explicit = true` so only the packages named in `[tool.uv.sources]` are looked up there
  and everything else still resolves from PyPI.
- **Verified rather than assumed**, which is the point of the entry. The `+cu128` local
  version tag only proves *which file installed*; it says nothing about whether the driver
  cooperates. So the check ran a real matmul on the device:

  ```
  torch          2.11.0+cu128
  cuda available True          cuda built  12.8
  device         NVIDIA GeForce RTX 3050 Laptop GPU   (sm_86)
  vram total     4.00 GiB      vram free   3.22 GiB
  matmul on gpu  OK
  ```

- **The VRAM number is worse than the docs assumed, and it drives the engine choice.**
  `PROJECT_STATE` §7 says "4–6 GB"; the real figure is **4.00 GiB total with ~3.2 GiB free**,
  because the desktop compositor is already holding 0.8 GiB. Whisper-medium in fp16 is
  ~1.5 GB of weights before activations, and Whisper pads every chunk to 30 s. That is the
  measurement behind `D30`'s bake-off mattering rather than being a formality.
- **`onnxruntime` stays the CPU build on purpose.** Silero VAD is a ~1 MB model and runs in
  well under a millisecond per frame on a CPU core. Putting it on the GPU would contest the
  scarcest resource on this machine with the one model that actually needs it, and
  `onnxruntime-gpu` would additionally drag in its own CUDA/cuDNN copies to disagree with
  torch's.

## D96. The audio path: a ninth port for VAD, a machine for endpointing, and a gate before the model
_P3 step 4b. The half of the phase with the hardware in it._

- **Problem.** `D88` left `IntakeService.on_turn` / `on_silence` / `on_max_duration` with a
  note saying *"nothing feeds this yet"*. Everything between a phone line and those three
  methods had to be built: normalisation, voice detection, endpointing, the STT worker, and
  a driver that ties them together without any of it blocking a call (`D12`).

### The four choices worth recording

**1. Voice activity is the ninth port.** Same argument as `SttEngine` (`D3`): it is a vendor
model we want to be able to swap and bake off. `ports/vad.py` returns a **probability per
frame**, not a decision — where an utterance starts and stops is a stateful judgement with
thresholds, minimum durations and padding in it, and putting that in the adapter would mean
re-implementing `D9`'s tuning once per vendor.

**2. `EnergyVad` is not a toy, it is the reason the audio path has tests.** CI runs
`uv sync --frozen` with no extras, so without a dependency-free detector the entire media
layer would only ever execute on the one laptop with a GPU — which is `B7`'s shape exactly.
It doubles as the degradation rung: if Silero fails to load on demo morning the call is
still endpointed, slightly worse. `ARCHITECTURE` §16 needs no new row.

**3. The endpointer is a machine with no I/O**, like `ivr/machine.py` and `intake/hold.py`
before it. `D9`'s inherited constants — threshold 0.65, 500 ms minimum speech, 100 ms
minimum silence, ~120/60 ms padding — are therefore **assertable against a list of floats**,
with no model, no audio and no clock. The 120 ms leading pad has its own test saying why it
exists: in Thai the first syllable often carries the tone that distinguishes the word.

**4. Ingestion never waits for the model.** Finished segments go on a queue and a single
consumer transcribes them, so frames keep being accepted while Whisper is busy, and turns
stay in order without a sorting step. The obvious alternative — a task per segment —
transcribes a short phrase faster than the sentence before it and delivers the caller's
words shuffled.

### What was measured rather than assumed

Real inference on this GPU, faster-whisper `tiny` at `int8_float16`: **155 ms** for a
segment with speech energy in it, against a p95 budget of 1.5 s (`ARCHITECTURE` §15). That
is the encouraging number. The discouraging one is `B14`, below, and it changed the design.

### What is deliberately NOT built

- **The encrypted recording.** `ARCHITECTURE` §6 asks the gateway to write it to object
  storage with per-recording key refs; that is P7's key management, and writing a caller's
  audio to disk before it exists is the one thing `D9` was careful to avoid.
- **The Thai bake-off table.** The harness is built, verified and correct — but a real WER
  or latency figure needs **real Thai telephone speech**, and synthetic tones measure
  Whisper's pathology rather than its performance (`B14`). `D30` is not closed, and it
  should not be closed with numbers from a signal generator.
- **The live transcript on the workstation**, and transcription of the agent's own leg
  during the call, which is P6 (`D26`).

### The vocabulary hint lives in `config/`

`config/stt_vocabulary.yaml`, loaded by the domain pack. Thai insurance jargon is a domain
literal and `services/` may not hold one (`D28`) — retargeting this to a hospital line
means editing a YAML file, not the transcriber. `B14` gives that file a second, sharper
reason to be reviewed carefully: its contents can come back out of the model's mouth.

## D97. The Thai call-centre dataset lives OUTSIDE the repo, and nothing it produces is committed
_The user downloaded *Thai H2M call-center audio with script* (AI x Block, Kaggle) so the
audio path could finally be measured on real speech rather than on tones._

- **What it is:** 6378 wav files / **22 GB**, in `krungsri/data/` beside `FullProject/`.
  3189 calls, each with a `_human` side and an `_ai` side, **16 kHz mono 16-bit** — already
  exactly the format `ports/stt.py` demands, so no conversion. Scripts are per-segment with
  timestamps and, valuably, explicit `noise` markers.
- **Decision — it stays outside the repository, and the prepared subset is gitignored.**
  Three reasons, in order of weight:
  1. **It is real customer speech.** The transcripts contain names, service details and
     account numbers — the first file read had a customer reciting a nine-digit number
     aloud. Committing that would be a PDPA breach in our own git history (`D14`), and git
     history is the one place you cannot delete something from.
  2. 22 GB does not belong in a git repository under any circumstances.
  3. `.gitignore` already excludes `*.wav`; `/tests/audio/thai_calls/` and `/models/` are
     now excluded explicitly, because the *references* are `.txt` and would otherwise have
     been committed while the audio was not — the worst of both.
- **The human side only.** `_ai` is a recorded bot prompt: clean, studio-level, and nothing
  like what this system will hear. Measuring on it would produce a flattering number that
  predicts nothing about a real call.
- **`scripts/prepare_dataset.py`** selects N calls reproducibly (seeded), writes
  `<stem>.wav` + `<stem>.txt` pairs the bake-off already understands, and emits
  `segments.tsv` carrying **every segment including the `noise` ones**. That second file is
  the part worth having: their `noise` spans are ground truth for *where nobody is
  speaking*, which is the one thing a CER number cannot tell us and exactly what our own
  endpointer decides (`D9`).
- **Known limitation, recorded rather than discovered later:** every one of the 3189 calls
  is domain **`Government`**, not insurance. So it measures Thai telephone ASR honestly and
  says nothing about insurance jargon — and our `stt_vocabulary.yaml` hint is *wrong* for
  this data, which makes it a fair test of whether the hint hurts when it does not apply.
  A genuinely insurance-domain set would still be worth having (`Q27`).
- **Also on disk:** `Copy of audiofiles.zip`, another 22 GB, which is the archive the
  extraction came from and can be deleted once the extraction is trusted.

## D98. A third guard asks whether that much speech was PHYSICALLY POSSIBLE
_The user's idea, in a form the measurements suggested. Closes the gap `B16` exposed._

- **The proposal, in the user's words:** watch how long the model takes relative to the
  audio length, build up a mean and standard deviation, and flag a transcription that takes
  wildly longer than it should — or, simply, *"for 1s of sound it should take no more than
  X ms"*.
- **The instinct is right and it is the important part:** the two existing guards both read
  the *shape of the text* (`looks_like_a_loop`, `echoes_the_prompt`), so a failure that
  happens to dodge both patterns sails through. A signal from a different axis entirely is
  worth more than a third pattern.
- **Decision: use output length per second of speech, not decode time.** Same instinct,
  strictly better instrument:
  - **it needs no calibration.** Decode time depends on the model, the GPU, the compute
    type and whatever else is on the card. On demo day we would have almost no samples to
    build a distribution from — `D85`'s shrinkage problem in a new place, and there it took
    a log-normal model to handle honestly.
  - **it is closer to the cause.** Whisper's decode time is long *because it emitted more
    tokens*; the token count is the thing, and timing is a proxy for it one step removed.
  - **it survives a hardware change.** A faster GPU moves every timing threshold and moves
    no speech rate.
- **The number is measured, from the user's own dataset**, over 61 hand-annotated segments
  of real Thai call-centre speech:

  | | characters per second |
  |---|---|
  | real speech, median | **7.6** |
  | real speech, p90 | 11.4 |
  | real speech, **fastest observed** | **15.0** |
  | the three real Whisper loops (`B16`) | **39 – 53** |

  `MAX_CHARS_PER_SECOND = 25.0` sits in that gap: 1.7x above anything a human actually
  said, and well under the slowest loop. A test pins it between the two populations, so a
  future retune has to respect both.
- **What this does NOT do, stated plainly.** It is a *detector*, not a preventer. By the
  time the rate is known the eight seconds (`B14`) have already been spent. **The
  preventer is a decode timeout, and it cannot be built yet:** `asyncio.wait_for` around
  `to_thread` does not kill the thread, so a runaway decode has to be killed with the
  process. `D2` already plans `entrypoints/stt.py` as a separate worker; the timeout
  belongs there, and half-implementing it now would be a guard that looks like one and is
  not — which is `B7`'s entire family.
- **Rejected: the statistical version.** Per-model, per-hardware mean and standard
  deviation over transcription time, scaled by audio length. It is the more general idea
  and it is what to build if the flat ceiling ever proves too blunt — but it needs a corpus
  we do not have, on hardware that will change, to measure something a simpler quantity
  measures directly.

## D99. NeMo is its own extra, and Typhoon ASR is not a Whisper model
- **Problem.** `D30` says Thonburian and Typhoon ASR are benchmarked against each other.
  Checking the actual model card rather than assuming: `scb10x/typhoon-asr-realtime` is an
  **NVIDIA NeMo FastConformer transducer** fine-tuned from
  `nvidia/stt_en_fastconformer_transducer_large`, shipped as a single `.nemo` file. It is
  not a Whisper checkpoint, it does not load through `transformers`, and it needs
  `nemo_toolkit[asr]`.
- **Decision:** `TyphoonAsrEngine` is written against the same `SttEngine` port, and NeMo is
  **not** added to the `ml` extra. It gets its own `asr` extra when somebody actually runs
  it — same standing rule as the `ml` extra itself: an unused multi-gigabyte dependency in
  the lockfile is a cost with no payer.
- **Why it is worth the trouble anyway**, and this is more interesting than "a second
  option". A transducer differs from Whisper in three ways that bear directly on failures
  this project has already hit:
  - **no 30-second padding.** Whisper transcribes a fixed window whatever you hand it,
    which is why a 2-second utterance costs what a 20-second one costs. If the latency
    budget turns out to be the problem on this GPU, this is the structural reason Typhoon
    might not have it.
  - **genuinely streaming.** `SttEngine.stream()` is implementable here and is a costume on
    Whisper.
  - **it should not hallucinate on silence** (`B14`) — a transducer has no free-running
    language-model decoder to invent with. *Should* is a hypothesis; the bake-off is how it
    stops being one, and it is one of the more useful things that table can settle.
- **`Q8` is now half-answered.** The model id is confirmed and the licence is `cc-by-4.0`.
  Pricing does not apply — this is open weights, run locally.

## D100. A transcriber that falls behind loses the OLDEST sentence, loudly, and never guesses
_The policy half of `B20`. The bug was that the buffer had no owner; this is the rule that
now decides who it belongs to and what happens when it cannot serve everyone._

- **The constraint that rules out the obvious answer.** `D12` says the call is never
  blocked on AI, so **backpressure is not available**: we cannot slow ingestion when the
  model falls behind, because the caller keeps talking whatever the GPU is doing. Frames
  arrive at wall-clock speed forever. That leaves exactly two ways to fail — grow the
  buffer until the process dies, or release audio somebody still needs — and `B20` shipped
  the second one by accident, silently.
- **Decision, in three parts.**
  1. **Every queued-but-untranscribed segment holds a claim on the buffer.** The floor is
     the oldest outstanding claim, not a fixed window behind the newest segment. One
     consumer draining FIFO makes this a `deque` and a `min`, not a bookkeeping problem.
  2. **The backlog is bounded at `_MAX_BACKLOG_SAMPLES` (120 s), and reaching it is a
     `warning`.** Past two minutes behind live audio the model has effectively stopped —
     the intake recording itself is capped well below that — so the honest choice is to
     abandon the oldest claim and say so. Silence here would recreate `B20`.
  3. **The oldest is what goes.** The newest utterance is the one the agent is about to
     need, and a caller's opening sentence is the one most likely already reflected in the
     menu path they keyed (`D37`). Dropping the newest to save the oldest would trade a
     useful sentence for a stale one.
- **Rejected: `asyncio.Queue(maxsize=...)`.** It is the idiomatic bound and it is exactly
  wrong here — a bounded queue makes `put` block, which makes `feed` block, which is
  backpressure on the media path by another name. `D12` again.
- **Rejected: transcribing in parallel to catch up.** It is the tempting fix for a latency
  problem and it breaks the ordering guarantee `stream.py` exists to provide: a two-word
  phrase finishes before the sentence in front of it and the caller's words reach the agent
  shuffled. If throughput is the problem the answer is a faster engine (`Q29`), not a
  second consumer.
- **What this does NOT fix**, stated plainly: paced over 12 real calls the p95 latency runs
  **4.5 s to 58.7 s** against a 1.5 s budget (`Q29`), and holding the audio correctly does
  not make the transcript arrive sooner — it only stops it arriving **wrong**. Those are
  separate problems and this decision is about the second one.
- **It does, however, make the failure legible**, which is the point of the `warning`. The
  120 s cap never fired across those 12 calls, so the 58.7 s is an honest end-to-end
  latency rather than a truncated one. Had this decision gone the other way — bound the
  buffer silently — the same run would have reported a comfortable latency and a quietly
  shorter transcript, which is `B20` again wearing a bound.

## D101. Dispatch policy: batch what is already waiting; pack to a MINIMUM, never to a maximum
_Two ideas from the user, separated, because they are not the same idea and one is much
safer than the other. Batching is built; packing is designed and deliberately not built._

### The problem, measured

Whisper encodes a **fixed 30-second window** whatever length of audio it is handed, so a
1.5-second utterance costs exactly what a 25-second one does. Measured over the 12 real
Thai calls (`bake_off.py`'s new `pad` column):

| | |
|---|---|
| seconds Whisper actually encoded, per second of call | **2.2 - 3.0, median 2.7** |
| model-seconds per second of audio (`busy`) | 0.16 - 1.48, median 0.45 |
| calls where `busy` exceeded 1.00 | **1 of 12** |

We ask the GPU to encode **2.7x more audio than the call contains**, because a call is cut
into 6-9 short utterances and each one costs a full window. Above `busy` 1.00 the
transcriber can never catch up, which is the mechanism behind the 23-59 s latencies in the
paced run.

### Where the number came from, and it is not our idea

The team's earlier Thai project runs the same model on the same class of hardware and is
noticeably faster. The reason is one argument:

```python
prediction_gen = pipe(KeyDataset(audio_dataset, "audio"), batch_size=4)
```

It puts **four clips through the GPU in one pass**. It also has the easy version of the
problem — a file on disk, so every chunk exists up front and a batch is always available.

### THE RESULT, FIRST: batching bought nothing on this GPU

Measured over the same 12 calls, `MAX_BATCH = 4` against `MAX_BATCH = 1`, everything else
identical:

| | without batching | with batching |
|---|---|---|
| `busy` median | 0.45 | **0.45** |
| `busy` worst | 1.48 | **1.46** |
| calls over 1.00 | 1 of 12 | **1 of 12** |
| CER median | 0.161 | 0.161 |
| turns | 82 | 82 |
| VRAM median | 2734 MB | 2735 MB |

Per call the change ranges from **-7% to +13%** and averages approximately zero. **This is
a null result and it is the useful kind**, because it says the bottleneck is not what I
assumed it was.

**Why it does nothing here.** Batching wins when a GPU is *underutilised* — when the cost
is dominated by launch overhead and there is spare capacity to overlap. A 4 GiB laptop card
running Whisper-medium at fp16 has no spare capacity: four 30-second windows is four times
the arithmetic, and four times the arithmetic takes four times as long however it is
scheduled. **Batching overlaps work; it does not remove any.**

**So the inference about the earlier project was wrong, or at least incomplete.** Its
`batch_size=4` was a plausible explanation for its speed and it is not the explanation —
whatever made that pipeline fast, this is not it. Worth stating because that inference is
in the previous session's write-up and somebody will otherwise repeat it.

**What this promotes.** Packing (Decision 2) is no longer the fallback; it is the option
that can actually work, because it reduces the **number** of 30-second windows rather than
overlapping them. Seven windows per call becoming one or two is a real reduction in
arithmetic, and it is the only lever in this design that is. The other real lever is a
cheaper window — the CT2 `int8_float16` build.

**Kept anyway**, at `MAX_BATCH = 4`: it is correct, tested, costs 1 MB of VRAM and changes
no output, and the demo machine is explicitly "whoever has the strongest GPU" (`PLAN.md`),
where the spare capacity that makes batching pay may actually exist. It is set to `1` to
disable, and a test asserts that switch works.

### Decision 1 (BUILT, and measured as a no-op here): batch what is already queued, never wait

`TranscriptionStream._consume` takes the segment it was waiting for and then drains up to
`MAX_BATCH - 1` more with `get_nowait()`. It **never waits** for a batch to fill.

- **A stream that is keeping up has at most one segment queued**, so this changes nothing
  in the common case. A batch can only form when the model is already behind — which is
  exactly and only when the speed-up is needed. The optimisation is self-scheduling.
- **It cannot make any output different.** Each clip is still transcribed as its own
  utterance with its own boundaries; only the GPU scheduling changes. That is the entire
  reason this is the version that ships first.
- **`BatchSttEngine` is a capability protocol, not a change to `SttEngine`.** An engine
  without a batch path is slower and identical in every other respect. Forcing every
  adapter to grow a fake batch method would make the port lie about what they can do.
- **The guards are unchanged and still run per segment** (`B14`, `B16`, `D98`, `B21`).
  Batching splits dispatch from preparation and publication precisely so it cannot weaken
  a check: a hallucination that arrives alongside three good utterances is exactly as
  dangerous as one that arrives alone.
- **`MAX_BATCH = 4`, capped rather than unbounded**, because every clip in a batch is
  still padded to 30 s and a batch of twenty would ask a 4 GiB card for twenty windows of
  activations at once.

### Decision 2 (DESIGNED, NOT BUILT): pack to a MINIMUM speech duration

The user's refinement, and it is the part that makes packing worth building at all.

The obvious form of packing — glue utterances together until the clip approaches 30
seconds — has two costs that are easy to understate: it **loses per-utterance boundaries**
(you learn only when the first one started and the last one ended) and it **adds delay by
construction**, up to the whole window.

The refinement: **do not pack toward the 30-second maximum. Pack past a configurable
minimum and stop.**

> *"there needs to be at least 5s or etc. (but less than 30s of course) of the gathered
> audio clip before it is sent into the model… while it's still makes the speech time
> window bigger and loses the exact time per utterance… both will not be as bad as the
> full 30s gathering one, and we will at least be able to say that the clip we send to the
> model is not literally just one single word"*

This is right, and it is right for a reason worth stating separately from throughput:
**the single-word clip is a correctness problem, not just a waste.** A one-word utterance
padded with 29 seconds of silence is close to the input that produced `B14` — 8.6 seconds
of decode and invented Thai out of near-silence. Packing to a floor removes the worst
inputs we hand the model, and the efficiency is a bonus rather than the point.

The shape, when it is built:

- **`pack_min_speech_ms`**, configurable, expected to land somewhere in **1500-5000 ms**,
  with a hard ceiling well under 30 s so a packed clip never approaches the window.
- **The cost scales with the setting**, which is what makes it tunable rather than a
  gamble: at 2 s the added delay and the boundary loss are both small; at 10 s both are
  larger and the padding waste is nearly gone. It can be set to 0 to disable packing
  entirely, which must stay a supported configuration.
- **A packed clip still yields one turn per utterance**, using the gap positions we
  already know from the endpointer to attribute the returned text. If that attribution
  cannot be made reliable, the honest fallback is one turn spanning the packed range with
  its `t_start_ms`/`t_end_ms` covering it — and **not** several turns with guessed
  boundaries, which is `B20`'s failure mode wearing a new hat.
- **Never pack across the `max_segment_ms` forced-cut boundary**, because a forced cut
  means the caller was still talking and the two halves are one thought.

### Why packing is not built yet — and why it is now the front-runner

Batching landed first because it cannot change any output. **It also changed nothing else**
(see the result above), which settles the question it was meant to answer: the padding
waste has to be *removed*, not overlapped.

Packing is still not built in the same commit because it changes **what the model sees**,
and every change to what the model sees on this project has produced a bug entry. It needs
its own measurement and its own tests, and the ordering matters: the CT2 and Typhoon rows
are cheaper to run and may make packing unnecessary. But if `busy` is still near or above
1.00 after those, **this is the design, and the user's minimum-threshold form is the version
to build** — not the naive pack-to-30-seconds one.

### Rejected: a second consumer task

The obvious way to go faster is to transcribe two segments concurrently. It breaks the
ordering guarantee `stream.py` exists to provide — a two-word phrase finishes before the
sentence in front of it and the caller's words reach the agent shuffled. If throughput is
the problem the answer is a faster engine or fewer windows, not a second consumer.

## D102. The engine is the CT2 `int8_float16` build of Thonburian — this is `D30`'s answer
> **SUPERSEDED IN PART by `D103` (same day).** The engine choice stands and the latency,
> `busy` and VRAM numbers below stand. **The accuracy numbers do not**: this table was
> measured on a digit-heavy test set (`Q30`) with only one of the two engines reading the
> vocabulary hint (`B19`). On a balanced set with both engines hinted the figures are
> CER 0.089 (fp16) against **0.087** (CT2), not 0.161 against 0.182. Read `D103`.

_The measurement `D30` has been asking for since P0. Paced, on 12 real Thai call-centre
calls, with the endpointer and guards that ship._

### The table

| | Thonburian medium fp16 | **CT2 `int8_float16`** |
|---|---|---|
| p95 utterance-end -> turn, median | 19.5 s | **1.65 s** |
| p95, worst call | **58.7 s** | **2.48 s** |
| p95, best call | 4.5 s | 1.21 s |
| calls inside the 1.5 s budget | **0 of 12** | 3 of 12 |
| `busy` (model-seconds per second of audio), median | 0.45 | **0.09** |
| `busy`, worst | **1.48** | **0.13** |
| CER, median | **0.161** | 0.182 |
| VRAM | 2731 MB | **1106 MB** |

### The decision

**Ship the CT2 build.** `models/whisper-th-medium-combined-ct2`, produced once by
`scripts/convert_ct2.py` from the same `biodatlab/whisper-th-medium-combined` weights.

- **Latency improves by 12x at the median and 24x at the worst**, which is the difference
  between a transcript that helps the agent and one that arrives after the call.
- **`busy` drops from 1.48 to 0.13 at its worst**, so no call is anywhere near the point
  where the transcriber falls permanently behind. That was the *stability* problem, and it
  is gone rather than reduced — `D101`'s whole concern about compounding backlog no longer
  has a case that triggers it.
- **VRAM drops from 2731 MB to 1106 MB.** On a card with 3.2 GiB free that is the
  difference between the STT model owning the machine and leaving room for something else.

### The cost, stated plainly

**CER median goes from 0.161 to 0.182** — about 13% relatively worse, and it is a real
cost, not noise: per call it moves in both directions but the median is clearly up. Some
calls improve (0.188 -> 0.125, 0.139 -> 0.089), several get worse (0.126 -> 0.185,
0.152 -> 0.273).

**It is worth paying, and the reason is not "small number beats big number".** A transcript
that is 2% less accurate is a transcript the agent reads with slightly more care. A
transcript that arrives 58 seconds after the caller finished speaking is not a transcript
at all — the agent has already answered, and `D12`'s promise that AI never delays the call
means the screen is simply empty when they do. The two failures are not on the same scale.

### What this does NOT close

- **The 1.5 s budget is still missed**, at 1.65 s median and 2.48 s worst — but by a factor
  of 1.1-1.7 rather than 13-39. `ARCHITECTURE` §15's number is now a target to close rather
  than a fantasy, and two levers remain: **packing** (`D101`, fewer 30 s windows) and the
  **distilled** checkpoint already in the HF cache, which cuts the decoder rather than
  quantising it. Try distil first; it is a download we have already paid for.
- **Typhoon is still unmeasured** (`D99`, NeMo now installed). A transducer has no 30 s
  window at all, so it is the one candidate that could make packing unnecessary.
- **`B21`'s digit guard was loosened**, and this table was produced on a digit-heavy test
  set (`Q30`). The accuracy numbers here are honest for *this* corpus; re-run on a mixed
  set before treating 0.182 as the engine's CER.

### The CER half of this table was not a fair comparison, and the latency half was

Found immediately after committing it, which is the right time to say so and the wrong
time to have found it.

**`FasterWhisperEngine` applies the vocabulary hint through `initial_prompt`.
`ThonburianHfEngine` did not apply it at all** (`B19`). So in the table above the **CT2 row
was hinted and the fp16 row was not** — with 15 insurance terms, on a corpus that is
entirely `Government` domain (`Q27`), so the hint was not even in-domain.

This is precisely what `B19` warned about in its own last line: *an engine comparison where
the engines disagree about whether they read a parameter is not a comparison.* I wrote that
sentence and then produced the table anyway.

**What survives and what does not:**

- **The latency, `busy` and VRAM conclusions stand.** An `initial_prompt` prepends a
  handful of tokens to the decoder context. It cannot produce 12x at the median and 24x at
  the worst, and it has nothing to do with a 2731 -> 1106 MB memory footprint. **The
  decision to ship CT2 is not in question.**
- **The CER comparison, 0.161 vs 0.182, is contaminated and should not be quoted.** The
  direction of the contamination is not even obvious: an off-domain hint plausibly *hurts*,
  in which case int8's real accuracy cost is smaller than measured; if it helped, the cost
  is larger. Either way the number is not the difference between fp16 and int8.

**Fixed both ways.** `ThonburianHfEngine` now builds `prompt_ids` from the vocabulary and
passes them in `generate_kwargs`, so the hint is genuinely applied (`B19` closed). And the
re-measurement runs `--no-hint`, so both engines are equally unhinted and the comparison is
of the quantisation and nothing else.

### Why the earlier CT2 VRAM figure of 585 MB is withdrawn

The first CT2 run reported 585 MB. That was measured with the previous engine's weights
still resident (`B22`), so the per-engine baseline was polluted and the delta was not the
model's footprint. The clean number is **1106 MB**. Recorded because the wrong figure is
more flattering than the right one, which is exactly when a withdrawn number needs saying
out loud.

## D103. The engine is CT2 int8 **with the vocabulary hint**, measured on a balanced set
> **SUPERSEDED as the engine choice by `D104` the same day.** Typhoon meets the 1.5 s
> budget on 20 of 20 calls where this reaches 7 of 20. Everything below stays correct
> and stays useful: **CT2 is now the documented fallback** for a box where NeMo will
> not install, and the hinted/unhinted 2x2 and the median-instability note below are
> what the rest of this table's methodology rests on.

_Supersedes the accuracy half of `D102`. Same conclusion about which engine; almost none of
the same reasoning, because both of `D102`'s inputs were wrong._

`D102` chose the CT2 build on a table with two defects, and the user had already flagged
the first one:

1. **the test set was digit-heavy** (`Q30`) — every call in it ended with a phone number
   read aloud, and phone numbers are the hardest thing in the corpus to transcribe exactly;
2. **the two engines disagreed about whether they read the vocabulary hint** (`B19`), so
   the CT2 row was hinted and the fp16 row was not.

Both are fixed. The set is now 20 calls chosen with `--mix --seed 7`, digit share spanning
0-49%, and `ThonburianHfEngine` applies the hint through `prompt_ids`.

### What the test set alone was worth

Same engine, same code, only the audio changed:

| Thonburian fp16, unhinted | CER median | mean | worst |
|---|---|---|---|
| digit-heavy set (12 calls) | 0.161 | 0.171 | 0.414 |
| **balanced set (20 calls)** | **0.089** | 0.109 | 0.295 |

**The choice of test set moved the headline number by 1.8x.** Every accuracy figure this
project recorded before today was measured on the pessimistic set. The user asked for this
and it mattered more than either of us expected.

### The 2x2 that `B19` was hiding

Balanced set, same audio and detector throughout. **Mean and median both shown, and the
mean is the one to read** — see the methodology note below, which cost this entry a
correction:

| CER | unhinted (mean / med) | hinted (mean / med) | hint effect, on the mean |
|---|---|---|---|
| Thonburian fp16 | **0.109** / 0.089 | 0.119 / 0.101 | +0.010 — slightly *worse* |
| CT2 `int8_float16` | 0.171 / 0.147 | **0.128** / 0.087 | **-0.043 — much better** |

Read the rows and the columns and two separate things fall out:

- **int8 quantisation genuinely costs accuracy.** Unhinted, CT2 is 0.147 against fp16's
  0.089 — 65% relatively worse. `D102` never saw this because its CT2 row had a hint and
  its fp16 row did not, so the penalty was masked by exactly the thing that repairs it.
- **The prompt repairs most of it, but not all.** Hinted CT2 is 0.128 against fp16
  unhinted's 0.109 — so int8 still costs about **17% relatively** once both are measured
  fairly. The likely mechanism for the repair is that a quantised decoder drifts more and
  the prompt anchors it, which is not a story worth believing on its own — except that the
  **speed moves the same way**: CT2's worst `busy` goes **0.46 unhinted to 0.11 hinted**. A
  steadier decoder emits fewer tokens, and fewer tokens is less time. Two independent
  measurements agreeing is worth more than either alone.

### A correction, and the methodology point behind it

This entry first claimed hinted CT2 was **the best cell on every axis**, on a median of
0.087 against fp16's 0.089. That was wrong, and the way it was wrong is worth more than the
number.

**The median is not stable at 20 samples.** Two runs of an *identical* configuration —
same engine, same audio, same hint, same detector — produced:

| CT2 hinted | CER median | CER mean | turns |
|---|---|---|---|
| run 1 (`--fast`) | **0.087** | 0.128 | 146 |
| run 2 (paced) | **0.124** | 0.130 | 146 |

Turn counts identical and most calls byte-identical: int8 inference is not
bit-reproducible, and with 20 spread-out samples a couple of calls crossing the middle
drags the median a long way while barely moving the mean. **On the mean the best accuracy
cell is fp16 unhinted (0.109), not CT2 hinted (0.128)** — so the trade is real and CT2 is
not free.

`bake_off.py` now prints a per-engine aggregate with mean, median and worst together, so
this is not recomputed by hand and a large mean-vs-median gap is visible as a warning that
one call is doing the talking.

### Decision

**Ship CT2 `int8_float16` with the vocabulary hint applied** — as a deliberate trade, not
because it wins everywhere:

| | fp16 unhinted | **CT2 hinted** |
|---|---|---|
| CER mean | **0.109** | 0.128 (17% worse) |
| `busy` median / worst | 0.25 / **1.25** | **0.08 / 0.11** |
| p95 latency, paced | 19.5 s / 58.7 s worst | **1.68 s / 2.53 s worst** |
| VRAM | 2716 MB | **~1000 MB** |

**The accuracy cost is real: about 17% relatively.** It is worth paying for the same reason
`D102` gave, which survives all of today's corrections intact — a transcript slightly less
accurate is read with more care; a transcript arriving 58 seconds after the caller stopped
speaking is an empty screen, because `D12` means the agent has already answered.

**The hint is now load-bearing rather than a nicety**, and that changes how
`config/stt_vocabulary.yaml` must be treated. It was already true that its contents can
come back out of the model (`B14`); it is now also true that removing it costs 6 CER
points. Both facts point the same way: change that file deliberately, and re-measure.

### `Q27` is answered

*Does an insurance vocabulary hurt on government-domain audio?* **No.** It is mildly
negative on fp16 (+0.011) and strongly positive on int8 (-0.060). The worry was reasonable
and the measurement says keep the hint.

### `distill-whisper-th-large-v3` is rejected

It was in the HF cache and cost nothing to try, which was the whole argument for trying it.
On the balanced set it is worse than CT2 on every axis: CER median 0.096 against 0.087,
`busy` worst 0.23 against 0.11, VRAM 1942 MB against ~1000. A distilled *large* is still a
large; quantising a medium wins. Recorded so nobody spends the download again.

### What is still open

- **The paced p95 on the balanced set: 1.68 s median, 2.53 s worst, 7 of 20 calls inside
  the 1.5 s budget.** `busy` worst 0.13, so the backlog cannot compound and what remains is
  the fixed per-utterance cost, not a queue. The budget is missed by **1.1-1.7x**, and
  closing it needs fewer or cheaper windows rather than better scheduling.
- **Typhoon** (`D99`) is installed and unmeasured. It is the only candidate with no 30 s
  window at all.
- **Packing** (`D101`) looks unnecessary now: it existed to cut the number of windows, and
  at `busy` 0.11 there is no throughput problem left to solve. It stays designed and
  unbuilt, which is the right state for it.

## D104. Typhoon ASR is the engine — it is the only one that meets the latency budget
_Supersedes `D103`'s engine choice, hours after it was made. `D99` predicted this
outcome and gave the structural reason; the measurement confirms it._

### The complete `D30` table, at last

Balanced 20-call set, Silero, same endpointer and guards throughout. Latency paced.

| | fp16 | CT2 int8 + hint | **Typhoon** |
|---|---|---|---|
| p95 utterance-end -> turn, median | 19.5 s | 1.68 s | **0.19 s** |
| p95 worst | 58.7 s | 2.53 s | **0.28 s** |
| **inside the 1.5 s budget** | **0 of 12** | 7 of 20 | **20 of 20** |
| `busy` worst | 1.25 | 0.11 | **0.020** |
| VRAM | 2716 MB | ~1000 MB | 1068 MB |
| CER mean | **0.109** (unhinted) | 0.128 (hinted) | 0.133 (no hint possible) |
| turns produced | 145 | 146 | 143 |

**`ARCHITECTURE` §15's budget has never been met by anything until now, and Typhoon meets
it on every call in the set** — nine times faster than the CT2 build that superseded fp16
this morning, and roughly a hundred times faster than fp16 itself.

### Why, and it is the reason `D99` gave before any of this was measured

Typhoon is an **NVIDIA NeMo FastConformer transducer**, not a Whisper model. `D99` wrote:

> *"it does not pad to 30 seconds. Whisper transcribes a fixed window whatever you give it,
> which is why a two-second utterance costs the same as a twenty-second one here. If the
> latency budget turns out to be the problem on this GPU, this is the structural reason it
> might not be."*

That is exactly what happened. Every Whisper variant pays a full 30-second encode per
utterance — the `pad` column measures it at **2.5-3.0x** the audio actually spoken. A
transducer processes what it is given. Quantising a Whisper (`D103`) makes each window
cheaper; Typhoon does not have the window.

**This is the one prediction in this project that was written down before the measurement
and then confirmed by it**, which is worth noting precisely because most of the others have
gone the other way.

### The accuracy cost, and the honest framing of it

**CER mean 0.133 against fp16's 0.109** — about 22% relatively worse, and it sits between
the two Whisper configurations rather than beating them:

    fp16 unhinted   0.109      <- the most accurate thing measured
    fp16 hinted     0.119
    CT2 hinted      0.128
    Typhoon         0.133      <- and it CANNOT be hinted
    CT2 unhinted    0.171

**Typhoon's number is an unhinted one and cannot be improved the way CT2's was.** A
transducer has no prompt mechanism, so `SttHint.vocabulary` does nothing for it — which is
now an *honest* nothing rather than `B19`'s silent one, because the adapter never claimed
otherwise. That also means the 6 CER points the hint buys CT2 are not available here, and
any future accuracy work on Typhoon is fine-tuning rather than prompting.

**It is still the right choice**, for the reason that has survived every correction today:
a transcript that is a few points less accurate is read with more care; a transcript that
misses the budget is one the agent does not have when they answer. Typhoon is the only
engine where the transcript is reliably *there*.

### Also worth recording

- **It produces 3 fewer turns than the Whisper engines** (143 against 146). Small, and not
  yet explained. It may be the same 3% of real speech the endpointer trims (`score_endpointer`),
  landing differently — but it has not been checked, and "the fast engine also says less"
  is exactly the kind of thing that turns out to matter.
- **`D99`'s other prediction is untested**: that a transducer should not hallucinate on
  silence the way Whisper does (`B14`). The three guards stay regardless.
- **The `pad` column is meaningless on this row.** It computes `30 x calls / audio`, a
  Whisper fact; Typhoon's honest pad is 1.0. Noted in `bake_off.py` rather than left to be
  misread.
- **`D101`'s packing is now definitively unnecessary.** It existed to reduce the number of
  30-second windows. The chosen engine has none.

### Both of the open questions above are now answered

**1. `D99`'s silence prediction is CONFIRMED, and it is a bigger deal than the accuracy gap.**

| 1 second of... | Typhoon | Whisper, from `B14` |
|---|---|---|
| digital silence | **149 ms, empty** | **8578 ms, invented Thai** |
| quiet line hiss | 131 ms, empty | — |
| 220 Hz tone | 146 ms, empty | — |

`B14` is the measurement that created all three guards: Whisper fed near-silence spent 55x
a real utterance's time and returned invented Thai, **including three terms from our own
vocabulary hint, in the hint's own order**. Typhoon returns nothing, quickly, on every
non-speech input tried. That removes the failure mode `D16` calls the dangerous kind —
invented text that looks credible — rather than catching it downstream.

**The three guards stay anyway.** They cost microseconds, they are the degradation path if
the engine is ever swapped back (`D103` keeps CT2 as the fallback), and "the new engine did
not do it in four tries" is not the same claim as "it cannot".

**2. The 3 missing turns are real, small, and not what they first looked like.**

All three are `the model returned nothing for a segment` — logging that exists only because
of `B20`, and would have been invisible two days ago. They are concentrated in **2 calls**;
the other 18 match CT2 exactly.

The obvious hypothesis was that a transducer needs more than a second of audio before it
will emit anything. **Tested directly against real annotated spans, and it is false:**

| span duration | spans tried | Typhoon returned empty | CT2 returned empty |
|---|---|---|---|
| under 1.0 s | 1 | 0 | 0 |
| 1.0-1.5 s | 2 | 0 | 0 |
| 1.5-2.5 s | 12 | 0 | 0 |
| 2.5-4 s | 12 | 0 | 0 |
| over 4 s | 12 | 0 | 0 |

(The two shortest buckets are thin — this corpus has few short annotated spans — so the
sub-1.5 s row is weak evidence, not strong.)

So Typhoon transcribes every span a human marked as speech. The 3 it declined were
**endpointer-cut fragments** whose boundaries are ours, not a human's. On those, Typhoon
declines and Whisper guesses. **Which behaviour is better is genuinely not obvious**:
declining loses content, guessing risks invention, and this project has a bug entry about
each.

### The weakness that IS real: digits

Splitting the 20 calls by how much of the reference is spoken digits, joined on row order:

| | calls | Typhoon | CT2 | gap |
|---|---|---|---|---|
| speech-heavy (<15% digits) | 8 | **0.137** | 0.171 | **-0.034**, Typhoon better |
| digit-heavy (>=15% digits) | 12 | 0.132 | **0.099** | **+0.033**, Typhoon worse |

A clean crossover, and a 0.067 swing between the two halves. **Typhoon is the better engine
on conversation and the worse one on numbers** — and on the call where it lost 2 turns, the
content it lost was part of a spoken phone number (`ห้า สี่ เจ็ด` absent from an otherwise
near-perfect 235-of-244-character transcript).

**Why this is a caveat and not a reversal.** `B21` established that a phone number is the
one item on an agent's screen that has to be exact — but the architecture already knows
that, and does not depend on speech for it. `D44`'s keypad capture is how a policy or
account number actually arrives, as typed digits with provenance, and `D20`'s ANI gives the
calling number without anyone saying it. Spoken digits in an intake recording are
corroboration, not the record. **If that ever stops being true, this decision should be
re-opened**, and the number to re-check is the digit-heavy row above.

### The deployment cost, stated plainly

Typhoon needs `nemo_toolkit[asr]`, which is a large install kept in its own `asr` extra
(`D99`). That is a real cost on a metered connection and a real risk on demo morning if the
box has not been prepared. **CT2 remains the fallback** and is now the *second* engine
rather than the first: it needs no extra beyond `ml`, and `D103`'s numbers stand as the
answer to "what if NeMo will not install".

### `STT_ENGINE=typhoon` now actually selects Typhoon

It did not until this entry. `build_stt` had `typhoon` in the "named in the enum and not
built" branch, so selecting it logged a warning and returned the **scripted** engine — the
second time in one day that the engine a decision entry had just chosen could not be turned
on (`B23` was the first). A test now asserts that every engine the docs recommend has a
branch in `build_stt`, because twice is a pattern.


## D105. Something has to drain the event bus, and it is not the sweep
_The live transcript's first blocker, and it would have been invisible._

- **Problem.** `InMemoryEventBus.publish()` only enqueues; handlers run on `drain()`. That
  is deliberate and load-bearing — nothing runs until you say so, which is why a scenario
  replay settles in the same order every time and its golden output is comparable at all
  (`D15`). But in the **running API** the only `drain()` was a FastAPI background task on
  `POST /v1/calls/intents` (`mobile.py`), plus one at shutdown. So a subscriber to anything
  other than `intent.created` would be **correct, tested, and never run** until some
  unrelated app request happened along.

  Measured before writing a line of the fix, because this project has a habit of asserting
  what it has not checked:

  ```
  published on the bus : ['intake.started', 'transcript.turn']
  subscriber saw       : []    <- after publish, before any drain
  subscriber saw       : []    <- after 5 sweeps
  subscriber saw       : ['transcript.turn']    <- after an explicit drain()
  ```

- **Decision.** A **second periodic driver** in `api/app.py`: `pump_once(container)` calls
  `bus.drain()`, `_pump_forever` runs it every `BUS_DRAIN_INTERVAL_S` (default **0.05 s**),
  and it is started and cancelled in `lifespan` beside the sweeper. The bus itself is
  unchanged.

### Why not a line inside `sweep_once`

That was the obvious version and it is wrong on a number. The sweep runs every **1.0 s**
and exists for offers, RONA and heartbeat expiry, where a second is nothing. The bus is on
the transcript's path to the screen, where `ARCHITECTURE` §15 allows **1.5 s** end to end
and the model already spends 0.19 s of it (`D104`). Borrowing a driver whose period is
two-thirds of the entire budget would have spent the whole win from choosing Typhoon on
scheduling. Two drivers, two deadlines, and a test asserts the sweep does *not* drain so
nobody tidies them back together.

### Why not drain on the request path

`accept_offer` could call `drain()` and return a complete snapshot. It would also run every
subscriber in the process — context assembly included — before the agent's screen could
paint, which is exactly what `D6` moved **off** the request path. The cost of not doing it
is that a turn published *inside* a request lands up to one pump later; at 0.05 s against
§15's 1 s for match-to-screen, that is not a cost worth buying with `D6`.

### The pump swallows exceptions, and that is not laziness

The bus is built `strict_handlers=True`, so one handler raising aborts the whole pass. In a
loop with no `try`, a single bad subscriber would stop **every event in the process reaching
every consumer, permanently**, and it would look like the system had gone quiet — `B11`'s
shape, where the only feedback is something not happening. `pump_once` logs the exception
and the next pass runs. A test asserts exactly that.

## D106. The transcript is held until somebody ACCEPTS, and every push is complete
_`services/transcription/delivery.py`. `ARCHITECTURE` §14 calls this consumer "Agent
Delivery"._

- **Problem.** Turns are produced, ordered, and published as `transcript.turn`. Nothing
  took them off the bus. And the obvious wiring does not work, for a reason that is the
  product rather than a technicality: **during intake the call is not assigned to anybody**.
  That is the whole point — the transcript is built *while the caller waits*, before an
  agent accepts — so at the moment a turn is published there is no `agent_id` for
  `AgentHub.send` to address.

- **Decision.** One service, three subscriptions, one piece of state:
  - `transcript.turn` -> append to that call's list; push **only if** the call already has
    an agent.
  - `offer.resolved` with outcome `accepted` -> record the agent and push everything so far.
  - `call.state.changed` to a terminal state -> forget the call.

### The flush is on ACCEPT, not on OFFER, and the line is deliberate

An offer can be declined or time out, and the call is re-matched to somebody else (`D52`).
Flushing on the offer would mean an agent who declines has read the caller's words
**verbatim** for a call they never took. The offer card keeps the gated *summary* it already
has (`D69`) — a preview built from `BriefOut`, which is a different disclosure from a
transcript. `D69` is the precedent for waiting, not a licence to widen it.

### Every push carries the WHOLE transcript, never a delta

`D68`'s rule — if the server knows it, the server says it — in the place it matters most.
A delta means the client accumulates, and a client that accumulates can drop one message
and show a transcript with a sentence **missing from the middle**, with nothing on screen
to say so. A complete payload is idempotent, needs no gap handling on reconnect, and makes
the newest message the only one that matters. A turn is a few hundred bytes; a whole intake
is smaller than one snapshot.

### It is NOT gated on assurance, and that is a decision rather than an omission

`D74` gates what an agent may **say and do**; `D53` and `B5` are about the customer's
*record* — a policy number the caller never mentioned. This is neither. It is the caller's
own speech on the call being taken, at whatever level they have reached, **including L0**.
An anonymous cold caller explaining their problem while they wait is precisely the case
with no other source of context, and it is the case the pitch is about. Withholding it
would delete the feature to protect nothing. What must never appear in this panel is
anything *looked up*, and nothing in it is.

### `WRAP_UP` deliberately does not clear it

The wrap-up is drafted **from the transcript** (`ARCHITECTURE` §12), and
`active_call_session_id` goes null the instant the record is saved (`D68`). So the snapshot
falls back to `wrapup_call_session_id`, and only a terminal state drops the buffer. Clearing
at hang-up would empty the panel at the one moment the agent is reading it.

### What it is not: durable

⚠️ This was true when written and is **no longer**: `D114` gave `transcript_turns` a
table and a subscriber that writes it per turn. What follows describes the state at
`D106`. `transcript_turns` had no table and no ORM model (`DATA_MODEL` §6), so this was a projection
with **no durable half** — said plainly rather than implied. A restart loses an in-flight
intake's transcript, which is the same thing a restart already does to the caller's place
in the queue (`D78`). Persisting turns is its own slice and `PLAN` lists it separately.

## D107. Nothing opened a recording, and the stage-safe engine had no lines
_Two gaps found by tracing the path from a WAV file to a browser tab and asking, at each
hop, what calls this. See `B24`._

- **Problem.** `TranscriptionService.open()` was called by `tests/unit/test_transcription_
  service.py` and by **nothing else in the repository**. `run_offer` returns a `HoldReport`
  saying `recording=True` and nothing acted on it, so the running system opened no leg,
  endpointed no frame and published no turn. Separately, `build_stt` constructed
  `ScriptedSttEngine([])` — the engine whose own docstring calls it *"the stage-safe demo
  path, when we would rather not bet on live ASR in a noisy room"* — so choosing the safe
  engine produced an empty transcript panel.

- **Decision, three parts.**
  1. **The demo endpoint opens the recording** when the caller consented, and closes it on
     accept. At P5 the telephony adapter does both; marked `# DEMO:` in place per
     `CLAUDE.md` rather than hidden in another repo.
  2. **`POST /v1/demo/calls` takes an `audio` filename** and plays that WAV down the open
     leg in 20 ms packets, in the file's own format, letting the gateway normalise — which
     is what `media/sources.py` was built for (`D96`). `audio_realtime` paces it at
     wall-clock speed. This is what makes the whole path runnable with no telephony.
  3. **The scripted engine's lines come from `config/demo_transcript.yaml`**, one per
     endpointed utterance. Timings are **not** in the file: `TranscriptionStream` stamps
     every turn with its segment's real start and end, so a scripted line inherits the
     timing of whatever audio was actually played. That is what makes the fallback look
     like a transcription rather than a slideshow.

### The filename is a bare filename, checked after resolution

`audio` names a file inside `Settings.demo_audio_dir` and may contain no separator. A path
in a request body is a file-read primitive, and rejecting `..` by inspecting the string is
the version of that check which keeps getting bypassed — so the comparison is between
*resolved* paths and the containing directory. That the endpoint exists only when
`demo_login_enabled` is not the argument: a demo flag left on is exactly the configuration
this would be exploited through, so the check is here **as well as** there.

### `ReplayableSttEngine`: a capability, because one instance per process is wrong for a script

The container builds **one** `SttEngine` for the process. Correct for a model, which is
stateless per utterance. Wrong for a script, which carries a cursor: the first demo call
consumed every line and the second — and every one after it — found the script exhausted
and rendered an empty panel. `TranscriptionService.open()` now calls `reset()` on an engine
that offers it, checked with `isinstance` against a `runtime_checkable` Protocol, the same
shape as `BatchSttEngine` (`D101`) and for the same reason: exactly one adapter has it, and
putting `reset()` on `SttEngine` would oblige every real engine to grow a meaningless no-op.

**With two recordings genuinely overlapping the cursor is shared and the second resets the
first.** Accepted deliberately: it is a demo artefact, the demo pattern is sequential, and
the alternative is an engine instance per call — which for a real engine means loading a
model per call.

### The rate guard applies to scripted lines too, and it is not obvious

`D98` refuses a turn carrying more than ~15 characters per second of the audio it arrived
on, measured from real Thai. It does not care that the text came from a file. So the
fallback script and the audio it plays over **have to be sized for each other**: a
35-character Thai sentence needs about three seconds of speech behind it, and on a
one-second utterance every line is silently dropped. Written into the config file's own
comments, because the failure is a blank panel with nothing in the log to explain it.


## D108. There are four ways a caller goes unplaced, not two
_Forced by `B25`. Extends `D50`, which split one outcome into two for the same reason._

- **Problem.** Making availability a hard filter (`B25`) changes what "no qualified agent"
  means. Before the fix an agent on their lunch break was a *candidate*; after it they fail
  a filter, so a floor where everybody is on a break would have been reported as
  `NO_QUALIFIED_AGENT` — whose Thai rationale reads *"ไม่มีเจ้าหน้าที่ที่มีทักษะ/ภาษาที่ตรง
  ออนไลน์อยู่"*, i.e. **fix your roster**. That is the exact conflation `D50` was written to
  prevent, reintroduced by a fix for something else.
- **Decision.** `MatchKind` gains two members. All four say something operationally
  different to whoever is watching the queue:

  | kind | what it means | what to do |
  |---|---|---|
  | `ALL_QUALIFIED_BUSY` | qualified, available people exist; this caller lost the matrix on this tick | nothing — it resolves in about a second |
  | **`NO_AGENT_AVAILABLE`** | people with the skill are signed in and none can take a call: break, ACW, already ringing | ask somebody. Waiting helps |
  | **`ALL_DECLINED`** | everyone who could take it has already been offered it and said no | since `D113`: the exclusions are cleared and the caller goes round again on the next tick. Before that, nothing helped |
  | `NO_QUALIFIED_AGENT` | nobody with the skill is here at all | roster problem; waiting cannot fix it |

- **`ALL_DECLINED` is the one worth pausing on.** The other three resolve with time; this
  one does not, because `D52`'s exclusion is permanent for the life of the call. It used to
  be indistinguishable from an empty roster, so a supervisor looking at a stuck caller was
  told the wrong thing about a floor of idle agents. **Naming it does not fix it** —
  whether the exclusion should ever be lifted is a policy question, and it was `Q31` —
  **closed by `D113`**, which clears the set and sends the caller round again.
- **Why the counts are computed the way they are.** `already_offered` is checked *first* in
  `hard_filter`, deliberately (`D50`), so an excluded agent's other reasons are invisible.
  That is right for this caller — "they already turned you down" beats "and they are also
  on lunch" — and it is why `declined` is counted as its own bucket rather than folded into
  the availability one.
- **Alternative considered: keep two kinds and put the detail in the rationale string.**
  Rejected for `D50`'s own reason: the kind is what a query filters on and what a dashboard
  groups by. A distinction that exists only inside a Thai sentence is a distinction nobody
  can count.

## D109. Declining and pausing are one click, because the alternative is twenty seconds of silence
_Raised by the user: "add a decline-and-not-ready button, about the same behaviour as
letting the ringing time run out."_

- **Problem.** A plain decline leaves the agent `READY`, and the decline route re-matches
  immediately — so the next caller can ring the same desk a second later. That is **correct**
  when the agent meant *"not this call"*, and wrong when they meant *"not right now"*: after
  a long call, when something has come up, or after accidentally pressing *Save & Ready*.
  The system already had the second ending and the only way to reach it was to **let the
  card ring out** — twenty seconds of a desk ringing at somebody who has already decided,
  and a caller waiting the whole of it for a `RONA` that was never in doubt.
- **Decision.** `POST /offers/{id}/decline` takes `stop_offering: bool`. When set, the
  agent lands in `AVAILABLE` + `NOT_READY` — the same place RONA puts them — **before** the
  re-match tick runs, so they are not a candidate for the caller they just declined nor for
  the next one in the same pass. The card gets a second button, *ไม่รับ + พักรับสาย*.
- **A flag, not a second request.** `NOT_READY` is the one intent the *platform* writes
  (`D51`); an agent cannot declare it. A client doing decline-then-declare would be refused
  by `declarable_intents`, correctly, and working around that on the client would put a
  rule in two places.
- **The reason is `declined_and_stopped`, not `rona_missed_offer`.** Same state, different
  fact: one is *nobody picked up*, the other is *somebody chose*. `D59` exists because
  `not_ready` alone cannot tell three situations apart, and this is a fourth; the screen
  says a different sentence for each. It is logged `set_by="agent"` where RONA is
  `set_by="platform"`, so the state log keeps the difference too.
- **What this deliberately is not:** a *"show me a different caller"* button. See `Q32`.

## D110. The encrypted recording: one wrapper does the crypto, and the key ring is the tenth port
_The last piece of P3, and it turned out to be four decisions rather than one. Closes
`ARCHITECTURE` §6's third clause, which the media gateway's own docstring had been quoting
as "not built" since `D96`._

- **Problem.** `ARCHITECTURE` §6 asks the media gateway to *"write the encrypted recording
  to object storage"* with a per-recording key reference, and `DATA_MODEL` §6 has had an
  `audio_recordings` table on paper since P0. None of it existed. What *did* exist was the
  MinIO container in `infra/docker-compose.yml`, `ports/blob_storage.py` with
  `encryption_key_ref` already on `StoredObject`, and `BlobStorageName.{memory,localfs,
  minio,s3}` in `Settings` — a config surface with **nothing behind it**, which is `B23`'s
  shape: a name that cannot select anything is worse than no name at all.
  `InMemoryBlobStorage` was written, correct, and instantiated by nothing in the
  repository, including its own tests.

### 1. Encryption is a WRAPPER over the port, not a feature of each backend

`EncryptingBlobStorage` wraps any `BlobStorage` and hands the backend ciphertext. Four
implementations exist and there will be more; if each encrypted its own objects there
would be four pieces of cryptography to review, and the one nobody reviewed would be the
one holding a real recording. **Envelope encryption**, AES-256-GCM: a fresh data key per
object, that key wrapped by a master which never leaves the ring, and the wrapped key
stored in the object's own header.

    RCE1 | u16 len(key_ref) | key_ref | u16 len(wrapped) | wrapped | nonce(12) | ciphertext+tag

Self-describing on purpose — restoring one recording needs the ring and that object and
nothing else. A blob that does not begin `RCE1` is returned untouched, which is how a
store that also holds plaintext prompt clips keeps working, and it is why `encrypt=False`
still reports `encryption_key_ref=None` rather than pretending.

**The checksum is of the plaintext.** GCM's nonce is fresh on every write, so a checksum
over the ciphertext could never answer the question the column exists for: *is this the
same recording?*

### 2. `KeyRing` is the tenth port, and `LocalKeyRing` says out loud that it is dev-grade

A key ref only means something if something outside this process owns the key. The port
has two methods — give me a data key for this object, unwrap this one — which is what
every KMS does, so P7's vault adapter is a second implementation and nothing else changes.

`LocalKeyRing` has two modes and **the difference is a startup decision rather than a
detail**. With `RECORDING_MASTER_KEY` it is durable. Without one it generates a master at
construction and logs a warning, which keeps the promise that this system runs with no
keys, no services and no GPU. `Settings._check_coherent` then **refuses an ephemeral key
against a durable store**: ciphertext nobody will ever read again is a recording that
exists, costs money, satisfies an audit on paper and plays nothing — strictly worse than
an honest gap. Against `memory` it is exactly right, because the objects die with the key.

### 3. `localfs` is allowed now, and the old refusal was right at the time

`InMemoryBlobStorage`'s docstring said a local store was *"deliberately not"* built,
because a real recording must never land unencrypted on a dev machine (`D14`). Correct
while nothing encrypted; wrong now. The factory always applies the wrapper, so what lands
in a directory is ciphertext with no key beside it, and the property the refusal protected
is true **by construction** rather than by absence. `build_blob_storage` is the
enforcement point — constructing a backend directly is a test's business, and nothing in
`services/` does it.

**One adapter covers `minio` and `s3`**, because the difference is an endpoint URL and
MinIO exists to be S3 on a laptop. boto3 is synchronous and runs in a thread, which is
fine for a call that happens once per recording and never on a request path; it lives in
an `s3` extra imported at point of use, the same shape as the STT adapters, because this
module is imported on a CI box with no AWS SDK. `ensure_bucket()` is a **capability**
(`ProvisionableBlobStorage`, the `D101`/`D107` pattern) called once at startup — not
lazily on the first `put`, because the first recording of the day is not the moment to
discover the credentials are wrong, and because `flush_pending` treats a write failure as
transient and would retry a misconfiguration forever.

### 4. The recorder is a SINK, and the upload never happens on the accept path

`services/recording/` **subscribes** to the gateway rather than living inside it. The
gateway already fans normalised frames to whoever asked and the transcriber is one such
consumer; a second costs nothing and keeps the boundary that lets P5 swap Asterisk for
Twilio — writing the object from inside would put a bucket, a key ring and a retention
policy behind it.

Two consequences, both load-bearing:

- **`MediaGateway.open_leg` is now idempotent.** Two independent consumers open a leg and
  neither may depend on the other running (`D12`): a transcriber that is down must still
  record, and a caller who declined analysis may still be recorded. Replacing the leg
  would have silently discarded the first opener's `sinks` — a component correct, running,
  and fed nothing, which is `B24` exactly. Where the two disagree about the format the
  first wins and the second is logged, because a silent disagreement decodes A-law as
  µ-law: loud, plausible garbage nobody would blame on the codec (`D96`).
- **`close()` seals; `flush_pending()` uploads, from the sweep.** An object-store round
  trip between an agent pressing Accept and the caller hearing them is `D12`'s rule broken
  in a new place. Sealing is a list handed to a queue. A failed upload keeps its place and
  is retried on the next pass, and **the row is written only after the store confirms it**
  — a reference to an object that was never written is a recording that looks retrievable,
  satisfies an audit, and plays nothing.

**Consent is checked at close, at the last possible moment.** A caller who pressed 2 has
frames flowing the whole time — the offer window IS the recording window (`D21`) — and no
`recording` consent, so their speech is transcribed in memory for the brief and **never
stored**. Checking at `open()` would be too early: consent arrives on a keypress that can
land after the leg does. Verified on a running server against real MinIO — two calls, one
consenting and one declining, one object in the bucket.

**Every ending that is not Accept arrives as a state change**, so the service subscribes
to `call.state.changed` for terminal states, the same shape as `TranscriptDeliveryService`
(`D106`). Without it an abandoned caller's buffer would sit open for the life of the
process, holding audio nobody stored. `WRAP_UP` is not terminal and does not seal.

### What the retention promise actually is

`delete_after` is written at upload time from `RECORDING_RETENTION_DAYS`, never computed
at purge time. Computing it later would mean lowering the setting silently shortened the
life of audio already held and raising it silently extended it — and the promise that
matters is the one made when the caller said yes.

`scripts/purge_recordings.py` is the job (`D14`'s *"erasure job across both stores and
object storage"*, for the audio half). **A script, not a background task**: a purge
running unattended inside the demo process is one more thing that can fail on stage, and
deletion is the operation you least want happening on its own. It prints the store it is
about to act on every time, has `--dry-run`, and **refuses outright on
`STORAGE_BACKEND=memory`**, where a clean run would be a green tick over an untouched
bucket — the most misleading possible outcome.

**The object goes before the row.** The other order can leave an object with no row
pointing at it, which is audio nobody knows they are holding and is invisible to every
report. A failed object delete keeps its row and is retried.

### What this is not

Not the live call — `D26`'s agent leg is P6. Not the transcript, which still has no table
(`DATA_MODEL` §6). Not a player on the agent's screen. And **not P7's real key
management**: `LocalKeyRing` holds the master in the process environment, so anyone who
can read that environment can read the recordings, and it says so in its own docstring.

## D111. An engine failure reaches the agent's screen, instead of only the log
_The smaller half of `NEXT_SESSION`'s "two small things in the audio path", and it had been
a **gap** rather than a wait since `D96`._

- **Problem.** `IntakeService._degradation()` returned `DegradationReason.NONE`
  unconditionally. Its docstring was honest about why — an intake with no turns can mean
  the caller said nothing or that the transcriber was down, and only a layer that can see
  the transcriber knows which — but that layer had existed since `D96` and nothing carried
  the knowledge back to it. Meanwhile `emptyTranscriptReason()` in `panels.tsx` has
  rendered a sentence for `stt_unavailable` since `D106`, and **no code path could reach
  it**. `TranscriptionStream._consume` swallowed engine failures (correctly — `D12`) and
  the fact died in the log.
- **Decision.** A second sink on the stream, `LossSink`, called with **how many utterances
  an engine failure just cost**. `TranscriptionService` forwards it to
  `IntakeService.on_transcription_lost`, which counts it on the live hold.
  `_degradation()` then answers for real.

### The rule is narrow on purpose

`stt_unavailable` is claimed only when the engine failed **and** nothing at all was
transcribed. That is exactly the case the screen has a sentence for: an empty panel whose
emptiness is ours rather than the caller's.

A call where five of six sentences arrived is *thinner*, and saying *"the transcription
system was unavailable on this call"* about it would be a bigger claim than the evidence
supports. It is logged loudly and the brief does not lie about it. If that partial case
ever needs to reach the screen it wants its **own** `DegradationReason`, not this one
stretched — the same argument `D50` and `D108` made about conflating unplaced reasons.

### The stream reports; it does not conclude

`TranscriptionStream` cannot tell a silent caller from a dead engine — it only ever sees
what it dispatched. So it counts and hands the count up. Putting the judgement in the
stream would have been the version that guesses, which is what the original docstring
refused to do.

### Two tests, and the second is the one that stops it over-reporting

A broken engine must produce `stt_unavailable`; **a quiet caller must not**. Most callers
who accept the recording and then wait in silence produce no turns at all, so a rule that
looked only at "the transcript is empty" would put a system failure on the agent's screen
about every one of them.

⚠️ The fake for the broken engine originally defined `transcribe` — a method the port does
not have — so the stream failed with an `AttributeError` and the test passed for a reason
that had nothing to do with a broken model. It is `transcribe_utterance` now, and the test
asserts the engine was **actually asked**.

## D112. The decode timeout, and the process that makes it real
_`D98` designed this guard and then refused to build it. This builds it, three phases
later, exactly where `D2` said the worker would go._

- **Problem, in `D98`'s own words:** the character-rate guard *"is a detector, not a
  preventer. By the time the rate is known the eight seconds (`B14`) have already been
  spent. **The preventer is a decode timeout, and it cannot be built yet:**
  `asyncio.wait_for` around `to_thread` does not kill the thread, so a runaway decode has
  to be killed with the process."* That was correct, and it is why nothing was
  half-built — a guard that looks like one and is not is `B7`'s entire family.
- **Decision.** `entrypoints/stt.py` is the worker `D2` planned; `SubprocessSttEngine` is
  the parent side, and it is **an adapter implementing `SttEngine`**. It wraps whichever
  engine `build_stt` would have made, is selected by `STT_WORKER=subprocess`, and nothing
  above it can tell — which is the ports-and-adapters point (`D3`) and the only reason
  this could be added after the fact at all.

### What happens on a timeout, exactly

The child is **killed**, a `TransientError` is raised, and `TranscriptionStream._consume`
catches it the way it catches any engine failure — so the utterance is lost, counted, and
since `D111` the reason the brief is thin reaches the agent's screen. The next utterance
respawns the worker and pays the model load once.

That is the trade: **one lost sentence and a reload**, against a decode that would
otherwise hold the single consumer for as long as it liked while every later utterance
queued behind it — `B20`'s compounding backlog, from the other end.

### The deadline covers the SEND as well as the reply

A wedged worker stops reading as well as answering. Whether that blocks the parent depends
on the OS pipe buffer, the utterance length, and how much the child consumed before it
wedged — none of which this code controls. It was seen to hang once during development, on
an utterance around 80 KB, and **it does not reproduce reliably**; that is the argument
rather than a counter-argument. A deadline that covers half an exchange is not a deadline,
and the half it leaves out is the half whose behaviour is platform-dependent.

### The model load is deliberately outside the deadline

`_ensure()` has its own generous bound (`startup_timeout_s`, 180 s) because a cold Typhoon
load is not a runaway decode and must not be killed as one. `STT_DECODE_TIMEOUT_S` is 8.0,
which is what `B14` measured a **single second of near-silence** costing on this GPU: the
guard is for the pathological case, not for a slow model. And `api/app.py` now warms the
engine at startup, in the background — the reason `SttEngine.warmup` exists at all, and
in the background because a 20-second startup would make a restart look like a hang.

### Two things the protocol had to get right

**stdout is frames and nothing else.** `readycall.logging` already writes to stderr — a
happy accident this design depends on — and the child additionally rebinds `sys.stdout` to
stderr before building the engine, because model libraries print and a stray `print` inside
a transformers import would corrupt the stream in a way that looks like a protocol bug.

**The child reads with BLOCKING I/O in a thread.** The first version used
`loop.connect_read_pipe(..., sys.stdin)` and died on Windows with
`OSError: [WinError 6] The handle is invalid` under the Proactor loop — a worker that would
have passed on Linux CI and failed on the demo laptop. Blocking reads are also simply right
here: the process answers one utterance at a time, so its event loop has nothing else to do
while it waits.

### What it deliberately does not do

**No `transcribe_batch`.** `BatchSttEngine` is a capability (`D101`) and this does not offer
it, so the stream falls back to a plain loop. That costs nothing measurable — `D101`
measured batching as a **no-op on this GPU** — and adding it would mean a partial batch
dying on one utterance's deadline, a worse trade than the one it buys.

**One request at a time.** The transcriber has a single consumer by design (`B20`), and a
worker that interleaved requests could not be killed on a deadline without taking somebody
else's utterance with it.

**`inline` stays the default.** Every test, every scenario and the stage demo run in-process
on the scripted engine, where a subprocess would buy nothing and cost a spawn. The timeout
is for the configuration that has a real model in it.

### A property worth knowing

Killing the parent leaves **no orphan**: the child's stdin closes, its blocking read returns
EOF, and it exits on its own. Verified by hard-killing the API and looking for the process.

## D113. A caller the whole floor declined goes round again, and the card says so
_Closes `Q31`, which had been open since `D108` named the problem. The three sub-decisions
were the user's; the reasoning for each is theirs and is recorded here because it is the
part that will still matter when the code has changed._

- **Problem.** `D52` excludes an agent who declined or missed an offer, for the life of the
  call. That is right for the *next tick* — without it the solver re-picks the person who
  just said no — but nothing in it argued for permanence, and permanence stranded people.
  Once every qualified agent had declined there were no candidates left, and **`D93`'s
  wait-ceiling rescue could not help either**, because it picks from qualified agents and
  every one of them was excluded. `D108` made the situation *legible* (`ALL_DECLINED`);
  it said plainly that naming it does not fix it.

  The bug had no exception and no failing test. The caller simply waited, for the life of
  the shift, while the queue showed them as being handled.

- **Decision.** When a tick reports `ALL_DECLINED`, clear the exclusions, increment a round
  counter, and let the next tick offer them again.

### Clearing the set, not expiring per agent

A caller is exhausted as a **set**. An agent-by-agent timer would re-offer to the person
who declined ten seconds ago while somebody who declined ten minutes ago stayed excluded —
arbitrary, and it would make the order of a second round depend on the timing of the
first rather than on fit.

### It happens on the NEXT tick, not the same one

A second solve inside the same tick would buy about a second for a caller who has already
been round a whole floor, at the cost of two decision records for one moment. The record
of the exhausted round is exactly the one a supervisor needs to read cleanly.

### RONA counts as a decline — the user's call, and the reasoning is theirs

*"The caller cannot tell the difference."* And the failure mode of the other reading is
worse than it looks: if a missed offer did **not** count toward exhaustion, one agent who
walked away from their desk would keep the pool looking un-exhausted forever, and the
caller would never get a second round at all. The state log still tells the two apart —
`rona_missed_offer` versus `declined_and_stopped`, `set_by="platform"` versus `"agent"`
(`D109`) — so the distinction survives where it means something.

### The cap is config, and the default is NO CAP

`guards.max_offer_rounds`, in `matching_weights.yaml`. **0 means keep circling**, and that
is what ships. This was the user's decision and the argument is worth keeping verbatim
enough to survive:

> *a caller who is cut off has to start again from the menu; a caller who is still holding
> can hang up whenever they choose, and that choice is theirs.* "Waited an hour and reached
> an agent" is a better outcome than "waited thirty minutes and was dropped" — and a
> patient customer who genuinely needed the line should not be cut off on their behalf.

They also asked that it be a knob rather than a rule, precisely because it is **business
logic** rather than an engineering property: an operation that would rather hand a
long-waiting caller to voicemail sets it to 2 or 3, and nothing else changes. It lives in
the weights file rather than in `Settings` for `Q26`'s reason — the matcher reads that
file, and an env var the code never consults is worse than no env var.

**When a cap does fire it is deliberately NOT a state change.** `D25`'s voicemail path —
record a message, create a briefed callback — is P6 and does not exist, so moving the call
to `VOICEMAIL` would strand it in a state nothing handles: the half-built guard `B7` keeps
teaching. The caller stays reported as `ALL_DECLINED`, which is true, and a supervisor sees
somebody nothing will resolve.

### The card says two things, and the second one is the interesting one

**"สายนี้ถูกส่งต่อครบทุกคนแล้ว และวนกลับมาอีกครั้ง (รอบที่ N)"** — with the count, at the
user's request. An agent seeing the same call twice with no explanation concludes the
system is broken; and naming it is also the point, because it is the sentence that makes
somebody take it.

**"ขณะนี้คุณเป็นเจ้าหน้าที่คนเดียวที่รับสายนี้ได้"** — shown only when this agent is the
only one who could take the call. The user raised this and then raised the obvious
generalisation — *always* show how many agents can take it — and then talked themselves
out of it, correctly. It is not shown, for three reasons:

1. **"Three others could take this" is a diffusion-of-responsibility prompt**, on a card
   whose other button is *decline*. The user got there first: *"it might be encouraging
   the agent to reject it."*
2. **It is not actionable.** The agent can do nothing with "3". It is information about
   the system, not about this caller.
3. **The asymmetry is the design.** Show the fact when it *increases* responsibility, never
   when it diffuses it. "You are the only one" is the one thing an agent cannot work out
   from the card, and it is the truthful answer to what declining will do — the call comes
   straight back to them.

**It is counted from the decision's own candidates**, where `hard_filter_failed is None`
means the agent passed **the same filter the matcher used**. Not a second computation of
availability: that is exactly how `B25` happened, with `PresenceView.offerable` and
`AgentPresence.is_available()` both answering a question the matcher never asked. And note
what it includes — an agent who declined earlier *in this round* is excluded and does not
count, which is the truthful reading for the person holding the card.

### Verified

On a running server, as the only motor agent: decline, and the offer comes back with
`offer_round: 2` and `sole_candidate: true`. In a browser, both Thai sentences render on
the card. Under load, `tests/integration/test_floor_under_load.py` declines every offer for
one caller across a whole floor repeatedly and asserts they are still in the pool and have
gone round more than once.

⚠️ **The stress suite's `D52` invariant still reads "nobody currently excluded is holding an
offer", and that is still true** — the set is cleared, so a re-offered agent is no longer in
it. What is no longer true is "nobody is ever rung twice about one call", which the
invariant never said.

⚠️ **And the consequence this entry failed to name, which cost `B28` the same day.** Clearing
the exclusions means **one agent can hold two assignments for one call** — the round-1
decline and the round-2 accept. Every lookup phrased as "this agent's assignment for that
call" had been quietly relying on there being only one, and the first one a dict yields is
the *decline*. `end_call` refused to end a call the agent was demonstrably on, and
`attest_identity` / `start_capture` went on authorising an agent who had said no. When a
change removes a uniqueness guarantee, the entry has to say which code was leaning on it.

## D114. The transcript is written down as it happens, by a fourth subscriber
_`ARCHITECTURE` §6 has asked for this since P0 and `DATA_MODEL` §6 has carried the column
list just as long, under a warning that nothing wrote them. `D106` said plainly that the
delivery service was "a projection with no durable half". This is the durable half._

- **Problem.** Turns were produced, ordered, published, held for delivery and rendered —
  and never written anywhere. A restart lost an in-flight transcript, which was defensible
  while it matched what a restart already does to the caller's place in the queue (`D78`),
  and stopped being defensible once the *audio* became durable (`D110`): a call whose
  recording survives and whose words do not is a worse asymmetry than losing both.

- **Decision.** `transcript_turns` is the eleventh table, and `TranscriptRecorder` is a
  **fourth subscriber** to `transcript.turn`.

### Its own subscriber, not a line inside delivery

`ARCHITECTURE` §14 lists Analysis, Agent Delivery and call-progress as the consumers;
persistence is another one and it gets its own. Putting the write inside
`TranscriptDeliveryService` would tie a storage failure to the agent's screen — and the
screen is the half that must not be blocked (`D12`). Separated, a failed write costs a
durable row and nothing else, which a test asserts by publishing through a store that
raises and checking the screen still received the turn.

That test is doing more work than it looks: the bus is built `strict_handlers=True`, so a
recorder that let an exception out would abort the whole drain pass and take Agent Delivery
with it. Same shape as `D105`'s reasoning for `pump_once` swallowing, one level down.

### Incremental, per turn

The alternative — write the whole transcript when the intake finalises — is one write
instead of a dozen, and it loses everything precisely when the thing that goes wrong is the
call ending badly, which is the case a durable transcript exists for. Verified on a running
server against Postgres: six turns in the table for a call **nobody accepted**.

### The event had to grow four fields, and that is the interesting part

`TranscriptTurnAdded` carried text and timings. `transcript_turns` has had `engine`,
`engine_version`, `is_final` and `intake_id` on paper since P0 — and **the event is the
only carrier a subscriber has**, so a column the event does not carry is a column nothing
can fill. Adding them to the store instead would have meant inventing values, which is how
a row ends up confidently wrong.

`is_final` is the one with a claim in it: `False` means the endpointer cut the utterance at
`max_segment_ms` rather than at a pause, so the caller was still talking and a brief built
from it must not read as a finished thought. A durable transcript that lost that would be
storing a sentence as complete when it was not.

### `turn_id` is the primary key, so a re-delivery overwrites

The bus is at-least-once by design (`D15`). A transcript with a sentence in it twice reads
as the caller having repeated themselves — which is not a formatting problem, it is a false
statement about what somebody said. Both stores upsert, and the contract suite publishes
the same turn twice and asserts one row.

### Reads are ordered by `seq`, in the store rather than by the caller

`seq` is the only thing that makes a transcript a transcript. A store that returned
insertion order would be right until two writes raced, and the failure would be a
plausible-looking transcript in the wrong order — `B20`'s family.

### What this is not

Not a read path anything uses yet: the agent's screen is still served from
`TranscriptDeliveryService`'s in-memory list, which is correct while the call is live and
is where the socket push comes from. The durable copy is for what comes *after* — P4's
analysis, P6's wrap-up draft and the Call Explorer, and `D14`'s erasure job, which now has
a `delete_for_call` on the text half to match `D110`'s on the audio.

## D115. The pitch extends forward into the broker's own job — comparison and closing
_Taken 2026-09-06, after the user attended the hackathon orientation. The first decision in
this project driven by re-reading the **briefing** rather than the code._

- **Problem.** The submitted deck (`../Krungsri.pdf`) answers one question: a customer is
  already calling, so make that call better. Context in, routed well, brief on the screen.
  That is real and it is built. Re-reading the briefing's own journey diagram against it
  shows the mismatch:

  | step | the brief's leak | us |
  |---|---|---|
  | 1 หาลูกค้า | wrong segment, no prioritisation | nothing |
  | 2 วิเคราะห์ความต้องการ | **re-collects data we already hold** | **this is ReadyCall** |
  | 3 แนะนำสินค้า | **LEAK สูงสุด** — too many options, slow, misses the pain point | nothing |
  | 4 ปิดการขาย | too many documents, drop-off mid-way | nothing |
  | 5 ดูแลหลังขาย | manual, not personalised, lapse | partial (service calls) |
  | 6 engagement | mass blast, customer feels abandoned | nothing |

  Two more things in the brief point the same way. The headline problem is stated as
  **mismatch** — people hold cover that does not fit their risk or life stage — not
  absence. And the named business outcome is **product holding per customer > 1.5**.
  Neither is a service-desk metric.

- **Decision.** Keep everything built. **Extend the spine forward** into steps 3 and 4 with
  two capabilities: **compare & best-fit**, and an **agent tool rail** that binds the call
  to the customer's screen. Reframe the domain from an insurer's call centre to a
  **broker's**.

### Broker, not agent, and why it is not cosmetic

An agent (ตัวแทน) represents one insurer: one-to-one. A broker represents the customer
across many: many-to-one. That is *why* comparison is the broker's core job, and it is why
the biggest leak sits where it does. It also splits the call taxonomy — a broker owns
renewal, enquiry, quoting, selling and policy service, and **hands claims and deep plan
adjudication to the insurer**, which is an outcome the system does not currently have.

The user's read that this is mostly the YAML is **right, and it is the strongest feasibility
evidence in the pitch** — `D28` put the whole domain in `config/` for exactly this. Three
things are not config: `Policy` has no `insurer` (meaningless for one insurer, first-class
for a broker), "handed to insurer X" is not an available ending, and the mock customers
need policies from several insurers or there is nothing to compare.

This also closes **`Q7`**, which parked the intent taxonomy as a straw-man *"awaiting the
team's domain review"*. The orientation was that review.

### The two guardrails on compare & best-fit, which keep it in scope

The brief puts **underwriting and premium pricing explicitly out of scope**. So: rank on
**real plan attributes against real gaps**, and let the model write the *reason sentence*
only. A figure that cannot be traced to a data field does not render — `D16`'s rule, in the
place it matters most commercially. Surface and compare; never quote, never underwrite.

### Why the tool rail is a generalisation rather than an invention

`D44`'s keypad capture is already the shape: a control the agent triggers, which changes
what the customer's device is doing, with the result landing on the workstation. The rail
is more of those — push a form, request a document, send a link, sign, OCR. Building it as
"a second tool of a kind that exists" is a much smaller claim than "a co-browsing product".

**Honest stubs are allowed and must be labelled.** Signature and OCR should look real and
be visibly stubbed; the pitch is judged on whether the flow holds.

### What this does NOT do

It does not delete the service-call spine — that is journey step 2, it is the in-scope
bullet *"สรุปลูกค้าให้ broker ก่อนคุย"*, and it is the only part of this that is finished.
It does not add lead scoring (step 1) or engagement (step 6): both are in scope for the
brief, neither fits the week, and both are named in the deck as roadmap instead.

## D116. The consent gate moves from HOLDING data to RECOMMENDING from it
_Corrects a reading the user proposed, using the briefing's own words. Sharpens `Q24`
rather than dissolving it._

- **Problem.** The user's position: data from banks, insurers and affiliates was consented
  where it was collected, so we only need consent for what we gather ourselves — the
  recording and what comes out of it.

- **Half of that is right,** and it is already how the system behaves. What a broker may
  **see** is a different question from what they may **say and do** (`D74`), and showing a
  routed broker a record the affiliate lawfully shared is internal processing.

- **The other half is contradicted by the briefing in writing:**

  > PDPA — ต้องมี consent ชัดเจนก่อนใช้ข้อมูลวิเคราะห์/**แนะนำเฉพาะบุคคล**
  >
  > ข้อมูลสุขภาพเป็น sensitive data ต้องขอ consent **แยก**

  **Personalised recommendation is exactly `D115`'s compare-and-best-fit feature**, and the
  brief requires explicit consent before it runs *regardless of where the data came from*.

- **Decision.** The gate does not disappear; it **moves**. Consent is not primarily about
  whether we may hold a field — it is about whether we may **use it to recommend**. So:
  1. Holding and displaying affiliate-sourced data stays as it is (`D74`).
  2. **Personalised recommendation is gated**, and the gate is checked where the ranking
     runs, not where the data is read.
  3. **Health stays a separate scope** (`D14`), now on the brief's authority rather than on
     our reading of PDPA.

### What this does to `Q24`

`Q24` asked whether a health-line caller speaking health data into a recording consented
only as `recording` + `ai_processing` is a problem. The brief answers the legal half:
health is sensitive and needs separate consent, stated. So the recommendation stands and is
no longer a judgement call — **name the health scope in the offer wording on health lines,
and gate extraction in code so health entities cannot be pulled without it.** Two
independent protections, one keypress.

It also *narrows* where the gate belongs, which is the useful part: the risk is in
**deriving** structured health facts from speech and in **recommending** from them, not in
rendering what an insurer already told us. A gate on display would have cost the broker the
record they were routed and protected nobody.

### Why this is worth points rather than a tax

I-F-C-U scores **Impact** and **User insight**. A consent moment that is one clear keypress
and visibly changes what the system does is a far better answer to the brief's privacy
section than a policy nobody reads — and it speaks directly to its fourth constraint, that
decisioning must not become discriminatory.

## D117. The domain is a BROKER's, not an insurer's
_Track A of the week plan (`D115`), and the first change driven by Krungsri's own duty-split
slide rather than by our reading of the brief. Closes `Q7`._

- **Problem.** Every part of the domain pack described a single insurer's call centre:
  `motor.claim` and `health.claim` skills, a `q_health_ipd` queue for pre-authorisation,
  intents like `health.claim.submit` and `motor.claim.status`, and a `Policy` model with no
  field for **which company underwrote it** — meaningless for one insurer, and the first
  thing a broker needs. The hackathon is *"Reimagine Insurance Broker"*.

- **The authority.** Insight deck p.30 (`docs/MARKET_FACTS.md` §4) splits the duties:

  | insurer | broker |
  |---|---|
  | รับประกันภัย (underwrite) | วิเคราะห์ความต้องการของลูกค้า |
  | ดูแลเรื่องความคุ้มครอง | **คัดสรรแบบประกันและบริษัทฯ ที่ตรงตามความต้องการ** |
  | **ชดใช้ค่าสินไหม** (adjudicate and pay claims) | บริการด้านกรมธรรม์ |
  | บริการด้านกรมธรรม์ | **ติดตามการต่ออายุกรมธรรม์** |
  | | สร้างความสัมพันธ์กับลูกค้า |

  And p.4: **broker is 75.2% of non-life distribution in 2025**, up from 55.3% in 2012,
  while the agent channel collapsed from 14.2% to 3.6%.

### The three structural changes

**1. Claims become handoffs.** There is no `*.claim` skill any more. `claims.assist` takes
the first notification, gathers what the insurer will ask for, and **hands over** — a
different job needing different people. Every such intent carries
`handoff_to_insurer: true`, which reaches the agent's screen *before they speak*, because
"I will check and call you back" and "I am passing you to the insurer now" are different
promises and only one of them is the broker's to make. `health.ipd.preauth` is gone
outright: pre-authorisation is the insurer's decision, and a broker screen implying
otherwise is the out-of-scope underwriting the brief names.

**2. Renewal gets its own desk.** `renewal.retention` and `q_renewal`. **72.55% of life
premium is renewal at 84% persistency** (`MARKET_FACTS` §6) — one policy in six lapses a
year, and chasing them is a named duty. It was previously buried inside `general.billing`,
which hid the largest single block of revenue in the business inside a payments queue. A
renewal conversation is retention work: different script, different success measure,
different person.

**3. Advice becomes a first-class skill per line.** `motor.advice`, `health.advice`,
`life.advice`, `travel.advice`, with `*.advice.compare` intents. This is the mandate
(*คัดสรรแบบประกันและบริษัทฯ*) and the brief's biggest leak. `life.advice.mortgage` earns its
own intent because mortgage-linked cover grows at **+9.91%** and the bank can *see* the home
loan — the cleanest right-customer x right-time trigger in the data.

### `Policy.insurer`, and why one field mattered so much

A single company's system has no use for it. A broker holds one customer's cover across
several carriers, and which carrier decides who a claim is handed to and whose terms a
comparison is against. The fixtures now give the demo customer a real **portfolio**: two
health policies from different carriers — one of them employer group cover capped at
฿1,500/day — and a motor policy from a third. Real carrier names from `MARKET_FACTS` §8, so
a judge from Krungsri Auto Broker recognises them on sight.

⚠️ **The affiliated carrier (Allianz Ayudhya) is deliberately one row among several, and
sometimes not the best answer.** A broker whose affiliate always wins is not a broker, and
the honest version is the one that survives a question about it.

### What this did NOT change

Not one line of `services/` logic. Routing, matching, presence, the offer handshake, the
transcript and the recording are untouched — this was `config/` plus one domain field, and
that is `D28`'s whole promise arriving on schedule. **Say this in the pitch**: it is the
strongest Feasibility evidence available to us.

### Verified

On a running server, not from tests alone: `health.claim.notify` routes to `q_claims`, the
brief carries `insurer: เมืองไทยประกันภัย` and `handoff_to_insurer: true`, and at L1 the
actions are correctly the three ungated ones — including *"ย้ำว่าการอนุมัติเป็นของบริษัท
ประกัน"* — with the L2 steps withheld.

## D118. Playbooks move to `config/playbooks.yaml`, guarded in both directions
_Closes `Q19`, which had been parked as "a P4 task" since P1._

- **Problem.** The recommended-action lists lived in `_PLAYBOOKS`, a dict inside
  `services/brief/builder.py`. That is insurance content in a service, against `D28`, with
  a comment admitting it. Tolerable at six short lists; not once `D117` needed twenty-four.

- **Decision.** `config/playbooks.yaml`, loaded into the domain pack as `PlaybookSpec` —
  exactly the shape `D72` used for the challenge list.

**Guarded both ways at startup**, the same discipline the prompt ids get: an intent naming
a playbook that does not exist is refused, **and so is a playbook no intent reaches**. The
one-way check is the half that never catches dead domain content somebody keeps editing.
`generic` must exist, because a missing playbook would otherwise render an empty action
list — which reads on screen as "there is nothing to do" rather than as a config error.

The steps encode the broker rule directly: none of them says *approve*, *is covered*, or
*the premium will be*. Where the insurer owns the outcome, the last step says so out loud.

## B30. The cold-call path knew the product line and threw it away
_Found by `D117`'s fixtures, not by a test — and it had been true since P1b._

- **Symptoms.** After giving the demo customer a real portfolio, four workstation tests
  began reporting `relevant_policy: None`. The agent's policy panel was empty for a caller
  whose policy we hold.
- **Root cause.** `demo.py`'s app path passes `product_line` into
  `ContextAssembler.build()`; the **cold-call path twelve lines below it does not**, though
  `body.intent_code` is right there and the expression is identical.
  `_pick_relevant_policy` falls back to "the only policy they have" when it has no line
  signal — so with a one-policy fixture the omission was invisible, and correct output was
  being produced for the wrong reason.
- **Fix.** Pass the line on both paths.
- **Lesson.** **A fixture with one of something tests nothing about choosing.** The
  assembler's own docstring says it declines to guess between unrelated policies; no test
  could reach that branch, because no customer had two. Cardinality is part of a fixture's
  design — `B13`'s "never test a contention rule without contention", one layer down.

## D119. The LLM seam, and the first sentence a model writes for a human
_Track D of the week plan (`D115`). Six phases after `D29` named the two adapters._

- **Problem.** `Settings.llm_provider` accepted `anthropic` and `openai_compatible`, and
  `_check_coherent` even validated their keys — with **nothing behind either name**. That
  is `B23`'s shape exactly: a config surface that cannot select anything. `RuleBasedLlm`
  existed, was correct, and was **constructed nowhere**, which is `B7`'s family from the
  other end. Both were caught on 2026-09-06 by reading the tree rather than the docs, and
  the docs had said the adapters were "both implemented" in the present tense.

- **Decision.** `build_llm` is the factory and the only place a client may be built —
  `build_blob_storage`'s enforcement point, for the same reason. Two real adapters:
  `AnthropicLlm` and `OpenAiCompatibleLlm`.

### Structured output is forced, never requested

`AnthropicLlm` hands the Pydantic schema over as a **tool definition** with
`tool_choice` pinned to it, so the model cannot answer in prose that happens to begin with
a brace. `OpenAiCompatibleLlm` uses `response_format: json_schema` where the server
supports it and falls back to schema-in-the-prompt where it does not — and **marks which
mechanism ran**, because *"the model was worse"* and *"the server could not constrain it"*
are different findings and a comparison table that conflates them is misleading.

One adapter covers Typhoon-hosted, OpenAI, vLLM, Ollama and LM Studio, because all five
speak the same wire format. That is why `D29` promised a *table* rather than an argument:
the comparison costs one adapter, not a project.

### Prompts are files with versions in their names

`prompts/th/<id>.<version>.md`, loaded by `PromptLibrary`. Two rules the format enforces
rather than documents:

- **Editing a live prompt in place is forbidden by construction.** A new prompt is a new
  file, because `analyses.prompt_version` names the file every stored result came from,
  and editing one makes every historical result unattributable (`D18`).
- **Every slot must be declared, and every declared slot must be used.** Rendering refuses
  a missing value *and* an undeclared one. The failure this prevents is the silent kind: a
  typo'd key that happens to exist renders the wrong thing, and a prompt that quietly
  loses its transcript still returns a confident-looking summary of nothing.

### The summary, and the three rules it inherits

`IntakeSummariser` is the first thing here that asks a model to write a sentence a human
reads. It returns `SummaryResult | None`, and **`None` is the ordinary case** — on a laptop
with no key it is the only case.

- **`D12`.** `summarise()` bounds the adapter's deadline AND the whole exchange, the same
  belt-and-braces `D112` argued for. It is called from `accept_offer` as a
  **fire-and-forget task**, never awaited: the agent is connected the instant that endpoint
  returns, already reading the rule-based summary, and the screen upgrades on a push if
  the model answers. That ordering is the one place it would be tempting to break `D12`,
  so it is written as code rather than as a comment.
- **`D16`.** The prompt forbids stating figures, and `_FIGURE` refuses the output if one
  appears anyway. Coverage amounts are read from the record; the model may reference them
  and may not state them. The guard assumes the prompt will one day fail.
- **`D13`.** `is_clear: false` is believed. A model allowed to say *"they were unclear"*
  invents less than one obliged to produce three sentences, and a refused summary leaves
  the rule-based one — which quotes the caller verbatim and cannot hallucinate.

A caller who barely spoke is not summarised **and the model is not called**: most callers
who take the recording then wait quietly, and summarising two syllables produces a
sentence that sounds like information. Same distinction `D111` drew for the degradation
reason.

### The read path must never call a model

`Container.brief_snapshot` runs on every `/me` and every socket push, so summarising there
would hit the provider dozens of times per call. `summarise_call` computes it once and
caches it per call; the read path only ever *prefers* what is already there.

### Measured, on the real provider

One live call through the whole path on 2026-09-07, `claude-sonnet-5`:

| | |
|---|---|
| latency | **4.5 s** |
| tokens | 1,561 in / 256 out |
| cost | **$0.0085** per call |

The summary was faithful — it named the procedure, the hospital, the document question and
the "must I pay upfront" question, and invented no figures.

⚠️ **4.5 s is well outside `ARCHITECTURE` §15's 1-second brief budget**, and that is
survivable *only* because this runs after the agent is already talking. If a summary is
ever wanted **before** accept, this measurement says it cannot be this prompt on this
model: it needs a smaller model, a shorter prompt, or streaming — and the number to beat
is on record now rather than assumed.

The `llm` extra carries both SDKs (`uv sync --extra llm`); neither is installed by default,
because the shipped provider is rule-based and needs nothing.

## D120. Binding the call to the customer's screen, and the two tiers that gate it
_Track C of the week plan (`D115`). The user's idea, and the best piece of user insight in
their orientation notes._

- **Problem.** A broker on the phone says *"go to the website, tap the menu at the top
  right, then Documents, then Upload"*. The brief prices exactly this: journey step 4 leaks
  because **เอกสารเยอะ ลูกค้า drop-off กลางทาง**. The customer who suffers most is the one
  least able to follow spoken navigation — but the leak is universal, which is why the
  tech-shy customer is the *proof* of the design and not the segment it serves.

- **Decision.** Bind the call to a screen, and let the broker **push** onto it. The
  customer never navigates.

### Pairing is a link, not a code, and the direction is the argument

The phone number is already the strongest binding available: the call is on it, and ANI
says which. So a link **sent to the number we are already talking to** is
self-authenticating to precisely what the ANI is worth — `D20`'s L1, probable and not
verified — and costs the customer one tap.

A read-back code is *stronger*: it proves the person is looking at the screen **and** on
the call. It is also a chore handed to somebody who rang because they were stuck. We take
the link and let assurance carry the rest, which is the same trade `D82` made about wrong
keypresses: do not spend a distressed person's effort on our paperwork.

Three entry paths, one destination:

| path | how pairing happens |
|---|---|
| in-app call | implicit — the app started the call and already carries the correlation token (`D6`) |
| phone call, then a link | the broker presses *send link*; whoever taps it holds the phone we are on |
| already in the app, called separately | the app is **told** by the server (`live_for`), rather than showing a button nobody can find |

That third row answers the user's own question directly. A permanently visible *"let an
agent help me"* button is odd on a screen nobody is calling from, and the customer would
have to go looking for it at the exact moment they are least able to. The server knows a
call is live for this signed-in customer, so the app can *offer* rather than *display*.

**And yes — this is a second real consumer for identity resolution.** Until now the
identity ladder only decided how much of the record to render. It now also decides whether
a customer can be told that help is available on the call they are already on.

### Two tiers, which are `D74`'s rule pointed at the customer's screen

| tier | reached by | what may be pushed |
|---|---|---|
| `GUEST` | tapping the link | anything true for **anybody**: plan comparisons, product information, a document checklist, how-to steps |
| `VERIFIED` | signing in | anything about **them**: their policies, a prefilled form, an upload, a signature |

This is the whole answer to *"do we have to make them register?"* — **no**, not to receive
help. Someone comparing plans gets everything they need without an account, because none of
it is about them; the wall appears only where the thing on screen is personal. Registering
first would gate the half with no privacy cost at all, which is the ordinary and wrong
design.

**A personal push to a link-only screen is refused, and the refusal is the feature.** The
broker is told *why* and asks the customer to sign in, instead of a stranger's policy
appearing on whoever happens to be holding that handset. `D42`'s argument, one layer out:
possession of a device is not identity.

### The shape of the two front ends, and why they differ

The customer's screen is **one static HTML file**, no build step — `D47`'s reasoning for
the simulator, and stronger here: this page opens from a link on a stranger's phone, and
anything needing a bundler is one more thing to be stale on the day. The workstation earns
its React build because it is a dense operator tool somebody sits in front of all shift;
this is a page somebody looks at for four minutes while a human talks to them.

It **polls at 1 s** rather than holding a socket. It is a *view* of what the broker pushed,
a one-second poll is invisible to a human, it needs no socket infrastructure, and it
survives the mobile proxies that eat WebSockets. The agent side keeps its socket because it
carries the live transcript, where the budget is 1.5 s (`D105`). Different problems,
different transports — and saying so is cheaper than discovering it at a venue.

⚠️ **The page re-renders only when the content signature changes**, so a poll cannot wipe a
form the customer is halfway through typing. That is the optimistic-UI hazard `D44`'s
keypad panel already taught, in the one place it would be most infuriating.

### What is deliberately not here

**Nothing is sent.** No SMS, no LINE — `NotifierPort` is P5, and until then the link comes
back to the broker to read out, which is also exactly what a rehearsal needs. **Nothing is
stored durably**: a pairing dies with the call plus a ten-minute grace, so a form somebody
was halfway through when the broker hung up still submits, and a token is never a standing
key to a screen (`D14`). And the closed `PushKind` set is closed for the intent taxonomy's
reason: an unknown kind is a blank panel on somebody's phone mid-call.

### Verified end to end, in a browser

On a running server, on a 375-wide viewport: the broker minted a link, a comparison
rendered on the customer's phone (group plan at ฿1,500/day against a top-up at ฿4,000, the
two carriers named), a **form push was refused at guest tier with the reason**, sign-in
unlocked it, the prefilled form appeared, the customer typed into it and submitted — and
`GET /v1/agent/calls/{id}/assist` showed the broker exactly what they had written. Journey
steps 3 and 4, on one call, without anybody reading a field name down a phone line.

## D121. The tool rail gets a screen, and the tier gate moves from KIND to PURPOSE
_Taken 2026-09-08, after the user tried to use `D120` and could not. Two changes that
turned out to be one: the rail became reachable, and the gate became right._

- **Problem, part one — nobody could press it.** `D120` shipped three working endpoints, a
  customer page, fifteen passing tests and a verified browser walkthrough. It shipped with
  **no control anywhere in the workstation**. The only way a broker could mint a link or
  push anything was `curl`, and the user's report was simply *"there's no tool rail or
  anything at all on the workstation"* — which was exactly true.

  That is `B24`'s family with a new member: written, correct, tested, and reachable by no
  user. It is worth naming the specific self-deception, because the previous session's
  verification note says *"verified end to end, in a browser"* and that was **true** — of a
  browser I drove by hand through the API. A feature is not reachable because a browser
  reached it; it is reachable when the person it was built for can find the button.

- **Problem, part two — the gate was asking the wrong question.** `_NEEDS_VERIFIED` was
  `{FORM, DOCUMENT_REQUEST}`: a gate on the *shape* of the push. The user's correction:

  > *"forms needs to be signed in is only like half true — it will depend on what kind of
  > form it is too … we need to look at the purpose/use of that tool, not just the type"*

  They are right, and it is wrong in **both** directions. A blank *"please quote me"* form
  is true for anybody, and gating it walled off the one part of the journey with no privacy
  cost at all — the ordinary and wrong design `D120` itself argued against, three
  paragraphs before implementing it. And it would have waved through the first personal
  `info` panel anybody wrote, because `INFO` was not on the list.

  ⚠️ **`diagrams/src/assist_pairing.mmd` was already drawing the right rule** — its gate
  node asks *"is the thing being pushed ABOUT THIS PERSON?"* — while the code asked about
  the kind. The drawing described the intent and the implementation did not, which is the
  reverse of this project's usual drift and is why this is a correction, not a redesign.

### The decision

**`config/assist_tools.yaml`** holds the rail: 4 groups, 11 tools, each declaring
`personal`, its `kind`, its fields, and whether it is a labelled `stub`. Domain content in
`config/`, which is `D28` — and the same move `D118` made for playbooks.

**`personal` is read from the file and never from the request.** `AssistService.push` takes
the whole `AssistToolSpec` rather than a `personal` argument, so a caller *cannot* supply
its own flag; the client sends a `tool_id` and nothing else. A workstation able to declare
its own push non-personal would be this gate's own bypass, and the workstation renders
permissions rather than computing them.

**Two startup guards**, in `D118`'s both-directions style:

- a tool naming a group that does not exist is refused;
- a tool declaring `prefill` while not `personal` is refused — **prefilling is what turns a
  form that is true for anybody into a statement about one customer**, so a tool claiming
  both is claiming something incoherent about its own disclosure.

### Greying is the courtesy; the refusal is still the gate

A personal tool renders `disabled` with its reason on a guest screen, so the broker learns
why *before* clicking rather than by collecting a 400 — `D71`'s pattern, where the two
forward outcomes are disabled with the reason in the tooltip. The server refuses
independently and the test that matters asserts the **server's** refusal, never the
client's `disabled` attribute.

This does change what the demo shows: the earlier "watch the personal push get refused"
moment is now "watch the personal tool be unavailable, and watch it unlock when they sign
in." That is the better product and the weaker theatre, and the product wins.

### Registration is the app's job, and the rail only points at it

The user asked whether an unregistered customer should be pushed a registration form or the
app download. **The download**, and their own reasoning is the argument: rebuilding
registration means owning credential handling for a demo, and it makes the customer sign up
on the web and then log in again in the app. `link.app_download` is a guest-tier tool.

### What the channel picker may and may not claim

The link is not sent — `NotifierPort` is P5 — so the picker records the choice and the UI
says plainly that nothing was sent. **LINE renders disabled with its reason**: the LINE
Messaging API cannot push to a phone number, only to a user id that exists after the
customer has added the official account or passed through LINE Login. Showing it greyed
with that sentence is honest where a picker that silently only ever does one thing is not.
⚠️ LINE's separate *Notification Messages* product does reach a phone number without
friendship; it needs formal approval and is restricted to transactional templates, and
whether Krungsri holds it is **not verified** and must not be claimed in the pitch.

### Verified, by driving the screen rather than the API

On a running server: signed in as A006, took a real call, minted the link **from the panel**,
opened it on a 375-wide viewport, opened the tool box (11 tools, exactly the 5 personal ones
disabled), pushed the blank quote form **to a guest screen**, filled it in on the phone,
submitted, and read the four typed values back on the broker's screen. Then signed in on the
phone, watched all 5 unlock, and pushed the claim form — which arrived carrying
`HL-2024-000811`, `เมืองไทยประกันภัย` and the holder's name from the frozen snapshot, with
the two fields we do not hold left blank. Two client bugs were found doing it (`B32`, `B33`),
neither of which any test could see.

### Amendment, 2026-09-08: the sign-in stops being the first thing on the page

The customer's link page opened with a full-width **เข้าสู่ระบบ** card above everything
else, and the guest prompt read *"เข้าสู่ระบบเพื่อให้เจ้าหน้าที่ช่วยกรอกข้อมูลและส่งเอกสารให้ได้"*.
The user's reaction on first opening a real link was *"it made it seem like a customer HAVE
TO login when they receive the link"* — and it did, which contradicts the whole argument
three sections up: **no account is needed to be helped**, and the wall exists only where
the content is personal.

It is now the **last** element on the page, collapsed, labelled *(ไม่บังคับ)* — not
required — and the guest prompt says what guests still get rather than what they are
missing. The tier logic is untouched; this was a presentation bug that argued against the
design it was implementing.

The user also asked the sharper question: *why is there a web sign-in at all, when `/sim`
already simulates the signed-in app?* That is the right question and it is answered by
`D122` — the verified tier's natural home is the app, and the web sign-in survives only as
the fallback for a customer who taps a link and then wants their own details filled in.


## D122. The app is not a keypad: contact menus are filtered, and the app joins the call
_Taken 2026-09-08 from the user's own walkthrough of `/sim`. Amends `D48`, and builds the
two rows of `D120`'s pairing table that were designed and never implemented._

- **Problem one, and it was visible on screen.** Tapping a travel policy the customer
  **already holds** and choosing *"contact us about this plan"* offered them
  **"ซื้อประกันเดินทาง"** — buy travel insurance. Tapping their motor policy offered
  *"เปรียบเทียบแผนและขอราคา"*, new-business wording for cover they own.

  The cause is `D48` taken too literally. *One `menus.yaml`, two surfaces* is right about
  the **taxonomy** and wrong about the **list**: the IVR's menu is written for somebody we
  know nothing about, because a keypad caller has told us nothing yet. The app knows which
  policy they tapped, and therefore knows they hold it.

  The user put it better than the decision did: *"call numpad starts with what kind of plan
  … from app you click on A plan (NOT a kind of plan) they HAVE"*.

- **Problem two.** `/sim` minted a correlation token and stopped. Tapping Contact produced
  a clean API trace and **no caller in any queue** — the app and the call centre were two
  demos that had never met. The user spotted this unprompted.

### The decision

**`contexts:` on a menu option**, in `menus.yaml`, naming which situations it belongs to:

| context | what it is |
|---|---|
| `plan` | *"about THIS plan"*, tapped from a policy they hold |
| `general` | *"something else"*: account admin, or cover they do not have yet |

Default is **both**, so an unmarked option behaves exactly as before, and **the IVR ignores
the field entirely** — there is nothing to filter on when the caller has told us nothing.
`label_plan_th` does the same job for wording rather than presence: comparing plans is
legitimate in both places and is not the same conversation, so a policyholder sees
*"เปรียบเทียบกับแผนอื่น ก่อนต่ออายุ"* — which is `D117`'s renewal-retention desk in a label.

⚠️ **Guarded per context at startup:** a reason menu that is *empty* in either context, or
that loses its catch-all in either, refuses to boot. That is
`test_every_reason_menu_still_ends_in_a_catch_all` moved one layer down — filtering is
exactly how a menu acquires a dead end without anybody noticing.

### `/sim` places a real call, and the app becomes the paired screen

`POST /v1/demo/calls` has accepted a `correlation_token` since P1b and resolves the intent
behind it. `/sim` now uses it, so an app contact **arrives in a real queue carrying the
context the app already assembled** — `D6`'s app path, joined up at last.

That also builds `D120`'s first and third rows, which were drawn in a diagram and shipped
as nothing. `GET /v1/app/assist` tells the app whether a call is live for this customer,
and pairs the screen **at `VERIFIED` with no link and no sign-in**. That is not a shortcut:
the customer is already authenticated to the app, and `D4`'s session is a far stronger
claim about who they are than tapping a link ever was. Asking them to sign in again, on the
device they are signed in on, is the pattern the link page stopped doing an hour earlier.

It is also the first caller `AssistService.live_for` has ever had — written for exactly
this, documented, drawn, and reachable by nothing.

**The two "is this call live" sets are deliberately different.** The token screen counts
`OFFERED`/`IN_CALL`/`WRAP_UP`, because a broker can only push once somebody has accepted.
The app counts from `CONNECTING`, because a customer holding for ninety seconds is very
much on a call — and the app says **which**, rather than claiming a conversation that has
not started. Two questions, two sets, rather than one stretched over both (`D50`'s
argument).

### Verified end to end, in two browser tabs

Signed in to `/sim` as คุณปัณณธัช, tapped their **travel** policy, and the reason list came
back **without** *"ซื้อประกันเดินทาง"* — keys 1, 3 and 4, keeping their IVR numbering
(`D81`). Chose แจ้งเคลม; the call appeared in `คิวช่วยเหลือด้านเคลม` on the workstation as
`travel.claim.notify` and was offered to A005. On accept the pairing was **already
`verified`** with no link minted. The broker pushed `form.claim_notify`; it arrived in the
app prefilled with `TR-2026-001204` and `ทิพยประกันภัย` — the *travel* policy, the one they
tapped. The customer filled in the rest, submitted, and the broker's screen showed every
field they had typed.

### Amendment, 2026-09-08 (evening): the customer's app looks like a phone on a call

Four presentation corrections from the user driving `/sim` and the tool rail, plus one
capability that turned out not to exist.

**The tool dialog.** No ปิด button — the backdrop closes it, which is a bigger target and
what every dialog on a phone does (`e.target === e.currentTarget`, so a click *inside*
does not bubble out and close it mid-form; Escape works too). And two columns: tools left,
what the customer sent back right. Stacked, the responses sat below four groups of tools,
so the thing the broker is waiting for was the thing furthest off screen. Each group is a
**horizontal strip**, so a fifth tool scrolls that row instead of making the dialog taller.

**`/sim` is a call screen while a call is on.** It was showing its browse and contact
controls throughout, so the only sign anything was happening was one line of text — an app
still offering *"contact us about this plan"* mid-call is not showing you a call. It now
has a call card with the status, a ticking timer, mute, speaker and **วางสาย**, and a
dedicated *จากเจ้าหน้าที่* area for what the broker pushes. Mute and speaker are local and
cosmetic, because there is no audio in the simulator and pretending otherwise is the kind
of stub `D115` says must be labelled.

⚠️ **Form controls had no styling at all**, so they fell back to the browser default —
a white box with black text on a dark panel. Every control now takes its colours from the
same tokens as the surface behind it.

**The customer can hang up.** `POST /v1/app/call/hangup`. This is not cosmetic: `D113`
ships `max_offer_rounds: 0`, meaning a caller the whole floor declines circles **forever**,
and the stated justification is *"a caller still holding can hang up whenever they
choose"*. That was true of a telephone and false of this system, which had no path for a
customer to end a call at all. A queue nobody can leave is a different product from the one
`D113` describes.

**The demo's instrumentation folds away.** *What the server did* and *API calls made* are
what make the pitch checkable rather than asserted, and on stage they compete with the app
for attention. One button hides them; the log keeps recording while hidden.

**And the app stops polling when the call ends.** Nothing new can arrive after the broker
rings off, so a poll every 1.5 s is asking a question whose answer cannot change.
Submitting still works, because that is a POST rather than something the poll carries.

**New tool: `form.free_text`.** The user's idea, and it is `D44`'s keypad capture with a
keyboard instead of a dialpad — deliberately unstructured, deliberately **not personal**
(an empty box is true for anybody), and useful precisely because it is vague: a spelling,
an address, a policy number read off a document, a hospital name the broker cannot catch
over a bad line. The system does not guess what it means, which is the same rule `D44` set
for captured digits.

### Amendment, 2026-09-08 (late): three layout corrections from watching it used

**The tool dialog is twice as wide, not the same width halved.** *"Keep the size"* meant
keep the room a group of tools gets — which is what makes three per row look right — so
two columns need twice the dialog, not two halves of the old one. `min(1400px, 96vw)`,
equal columns.

**Folding the plumbing away centres the app.** The toggle lives with the phone rather than
with the panel it hides (so hiding it cannot hide the way back), and the grid collapses to
a single centred column. Left pinned to the left of an empty half, it read as something
having failed to load rather than as a deliberate view.

**And the customer's วางสาย had to become real**, which turned out to be `B38` rather than
a wiring job.


## D123. "Something else" asks which kind of cover, because that is how you ask for cover you do not have
_Taken 2026-09-08. Completes the half of `D122` that was filtered correctly and then left
with nowhere to go. Journey step 3, which `D115` calls the brief's biggest leak._

- **Problem.** `D122` split the app's contact menu in two and got the *"about this plan"*
  half right: an option that only makes sense for somebody **without** the cover is
  filtered out, so a customer holding a travel policy is no longer offered
  *"ซื้อประกันเดินทาง"*.

  The other half — *"something else"* — went straight to `general_reason`, which is
  account admin: change my details, request a document, pay a premium, complain. So the
  app could ask about **a policy they hold** and about **their own paperwork**, and could
  not ask about **cover they do not have yet.** The new-business options were not missing;
  they were sitting in the per-line reason menus, in the `general` context, reachable from
  the keypad and from nothing else. `travel.advice.quote` is the clean example: filtered
  out of the plan surface by `D122`, and shown on no other surface.

  That is not a small gap. Journey step 3 — แนะนำสินค้า — is the leak the brief marks
  **สูงสุด**, `*.advice.compare` is the broker's actual mandate (`D117`), and the app is
  the channel with the most context. It was the one thing the app could not do.

- **Decision.** Put the keypad's **own first question** in front of that branch:
  `GET /v1/app/contact-lines` returns the `product_line` menu, the app shows it, and
  picking a line fetches that line's reasons at `context=general`.

### Why this is the step-1 menu rather than a new one

Adding a line means editing `menus.yaml`, exactly as it does for the IVR. A hand-written
list of lines in the app would fork the taxonomy at the one question the entire rest of the
walk depends on — the failure `D48` exists to prevent, at the worst possible node.

### And why this list is deliberately NOT filtered

`contexts` describes **reasons**. This question is the thing that *establishes* the
context: a customer who has not tapped a policy has told us exactly as little as somebody
who has just dialled, so there is nothing to filter on — which is `D122`'s own argument for
why the IVR is never filtered, arriving one level up. Filtering here would mean guessing
what they want before asking, and the guess we could make (hide lines they already hold) is
backwards: a second car and a top-up over group cover are both real.

### `unknown` is a row, not a special case

The keypad's own *"เรื่องอื่นๆ"* is one of the options, and following it lands on
`general_reason` — which is exactly where this branch used to start. Nothing was taken
away; a step was put in front of it. That also means the endpoint has no bespoke fallback
logic to disagree with `reason_menu_for`'s.

### What this did NOT change

No config, no menu, no intent, no service logic. One read-only endpoint over the pack the
app was already reading, plus the app's own second step. The per-context startup guards
from `D122` are what make it safe to iterate the lines blindly: a reason menu that is empty
in either context, or that loses its catch-all in either, already refuses to boot — so
"every line the app offers leads somewhere with a way out" is guaranteed by the loader
rather than by this endpoint remembering to check.

⚠️ **Noticed while doing it, and deliberately not acted on:** `motor.advice.quote`,
`health.advice.quote` and `life.advice.quote` exist as intents, with slots and playbooks,
and **no menu reaches any of them** — only `travel.advice.quote` is on a menu. It is not
dead config exactly (a client may name any intent, and a future "get a quote" tool would
raise one), but on every human surface those three lines answer "I want to buy" with
*"เปรียบเทียบแผนและขอราคา"* — compare plans and ask a price — which is a reasonable label
for the same conversation. Splitting them would add a seventh option to three phone menus,
and that is a domain call rather than an engineering one. Recorded as `Q36`.

### Verified in a browser, not from the tests

On a running server: signed in to `/sim` as คุณภัทธีรา (C000001, who holds two health
policies and a motor one and **no travel cover**), pressed *Contact us — something else*, got the four lines plus เรื่องอื่นๆ
(`GET /v1/app/contact-lines`), chose ประกันเดินทาง, and the reason list came back carrying
**ซื้อประกันเดินทาง** — the option the user found missing —
(`GET /v1/app/contact-reasons?product_line=travel&context=general`). Choosing it placed a
real call: `POST /v1/calls/intents` 201, `POST /v1/demo/calls` 200, the app turned into a
call screen with a ticking timer, and the call routed to **`q_advice_travel`** at
**`l3_verified`** — an advice desk, from a customer holding no travel policy at all.

## D124. Handing the call to the insurer is something the broker DOES, not a banner they read
_Taken 2026-09-08. Builds the external half of the user's two-tab transfer design. `D117`
made claims a handoff; this is the first code that performs one. The internal half —
`D63`'s consulted transfer to another desk — is **deliberately not built**, and the last
section says why._

- **Problem.** `D117` split the duties from Krungsri's own slide: the insurer underwrites,
  rules on coverage and **pays claims**; the broker analyses needs, selects the plan *and
  the company*, services the policy and chases renewals. Since `D117` every intent whose
  work belongs to the insurer carries `handoff_to_insurer: true`, and that reaches the
  broker's screen as a banner **before they speak** — because *"I'll check and call you
  back"* and *"I'm passing you to the insurer now"* are different promises and only one of
  them is a broker's to make.

  And the banner was the whole feature. The broker read it, said the sentence, pressed
  วางสาย, and typed whatever they remembered into the wrap-up. **Which company, and why,
  existed nowhere** — a broker cannot count handoffs per carrier, and the customer's
  record does not say who has their claim.

- **Decision.** A **ส่งต่อ** button beside วางสาย, and a dialog that records who the call
  is going to and why, prefills the wrap-up, and ends the call.

### The record beats the menu, and that is the whole shape of `insurers.yaml`

The carriers are real, with real 2025 market share from `MARKET_FACTS` §8 — a judge from
Krungsri Auto Broker recognises them, and "Insurer A / Insurer B" throws away realism that
costs nothing. ⚠️ Allianz Ayudhya is deliberately one row among several (`D117`).

But the file is a **menu, not a whitelist.** The carrier that underwrote the customer's
policy comes from `Policy.insurer` and is offered as its own row whether or not it appears
in config, because on hackathon day the real extract arrives carrying carriers nobody has
typed in — and a broker who cannot hand a claim to the company that actually wrote the
policy has no product at all. A startup guard demanding the two agree would refuse to boot
on exactly the data the `CoreDataProvider` seam exists to absorb (`D3`).

One guard does run, and it is `D122`'s shape: **if every handoff reason required a policy,
a caller who holds none could never be handed over** — a list that filters to empty is a
dead end, and it is silent when it happens. That refuses to boot.

### The client names WHICH, never WHAT IT IS CALLED

The request carries a reason code and either an insurer code or the flag
`use_policy_insurer`. There is no field for a company **name**, so a client cannot file a
handoff to a company that never wrote anything for this customer — and the wrap-up would
have recorded that as fact. Same rule as `D121`'s `personal` flag and `D4`'s `customer_id`:
the client says which of the server's options, and the server supplies the content.

### ⚠️ Why this does **not** use `CallState.TRANSFERRED`

The obvious move, and it is wrong twice.

`TRANSFERRED` is **terminal**, so a call in it can never reach `WRAP_UP` — and after-call
work on a handoff is real work: what was gathered, what the insurer will need, what the
customer was told. Making it non-terminal instead would give the system **two states that
both mean "the media is over and the agent is filing"**, and every reader of *"is this
agent in after-call work"* would have to check both. That is `B25`/`B26`'s shape exactly —
two places answering one question, disagreeing later, in silence.

So a handoff ends the call the ordinary way, `IN_CALL -> WRAP_UP`, and what makes it a
handoff is the **record**: a `handed_to_insurer:<code>` transition reason, a
`call.handed_off` event, and a disposition the broker confirms. A test asserts
`TRANSFERRED` is not entered, so this cannot be "tidied" back later without reading why.

That leaves `CallState.TRANSFERRED` entered by nothing, which in this project is normally a
bug. Here it is a finding rather than an oversight and is recorded as **`Q37`**: the state
models a *blind* transfer — push the caller into another queue and hang up — which `D63`
explicitly rejects as "what call centres do today and the reason people hate being
transferred".

### Nothing is written until the broker files it

The handoff is held for the length of the call and prefills the wrap-up; the durable
statement is the `call_wrapups` row the broker actually submits. `D45`: nothing saves on
the person's behalf, and the broker is the one who made the promise to the customer.
`was_edited` still separates a confirmed disposition from an accepted default.

⚠️ **This is `Q34`'s family and inherits its weakness**: a real product wants a
`call_handoffs` table with a retention rule (`D14`), and this dies with the call.

### The prefill is applied once, keyed on the handoff's own timestamp

Initialising `useState` from the prop misses the case where the panel is already mounted;
re-applying every render wipes whatever the broker has typed since. Both were live risks —
this is `D120`'s optimistic-UI hazard in a form somebody is mid-sentence in.

The **order** in the endpoint is load-bearing for the same reason: record first, end the
call second. The wrap-up form renders the instant the state changes, so a prefill computed
afterwards is a prefill nobody ever sees.

### What is NOT built, and why the second tab says so on screen

The user's design has two tabs: this one, and **โอนสายภายใน** — transfer to another broker
on the floor. The second tab renders as a **labelled stub** naming `D63` and describing
what it will do, which is the rule for anything that looks real and is not (`D115`).

That is a scope call and it is worth stating plainly rather than burying. `D63` designed
the internal transfer at P2b as a *consulted* handover: the caller keeps talking to the
first broker while the second decides, and moves only when the first presses Release. That
needs a **transfer offer distinct from a queue offer** — `AssignmentService.offer` requires
`MATCHED` and transitions to `OFFERED`, and neither is true of a call that must stay
`IN_CALL` — plus a way for one agent to hand an accepted assignment to another, plus a
rework of *"which call is mine"* from a fact about the **call** into a fact about the
**assignment** (today `_active_call_id` would leave the first broker's screen showing a
live call they are no longer on, with a วางสาย button that would end somebody else's).

That is a day of careful work in `services/agents/`, which is where `B7`, `B25` and `B28`
all lived, four days before the pitch. The external half touches none of it, is complete,
and is the one that expresses `D117`. **The internal half is the user's call to schedule,
with that risk stated.**

### Verified by driving the workstation, not from the tests

On a running server, signed in as A006 on a live `health.claim.notify` call: **ส่งต่อ**
appears in the call bar beside วางสาย; the dialog opens carrying `D117`'s banner, the
customer's own carrier **เมืองไทยประกันภัย** as its own row above a 14-carrier health
menu, and all six reasons live because this caller holds a policy. Choosing *การพิจารณาและ
จ่ายค่าสินไหม* and pressing ส่งต่อและวางสาย ended the call, closed the dialog, raised the
toast, and left the wrap-up form carrying `handed_to_insurer` and
**"ส่งต่อ เมืองไทยประกันภัย — การพิจารณาและจ่ายค่าสินไหม"** — with the ACW clock running.

## D125. The plan catalogue is live data behind `CoreDataProvider`, not a file in `config/`
_Taken 2026-09-08. Track B's first slice, and it exists because **the user caught a
category error before it was made.** The plan of record said `config/products.yaml`._

- **Problem.** `D115` extends the pitch into the broker's own job: compare plans across
  carriers and recommend the one that fits. That needs a **catalogue** — and every earlier
  note about Track B said to put it in `config/products.yaml`, beside the intents, the
  menus and the playbooks.

  The user's question was the whole correction: *"shouldn't the comparison data come from
  the company's database, not a yaml?"*

- **They are right, and the reasoning generalises.** `config/` holds the **domain pack** —
  taxonomy that describes how *we* work: what intents exist, which skill takes them, what
  the menu says, what steps a playbook has. It ships with the code and changes when we
  change.

  A plan catalogue is none of those things. It is **live data owned by somebody else**: new
  plans appear, plans are withdrawn, cover is repriced, and none of it involves us. It is
  the same kind of fact as a policy or a claim, and it belongs behind the same seam.

  And the practical half is sharper: **`config/` is not reachable by the hackathon-day data
  swap.** `DATA_MODEL` §4 and `D3` exist so that "the data is not what we assumed" is a
  config line and a green contract suite rather than a rewrite. Putting the one thing the
  comparison feature reads *outside* that seam would have meant the feature the pitch leans
  on hardest was the one feature the swap could not fix.

  ⚠️ The port **already had `get_product()`** and `mock/bank_core/fixtures/products.json`
  **already existed**. The catalogue was never missing; it was insurer-shaped and nobody
  could list it.

### What landed

**`list_products(line=…, active_only=…)` on the port, and on every adapter** — fixtures,
null, and the caching decorator, which keys on **both** arguments because a decorator
keying on the method name alone would serve the motor catalogue to a health call.

**`Product` gained `insurer` and typed `coverages`.** The same field `D117` added to
`Policy`, for the same reason: a single company's catalogue has no use for it, and a
broker's mandate is *"คัดสรรแบบประกันและบริษัทฯ"* — select the plan **and the company**.
The figures are `Coverage` rows, the same model `Policy` already uses, read by **one
parser**: a gap analysis compares what the customer HOLDS against what a plan OFFERS, and
two shapes for one concept is two mapping bugs that would disagree exactly where the
comparison is drawn.

⚠️ **There is no premium field, and that is the decision, not an omission.** The brief puts
premium pricing and underwriting explicitly out of scope. A `Product` says what a plan
*covers*; what it costs this customer depends on their age, their risk and the insurer's own
underwriting. Surface and compare, never quote (`D115`).

**`None` means not stated, never zero.** A plan silent on outpatient cover and a plan that
excludes it are different products, and a table rendering both as `0` makes a claim the data
does not support — `D16` in the place a customer reads it.

**Ordering is the adapter's and means nothing.** Ranking is a decision, it happens in
`services/` on real attributes, and it is explicitly not "the order the file happened to be
in" — which is exactly how an affiliated carrier ends up silently first.

### The fixtures, and two things that were quietly broken

19 plans across 6 carriers with real 2025 market share (`MARKET_FACTS` §8), including a
**withdrawn** one so `active_only` has something to exclude — `B30`'s rule: a filter with
nothing to filter has never run.

Two faults surfaced by writing the contract test rather than by using anything:

1. **`EMP-GROUP-HEALTH` and `KS-MOTOR-2ND` were on policies and in no catalogue at all.**
   `get_product` returned `None` for two of the demo customer's three policies, and the
   only symptom would have been an empty column on the customer's phone.
2. **The same carrier was spelled two ways.** A policy said *"อลิอันซ์ อยุธยา"* and the
   catalogue *"อลิอันซ์ อยุธยา ประกันภัย"* — and the suffix is what separates the general
   arm from the life company. A comparison cannot group a customer's cover by carrier if
   the two halves spell it differently. The test asserts they agree, per policy.

⚠️ **The affiliated carrier is deliberately not the best row**, and a test now asserts it —
because that is a property of the *demo* rather than of the code, and it is exactly the kind
of thing a later fixture edit reverses without anyone noticing. A broker whose affiliate
always wins is not a broker, and the honest version survives a judge asking about it.

### What this slice does NOT do

**No ranking, no gap analysis, no comparison on screen yet.** The transport has existed
since `D120` — the broker can already push a comparison to the customer's phone and it
renders. What is now available is the *data* it should be built from. The next slice is the
gap analysis: the customer's cover against each candidate, differences **ranked by size**,
with the model writing only the reason sentence (`D16`, `D115`).

## D126. Compare & best-fit: the ranking is arithmetic, and the model only writes the sentence
_Taken 2026-09-08, on top of `D125`'s catalogue. Track B's second slice, and the first
time the broker's actual mandate appears on a screen. Journey step 3 — the brief's
**LEAK สูงสุด**._

- **Problem.** `D120` built the transport in September and it has worked ever since: the
  broker presses a button and a comparison table appears on the customer's phone. What
  filled it was **hand-written in the demo**. `D125` then put a real catalogue behind the
  port. What was still missing is the thing in between — deciding *which* plans, in *what
  order*, and *why*.

- **Decision.** `services/comparison/`, a no-I/O service that takes the customer's policy
  and a catalogue and returns a ranked answer, with `config/comparison.yaml` saying which
  figures matter and which way is better.

### The rule, and why the split is where it is

**Rank on facts, explain with the model** (`D16`, `D115`). Every position in the table is
arithmetic over weights in config, applied to figures read from `Product.coverages`. A
model — when one is wired in — rewrites only the **reason sentence**, on top of an ordering
it did not choose. A model that ranks is a model that can be argued into putting a plan
first; a model that explains an arithmetic ranking cannot.

⚠️ **The model is not wired in yet.** The sentence is generated from the same differences
the ranking used — duller, and it cannot invent anything. `Candidate.reason_th` is the
seam and the ranking is untouched by filling it.

### Why the weights are config and the catalogue is not

`D125` moved the *catalogue* behind `CoreDataProvider` because a list of plans is live data
owned by somebody else. This file is the opposite kind of thing: *"a higher room rate is
better, and it matters three times as much as an outpatient limit"* is **insurance
knowledge about how to compare**. The test of the distinction, written into the file: **if
the answer changes because an insurer launched a plan it is data; if it changes because we
decided differently it is config.**

`better: higher|lower` refuses to boot on anything else, because getting it backwards is
silent — every plan still renders, in the wrong order, with a confident sentence attached.

### Four rules the arithmetic obeys, each of which was a way to lie to somebody

1. **`None` means NOT STATED, never zero.** A plan silent on outpatient cover and a plan
   that excludes it are different products; a table rendering both as `0` makes a claim the
   data does not support. An unstated figure contributes nothing in either direction and
   renders as "—" — and it can never win its row.
2. **Improvement is clipped.** A hundred-million-baht annual limit must not carry a plan
   that is worse on everything else. A comparison whose order is decided by one outlier is
   a sales script with a table around it.
3. **Below a floor, "better" is not reported as better.** A 2% higher room rate is not a
   reason to change carrier. The figure still renders — the customer sees both numbers —
   it simply is not called an improvement.
4. **The plan they already hold is never a candidate.** It is the column everything else is
   measured against. Offering it back is a table recommending the status quo it was asked
   to challenge.

And a fifth that only appeared when the output was looked at: **with no policy to improve
on, every weighted improvement is exactly zero.** That is not a tie, it is *no ranking at
all*, and the order then falls back to whatever the catalogue happened to be in — which is
precisely how an affiliated carrier ends up silently first. New business is ranked against
the candidate set instead, min-max per attribute. `D123` had just made that path reachable
from the app, so it was a live case rather than a hypothetical.

### The table is composed on the server, and that is `D16` rather than tidiness

Until now `compare.plans` pushed **whatever payload the client sent**, so the coverage
figures on a customer's phone would have been assembled in a browser. A coverage number is
data read from the record; *"the client built it"* is not that. The push endpoint now
discards the request's payload for `kind: comparison` and builds it, and a test sends a
fake table to prove it is thrown away.

### ⚠️ A disclosure leak, found by reading the output rather than the code

The table's first column is headed **"แผนปัจจุบันของคุณ"** and carries the customer's real
coverage figures — and `compare.plans` is `personal: false`, so the first push landed all
of it on a **guest** screen. Tapping a link proves possession of a phone, not identity
(`D42`).

Both obvious fixes are wrong. Marking the tool `personal` walls off the one part of this
journey with no privacy cost at all, which is the ordinary and wrong design `D120` argued
against at length. Leaving it is a stranger's cover on whoever holds the handset.

**So the tool stays guest-safe and the COLUMN moves.** A guest sees the market; a signed-in
customer sees the market *against their own cover*. Same tool, same button, and the page
says **why** the column is missing rather than leaving an absence a guest would read as
*"they do not have my policy"*. That is `D121`'s correction — look at the purpose of what
is being pushed, not its shape — arriving **inside** a single push rather than between two
tools. The flag comes from the session and never from the request, for `personal`'s reason.

### What it must never do

Quote a premium or imply approval. Pricing and underwriting are out of scope (`D115`,
`D117`) — `Product` has no premium field to read even if this wanted one — and every
pushed table carries the sentence saying so, because a customer reading a comparison will
assume a price is implied unless told otherwise.

### Verified by driving the workstation

On a running server, on a live `health.advice.compare` call: the panel ranked **เมืองไทย
เฮลท์ พลัส** first, **กรุงเทพ เฮลท์ อีลิท** second *despite the best figure in three of
five rows* — because of its ฿30,000 deductible, shown as a red chip beside the green ones —
and **โตเกียวมารีน** third. Pushing to a guest screen produced a four-column market table
with `held_hidden: true`; signing in and pushing again added **แผนปัจจุบันของคุณ** as
column 1. 14 unit tests on the rules, 4 over HTTP.

## D127. The comparison becomes one answer inside a plan-information panel
_Taken 2026-09-09, from the user's reaction to `D126` on screen: *"it's just there on the
side on the right, and is just put into there with not much thought at all."* They are
right, and the fix is not layout._

- **Problem.** `D126` shipped the comparison as a permanently expanded block in the
  workstation's right column: a ranked list, a five-column table and a push button, in a
  340-pixel lane. The table needed its own horizontal scrollbar to exist there. The
  feature arrived before a place for it did.

  The user's own framing named the missing thing: *"what if we do an info panel, from which
  there'll be multiple stuff about the company's plans/info, and one of the things in there
  will be to create the plan comparison."*

- **They are describing the actual question.** A broker on a call does not want *"the
  comparison"* — they want **what the market offers**. Comparing against what the customer
  holds is one answer at one zoom level; reading a single plan's figures out loud, or
  checking what a carrier covers before saying anything, are the same question at another.
  Building the narrow answer as the whole surface is what made it look bolted on, because
  it was.

- **Decision.** A compact **ข้อมูลแผนประกัน** panel on the rail with a one-line preview of
  the current best match, opening a dialog with a shared line selector and two tabs:
  **เปรียบเทียบ** (`D126`'s ranking, unchanged) and **แผนทั้งหมด** (the catalogue, plan by
  plan, with every figure the ranking uses). Either can be pushed to the customer.

`GET /v1/agent/calls/{id}/plans` is the new read. `held` marks the plan behind the
customer's own policy — **a flag, not extra detail**: the policy panel already discloses
that record at the level assurance allows, and this must not become a second, ungated route
to the same fact.

`info.plan_detail` pushes one plan, and it obeys `D126`'s rule exactly: **the client names
WHICH plan, the server says what it covers.** A test pushes fake bullets alongside the
product code and asserts they are discarded.

### ⚠️ The bug the line selector exposed the moment it existed

Switching the dialog to **motor** on a health call announced *"ลูกค้ายังไม่มีความคุ้มครอง
ในหมวดนี้"* — to a customer holding a motor policy — and then ranked the motor catalogue as
**new business**.

The baseline was `relevant_policy`: the policy *this call* is about. That is right for the
policy panel and for a handoff, and wrong the instant the broker asks about another line —
which is the entire point of a broker's portfolio. `D117` gave the demo customer cover from
three carriers across two lines precisely so this case exists, and `B30`'s lesson applies
again in a new place: **a fixture with one of something tests nothing about choosing.**

It now reads `active_policies` and takes the policy **on the line being asked about**,
preferring the call's own when it matches so the default view still names the policy the
rest of the screen is about. Two tests: one that motor finds `MT-2024-008830`, and one that
a line they hold nothing on is still comparable — as new business, which is a different
question with a different answer and must not be reached by accident.

### Why this is not just a bigger panel

The dialog is where a broker *looks something up*; the rail is where they see that there is
something to look up. Putting the preview line on the rail (*"ตรงที่สุดตอนนี้: …"*) keeps
the useful half visible at all times and costs no width, which the permanently expanded
version could not do at any size.

## D128. One tool sends what the broker typed, and the customer is told a person wrote it
_Taken 2026-09-09 from the user's idea: `form.free_text` (`D122`) lets the customer type to
us; this is the same box pointed the other way._

- **Problem.** Every control on the tool rail sends something the **system** composed from
  a record — a comparison, a prefilled form, a document checklist. There was no way for the
  broker to put *their own sentence* on the customer's screen: a hospital name, a spelling,
  a reference number, three steps to follow. All of it currently goes down the phone line
  and has to be caught by ear, which is the exact failure `D120` was built to remove.

- **Decision.** `note.agent_message` — a composer in the rail rather than a button. The
  broker types or pastes, presses send, and it appears on the customer's screen. Line
  breaks survive, because an address has them and losing them is the reason not to bother.

### ⚠️ The tier gate cannot help here, and saying so is the design

`D121` established that `personal` describes **what a tool is FOR**. This tool is for
whatever the broker needs to say, so the system cannot classify its content — it did not
write it. Marking it `personal: true` would wall off the ordinary case (a hospital's name
is true for anybody) and would not stop the broker typing a policy number anyway.

So the composer puts the **screen's current tier in front of them while they type**:
*"ลูกค้ายังไม่ได้เข้าสู่ระบบ — เลี่ยงข้อมูลส่วนบุคคล"*, or green once they have signed in.
That is `D71`'s pattern — give the person the fact and let them decide — rather than a
refusal the system is not equipped to make. The broker is already choosing what to say out
loud to whoever is holding that phone; this is the same judgement, with the screen's status
visible for once.

### The customer is told a person wrote it

`from_agent: true` renders **ข้อความจากเจ้าหน้าที่** above the text. Every other panel on
that screen is the system quoting a record, and a typed sentence rendered identically would
borrow an authority the person who wrote it did not claim.

### And it made an existing hazard real

`/sim` interpolated `payload.text_th` into `innerHTML` **unescaped**. That was latent while
every payload was server-composed; the moment a human types into the box it is a script
injection with a keyboard attached. Both customer surfaces escape now, and `/sim` gained the
comparison table it had been silently degrading to a bare title (`D122` made the app a
paired screen; its renderer never caught up).

### Not built: images and files

The user asked *"maybe even images/files upload and send too?"*. Deliberately not, and it is
a scope call rather than a shrug: agent→customer files need blob storage on the assist path,
a size and type policy, and a retention rule (`D14`) — the same list `Q34` already has open
for form answers. **The text half is the part that removes the failure being solved**, and
it needs none of that.

## D129. One-of-many renders as pills, not as radio rows
_Taken 2026-09-09 from the user's note on the transfer dialog: the bullets show, they do not
line up, and there is a lot of empty space._

- **Problem.** `D124`'s dialog used native `<input type="radio">` rows. Three things go
  wrong at once with Thai labels: the marker sits on the first line's baseline while the
  label wraps beneath it, so nothing lines up; each option owns a full-width row, so six
  short reasons make a tall column of mostly nothing; and twelve carrier names became a
  scroll.

- **Decision.** A `Pill` control — `<button role="radio" aria-checked>` inside a
  `radiogroup` — wrapping into whatever width it has. **The semantics stay in the markup**,
  so dropping the input element does not drop one-of-many for anybody using a screen
  reader, and selection moves three things at once (border, fill, weight) rather than
  relying on colour alone, because it has to survive a glance across twelve names.

The customer's own carrier keeps its own full-width pill above the grid: it is a different
**kind** of answer, not one more row in the same list (`D124`). The same control is reused
for the plan dialog's line selector (`D127`), which is the same question shape.

## D130. The cost table was wrong in three ways, and a comparison harness now exists
_Taken 2026-09-09, after the user supplied an OpenAI key and asked which model should write
the summary. Closes `PLAN.md`'s P4 exit criterion **"a comparison table produced by the
harness, not by opinion"**, which had been open since the phase was written._

- **Problem, part one — the key was in `.env` and unread.** The user put `OPENAI_API_KEY`
  beside `ANTHROPIC_API_KEY`, which is the name OpenAI's own SDK and every tutorial use.
  `Settings` declared `llm_api_key` and not that, and `pydantic-settings` ignores an
  unknown name silently. **A key sitting in `.env` under a name nothing reads is
  indistinguishable from no key at all** — and the failure is the quiet kind: everything
  starts, the provider is simply never usable.

- **Problem, part two — `NEXT_SESSION` had flagged the cost table as wrong, and it was
  worse than flagged.** `_PRICES_PER_MTOK` in `adapters/llm/anthropic.py` carried Opus at
  `$15/$75` and Sonnet at `$3/$15`. Verified against current pricing documentation on
  2026-09-09: **Opus 5 is `$5/$25` and Sonnet 5 is `$2/$10`.** And the third fault was the
  one nobody had noticed: the Haiku key was `claude-haiku-4-5-20251001`, a **date-suffixed**
  id, so `model.startswith(prefix)` against the ordinary id `claude-haiku-4-5` matched
  nothing and priced silently at `None`.

  That table is where `D119`'s published **"$0.0085 per call"** came from. The measured
  figure on the corrected table is **$0.00569**. The old number was ~1.5x too high, which
  is the direction that flatters nobody and would have been quoted at a pitch.

- **Decision.** Declare `openai_api_key` as a secret and read it as the fallback for
  `llm_api_key`; correct both price tables against live documentation; and build
  `scripts/compare_llm.py`.

### The rule the price tables now carry

**A price table is a claim about the world and it goes stale on the vendor's schedule
rather than on ours.** Both tables say so in a comment and both name the date they were
verified. This is `B17`'s lesson — *a model id is a fact, not a naming convention* — moved
one field along: a *rate* is a fact too, and inventing one from memory is the same mistake
as inventing `...-combined-ct2` was.

Matching is **longest-prefix** now, in both tables, so `gpt-5.4-mini` cannot price as
`gpt-5.4` and `claude-opus-4-8` cannot price as some shorter family name.

⚠️ **OpenAI's rates are applied only when the base URL is actually OpenAI's.** The same
adapter serves Typhoon-hosted, vLLM, Ollama and LM Studio; a self-hosted model has no
per-token price, and Typhoon's is `Q8` and still unverified. For all of those the cost
stays `None`, because a blank cell in a cost comparison is honest and a fabricated figure
is not.

### `max_tokens` is not the field every server wants any more

Measured, not assumed: the `gpt-5` family **rejects `max_tokens` with a 400** and demands
`max_completion_tokens`; `gpt-4.1` accepts either. Rather than carry a list of model
families — which would be wrong the week a new one ships — the adapter believes the
server: the first 400 that names the other field flips a per-instance flag and retries
once, and only ever flips forward, so it cannot loop. A rejected parameter is refused
before any inference, so the retry costs a round trip and no model time.

### What the harness measures, and what it refuses to

It drives the **real** `IntakeSummariser` through the **real** `build_llm` over the **real**
prompt file, so what comes back is what the product would have produced. It reports
latency, tokens, cost, and every objective guard the service already applies: schema
validity, the model's own `is_clear`, `D16`'s figure refusal, and a per-case check that the
summary states nothing the caller did not say.

It does **not** score Thai prose quality, and that is deliberate. There is no honest
automatic way to do it here, and a fabricated quality column is exactly the metric problem
`Q28` warns about. It dumps every model's output in full instead, and its own docstring
says **read the dump; do not rank on latency alone.**

⚠️ **That instruction earned itself immediately.** The `clean` column gave `gpt-4.1-nano`
12/12 — and reading the dump showed it had written *"รถชนเมื่อคืนนี้"* (crashed **last
night**) about a caller who said *"เมื่อเช้านี้"* (**this morning**), while `gpt-4.1-mini`
leaked the raw English line label `motor.` into Thai prose. Neither is catchable by a
substring rule, both would reach a broker's screen, and the cheapest model is the one that
did it.

### The table, 4 Thai intakes x 3 repeats, 2026-09-09

| model | p50 | worst | $/call |
|---|---|---|---|
| `gpt-5.4-mini` | **0.89 s** | 1.14 s | $0.00072 |
| `gpt-4.1-nano` | 1.09 s | 1.29 s | **$0.00007** |
| `gpt-4.1-mini` | 1.25 s | 1.38 s | $0.00031 |
| `gpt-5.4-nano` | 1.40 s | 3.49 s | $0.00020 |
| `gpt-5.4` | 2.01 s | 2.49 s | $0.00258 |
| `claude-haiku-4-5` | 2.58 s | 2.89 s | $0.00248 |
| `claude-sonnet-5` | 4.40 s | 5.07 s | $0.00569 |

**`claude-sonnet-5` at 4.40 s reproduces `D119`'s single measured 4.5 s**, which is the
check that the harness is measuring the same thing that decision did.

Two findings worth keeping. **The fast tier is 3-5x faster than Sonnet and loses very
little** on this task — summarising six plain sentences is not where a frontier model earns
its latency. And **Anthropic spends ~1,500 input tokens where OpenAI spends ~455** for the
identical prompt, because the schema is handed over as a forced tool definition; that is
most of the cost gap and none of the quality gap.

## D131. The AI summary runs twice: a preview during the offer, the real one after Accept
_Taken 2026-09-09, from the user's own design. `Q35` has been open since `D119` because the
summary takes 4.5 s against a 1 s brief budget — this is the answer to it._

- **Problem.** `D119` starts `summarise_call` from `accept_offer`, so the AI summary lands
  **after** the broker is already talking. The pitch's central claim is *"the agent has the
  brief before they speak"*, and for the AI half of that brief it was not true. `Q35`
  recorded the gap honestly and parked it.

  The seconds are sitting right there and doing nothing: `OFFER_TIMEOUT_S` is 20 s, and
  `D21` already establishes that the caller **goes on talking for the whole of that
  window** — the offer window *is* the intake grace period. So there is a gap between the
  card appearing and Accept being pressed in which a model could run, and a transcript
  that keeps growing through it.

- **Decision.** Summarise **twice**. A **preview** on the fast model when the offer card
  appears, from the transcript so far; the **final** pass on the careful model from the
  accept path, over the whole transcript. The second replaces the first.

### The user's own framing, which is the correct one

> *"the system cuts whatever has been said up to when it showed the card … and so we will
> feed the full thing into the model again when the call is accepted? and update the
> summary to the one that used the transcription in full?"*

Yes, and the reason the second pass is not optional is `D21`: a preview is **necessarily**
a summary of part of a call, because the caller is still speaking while the card rings.
Shipping only the preview would put a confidently incomplete brief on the screen for the
whole conversation.

### Almost none of this needed new plumbing, and that is the point

`OfferOut.summary_th` already renders whatever `render_brief` produced, and `render_brief`
already **prefers** the AI summary over the rule-based one. So a summary that exists early
enough appears on the offer card with **no schema change and no client change**. The seam
had been there since `D119`; nothing had ever put a result into it in time.

`D106` had also already drawn the disclosure line this needs: the **verbatim transcript** is
flushed on Accept and never on the offer, because an agent who declines must not have read
a caller's words for a call they never took — but `D69`'s gated *summary* preview on the
card is a different disclosure and stays. A preview summary is on the permitted side of a
line somebody else already argued.

### ⚠️ The bug this nearly shipped with, caught before commit

The trigger was first written into `sweep_once`, reading the offers off `DispatchResult`.
**`DispatchService.tick()` has two callers**: the sweep, and `POST /v1/demo/calls`, which
ticks the dispatcher itself — and the demo endpoint is the path the entire demo and every
test actually take. The preview would have been correct, tested at the unit level, and
**dead on the only path anybody uses**: `B36` exactly, and `B24`'s family again.

So it hangs off an `on_offer` hook **at the point the offer is made**, which no future
caller of `tick()` can bypass. `DispatchResult.offers` still exists as reporting and test
surface and its docstring says in terms: *do not drive side effects from it.*

The hook is a **callback, not a dependency**, for the same reason `AgentNotifier` is one: a
service that rings a workstation must not import from `api/` and must never acquire an LLM.
It is synchronous and returns immediately, having started a task.

### Three rules the two passes obey

1. **The supersede key is the turn count, not presence.** The old early-return was
   `if call_session_id in self.ai_summaries: return` — with two passes that would make the
   preview permanently *win*, so the final pass would never run and the screen would keep a
   summary of the first half of the call. A preview is replaced when there is more to say;
   a final result is never replaced.
2. **A late preview must never overwrite a final summary.** The agent can press Accept
   while the fast model is still thinking. Losing that race must not downgrade a
   whole-transcript summary to half of one, so the write checks what is already there.
   There is a test that lands them in the wrong order on purpose.
3. **A quiet caller costs one model call, not one per tick.** Without the turn-count key a
   caller who says nothing while being re-offered under `D113` would pay for a fresh,
   identical summary every sweep, forever.

### The screen says which pass wrote it

`BriefOut.summary_is_preview` — `D68`, *if the server knows it, the server says it*. Without
it the summary silently rewords itself a few seconds into the call and the broker has no way
to tell an upgrade from a glitch. The workstation renders it as a **สรุประหว่างรอรับสาย**
badge that disappears when the final pass lands.

### One model or two, and why the second is optional

`LLM_FAST_MODEL` unset means *use the same model for both*, so one model stays the simple
configuration and nothing changes for anyone who does not set it. What a second model buys
is **only speed**, and `D130`'s table is what makes that a measurement rather than a
preference: `gpt-5.4-mini` p50 0.89 s against `claude-sonnet-5`'s 4.40 s is the difference
between a summary that reaches the card and one that does not.

The preview gets its **own, tighter deadline** (`LLM_PREVIEW_TIMEOUT_S`, 4 s). A preview
that arrives after Accept is not a late preview, it is wasted money — the final pass is
about to run anyway.

### ⚠️ The cold-start cliff, and why the LLM is warmed at startup

Measured: the **first** hosted-model call of a process pays DNS, TLS and — on the
OpenAI-compatible path — the one-off `max_tokens` rejection that teaches the adapter which
field the server wants. Cold, that came to over 5 s and blew the preview's deadline, so
**the first caller of a demo would have been the one caller with no preview**. `_warm_llm`
makes one throwaway call per stage at startup, in the background, non-fatal — the same
shape and the same argument as `_warm_stt`. It costs a fraction of a cent per process start.

Its log line reports whether the call actually **answered**, because `summarise()` swallows
its own failures and returns `None` (`D119`), and an unconditional "warmed" would claim
success for a call that timed out.

### Verified on a running server, not from the tests

`motor.claim.notify`, scripted engine, `LLM_FAST_MODEL=gpt-5.4-mini` and
`LLM_MODEL=claude-sonnet-5`. The card carried the rule-based summary; **1.8 s after the desk
went ready** it was replaced by *"ลูกค้าแจ้งว่าเกิดเหตุรถชนเมื่อเช้านี้ บริเวณแถวรัชดา ขณะนี้จอดรถอยู่ข้างทาง
และไม่มีผู้ได้รับบาดเจ็บ…"* — **before Accept**. Accepting replaced it 4.0 s later with the
Sonnet summary over all six turns, and `summary_is_preview` went to `False`.

⚠️ **A finding from that run worth keeping:** the first attempt placed a
`health.claim.notify` call while `config/demo_transcript.yaml` is a **motor** crash, and
Sonnet correctly answered `is_clear: false` and refused to summarise. The model was right
and `D119`'s "believe `is_clear`" rule did its job — but it means a demo whose scripted
lines do not match its intent code will show **no AI summary at all**, and look broken.
The two have to be chosen together, which is `D107`'s rule about the script and the audio
being sized for each other, one field along.

## D132. The test call gets options, and the dialog reports the machine rather than describing it
_Taken 2026-09-09 from the user's own proposal: **"i basically just want a way i can test
and see and show the llm actually working, cause rn i can't really do that easily."** They
were right that it could not._

- **Problem.** `+ สายทดสอบ` places a caller with fixed defaults and no recording, so the two
  most interesting things this system does — a transcript arriving while somebody waits,
  and the AI summary landing on the offer card before Accept — were reachable only from a
  `curl` line in the README. **A feature nobody on the team can press is a feature nobody
  can rehearse**, four days before a pitch that has to show it.

  This is `D121`'s lesson arriving again, one layer along: the tool rail worked for a
  fortnight with nothing anywhere to click, and the verification note honestly said
  *"verified end to end in a browser"* — of a browser driven by hand through the API.

- **Decision.** Keep the plain button exactly as it is, and add a second one that opens a
  dialog. The dialog **starts on the plain button's defaults**, so placing without touching
  anything gives the same call; what it adds is every stage turned on deliberately.

  `GET /v1/demo/call-options` feeds it, and everything in that payload is read from the
  **loaded domain pack, the real fixtures and the live `Settings`** — so the dialog cannot
  offer an intent that does not exist, a persona with no customer, or an audio file that is
  not on disk. A hand-written list in the client is the thing `D48` argues against, pointed
  at the demo surface.

### Two things are reported, not offered, and that is the whole design

**Assurance is derived from evidence** (`D20`). A number nobody holds is L0; a number the
core holds is L1; the app's correlation token is L3. A dropdown offering "L3" would assert
a level with nothing behind it — precisely what `D84` deleted from the IVR and what `D44`
refuses. So each caller carries the level it **produces**, and the dialog shows it as a
consequence. That also makes the ladder legible to anybody driving the demo, which is the
cheapest place this system ever gets to explain itself.

**Urgency comes from the intent**, out of `config/intents.yaml` (`D28`). Pick a reason and
the dialog shows the urgency, the line and the skill it carries. Neither is a knob, and a
knob would teach the person driving it something false about how the system decides.

### Three guards, and every one of them caught a real trap during the build

1. **The script must match the intent.** `config/demo_transcript.yaml` now declares
   `matches_intent: motor.claim.notify`, and the dialog warns on a mismatch. This is not
   cosmetic: the summariser's prompt gets the intent label *and* the transcript, so a motor
   crash filed as a health claim makes a good model correctly answer `is_clear: false` and
   refuse (`D119`). The screen then shows **no AI summary at all** and looks broken while
   working exactly as designed. It cost a verification round on 2026-09-09 before this
   existed. The warning names the intent to pick, and says that leaving it is a fair way to
   watch the refusal work.
2. **The intent must be one this agent can receive.** Since `D117` an advisor and a service
   agent on the same line hold different skills, and `claims.assist` is cross-line — so a
   motor *advisor* placing a motor *claim* is never offered it and the caller just sits in
   the queue as `no_qualified_agent`. `D117`'s own note says that cost about an hour to
   recognise the first time; this dialog is the easiest possible way to walk into it, so
   unreachable intents are marked in the list and warned about below it. **Found by doing
   exactly that while verifying.**
3. **The default caller must be one the system recognises** (`Q38`). At L0 there is no
   customer, so no context snapshot, so `render_brief` returns nothing — and the AI summary
   is rendered as part of the brief. Defaulting to the anonymous caller meant the default
   configuration was the one path where the feature *cannot* appear. Also found by
   verifying, and the underlying incoherence is recorded as `Q38` rather than patched.

### The dialog may be used while un-ready, unlike the plain button

The plain button is disabled when the agent cannot receive a call, because a one-click
caller placed with nobody to take it sits unmatched and reads as a routing bug. **The
dialog is deliberately not**, because placing first and going ready afterwards is exactly
the sequence that lets somebody watch the transcript arrive and the preview land *before*
Accept. It says what will happen instead of refusing, which is `D71`'s pattern.

### Verified by driving the screen, not the API

On a running server with `LLM_MODEL=claude-sonnet-5` and `LLM_FAST_MODEL=gpt-5.4-mini`:
the dialog listed 33 intents and 4 callers with their derived levels; ticking the two
recording boxes flipped the *"what this call will test"* panel from ⬜⬜ to ✅✅; the
mismatch guard fired on `motor.other` against a `motor.claim.notify` script and cleared
when the intent was corrected; and the placed call put the AI preview on the offer card
**2.2 s after the desk went ready**, replaced after Accept by the Sonnet summary over all
six turns.

## D133. The customer can TYPE their intake, and the app asks before it dials
_Taken 2026-09-09 from the user's idea: **"since this is in app, we can do something like
'while you wait would you like to record or write…' instead"**. The writing half turned out
not to be a stub at all._

- **Problem.** The pre-call intake is the pitch's second move and it needs a microphone, a
  media path from the browser, and an ASR engine — none of which exist on the customer's
  side. `/sim` therefore always sent `intake_keys: ["2"]`: every app call declined the
  offer, so the app could never demonstrate the feature the deck leads on.

- **Decision.** Two halves, and they are deliberately not the same kind of thing.

  **Typing is a first-class path, not a stand-in.** `D88` put the intake seam at *turns
  rather than frames* precisely so a strategy is a pure function of what was **said**,
  however it was said — the gateway, the detector and the STT engine all sit on the far
  side of it. **Typed text is a turn that never needed a model.** So it goes straight into
  `IntakeService.on_turn`, and the held transcript, the agent's screen, the durable
  `transcript_turns` row, the rule-based brief and the AI summary all work unchanged.

  **Voice is a labelled stub**, which is `D115`'s rule for anything that looks real and is
  not. Choosing it consents exactly as typing does and then says on screen that the audio
  path is not connected, rather than miming a recorder that captures nothing.

### What this buys, and it is worth more than it sounds

**The entire AI story now runs with no audio anywhere.** Verified end to end: three
sentences typed into `/sim`, no microphone, no WAV, no GPU, no ASR — and the broker's offer
card carried a `gpt-5.4-mini` summary **2.5 s** after the desk rang, replaced after Accept
by the Sonnet summary over all three turns.

Four days from a pitch, on a shared venue laptop, that is the difference between a demo
that depends on a working sound card and one that does not.

### ⚠️ The offer is asked BEFORE dialling, and that was forced as well as better

The user asked for it *during* the wait, which is where a telephone asks it. Two reasons it
moved:

1. **An app has somewhere better to put the question.** It is already asking which plan and
   which reason; one more tap costs nothing, while a spoken question costs everybody thirty
   seconds in an earpiece. That is `D48`'s argument exactly — answer on a screen what would
   otherwise be read out.
2. **It is the only place it can be asked.** `INTAKE_ACTIVE` is reachable only from
   `QUEUED`, and this system moves a caller to `MATCHED` the instant the offer resolves —
   so there is effectively **no window** during the wait in which an intake can legally be
   opened. Attempted, refused by the state machine with
   `IllegalTransition: matched -> intake_active`, and **left refused**: reaching into the
   call lifecycle four days before a pitch to widen a transition is not a change to make
   casually. Recorded as `Q39`.

A first version of this shipped an app-side "standing offer" endpoint (`POST
/v1/app/intake/answer`) plus `HoldMachine.on_app_answer`, on the argument that a card on a
screen never stops being on screen. It was correct, tested — and once the question moved to
before dialling, **called by nothing**. It was deleted rather than left in place, because
that is this project's single most repeated fault and the fix is to notice it before
committing rather than after.

### Two rules the typed turn obeys

**`engine` is `typed`, never an ASR name, and `asr_confidence` stays `None`.** Recording a
keyboard as though it were a transcriber puts a confidence score on something that was
never uncertain, and makes the two indistinguishable in `transcript_turns` — the table
P4's analysis and P6's wrap-up draft both read.

**`D98`'s rate guard does not apply, and must not be added.** It refuses more characters
than a human could have *spoken* in the seconds of audio a turn arrived on. A keyboard has
no such limit; applying it here would police a customer instead of catching a hallucinating
model. A test asserts a long note survives.

### ⚠️ The sequence comes from the strategy, and taking it from the screen was a bug

The first version numbered each note from
`len(TranscriptDeliveryService.turns_for(call_id))`. That service is a **projection fed by
the bus**, and `publish()` only enqueues (`D105`) — so two notes typed inside one drain
window both read a count of zero and both became `seq=1`, silently corrupting the order of
the transcript. `PassiveRecordIntake` already holds every turn of the intake and is
synchronous, so the number comes from there.

`B26`'s shape in a new place: a lagging read used as the source of truth for a value
something else owns. **Found by reading a log line that said `seq=1` twice**, which is the
2026-09-08/09 discipline paying for itself again — no test asserted ordering across two
notes, and the assertion that now does was written after the log gave it away.

### The way back, which did not exist

`/sim` kept the call screen up for the whole ten-minute grace window after the broker rang
off (`D120`), with browse and contact hidden behind it — so a customer whose call ended
*while something was on their screen* had **no way back to their plans at all**. Hanging up
from their own side happened to clear it; being hung up on did not. There is a
**← กลับไปหน้าแผนของฉัน** button now, shown only once the call has ended, because offering
it mid-call would be an app inviting somebody to walk away from a conversation they are
having.

## D134. "ข้อมูลอื่นๆ ที่มีเกี่ยวกับลูกค้า" — the panel for what the bank already knew
_Taken 2026-09-09 from the user's own idea, and it is the single biggest thing in this
project that was **already built and thrown away**. ⚠️ **Committed with the verification
half-finished — read "what is NOT verified" at the bottom before trusting any of it.**_

- **Problem, and it is a measurement rather than an opinion.** `ContextAssembler` fetches
  and freezes, on **every call**: `holdings` (deposits, loans, cards, funds), `life_events`
  (mortgage / new_child / job_change / relocation, each with a confidence and a source),
  full `recent_claims` and full `recent_interactions`.

  `BriefOut` exposed **`other_policy_count`**, **`recent_claim_count`** and one
  **`last_contact_th`** string. `holdings` and `life_events` reached **no screen anywhere
  in the system** — assembled every call, frozen into the snapshot, dropped at the DTO
  boundary. Verified by grep before building anything.

  That is the brief's own promise unkept. The briefing's journey step 2 leak is
  *"re-collects data we already hold"*, and the in-scope bullet is
  *"สรุปลูกค้าให้ broker ก่อนคุย"*.

- **Decision.** `BriefOut.context` carries the rest of it: every fact, worded on the
  server, each naming its source, with a model-written summary over the top and the raw
  rows folded away underneath.

### Why this is the strongest answer to "where is the AI"

The challenge statement is **right coverage · right customer · right time · right
channel**. Before this the system answered *right coverage* (the comparison) and half of
*right channel*. It said nothing about **right customer** or **right time** — and those
two are exactly what a life-event signal is.

The fixtures already held the story `MARKET_FACTS` §7 calls *"the cleanest concrete example
of right customer × right time in the whole deck"*: **C000002 has a mortgage** (2022,
confidence 0.95, source `loan_origination`) **and a new child** (2024, 0.70), a 1m–5m loan,
and an SME consult at a branch. Mortgage-linked life is growing at **+9.91%**. None of it
had ever reached a broker.

### It SHOWS and it does not RECOMMEND, and that line is the compliance story

`D116` moved the consent gate from **holding** data to **recommending** from it. So:

- describing a record an affiliate lawfully shared is internal processing (`D74`);
- *"they took a home loan in 2022"* is a fact, *"so offer them mortgage protection"* is a
  recommendation and belongs on the far side of a consent check this panel does not make.

The prompt forbids it **and `_RECOMMENDS` refuses the output if it appears anyway** — the
same belt-and-braces `_FIGURE` gets for `D16`, on the same reasoning: the guard assumes the
prompt will one day fail.

### Three rules the facts obey

1. **Every fact names its source** (`D18`). A broker who cannot say where something came
   from cannot use it in a conversation.
2. **An inference is labelled as one.** A life-event signal carries its confidence;
   a stored row carries `None`. `income_pattern` at 0.6 is a guess and `loan_origination`
   at 0.95 is nearly a record, and a broker who cannot tell them apart will say the wrong
   one out loud. Inventing `1.0` for a record would have destroyed exactly that distinction.
3. **No number is invented and none is computed.** A balance band renders as the band the
   upstream gave; nothing adds, converts or estimates (`D16`).

**Order is a product decision**: life events first, because they are the only thing here
that answers *"why now"* and the only class the broker could not have guessed from the
policy list they are already looking at. Claims are deliberately **not** repeated —
`recent_claim_count` and the policy panel already carry them, and two renderings of one
fact is how two halves of a screen come to disagree.

### ⚠️ Two faults found while verifying, both by looking at the output

**1. `summarise_context` was called by nothing.** The patch meant to add it to
`DispatchService`'s `on_offer` hook silently did not apply — the method was written,
correct and typechecked, and the panel rendered its facts with no sentence, which reads
exactly like a model that declined. This project's most repeated fault, caught only
because the *output* was read rather than the code. There is now a test that drives **the
hook**, not the method (`test_the_context_summary_is_started_by_the_offer_hook`).

**2. A Buddhist year is not a coverage figure.** `_FIGURE` refuses any run of four or more
digits, which is right for an intake summary and wrong here: this project renders dates in
the Buddhist era, so a correct sentence about a loan taken `30/08/2565` was thrown away by
our own guard. Measured — `gpt-5.4-mini` produced a faithful summary and it was refused.
`_DATE_LIKE` strips dates **before** the money check; the money check itself is unchanged
and a test asserts `3,500,000 บาท` is still refused.

### Verified, and NOT verified

**Verified on a running server** (`A003`, renewal desk, caller `+66898887777` = C000002 at
`l1_probable`): the panel rendered **6 facts** in the right order — new child (0.70),
mortgage (0.95), loan `1m-5m`, deposit `500k-1m`, then two interactions — every one
carrying a `core:` source and a Buddhist date, confidence present on the two life events
and absent on everything else.

⚠️ **NOT verified: the model-written sentence, end to end, after the date fix.** The run
that produced the facts above is the run where the summary was refused by `_FIGURE`. The
fix has unit tests (16 pass in `test_preview_summary.py`) and has **not** been seen
working against a live model. **That is the first thing to do next** — the commands are in
`NEXT_SESSION.md` under "continue here".

## D135. The context sentence was verified, and verifying it is what showed the prompt was wrong
_Taken 2026-09-09 (late), completing the verification `D134` shipped without. The panel's
data half was already proven; this is the model-written sentence over it._

- **Problem.** `D134` committed with its own verification half-finished and said so: the
  facts rendered correctly on a running server, but the **sentence** had never been seen
  working end to end after the `_DATE_LIKE` fix. The one live run that reached it was
  refused by `_FIGURE` reading a Buddhist year as a coverage amount.

  Re-run against a live model, the guard turned out to be **fine** — and the *sentence*
  turned out not to be. This is `NEXT_SESSION`'s third pattern paying for itself: the
  counters were green and the prose was wrong, and only reading the prose showed it.

- **Decision.** `prompts/th/summarize_context.v2.md`, and `ContextSummariser.PROMPT` moves
  to it. `v1` stays on disk (`D18`: `analyses.prompt_version` names the file a result came
  from, so deleting it makes old summaries unexplainable).

### What v1 actually did, measured rather than asserted

Six live runs on `gpt-5.4-mini` over the real `C000002` facts:

| | v1 | **v2 (shipped)** |
|---|---|---|
| answered / refused | 5 / 1 | **6 / 0** |
| led with a life event | **0 of 5** | **6 of 6** |
| named the mortgage | 4 of 5 | **6 of 6** |
| asserted an absence | 1 of 5 | **0 of 6** |
| copied a balance band | 0 of 5 | **0 of 6** |
| printed a confidence decimal | 0 of 5 | **0 of 6** |

Confirmed at **8 of 8** clean on the shipped file under its final name, plus 3 of 3 on
`claude-sonnet-5` (the `self.llm` fallback when no fast client is configured), plus one
end-to-end run on a live server: **1.8 s**, `facts=6`, on the fast model.

### The three faults, and the one that mattered

1. **It never led with the life events, in any run.** `v1`'s rule 5 said *"emphasise what
   is relevant to today's reason first"*, and `known_facts()`'s docstring says the opposite
   and says why: **life events come first because they are the only thing on this panel
   that answers "why now"**, and the only class the broker could not have guessed from the
   policy list already in front of them. The prompt and the ordering decision disagreed,
   and the prompt won on every call. So the sentence a judge would read opened with *"they
   once rang about renewing a motor policy, and it is closed"* while the mortgage and the
   new child — `MARKET_FACTS` §7's cleanest **right customer × right time** example, and
   the reason this panel exists — came last or not at all.
2. **One run asserted an absence**: *"ยังไม่พบข้อมูลการต่ออายุกรมธรรม์เดิม..."* — no renewal
   record found. The prompt is handed a **selected** list, never the whole record, so
   "not found" is a claim about data it was never given. `v1`'s rule 1 offered
   *"if the list is thin, say it is unclear"* and the model applied it to the
   topic-relevant **subset** rather than the list.
3. **`_FIGURE` and `_DATE_LIKE` were never the problem.** 0 refusals in 16 clean runs with
   Buddhist dates in every sentence. `D134`'s fix is correct as written.

### ⚠️ The regression the counters would have hidden

`v2`'s first draft fixed all three and was green on every counter — and **recited the
customer's balance bands** (*"สินเชื่อช่วง 1m-5m และบัญชีเงินฝากช่วง 500k-1m"*) in **6 of 6**
runs, where `v1` had done it in 0 of 5. `_FIGURE` cannot see it: there is no run of four
digits in `1m-5m`. It also printed the raw confidence decimals in 2 of 6, which is
telemetry in prose that rule 4 exists to express in **words** so the broker does not have
to read a float mid-conversation.

The cause was mine: telling the model to write *"then the products held"* invited it to
enumerate holdings, and the band is on the fact line. Both are now refused by name in
rule 3 and rule 4. **A green counter is not a read output** — this is the same lesson as
`D134`'s own two faults, arriving one layer up.

A second draft then drifted the other way once: *"...และอาจเกี่ยวข้องกับการทบทวนความคุ้มครองใน
ช่วงนี้"* — the model **guessing a motive**, which `_RECOMMENDS` cannot catch because it
names no product and uses none of the seven listed phrases. Rule 2 forbids it explicitly
now. ⚠️ **The guard was not widened to match**, deliberately: `_RECOMMENDS` catches
sell-language, and stretching it to catch "may relate to" would start eating truthful
sentences. If motive-guessing recurs it wants its own pattern, not a looser one.

### Two rules that only appeared once it was written down

- **Dates are stated, never paraphrased.** An early draft dropped the dates entirely, and
  one run rendered `15/03/2567` as *"early last year"* — which is wrong, it is two years
  ago. The date **is** the "why now" evidence, so rule 7 requires it verbatim from the
  list. This also keeps `D134`'s `_DATE_LIKE` fix exercised on the shipped path rather than
  only in a test.
- **`is_clear: false` is the way to say "not much here", not a Thai sentence saying so.**
  `v1` asked for prose in that case, which is what produced fault 2. The schema already
  has the field and `D13` says a model allowed to decline invents less.

### The test that was missing, and it is this project's oldest shape

`ContextSummariser.PROMPT` naming a file that is not on disk fails **only** when a real
model is configured. `LLM_PROVIDER=rulebased` is the shipped default, so the suite, CI and
every offline run stay green while the panel is dead on the one machine that matters —
`B7`'s family again, and the same reasoning as the voice pack's two-way id guard (`D28`).
`test_the_shipped_context_prompt_exists_and_takes_the_slots_it_is_handed` renders the
shipped ref with the real slots; verified by pointing it at `v9` and watching it fail.
A second test asserts `v1` is still on disk (`D18`).

- **Alternatives.** *Edit `v1` in place* — refused, `D18`: a prompt is a file with its
  version in the name. *Rank the drafts on the counters alone* — that is what would have
  shipped the balance-band regression. *Widen `_RECOMMENDS`* — see above.
- **Tradeoffs.** `v2` is a longer prompt: 1300 tokens in against `v1`'s 740, roughly
  \$0.0002 more per call at `gpt-5.4-mini` rates, and no measurable latency change
  (1.1–2.9 s across 19 runs). Five explicit prohibitions is a lot of rope for a small
  model; the counters are the thing to re-measure if the fast model is ever changed.
- **Future.** The probe used here is throwaway. If a third prompt version is ever needed,
  the honest move is to fold these checks into `scripts/compare_llm.py` as a
  `summarize_context` case, so the objective columns are re-measured by the harness rather
  than by hand — and, per `D130`, the Thai still gets read by a human either way.

## D136. The container ships on the scripted engine, and the image is the "starts anywhere" story
_Taken 2026-09-09 (late). `PLAN.md`'s Track E, and queue item 2 in `NEXT_SESSION`._

- **Problem.** There was no `Dockerfile`. The system runs on a clean clone in about a
  minute — but only if you have `uv`, and `/workstation` only if you also have Node. Four
  days from a pitch that is two things that can go wrong on somebody else's laptop, and
  `PLAN.md`'s risk register has one rule about venues: **never depend on one.**

- **Decision.** A two-stage image — Node builds the React bundle, `python:3.11-slim` runs
  it — plus two compose **profiles**, so that every command that worked before this
  existed still does exactly what it did.

  ```
  docker compose -f infra/docker-compose.yml up -d postgres      # unchanged
  docker compose -f infra/docker-compose.yml --profile demo up    # the whole demo
  ```

### What the image runs on, and why each default is what it is

| | | why |
|---|---|---|
| `STT_ENGINE=scripted` | the only real degradation | a plain container **cannot reach the GPU** without host setup that varies by machine, which is exactly what fails at a venue. The real Typhoon engine runs on our own box for the pitch and the video |
| `LLM_PROVIDER=rulebased` | models off unless a key is passed | `rulebased` is a real adapter, not a stub (`D119`), so the image starts with nothing configured. `-e` switches the models on |
| `STORAGE_BACKEND=memory`, `BLOB_STORAGE=memory` | no containers required | this is what makes it startable on a venue laptop with nothing else running. `Q21` is still open; the `demo-postgres` profile is the other half of that question, one command away |
| non-root `uid 10001` | | nothing writes outside `/app` and the demo stores are in memory |

### ⚠️ The whole AI story runs in this container, with no audio at all

That is `D133`'s dividend and it is worth stating plainly, because it is the demo path a
venue can actually run. **Verified end to end inside the image**, with keys passed at
`docker run`: three Thai sentences typed on `/sim` reached the broker's transcript panel as
three turns; a `gpt-5.4-mini` preview of them was on the offer card **before Accept**
(`D131`); `claude-sonnet-5` replaced it after; and `D135`'s context panel carried its own
sentence. Every `*.wav` is excluded by `.dockerignore` — there is no audio in the image and
none is needed.

### Three faults found by running the container rather than by building it

1. **`/sim` and `/assist/<token>` 404'd, silently.** `create_app` mounts both only
   `if <dir>.is_dir()` — correct for a dev clone with no built workstation, and it means an
   image missing `apps/customer_sim/` **starts, reports healthy, serves `/workstation`, and
   loses two of the three customer surfaces with no error anywhere.** The first build did
   exactly that. Found by curling `/sim`, which is this project's `B24` lesson in a new
   place: a green build is not a running system.
2. **`DATABASE_URL` is read by nothing.** The `demo-postgres` profile was written with the
   obvious name; `Settings.database_url` is a computed **property** over
   `readycall_database_url`, so the app would have quietly talked to `127.0.0.1` inside its
   own container. That is `Q26`'s "env var that changes nothing", caught before it shipped
   because the setting was looked up rather than assumed.
3. **`chown -R` cost 109 s on every rebuild.** Recursively chowning ~4,000
   bytecode-compiled files. Creating the user *before* anything is written and copying with
   `--chown` took the rebuild from **484 s to 44 s** — which on a feature-freeze day is the
   difference between iterating and not.

⚠️ **Two of my own test scripts reported faults that were not there**, and both are worth
remembering because the shape recurs: `/v1/agent/me` carries `brief` and `transcript` at
the **top level**, not under `call`, and the offer card's AI summary is `offer.summary_th`,
not `offer.brief.summary_th` — reading the wrong path renders a working feature as `None`.
And a **stale caller from an earlier failed run** was offered ahead of the new one, because
`max_offer_rounds: 0` means a caller nobody takes circles forever (`D113`) — so the
transcript read as empty for a call that never had one. **Compare the `call_session_id` you
placed against `active_call_session_id` before believing anything about a call.**

- **Alternatives.** *Commit `apps/workstation/dist/`* (`Q17`) — not needed for the image,
  which builds the bundle itself; `Q17` stays open for the non-Docker venue case.
  *Ship the `ml`/`asr` extras* — ~3 GB of CUDA wheels for an engine this image cannot run.
  *No profiles, a second compose file* — profiles keep one file and leave §3/§4's commands
  untouched.
- **Tradeoffs.** The image is a full `slim` Python plus both LLM SDKs, so it is not small,
  and a cold build pays for `npm ci` and the dependency resolve. Neither matters for a
  demo image built once. The scripted engine means the container **cannot** demonstrate
  real Thai ASR — that is the point of the split, and the README says so where somebody
  running it will read it.
- **Future.** If the image is ever handed to a judge, `DEMO_LOGIN_ENABLED` is the flag to
  think about: it is `true` by default and is what lets `/sim` become any customer.
  Harmless on fixture data, wrong the moment the data is real (`D47`).

## D137. The model writes the comparison's reason sentence, and four guards keep it honest
_Taken 2026-09-09 (late). `NEXT_SESSION`'s queue item 3 — *"the cheapest remaining feature,
if there is time for exactly one"* — and the last piece of `D126`._

- **Problem.** `D126` split compare-and-best-fit deliberately: **the ranking is arithmetic
  over config weights, and a model rewrites only the sentence beside it.** The seam,
  `Candidate.reason_th`, has been filled by a generated sentence ever since — honest,
  traceable, and dull. It names the single biggest better row and the single biggest worse
  row and stops there.

  It is also the cheapest way left to make *"we use AI"* concrete without letting a model
  near an ordering, which is the gap `NEXT_SESSION` has carried as **STILL OPEN** since the
  user's *"it feels like a hackathon without AI"*.

- **Decision.** `prompts/th/comparison_reason.v1.md` +
  `services/analysis/comparison_reason.py`. The writer is handed the ranked candidates and
  the **same `Difference` rows the arithmetic used**, and returns one sentence per plan.
  `ComparisonService` is untouched and never learns this module exists.

### Four guards, and the first one is the whole design

1. **Every digit run in the output must appear in the input** (`D16`). Not *"no figures"* —
   the useful sentence **quotes** coverage amounts, and that is what makes it a comparison
   rather than an adjective. So the check is **provenance**, not absence: a number the
   model wrote that was never handed to it is a coverage figure invented by a model.
   Bare one- and two-digit runs are exempt on purpose (*"ดูแล 24 ชั่วโมง"*), because a
   coverage amount in this catalogue is never that small and refusing them would refuse
   almost every correct sentence — a guard that gets switched off.
2. **No ranking language.** *"ดีที่สุด"*, *"ควรเลือก"*, *"อันดับ 1"*. A model that can
   announce a winner is a model that can be argued into one, which is exactly the split
   `D126` drew.
3. **No price.** `Product` has no premium field, so any price is invented by construction.
4. **No promise of cover.** Underwriting is the insurer's (`D117`).

A refusal keeps the generated sentence, so none of this can break the table (`D12`).

### Why this one call is AWAITED, when every other model call in the system is not

`D119` and `D131` are emphatic that the summary is fire-and-forget and must stay that way.
This is the exception and the reason is mechanical: **the plan panel is fetched once when
the broker opens it and is never polled**, so a background task would complete into a
screen nothing refreshes. Everything that makes `D12` bind is still true — the ranked table
is already computed when the wait starts, the wait is bounded by
`LLM_COMPARISON_TIMEOUT_S`, a timeout renders the generated sentences, and none of it is
anywhere near the call path. `0` disables the model entirely.

Measured on a live server: **2,375 ms** for three candidates on `gpt-5.4-mini`, then **4 ms**
from the per-call cache.

### ⚠️ Two faults on the first live run, and neither was a guard

**1. The startup warm-up was dodging the cost it exists to absorb.** The first run returned
`generated` for all three plans. The log said the call failed with the provider's one-off
*"`max_tokens` is not supported with this model"* 400 — the negotiation `_warm_llm` exists
to pay once per process. The warm-up had **timed out** first: it ran on
`LLM_PREVIEW_TIMEOUT_S` (4 s), and a cold process paying DNS + TLS + that rejection
round-trip exceeds it. So the warm-up returned "did not answer", the negotiation went
unpaid, and **the next real call failed outright.**

The warm-up now takes its own 20 s deadline through `IntakeSummariser.with_timeout()` — a
new instance, not a mutation, because the live preview summariser must keep its tight one.
Nothing waits for a warm-up, so a short deadline on it buys nothing and costs exactly what
it was supposed to prevent. After the fix: `llm warmed answered=True stage=preview`, and
all three sentences came back `source: model`.

**2. My own cache made a transient failure permanent.** The first version cached the empty
result with the reasoning *"a model that refused or timed out will do it again"*. Half
right, and the wrong half matters: **a guard refusal is deterministic, a failed call is
not.** `write()` returns `{}` for the first and `None` for the second, and only `{}` is
cached. Without that, one cold-start 400 would have left the panel with generated sentences
for the rest of the call even though the very next request would have worked.

### The output, which is the point

On the fixture data, `กรุงเทพ เฮลท์ อีลิท` — the plan with the better figure in three of
four rows that still ranks **second**:

> *เพิ่มค่าห้องและค่าอาหารจาก 3,000 เป็น 6,000 วงเงินผู้ป่วยในต่อปีจาก 1,000,000 เป็น
> 10,000,000 และค่ารักษาผู้ป่วยนอกจาก 1,500 เป็น 2,500 **แต่ค่าใช้จ่ายส่วนแรกสูงขึ้นจาก 0
> เป็น 30,000***

Every figure traceable, the downside named, and the ฿30,000 deductible that decides the
ranking stated in prose. The generated sentence named one better row and one worse row; this
names all four differences in the order the weights care about.

- **On screen**: a small **AI** chip marks a model-written sentence, quieter than the
  ok/bad chips because it is provenance rather than a verdict. It is **absent** rather than
  reading "generated" when no model wrote it — a badge on every row stops being
  information. `reason_source` is on the wire for the same reason the server reports
  everything else it knows (`D68`).
- **Alternatives.** *Let the model rank and explain in one call* — the whole of `D126`
  argues against it. *Fire-and-forget with a polling panel* — a poll on a dialog is `B33`,
  and adding one to make a sentence arrive is a worse trade than a bounded wait.
  *Widen the figure guard to "no numbers at all"* — that refuses the sentences worth having.
- **Tradeoffs.** One extra model call per call+line, ~450 tokens in. The broker waits up to
  3 s the first time they open the panel. And the guards are regex over Thai, which will
  miss a phrasing eventually — which is why a refusal is silent and lossless rather than an
  error, and why the ranking never depended on any of this.
- **Future.** `Q40`'s customer×plan fit score is still **out** and still collides with
  `D126`. If the guards ever need to catch motive-guessing the way `D135`'s prompt does, it
  wants its own pattern rather than a looser existing one.
