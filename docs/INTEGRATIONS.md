# INTEGRATIONS

_Every external thing the system touches: the port that hides it, the adapters behind it, and the config that selects one._
_Status: **mostly design; the persistence stack is real.** Last updated: 2026-08-25._

> **Real as of P2c (complete):** SQLAlchemy 2.0 (async) + Alembic + `asyncpg`, against
> Postgres 16 in `infra/docker-compose.yml`, verified on a live container — **nine tables**,
> and a restart verified by ending a real uvicorn process. `aiosqlite` is a **test-only**
> dependency: the fast path that lets the store suites run with no container (`D75`), never
> a deployment target. Everything else on this page is still design.
>
> `STORAGE_BACKEND=memory|postgres` picks the set of stores (`build_storage`, `D78`).
> **`memory` is not "no persistence"** — it builds real in-memory stores, so the
> write-through path runs on the default configuration and in every test rather than only
> when somebody starts a container (`B7`).
>
> The suite has **its own database**, `readycall_test`, because it drops its tables on
> teardown (`D79`).

---

## 0. The rule

Business logic imports from `readycall/ports/`. It **never** imports `twilio`, `anthropic`,
`transformers`, `boto3`, or an ARI client. Every port has at least: a **real** adapter, a **fake**
adapter for tests, and a **null/degraded** adapter that exercises the failure path. Adapter choice is
one env var. (`D3`)

```
readycall/ports/          telephony.py  stt.py  llm.py  tts.py  core_data.py
                          event_bus.py  blob_storage.py  agent_directory.py  notifier.py
readycall/adapters/<port>/<vendor>.py
```

---

## 1. Telephony / VoIP

The hardest dependency and the one most likely to change, so it gets the strictest seam.

```python
class TelephonyProvider(Protocol):
    async def originate(self, target: DialTarget, ctx: CallContext) -> TelephonyCallId: ...
    async def answer(self, call: TelephonyCallId) -> None: ...
    async def play(self, call: TelephonyCallId, audio: AudioRef | TtsRequest) -> None: ...
    async def start_media_fork(
        self, call: TelephonyCallId, sink: MediaSinkSpec
    ) -> MediaStreamId: ...
    async def stop_media_fork(self, stream: MediaStreamId) -> None: ...
    async def bridge(self, call: TelephonyCallId, agent_endpoint: str) -> None: ...
    async def hangup(self, call: TelephonyCallId, reason: str) -> None: ...
    def events(self) -> AsyncIterator[TelephonyEvent]: ...
```

| Adapter | Stack | When |
|---|---|---|
| **`AsteriskAriAdapter`** ⭐ *recommended default* | Asterisk 20 LTS, `chan_pjsip`, WSS/WebRTC for the app, **ARI** over WebSocket for control, **AudioSocket** (or `externalMedia`) for the audio fork | Self-hosted, free, behaves like a real contact centre (queues, bridges, transfers), no per-minute cost while iterating |
| `TwilioAdapter` | Programmable Voice + **Media Streams** (audio over WSS) + TwiML | Fastest to a real phone number; costs per minute; needs a public HTTPS callback |
| `LiveKitAdapter` | LiveKit rooms + SIP bridge + track subscription | If the app side is WebRTC-first and we want managed media |
| `SimulatedTelephonyAdapter` | Pure Python; feeds a WAV file as if it were live media | **Every test and every dev run.** No phone required. |
| *(future)* `GenesysAdapter` / `AvayaAdapter` | Real bank contact-centre platforms | What a production rollout would actually integrate with |

**Identity binding.** WebRTC is preferred precisely because the correlation token rides in the
signalling — identity is bound to the call cryptographically. PSTN falls back to ANI matching +
pending-intent window + IVR verification — the assurance ladder in `ARCHITECTURE.md` §3.

**Audio fork format.** AudioSocket delivers 8 kHz signed-linear mono over TCP; Twilio delivers 8 kHz
µ-law base64 over WSS. The Media Gateway normalises everything to **16 kHz mono float32** before
anything else sees it — every downstream component assumes exactly that.

