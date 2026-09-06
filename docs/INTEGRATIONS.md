# INTEGRATIONS

_Every external thing the system touches: the port that hides it, the adapters behind it, and the config that selects one._
_Status: **mostly design; the persistence stack, the whole audio path, the transcript's route to the agent's screen and the encrypted recording are real.** Last updated: 2026-09-06._

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

> **Built as of P3 step 4b (2026-09-02).** The table below now says what exists rather than
> what was planned, and the names differ from the original sketch — the port method is
> `transcribe_utterance(frames, hint=...)` taking `AudioFrame`s rather than a raw array, and
> the classes are `…Engine` not `…Adapter`. Three are real, one is a fake, and the rest are
> still only names.

| Adapter | Model / stack | Status |
|---|---|---|
| **`TyphoonAsrEngine`** ⭐⭐ **THE ENGINE** (`D104`) | `scb10x/typhoon-asr-realtime` — a NeMo **FastConformer transducer**, not a Whisper | **BUILT AND CHOSEN.** The only engine that meets `ARCHITECTURE` §15: **p95 0.19 s median / 0.28 s worst, 20 of 20 calls inside the 1.5 s budget**, `busy` worst 0.020, VRAM 1068 MB. **No 30 s window**, which is the structural reason (`D99`, predicted before measuring). Returns **empty in 149 ms** on silence where Whisper spends 8578 ms inventing Thai (`B14`). ⚠️ CER mean **0.133** vs fp16's 0.109; **cannot use `hint.vocabulary` at all** (a transducer has no prompt); measurably weaker on spoken digits. Needs the **`asr`** extra. |
| **`FasterWhisperEngine` (CT2)** ⭐ **THE FALLBACK** (`D103`) | CTranslate2 `int8_float16` over a locally converted Thonburian | **BUILT.** For a box where NeMo will not install: needs only `ml`. p95 1.68 s / 2.53 s, 7 of 20 inside budget, CER mean 0.128 **with the hint** and 0.171 without — the hint is load-bearing here, and it also steadies the decoder (`busy` worst 0.46 → 0.11). ⚠️ **Thonburian publishes no CT2 build** (`B17`); `scripts/convert_ct2.py` converts it once. `STT_MODEL` must stay unset (`B23`). |
| **`ThonburianHfEngine`** the original | `biodatlab/whisper-th-medium-combined` via `transformers` + `torch` | **BUILT, and too slow to ship**: p95 **19.5 s median / 58.7 s worst**, 0 of 12 calls inside budget, `busy` worst 1.25 — above 1.00 the transcriber never catches up. It is the **most accurate** engine measured (CER mean 0.109 unhinted) and that did not save it. `B19` is fixed: the hint is applied via `prompt_ids`, and it makes this engine slightly *worse* (+0.010) while helping int8 a lot. |

| **`TyphoonAsrEngine`** | `scb10x/typhoon-asr-realtime` — **NVIDIA NeMo FastConformer transducer**, `cc-by-4.0` | **BUILT, not installable yet.** Not a Whisper model (`D99`): no 30 s padding, genuinely streaming, and no free-running decoder to hallucinate with — which is exactly why it is worth measuring against `B14`. Needs `nemo_toolkit[asr]`, deliberately **not** in the `ml` extra. |
| **`ScriptedSttEngine`** | Replays known turns with realistic timings | **BUILT.** Every test, all three scenarios, and the stage-safe demo path. The default (`STT_ENGINE=scripted`). Its lines come from `config/demo_transcript.yaml` since `D107` — it was built with an **empty list** until then, so choosing the safe engine produced a blank transcript panel. It is also the only adapter that implements `ReplayableSttEngine`. |
| `ThonburianDistillEngine` | `biodatlab/distill-whisper-th-large-v3` | **MEASURED AND REJECTED** (`D104`). It was already in the HF cache so it cost nothing to try, and it is worse than CT2 on every axis: CER 0.096 vs 0.087 median, `busy` worst 0.23 vs 0.11, VRAM 1942 vs ~1000 MB. A distilled *large* is still a large. Recorded so nobody spends the download again. |
| `CloudSttEngine` | Google / Azure / Gemini | Named only. Backup with no GPU; a data-residency question in production. |

### 2.0.0 Two capability protocols on `SttEngine`

Neither is a requirement, and both are `runtime_checkable` Protocols checked with
`isinstance` rather than methods on the port — because exactly one adapter implements each,
and putting them on `SttEngine` would oblige every other adapter to grow a meaningless
version.

