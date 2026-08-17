# INTEGRATIONS

_Every external thing the system touches: the port that hides it, the adapters behind it, and the config that selects one._
_Status: **design only**. Last updated: 2026-08-17._

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
    async def start_media_fork(self, call: TelephonyCallId, sink: MediaSinkSpec) -> MediaStreamId: ...
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
pending-intent window + IVR code (see `ARCHITECTURE.md` §4.5).

**Audio fork format.** AudioSocket delivers 8 kHz signed-linear mono over TCP; Twilio delivers 8 kHz
µ-law base64 over WSS. The Media Gateway normalises everything to **16 kHz mono float32** before
anything else sees it — every downstream component assumes exactly that.

Libraries: `aiohttp`/`websockets` (ARI + media WS), `aiortc` (pure-Python WebRTC if needed),
`pjsua2`/`pysip` only if a softphone is required for testing. Prefer speaking the protocols directly
over a heavyweight wrapper — fewer surprises, easier to debug.

---

## 2. Speech-to-Text (Thai)

```python
class SttEngine(Protocol):
    async def transcribe_utterance(self, pcm: FloatArray, sr: int, *, hint: SttHint | None = None) -> SttResult: ...
    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttPartial]: ...
    @property
    def info(self) -> EngineInfo: ...   # name, version, device, expected latency
```

| Adapter | Model / stack | Notes |
|---|---|---|
| **`ThonburianHfAdapter`** ⭐ default | `biodatlab/whisper-th-medium-combined` via `transformers` + `torch` | WER 7.42 on Common Voice 13; the balanced size. Same family the user already ran successfully in the scam project. |
| `ThonburianFasterWhisperAdapter` | the same checkpoint converted to CTranslate2 (`faster-whisper`) | The latency play — typically several× faster, lower VRAM. **Conversion must be verified before relying on it.** |
| `ThonburianDistillAdapter` | `biodatlab/distill-whisper-th-medium` / `-large-v3` | CPU-only / laptop-demo fallback |
| `CloudSttAdapter` | Google STT / Azure Speech / Gemini / Typhoon ASR | Backup when there's no GPU; a bank-data-residency question in production |
| `ScriptedSttAdapter` | Replays known transcript turns with realistic timings | Tests, scenario runner, and a safe stage demo |

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
    async def complete_structured(self, prompt: PromptRef, vars: dict,
                                  schema: type[BaseModel], *,
                                  timeout_s: float) -> LlmResult[BaseModel]: ...
    async def stream_text(self, prompt: PromptRef, vars: dict) -> AsyncIterator[str]: ...
```

| Adapter | Model | Notes |
|---|---|---|
| **`AnthropicAdapter`** ⭐ default for build/demo | `claude-opus-5` (quality) / `claude-sonnet-5` (latency+cost) | Strong Thai; structured output via tool-use/JSON schema; prompt caching for the fixed system prompt |
| `TyphoonAdapter` | SCB10X Typhoon | Thai-native; the credible "can run inside the bank" answer |
| `GeminiAdapter` | Gemini | Alternative; also a possible audio-native path |
| `OllamaLocalAdapter` | Typhoon / Qwen locally | Offline demo insurance, data-residency story |
| `RuleBasedAdapter` | No model at all | The degradation rung: keyword/regex intent + template summary |

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

## 4. TTS (needed for the future conversational intake)

```python
class TtsEngine(Protocol):
    async def synthesize(self, text: str, voice: VoiceSpec) -> AudioRef: ...
    async def stream(self, text_chunks: AsyncIterator[str], voice: VoiceSpec) -> AsyncIterator[AudioFrame]: ...
```

Candidates: Azure Speech (Thai neural, good streaming), Google Cloud TTS, Botnoi Voice (Thai vendor),
ElevenLabs (quality, cost). Only `PrerecordedPromptAdapter` (fixed WAV prompts) is needed for v1 —
the port exists now so the conversational strategy is a drop-in later, not a redesign. (`D10`)

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
`fastapi`, `uvicorn[standard]`, `pydantic` v2, `pydantic-settings`, `sqlalchemy` 2.0, `alembic`,
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
Mobile: React Native (or Flutter) for the real app + a **web "customer simulator"** page for dev and demo.

**Infra / dev**
Docker + Docker Compose, Postgres 16, Redis 7, MinIO, Asterisk 20, Prometheus + Grafana + Loki,
GitHub Actions, `ruff`, `mypy`, `pytest` + `pytest-asyncio` + `testcontainers`, `hypothesis` (mapping
edge cases), `locust`/`k6` (queue load).

---

## 7. Configuration surface (`.env` / `pydantic-settings`)

Everything selectable, nothing hardcoded:

```ini
# --- adapter selection ---
TELEPHONY_PROVIDER=simulated          # asterisk | twilio | livekit | simulated
STT_ENGINE=thonburian_hf              # thonburian_hf | faster_whisper | distill | cloud | scripted
STT_MODEL=biodatlab/whisper-th-medium-combined
STT_DEVICE=auto                       # cuda | cpu | auto
LLM_PROVIDER=anthropic                # anthropic | typhoon | gemini | ollama | rulebased
LLM_MODEL=claude-sonnet-5
CORE_DATA_PROVIDER=mock_postgres      # mock_postgres | fixtures | http_api | sql_passthrough | null
CORE_MAPPING_FILE=config/core_mapping.yaml
INTAKE_STRATEGY=passive               # passive | guided | conversational
EVENT_BUS=redis                       # redis | kafka | memory
BLOB_STORAGE=minio                    # minio | s3 | localfs

# --- behaviour ---
INTAKE_MAX_DURATION_S=180
INTAKE_SILENCE_TIMEOUT_S=6
ANALYSIS_DEBOUNCE_S=5
CONFIDENCE_FLOOR=0.55                 # below this → "intent unclear", no % shown
LLM_TIMEOUT_S=8
BRIEF_DEADLINE_MS=1000                # queue pop → brief on screen
ROUTING_WEIGHTS=config/routing_weights.yaml
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