Libraries: `aiohttp`/`websockets` (ARI + media WS), `aiortc` (pure-Python WebRTC if needed),
`pjsua2`/`pysip` only if a softphone is required for testing. Prefer speaking the protocols directly
over a heavyweight wrapper — fewer surprises, easier to debug.

### 1.1 The telephony options, in detail

| | **Asterisk (self-hosted)** ⭐ | **Twilio (CPaaS)** | **LiveKit** | **Simulated** |
|---|---|---|---|---|
| What it is | A full open-source PBX in a container: SIP registration, queues, IVR, DTMF, hold music, bridging, transfers, recording | Cloud telephony. You buy a number; their platform calls your webhooks; `Media Streams` sends you the audio over a WebSocket | A modern WebRTC SFU with an agents framework and a SIP bridge | Pure Python; feeds a WAV file as if it were a live call |
| How we control it | **ARI** — REST + a WebSocket event stream, driven from Python | Webhooks + TwiML; needs a **public HTTPS URL** (ngrok in dev) | Server SDK; subscribe to audio tracks directly | Function calls |
| How we get audio | **AudioSocket** (dead-simple TCP, 8 kHz signed-linear) or `externalMedia` (RTP) | WSS, 8 kHz µ-law base64 | Track subscription, no SIP knowledge needed | Straight from disk |
| Real phone number | Needs a SIP trunk / DID from a provider (paperwork + cost) — **or skip it entirely** and use softphones | Included; **Thai DIDs need regulatory documents**, a US number works for testing | Needs the SIP bridge plus a trunk anyway | No |
| Cost | Free | **Per minute**, both legs | Free self-hosted / paid cloud | Free |
| Works offline | **Yes** | No — needs internet, their uptime, and a tunnel | Yes if self-hosted | Yes |
| Contact-centre realism | High — queues, transfers, DTMF, hold all behave like the real thing | Medium — you build queueing yourself, or learn TaskRouter | Low — it is not a PBX | None |
| Pain | Steepest curve: `pjsip.conf`/`extensions.conf` are arcane, NAT/ICE, WebRTC certificates, SIP debugging | Easy start, then tunnel flakiness and metered testing | Great for AI voice agents, weak on IVR/queue/DTMF semantics | Not a real call |
| Time to first working call | 2–5 days | Hours | 1–2 days | Minutes |

**The recommendation, and why:** build against **Simulated** from P0, then **Asterisk** at P5.

The decisive argument is the demo. A stage demo that depends on venue Wi-Fi, a tunnel, and a vendor's
uptime is a demo that can fail in front of judges. Asterisk runs entirely on the laptop — and here is
the trick that gets both realism and safety: **install a softphone (Zoiper / Linphone) on a real
mobile, point it at the laptop's Asterisk over local Wi-Fi, and the demo is a genuine VoIP call from a
genuine phone, with no internet involved.** That covers "the judges want to see a real call" without
the network risk.

Twilio stays worth adding *afterwards* as a second adapter if a publicly dialable number would land
well — the port makes it an evening's work, and the contract tests already exist. LiveKit becomes
interesting only when `ConversationalAgentIntake` gets built, since its agents framework is built for
exactly that.

---

## 2. Speech-to-Text (Thai)

```python
class SttEngine(Protocol):
    async def transcribe_utterance(
        self, pcm: FloatArray, sr: int, *, hint: SttHint | None = None
    ) -> SttResult: ...
    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttPartial]: ...
    @property
    def info(self) -> EngineInfo: ...  # name, version, device, expected latency
```

