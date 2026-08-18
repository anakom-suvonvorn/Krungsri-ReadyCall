# DECISIONS

_Significant engineering decisions and their rationale. Append new ones at the bottom; never silently reverse one without a new entry explaining why._
_Last updated: 2026-08-19._

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
  insurance call — a motor claim from the roadside, dialled off the windscreen sticker — has no app,
  no intent record, and possibly no identified customer. A design that assumes the app fails exactly
  when it matters most.
- **Decision:** The `CallSession` is primary and `intent_id` is optional. Every stage must work with
  the enrichments absent: no intent, no identity, no consent, no transcript. Entry channels are
  in-app, **product-line DID**, general hotline, callback, and transfer.
- **Reasoning:** Coverage of real behaviour, and it makes the value proposition robust: even a cold
  call gets caller-ID context, a DID-derived product line, and an optional recorded intake.
- **Bonus:** A **dedicated number per product line** (the number printed on the sticker/card/policy)
  supplies the intent for free, with no app and no menu — the cheapest routing signal in the system.
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
  regenerates only what changed. The call plays cached files. Dynamic sentences (queue position, name)
  are rendered on first use and cached by their rendered text, so they are warm within minutes.
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