| Capability | Implemented by | Why it exists |
|---|---|---|
| **`BatchSttEngine`** (`D101`) | `ThonburianHfEngine` | Several utterances in one GPU pass. Built, measured as a **no-op on this GPU**, and kept: it is correct, costs 1 MB, changes no output, and a machine with spare capacity may benefit. Irrelevant to Typhoon, which has no 30 s window to amortise |
| **`ReplayableSttEngine`** (`D107`) | `ScriptedSttEngine` | `reset()`, called by `TranscriptionService.open()`. **One engine instance per process is right for a model and wrong for a script**: a cursor through canned lines meant the first demo call consumed all of them and the second showed an empty panel. With two recordings genuinely overlapping the cursor is shared and the second resets the first — a demo artefact, accepted, because the alternative is loading a model per call |

### 2.0 Voice activity — the ninth port (`D96`)

Endpointing was originally folded into "the STT stack". It is its own port, because it is a
separate vendor model with its own swap and its own bake-off:

```python
class VoiceActivityDetector(Protocol):
    @property
    def frame_samples(self) -> int: ...          # Silero v5 wants EXACTLY 512
    def speech_probability(self, samples) -> float: ...   # a probability, not a verdict
    def reset(self) -> None: ...                 # per call — state leaks the first word
```

| Adapter | Stack | Status |
|---|---|---|
| **`SileroVad`** | `silero-vad` package, CPU | **BUILT.** The production detector (`D9`). Loaded from the **installed package**, never `torch.hub` — a network fetch during a live call is unacceptable. CPU on purpose (`D95`): ~1 MB model, and the GPU has 3.2 GiB for Whisper. |
| **`EnergyVad`** | RMS against an adaptive noise floor. No dependencies. | **BUILT, and load-bearing.** CI installs no extras, so without it the whole audio path would only ever run on one laptop (`B7`'s shape). Doubles as the degradation rung if Silero fails to load. |

**Where the utterance boundary is decided** is `services/transcription/endpointer.py` — a
machine with **no model, no I/O and no clock**, so `D9`'s inherited constants are assertable
against a list of floats. The adapters supply probabilities and nothing else.

### 2.0.1 Three guards between the model and the agent

Deliberately three *different kinds*, because a failure that dodges one rarely dodges all
three. Every one exists because of something measured, not anticipated:

| Guard | Asks | Came from |
|---|---|---|
| level gate (before dispatch) | is there any energy in this segment at all? | `B14` — 1 s of digital silence cost **8578 ms** and came back with invented Thai, against **155 ms** for real speech |
| `looks_like_a_loop` | is one chunk repeated until it buries the sentence? | `B16` — the first version split on whitespace and caught **0 of 3** real Thonburian loops, because **Thai has no spaces** |
| `echoes_the_prompt` | is this mostly our own vocabulary hint handed back? | `B14` — fed silence with the hint, the model returned three of `stt_vocabulary.yaml`'s terms in that file's own order |
| `implausible_speech_rate` | could a human have said this much in that long? | `D98`, the user's idea. **Measured** on 61 annotated segments of real Thai: median **7.6** chars/s, max **15.0**; the real loops sit at **39–53**. Ceiling: 25. |

The rate guard **detects but cannot prevent** — the seconds are already spent. The preventer
is a decode timeout, and it needs the killable worker process `D2` already plans;
`asyncio.wait_for` around `to_thread` does not kill a thread, so it is **not** faked.

### 2.1 The hardware reality (RTX 3050 laptop)

**Measured 2026-09-02, replacing the estimate.** The dev/demo machine is an **RTX 3050 Laptop
(sm_86)**, driver 581.08, torch `2.11.0+cu128`. The real figure is **4.00 GiB total and about
3.2 GiB free** — Windows holds the rest — which is tighter than the "4–6 GB" this section used
to say. Thonburian medium fp16 sits at **~2.8 GiB** and peaks near **3.8** with Silero alongside:
it fits, with very little room, and `large-v3` probably will not.

⚠️ **`torch` must come from the CUDA index, not PyPI** (`D95`). The PyPI wheel is CPU-only and
installing it fails **silently** — everything imports, everything runs, Whisper is ten times too
slow and `cuda.is_available()` is quietly `False`. `pyproject.toml` pins the index; check the
version string carries `+cu128`.

The streaming latency budget is tight:

- **Whisper pads every chunk to 30 s.** A 3-second utterance costs roughly what a 30-second one does
  under the plain HF pipeline. This is the single biggest reason `faster-whisper`/CTranslate2 matters
  here — it handles short segments far better and `int8_float16` cuts VRAM roughly in half.
- **Do not run a local LLM and Whisper on the same 4–6 GB card.** They will not both fit with room to
  work. The default split is **STT local on the GPU, LLM via API**. If a fully local stack is wanted,
  it needs a bigger card or a second machine.
- **Benchmark, don't guess** — and `scripts/bake_off.py` now does it. **First real numbers**
  (**Typhoon**, 20 real Thai call-centre calls on the balanced set, `D104`): **CER mean 0.133**, p95 **0.19 s**, VRAM 1068 MB. Earlier figures of 0.47–0.76 (`B20`) and 0.161 (a digit-heavy set plus `B21`) are **withdrawn**; rank on the **mean**, never the median (`D103`),
  throughput **rtf 0.12**, **2.8 GiB**. Speed and memory are comfortable; **accuracy is not, and
  is not yet explained** — four candidate reasons are listed in `NEXT_SESSION`, none eliminated.
  Do not read 0.6 CER as a verdict on the model; it is a verdict on this pipeline against this
  reference.
- ⚠️ **Rank on CER, never WER** (`B18`). Whitespace WER on unsegmented Thai compares one arbitrary
  segmentation against another and read **0.94–1.12** on a model that was working fine.
- ⚠️ **The detector is half of any accuracy number** — it decides what the model is even asked to
  transcribe. Real measurements pass `--vad silero`.
- Whoever on the team has the strongest GPU should own the demo machine.

Model menu (**the upstream repo's own numbers, on read speech — not comparable with ours on
telephone audio**), WER on Common Voice 13:
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

Worth keeping from the reference, and **all of it was kept**: the VAD parameters (threshold
`0.65`, `min_speech_duration_ms=500`, `min_silence_duration_ms=100`) and the **padding trick**
(~120 ms before / 60 ms after) now live in `EndpointSettings`, with a test each saying why. The
repetition guard was kept too — and **shipped broken** (`B16`): it split on whitespace, which is
useless for Thai. The reference project's own three loop outputs, supplied by the user, are now
its test data.

**What is actually installed** (`uv sync --extra ml`, ~3 GB): `torch` **from the CUDA 12.8
index**, `transformers`, `faster-whisper` (brings `ctranslate2` and the
`ct2-transformers-converter` CLI), `onnxruntime` (**CPU build on purpose** — Silero is 1 MB and
the GPU is scarce), `silero-vad`, `soundfile`. Nothing else in the system needs any of it.

**Deliberately NOT installed:** `nemo_toolkit[asr]` for Typhoon (`D99` — its own `asr` extra when
somebody runs it), `librosa`/`pydub`/`ffmpeg` (the media layer is **pure Python** so it works in
CI with no extras — see `media/audio.py`), and `pyannote.audio` (`D26`: legs are forked
separately, so speaker identity is structural and there is nothing to diarise).

⚠️ **Windows landmine:** CTranslate2 loads cuDNN by name and torch's copy in `torch/lib` is not on
the DLL search path, so faster-whisper fails with `Could not locate cudnn_ops64_9.dll` — which
reads like a broken CUDA install and is not. The adapter fixes the search path itself.

---

## 3. LLM

```python
class LlmClient(Protocol):
    async def complete_structured(
        self, prompt: PromptRef, vars: dict, schema: type[BaseModel], *, timeout_s: float
    ) -> LlmResult[BaseModel]: ...
    async def stream_text(self, prompt: PromptRef, vars: dict) -> AsyncIterator[str]: ...
```

**Two adapters are to be implemented on P4's first day; the rest are defined but left as stubs**
(`D29`). ⚠️ **Neither exists yet.** As of 2026-09-06 `src/readycall/adapters/llm/` contains
`rulebased.py` and nothing else, and P4 has not started — so the "status" column below is a
plan for every row but the last. `Settings.llm_provider` already accepts `anthropic` and
`openai_compatible` and there is **no factory behind either name**, which is `B23`'s shape:
a name that cannot select anything. Wiring `build_llm` is P4 step zero.

| Adapter | Status | Covers |
|---|---|---|
| **`AnthropicAdapter`** ⭐ | ☐ planned, P4 | `claude-opus-5` (quality) / `claude-sonnet-5` (latency+cost). Strong Thai; structured output via tool-use; prompt caching for the fixed system prompt |
| **`OpenAiCompatibleAdapter`** ⭐ | ☐ planned, P4 | One adapter, parameterised by `base_url` + key — **covers Typhoon's hosted API, OpenAI, self-hosted vLLM, Ollama and LM Studio at once**, because they all speak the OpenAI wire format |
| `GeminiAdapter` | ☐ defined only | Different wire format; add if wanted |
| `RuleBasedAdapter` | ☑ **built** (P0) | No model at all — the degradation rung: keyword/regex intent + template summary. Written, correct, and **instantiated nowhere** — see the warning above |

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
5. **Dynamic sentences** (menu option lines, the reserved-key hint and the apology that names it,
   and — when they land — the caller's name) are rendered on first use and cached by their
   **rendered text**, so they warm up within minutes of a new deployment. A prompt may also declare
   `warm:` values so the *first* caller does not pay for it either. *(A queue-position line was the
   original example here and is gone: `D91` removed it, because the matcher has no queue order to
   report.)*
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
| `EventBus` | **Redis Streams** (consumer groups, replay, at-least-once) | `KafkaAdapter` for real scale; `InMemoryBus` for tests **and for everything that runs today**. ⚠️ **`publish()` only enqueues; handlers run on `drain()`** (`D15`) — which is what makes a scenario replay byte-identical, and which means a live process needs something to *call* `drain()`. `api/app.py`'s `pump_once` does, every `BUS_DRAIN_INTERVAL_S` (0.05 s). Before `D105` nothing did, and a subscriber to anything but `intent.created` was correct, tested and unreached (`B24`) |
| `BlobStorage` ✅ | `memory` by default; **MinIO** (S3 API) in dev, real S3 in production | `LocalFsBlobStorage` (a directory) and `S3BlobStorage` (one adapter for both `minio` and `s3` — the difference is an endpoint URL). ⚠️ **Every backend is wrapped in `EncryptingBlobStorage`** by `build_blob_storage`, which is the only place a store should be constructed: AES-256-GCM envelope encryption, one implementation for all of them (`D110`). That is what makes `localfs` safe now, where the earlier note said "never for real recordings" — the property is enforced by the wrapper rather than by refusing the backend. `minio`/`s3` need the **`s3` extra** (`uv sync --extra s3`) and the factory refuses them with a useful message when boto3 is absent |
| `KeyRing` ✅ | `LocalKeyRing` — one master key from `RECORDING_MASTER_KEY` | **The tenth port** (`D110`). Two methods, the same two a KMS has: generate a wrapped data key, unwrap one. P7 replaces this with a vault adapter and nothing else changes. ⚠️ **Dev-grade**: the master sits in the process environment. With no key set it generates one per process and warns; a *durable* store with an ephemeral key is refused at startup, because ciphertext nobody can ever read is worse than an honest gap |
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

**Two extras, not one** (`D99`, `D104`): `ml` carries the Whisper stack above; **`asr` carries `nemo_toolkit[asr]`** and is what the shipped engine needs. They are separate because NeMo is a large install with a heavy transitive tree, and a box that only runs the CT2 fallback should not pay for it. `uv sync --extra ml --extra asr` for the full audio box.
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

> **`.env` is gitignored; `.env.example` is committed and carries names only.** Since
> 2026-09-06 the example file marks every secret `[READ]` (an adapter uses it today) or
> `[SLOT]` (the field exists, its adapter does not). Declaring an unread *secret* is
> deliberate and is not `Q26`'s complaint about dead env vars: a declared secret is
> redacted from every log line by name the moment somebody sets it, and having the slot
> ready is what stops a live key being pasted somewhere with no home. Declaring an unread
> *behaviour knob* is still a lie about what the system does.

**The audio path, added at P3 step 4b.** Every one defaults to needing nothing installed:

| Variable | Default | Notes |
|---|---|---|
| `STT_ENGINE` | `scripted` | `scripted` (the stage-safe default) · **`typhoon` is the SHIPPED engine** (`D104`) and needs the `asr` extra · `thonburian_ct2` (faster-whisper) is the documented fallback · `thonburian_hf` · `distill` and `cloud` are named and **not built**, and say so in the log rather than falling back silently |
| `VAD_ENGINE` | `energy` | `energy` needs nothing and is also the degradation rung; `silero` is the real one (`D9`) and needs the `ml` extra |
| `STT_MODEL` | `biodatlab/whisper-th-medium-combined` | For `thonburian_ct2` this must be a **local converted directory** (`scripts/convert_ct2.py`), not an HF id (`B17`) |
| `STT_DEVICE` | `auto` | Resolves to `cuda` when a GPU is genuinely usable, `cpu` otherwise — resolved in `build_stt`, so importing config never imports torch |
| `STT_COMPUTE_TYPE` | `int8_float16` | int8 weights, fp16 compute. The default because of the measured 4.00 GiB / ~3.2 GiB free (`D95`) |
| `STT_WORKER` | `inline` | `subprocess` runs the engine in a child process so a runaway decode can be **killed** (`D112`). `inline` is what every test, scenario and the stage demo use |
| `STT_DECODE_TIMEOUT_S` | `8.0` | The deadline one utterance gets, enforced only under `subprocess`. 8 s because that is what `B14` measured a *single second of near-silence* costing on this GPU — the guard is for the pathological case, not for a slow model. The model **load** is outside it (`startup_timeout_s`, 180 s) |

**The recording, added at `D110`.** Also defaults to needing nothing installed:

| Variable | Default | Notes |
|---|---|---|
| `RECORDING_ENABLED` | `true` | `false` puts the system back where `D9` left it: audio analysed per utterance, in memory, never reaching a disk |
| `BLOB_STORAGE` | `memory` | `memory` · `localfs` · `minio` · `s3`. The last two need `uv sync --extra s3` |
| `RECORDING_MASTER_KEY` | *(unset)* | Base64, 32 bytes. Unset means a master generated per process, which is coherent only with `BLOB_STORAGE=memory` — **any durable store refuses to start without one**. Generate: `python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"` |
| `BLOB_ROOT` | `var/blobs` | Where `localfs` writes. Gitignored: ciphertext of a real person is still not a thing to commit (`D97`) |
| `BLOB_BUCKET` | `readycall-recordings` | Created at startup if missing, through the `ProvisionableBlobStorage` capability |
| `BLOB_ENDPOINT_URL` | `http://127.0.0.1:9000` | MinIO. Leave unset for real AWS. ⚠️ **9000 is a popular port** — on the dev laptop another process held it and boto3 reported a *protocol violation* rather than a bind failure, because Docker's proxy answered second. `MINIO_PORT` in compose overrides the published port |
| `BLOB_ACCESS_KEY` / `BLOB_SECRET_KEY` | *(unset)* | `readycall` / `readycall123` for the compose MinIO |
| `RECORDING_RETENTION_DAYS` | `90` | Written onto each row as `delete_after` **at upload time**, so changing this never silently re-dates audio already held (`D14`) |


Everything selectable, nothing hardcoded:

```ini
# --- adapter selection ---
TELEPHONY_PROVIDER=simulated          # asterisk | twilio | livekit | simulated
STT_ENGINE=thonburian_hf              # thonburian_hf | thonburian_ct2 | distill | typhoon | cloud | scripted
STT_MODEL=biodatlab/whisper-th-medium-combined
STT_DEVICE=auto                       # cuda | cpu | auto
STT_COMPUTE_TYPE=int8_float16         # ct2 only
STT_WORKER=inline                     # inline | subprocess   (D112)
STT_DECODE_TIMEOUT_S=8.0
LLM_PROVIDER=anthropic                # anthropic | openai_compatible | gemini | rulebased
LLM_MODEL=claude-sonnet-5
LLM_BASE_URL=                         # set for openai_compatible (Typhoon API, OpenAI, vLLM, Ollama)
TTS_ENGINE=azure                      # used at BUILD time to render prompts, not during calls
CORE_DATA_PROVIDER=mock_postgres      # mock_postgres | fixtures | http_api | sql_passthrough | null
CORE_MAPPING_FILE=config/core_mapping.yaml
INTAKE_STRATEGY=passive               # passive | guided | conversational
EVENT_BUS=redis                       # redis | kafka | memory
BLOB_STORAGE=minio                    # memory | localfs | minio | s3   (D110)
RECORDING_ENABLED=true
RECORDING_MASTER_KEY=                 # base64, 32 bytes; REQUIRED for any durable store
BLOB_ENDPOINT_URL=http://127.0.0.1:9000
BLOB_BUCKET=readycall-recordings
BLOB_ACCESS_KEY=readycall
BLOB_SECRET_KEY=readycall123

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