| Adapter | Model / stack | Notes |
|---|---|---|
| **`ThonburianHfAdapter`** ⭐ default | `biodatlab/whisper-th-medium-combined` via `transformers` + `torch` | WER 7.42 on Common Voice 13; the balanced size. Same family the team already ran successfully in the scam project. |
| **`ThonburianFasterWhisperAdapter`** | the same checkpoint converted to CTranslate2 (`faster-whisper`) | The latency play — several× faster, much lower VRAM. **Likely required on the target hardware** (§2.1). Conversion must be verified. |
| `ThonburianDistillAdapter` | `biodatlab/distill-whisper-th-medium` / `-large-v3` | Lighter fallback |
| **`TyphoonAsrAdapter`** | Typhoon's Thai ASR, `mode = api \| local` | First-class alternative to benchmark head-to-head against Thonburian in P3 (`D30`). Model names, licence and pricing to be **re-verified at implementation time**, not trusted from memory. |
| `CloudSttAdapter` | Google STT / Azure Speech / Gemini | Backup when there's no GPU; a data-residency question in production |
| `ScriptedSttAdapter` | Replays known transcript turns with realistic timings | Tests, scenario runner, and a stage-safe demo |

### 2.1 The hardware reality (RTX 3050 laptop)

The known dev/demo machine is an **RTX 3050 laptop (4–6 GB VRAM)** — the same one that ran Thonburian
medium for the scam project, so it works, but the streaming latency budget is tight:

- **Whisper pads every chunk to 30 s.** A 3-second utterance costs roughly what a 30-second one does
  under the plain HF pipeline. This is the single biggest reason `faster-whisper`/CTranslate2 matters
  here — it handles short segments far better and `int8_float16` cuts VRAM roughly in half.
- **Do not run a local LLM and Whisper on the same 4–6 GB card.** They will not both fit with room to
  work. The default split is **STT local on the GPU, LLM via API**. If a fully local stack is wanted,
  it needs a bigger card or a second machine.
- **Benchmark, don't guess.** P3 records real numbers (WER, p95 utterance latency, VRAM) for
  Thonburian-HF vs Thonburian-CT2 vs distilled vs Typhoon ASR on this exact laptop, and writes them
  into `PROJECT_STATE.md`. The engine choice follows the table, not the reputation.
- Whoever on the team has the strongest GPU should own the demo machine.

Model menu (from the upstream repo, WER on Common Voice 13):
`whisper-th-small-combined` 11.0 · **`whisper-th-medium-combined` 7.42** · `whisper-th-large-combined` 7.69 ·
`whisper-th-large-v3-combined` 6.59 · `distill-whisper-th-small` 11.2 · `distill-whisper-th-medium` 7.6 ·
`distill-whisper-th-large-v3` 6.82.

**We re-implement, we do not copy.** The reference project's `main.py` is a *batch, file-in/file-out*
CLI: convert → VAD the whole file → chunk to disk → HF pipeline over a `Dataset` → CSV/SRT. ReadyCall
needs the opposite shape — **streaming, in-memory, incremental** — so:

| Reference behaviour | ReadyCall behaviour | Why |
|---|---|---|
| Whole file VAD'd up front | Rolling buffer, VAD **endpointing** as audio arrives | Text must exist *while* the customer is still on hold |
| Chunks written to a temp dir | In-memory frames | Latency + no PII on local disk |
| `torch.hub.load("snakers4/silero-vad")` at runtime | `silero-vad` pip package / bundled ONNX | A network fetch at call time is unacceptable |
| Model loaded per invocation | Model loaded once in a long-lived, GPU-pinned worker | ~5–20 s load time can't sit in the call path |
| Syllable re-segmentation for subtitles | Turn-level segments with confidence | We need conversational turns, not subtitle lines |
| CSV/SRT output | `TranscriptTurn` events on the bus + DB rows | Everything downstream is event-driven |

Worth keeping from the reference: the VAD parameters (threshold `0.65`, `min_speech_duration_ms=500`,
`min_silence_duration_ms=100`) and the **padding trick** (~120 ms before / 60 ms after each segment) —
that padding measurably helps Whisper not clip the first syllable. Also keep a repetition guard
(their `postprocess_text` capped runaway repeated tokens — Whisper does that on silence/noise).

Supporting libs: `silero-vad` (ONNX via `onnxruntime`) or `webrtcvad` as a light fallback,
`numpy`, `soundfile`, `librosa`, `pydub` + system `ffmpeg`, `torchaudio`.
Optional later: `pyannote.audio` for diarisation once the *live call* (two speakers) is transcribed.

---

## 3. LLM

```python
class LlmClient(Protocol):
    async def complete_structured(
        self, prompt: PromptRef, vars: dict, schema: type[BaseModel], *, timeout_s: float
    ) -> LlmResult[BaseModel]: ...
    async def stream_text(self, prompt: PromptRef, vars: dict) -> AsyncIterator[str]: ...
```

**Two adapters are implemented from day one; the rest are defined but left as stubs** (`D29`).

| Adapter | Status | Covers |
|---|---|---|
| **`AnthropicAdapter`** ⭐ | **implemented P4** | `claude-opus-5` (quality) / `claude-sonnet-5` (latency+cost). Strong Thai; structured output via tool-use; prompt caching for the fixed system prompt |
| **`OpenAiCompatibleAdapter`** ⭐ | **implemented P4** | One adapter, parameterised by `base_url` + key — **covers Typhoon's hosted API, OpenAI, self-hosted vLLM, Ollama and LM Studio at once**, because they all speak the OpenAI wire format |
| `GeminiAdapter` | defined only | Different wire format; add if wanted |
| `RuleBasedAdapter` | implemented P0 | No model at all — the degradation rung: keyword/regex intent + template summary |

So "run Typhoon" is a config choice, twice over:

```ini
# Typhoon hosted API
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.opentyphoon.ai/v1
LLM_MODEL=<typhoon model id>

# Typhoon self-hosted (vLLM or Ollama on your own box)
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=<local model id>
```

**Provider comparison harness** (`scripts/compare_llm.py`): runs the golden set through every
configured provider and prints intent accuracy, entity F1, summary faithfulness, p50/p95 latency and
cost per call side by side, with sample outputs. Plus a runtime switch so a live demo can flip
providers mid-session. This turns "Claude or Typhoon?" from an argument into a table — and the table
itself is good competition material.

> Model ids, context windows, licences and pricing for Typhoon (and anything else) must be
> **re-verified against current documentation when the adapter is written**. Do not hardcode from
> memory or from this file.

**Tasks and their contracts** (each has a versioned prompt + a Pydantic output schema + a golden set):

| Task | Output |
|---|---|
| `intent_classify` | `{intent_code, label_th, label_en, confidence, alternatives[]}` from a **closed taxonomy** |
| `entity_extract` | `{hospital, admission_date, policy_no, claim_id, amounts[], dates[], people[]}` |
| `summarize_intake` | 2–3 Thai sentences, factual, no invention |
| `build_case_brief` | the whole structured brief |
| `next_best_action` | one action + 3–5 ordered recommended steps, chosen from a **playbook**, not freehand |
| `suggested_opening` | one Thai sentence, polite register, uses คุณ + name, mentions the known context |
| `wrapup_summary` | disposition + summary + follow-up tasks (draft only) |
| `pii_detect` | spans to mask |

Non-negotiables:
- **Closed taxonomies.** Intent codes, skill codes and NBAs come from a fixed list; the model
  *selects*, it doesn't invent. Anything off-list → `unknown` → the low-confidence UI.
- **No policy numbers, coverage amounts or eligibility from the model.** Those are read from the
  data. The model may only *reference* them. This is the single biggest hallucination risk in an
  insurance context, and it maps straight onto the brief's "out of scope" list (no underwriting, no
  pricing, no legal advice).
- **Timeouts + budget caps** per call; on breach, degrade (`ARCHITECTURE.md` §12).
- **Prompts are versioned files** in `prompts/`, referenced by id, with `prompt_versions` rows and an
  eval score. Never inline a prompt string in a service.
- Log token counts + cost per call so the "is this economically sane at scale" question has an answer.

---

### 3.1 Why no LLM framework — the full comparison (`D31`)

This is a real trade-off, not a preference, so here is the honest version.

**What our AI stage actually is:** four to six *independent*, short, structured calls
(classify → extract → summarise → next-best-action → opening), each with its own Pydantic schema, its
own timeout, and its own degradation path. Several run in parallel. There is no chain, no retrieval,
no dynamic tool loop. In plain asyncio that is roughly 150 lines.

| Option | What it gives you | What it costs you | Verdict here |
|---|---|---|---|
| **Raw SDK + our ports** ⭐ | Exact control of the request, response, tokens, cost and timing — which we must persist per call (`D18`). Tiny dependency surface. Trivial to trace and to explain to a judge. | You write retry, timeout, fallback and tracing yourself (~150 lines, and it is exactly the code whose behaviour we need to defend). | **Chosen** |
| **LangChain** | Provider abstraction, prompt templates, output parsers, a huge integration catalogue, LangSmith tracing. | Big, fast-moving transitive dependency tree — a breaking upgrade mid-competition is a lost day. Abstractions sit between you and the exact prompt/token/latency data we are required to store; you get it back through callbacks, i.e. by fighting the framework. Extra layers on a ≤3 s budget. Most of the catalogue (vector stores, loaders, retrievers) we simply do not use. Our ports already do the one thing we would want from it (provider swapping) and do it better, because they also cover STT, TTS and telephony. | Rejected |
| **LangGraph** | Genuinely good at *dynamic, branching, stateful multi-turn* agent flows with checkpointing. | Solves a problem our fixed pipeline does not have. Same dependency weight. | Rejected **now** — revisit for `ConversationalAgentIntake`, where the control flow really is dynamic. That is a legitimate future fit. |
| **LlamaIndex** | Excellent document ingestion + retrieval. | We have no corpus in v1 — our "knowledge" is structured rows behind `CoreDataProvider`. | Rejected now — **revisit if** we build Q&A over policy-wording documents, and then only for the ingestion/retrieval half, behind our own port. |
| **`instructor` / `pydantic-ai`** | Small and focused: schema-validated outputs with automatic re-ask on validation failure. Does not take over your architecture. | One more dependency; Anthropic tool-use + a Pydantic model already gets ~90% of this in ~30 lines. | **Not at first.** Adopt inside the adapter if validation retries get tedious. This is the sanctioned middle ground. |
| **LiteLLM** | One call signature across ~100 providers, plus cost tracking. | Our `OpenAiCompatibleAdapter` already covers OpenAI + Typhoon + vLLM + Ollama + LM Studio; `AnthropicAdapter` covers the rest of what we care about. | Not needed — reconsider only if the provider list grows a lot. |
| **DSPy** | Programmatic prompt optimisation against a metric. | Real learning curve; needs a solid golden set first (we will have one — this could become interesting *later*, for tuning the intent classifier). | Not now; genuinely interesting for P4+ tuning. |
| **Semantic Kernel / Haystack** | Enterprise-ish orchestration / NLP pipelines. | Same objections as LangChain, smaller ecosystems, no advantage for our shape. | Rejected |

**The general principle:** frameworks earn their keep when they absorb *variety* — many providers, many
document types, many dynamic flows. They cost you when your requirement is *precision* — exactly this
prompt, exactly this timeout, exactly this recorded cost, degrading exactly this way. Our AI layer is a
precision problem sitting inside a 3-second budget with an auditability requirement, so the plumbing
stays ours.

**Two named triggers to revisit**, each requiring a new decision entry: dynamic conversational control
flow (→ LangGraph), and policy-document retrieval (→ LlamaIndex). Observability, if we ever want more
than the `analyses` table, would be **Langfuse** (self-hostable) rather than a framework rewrite.

---

## 4. TTS — pre-rendered prompts now, streaming later

```python
class TtsEngine(Protocol):
    async def synthesize(self, text: str, voice: VoiceSpec) -> AudioRef: ...
    async def stream(
        self, text_chunks: AsyncIterator[str], voice: VoiceSpec
    ) -> AsyncIterator[AudioFrame]: ...
```

**Every spoken line in the IVR is generated by TTS at build time, not recorded by a human and not
synthesised during the call** (`D24`). **Steps 1-3 and 5-6 are built as of P3**; the missing piece
is a real voice — `TTS_ENGINE` defaults to `null`, which records what it was asked to say and
synthesises nothing, so the pack today is a manifest rather than audio. Choosing the engine is a
config change and a re-run.

1. `config/voice_prompts.yaml` maps a prompt id → Thai text + voice + variant.
2. `scripts/build_prompts.py` renders each to a WAV in object storage, keyed by
   `hash(text, voice, engine)`; unchanged prompts are skipped.
3. The call plays the cached file — **zero call-time latency, deterministic, works with no internet**,
   which is what makes a stage demo safe.
4. Editing wording is a YAML change plus a re-render; an admin **prompt studio** page lets someone
   change a sentence and hear it seconds later — ideal for tuning on the day.
5. **Dynamic sentences** (queue position, wait estimate, the caller's name) are rendered on first use
   and cached by their rendered text, so they warm up within minutes of a new deployment. A prompt
   may also declare `warm:` values so the *first* caller does not pay for it either.
   **A menu is a special case and is always composed** (`D80`): personalised ordering means the
   order differs per caller, so no single baked clip can exist. Lead-in + one option line each +
   the reserved-key hint, with the labels read from `menus.yaml` so they exist in one file only.
   Dedupe is by rendered text, which is why 32 prompts and 32 menu options come to **63 clips**.
6. A checked-in **prompt pack** is the offline fallback if the TTS provider is unreachable at build time.

Streaming synthesis is only needed for `GuidedPromptIntake` and `ConversationalAgentIntake` — the same
port serves both, so the future does not need a redesign (`D10`).

Candidate engines: Azure Speech (Thai neural, good streaming), Google Cloud TTS, Botnoi Voice (Thai
vendor), ElevenLabs (quality, cost). Choose on a listening test of the actual prompts, not on specs.

---

## 5. Event bus, storage, and the rest

| Port | Default adapter | Alternatives |
|---|---|---|
| `EventBus` | **Redis Streams** (consumer groups, replay, at-least-once) | `KafkaAdapter` for real scale; `InMemoryBus` for tests |
| `BlobStorage` | **MinIO** (S3 API) in dev | `S3Adapter`, `LocalFsAdapter` (dev only, never for real recordings) |
| `AgentDirectory` | Our `agents` tables | `LdapAdapter` / bank HR feed |
| `Notifier` | WebSocket push to agent desktops | Email/LINE/webhook for the "desktop offline" degradation rung |
| `MetricsSink` | Prometheus | OTLP |

---

## 6. Full library / tooling list

**Runtime (Python 3.11, managed with `uv`)**
`fastapi`, `uvicorn[standard]` (**installed at P1b**, via the `web` extra), `pydantic` v2,
`pydantic-settings`, `sqlalchemy` 2.0, `alembic`,
`asyncpg` + `psycopg[binary]`, `redis`, `httpx`, `websockets`, `aiohttp`, `structlog`,
`opentelemetry-sdk` + instrumentation, `tenacity` (retries), `orjson`, `python-multipart`,
`passlib`/`pyjwt` (agent auth), `apscheduler` (retention/rollup jobs).

**Audio / ML**
`torch`, `torchaudio`, `transformers`, `accelerate`, `faster-whisper` (+`ctranslate2`), `onnxruntime`,
`silero-vad`, `numpy`, `soundfile`, `librosa`, `pydub`; system **`ffmpeg`**.

**AI clients**
`anthropic`, plus thin HTTP adapters for Typhoon/Gemini/Ollama (no framework in between —
LangChain/LlamaIndex are deliberately *not* used; see `D11`).

**Frontend**
Agent desktop: React 18 + TypeScript + Vite, TanStack Query, native WebSocket, Tailwind (or plain CSS
modules), `recharts` only if a chart is actually needed.
Customer side: a responsive **web customer simulator** (§8); React Native/Expo only if a real
installable app is wanted later.

**Infra / dev**
Docker + Docker Compose, Postgres 16, Redis 7, MinIO, Asterisk 20, **pgweb** (DB browser, §9),
Prometheus + Grafana + Loki, GitHub Actions, `ruff`, `mypy`, `pytest` + `pytest-asyncio` +
`testcontainers`, `hypothesis` (mapping edge cases), `locust`/`k6` (queue load),
`scipy`/`munkres` (the Hungarian solver in the matcher — or ~60 lines of our own).

---

## 8. Where the system physically lives (the two front-ends)

### Agent workstation — **React + Vite, and the softphone lives in it** (`D32`)

**Chosen: React 18 + TypeScript + Vite.** This is not just an info screen — it is the agent's whole job
surface, and **the call itself happens in the tab**. That raises the bar past what server-rendered
templates comfortably handle: ten live panels, a WebSocket feed, a live-updating transcript, an
animating brief, *and* a WebRTC session with call controls, all in one page.

**The in-page softphone stack:**

| Piece | Choice | Notes |
|---|---|---|
| SIP signalling | **SIP.js** (or JsSIP) over **WSS** to Asterisk `chan_pjsip` | The page registers as a real SIP endpoint; Asterisk bridges to it exactly as to a hardware phone |
| Media | WebRTC, **Opus** | Browser-native echo cancellation, noise suppression, auto gain |
| Controls | accept / decline / mute / hold / hangup / DTMF / transfer / conference | Driven from the page; server-side state stays authoritative in the Orchestrator |
| Devices | `enumerateDevices` picker + level meter + **pre-shift audio self-test** | "My headset wasn't selected" is the classic five-minutes-before-demo failure |
| Resilience | Audio session and data session are independent | A UI reload does **not** drop a live call; the workstation re-attaches on reconnect |

**Landmines to plan for now, not discover at P5:**
- Microphone access requires a **secure context**. `localhost` is fine for one machine; agents on other
  machines on the LAN need real certificates (`mkcert` in dev).
- SIP-over-WSS needs a cert Asterisk serves — this is most of the "WebRTC certs are fiddly" pain in
  §1.1, and it lands on the agent side, not the customer side.
- Autoplay policy: the ringtone needs a prior user gesture, so the workstation has an explicit
  "go on shift" action that unlocks audio.

*(Alternatives considered and rejected: Jinja + htmx — viable for a display-only screen, awkward once
the page is also a softphone with a dozen live panels; Next.js — SSR buys nothing here;
Streamlit/Gradio — looks like an internal tool, wrong for the screen judges stare at.)*

### Customer side — a **responsive web app that fakes the Krungsri app**

| Option | For | Against |
|---|---|---|
| **Web customer simulator** ⭐ | Opens on a real phone's browser during the demo and looks like an app; zero install; instant iteration; can hold a WebRTC call itself | Not literally an app |
| React Native / Expo | A real installable app; Expo Go makes it plausible | Build/signing overhead; another toolchain for a thing the real system replaces anyway |
| Flutter | Same as above | Adds a whole language (Dart) for no gain |

**Recommendation: web simulator.** The crucial design rule is that it talks to the **same public
`/v1/…` API the real Krungsri app would** — so "replace the demo with the real app" means Krungsri's
app calls those endpoints, and nothing server-side changes. The simulator includes a **demo login /
persona picker** (choose which mock customer you are) which is explicitly a demo affordance, not part
of the real system.

---

## 9. Inspecting the databases

- **`pgweb`** as a compose service ⭐ — single Go binary, clean web UI, browse/query both schemas.
  (`adminer` is an equally fine one-container alternative; **DBeaver** on the desktop for heavier work.)
- **Drizzle Studio** — what the team used previously, and it does support Postgres, but it needs a
  Drizzle schema definition that would duplicate the SQLAlchemy models. Not worth the drift.
- **The Call Explorer** (our own admin page, and the one that actually matters): pick a
  `call_session_id` and see the whole story — state timeline with timings, identity resolution and
  assurance level, context snapshot with per-field provenance, every transcript turn, every brief
  version side by side, the full matching decision with candidate scores, and the wrap-up.
  Raw table browsing answers "what is in the DB"; this answers "why did the system do that", which is
  the question actually worth asking. It doubles as demo material — it is the direct descendant of the
  multi-song debug screen in the team's previous project, which is the pattern that made that project
  debuggable.

---

## 7. Configuration surface (`.env` / `pydantic-settings`)

Everything selectable, nothing hardcoded:

```ini
# --- adapter selection ---
TELEPHONY_PROVIDER=simulated          # asterisk | twilio | livekit | simulated
STT_ENGINE=thonburian_hf              # thonburian_hf | thonburian_ct2 | distill | typhoon | cloud | scripted
STT_MODEL=biodatlab/whisper-th-medium-combined
STT_DEVICE=auto                       # cuda | cpu | auto
STT_COMPUTE_TYPE=int8_float16         # ct2 only
LLM_PROVIDER=anthropic                # anthropic | openai_compatible | gemini | rulebased
LLM_MODEL=claude-sonnet-5
LLM_BASE_URL=                         # set for openai_compatible (Typhoon API, OpenAI, vLLM, Ollama)
TTS_ENGINE=azure                      # used at BUILD time to render prompts, not during calls
CORE_DATA_PROVIDER=mock_postgres      # mock_postgres | fixtures | http_api | sql_passthrough | null
CORE_MAPPING_FILE=config/core_mapping.yaml
INTAKE_STRATEGY=passive               # passive | guided | conversational
EVENT_BUS=redis                       # redis | kafka | memory
BLOB_STORAGE=minio                    # minio | s3 | localfs

# --- intake / IVR ---
INTAKE_MAX_DURATION_S=180
INTAKE_SILENCE_TIMEOUT_S=6
INTAKE_REOFFER_AFTER_S=90             # re-offer once to a caller who declined
IVR_BARGE_IN=true
VOICE_PROMPTS_FILE=config/voice_prompts.yaml
DIDS_FILE=config/dids.yaml

# --- analysis ---
ANALYSIS_DEBOUNCE_S=5
CONFIDENCE_FLOOR=0.55                 # below this -> "intent unclear", no % shown
LLM_TIMEOUT_S=8
BRIEF_DEADLINE_MS=1000                # match -> brief on screen
LIVE_CALL_TRANSCRIPTION=true
LIVE_CALL_STT_ENGINE=thonburian_ct2   # latency matters less here; a lighter model is fine

# --- matching ---
MATCHING_WEIGHTS=config/matching_weights.yaml
MATCHER_TICK_MS=1000
MATCHER_SOLVER=hungarian              # hungarian | greedy | fifo
TARGET_WAIT_S=45
MAX_WAIT_BEFORE_ANY_AGENT_S=180       # past this, fit is ignored entirely
DEFER_ENABLED=true
DEFER_MAX_WAIT_S=60                   # never defer a caller who has waited longer than this
DEFER_MAX_HOLD_S=25                   # never defer for a longer predicted wait than this
DEFER_MIN_FIT_GAP=0.25

# --- identity ---
IDENTITY_PENDING_INTENT_WINDOW_S=900  # ANI + recent intent -> assurance L2
REQUIRE_L2_FOR_POLICY_DETAILS=true

# --- agent workstation (D32, D33) ---
AGENT_ACCEPT_MODE=manual              # manual | auto  (per-agent/queue override in DB)
OFFER_TIMEOUT_S=20                    # decline/timeout -> re-match + flip agent out of READY
ACW_TIMER_S=45                        # after-call work; 0 = straight back to available
ACW_MAX_S=300
AGENT_HEARTBEAT_S=10
AGENT_PRESENCE_TTL_S=30               # closed tab drops out automatically
SIP_WSS_URL=wss://asterisk.local:8089/ws
SIP_REALM=readycall.local

# --- retention ---
RECORDING_RETENTION_DAYS=90
TRANSCRIPT_RETENTION_DAYS=365
```

Secrets (`ANTHROPIC_API_KEY`, DB URLs, Asterisk/ARI credentials, S3 keys) live in `.env` locally and a
vault in production. **Never committed** — `.env.example` documents the keys with dummy values.

---

## 8. Credits (carry these into any submission)

- **Thonburian Whisper** — Looloo Technology & Biomedical and Data Lab, Mahidol University.
  <https://github.com/biodatlab/thonburian-whisper> (ICNLSP 2024).
- **Whisper** — OpenAI. **Silero VAD** — snakers4/silero-vad.
- Competition context: Krungsri Universe × KMITL Hackathon briefing (`KS_Hackathon_Briefing…pdf`).
