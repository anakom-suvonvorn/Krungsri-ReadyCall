# Krungsri ReadyCall

**An AI context layer for insurance service calls: it turns hold time into preparation time.**

1. **Context-aware calling** — tapping *Contact* on a plan inside the logged-in app carries verified
   identity, the selected product, active policies and recent activity into the call.
2. **AI pre-call intake** — while queued, the customer can describe their issue; it is recorded,
   transcribed (Thai), summarised and structured into a case brief before they reach the front.
3. **A ready agent screen** — who, which policy, what they want, what to say, what to do — plus the
   routing rationale and a calibrated confidence signal.

Built for the Krungsri Universe × KMITL Hackathon (*Reimagine Insurance Brokerage*) by team
**HACK VIDVA**.

---

## Status

**P0 · P1 · P1b · P2a · P2b · P2c complete. P3 complete — the audio path, the live transcript, and the encrypted recording in object storage.**

The identity ladder, the keypad IVR, the intake offer, the context assembler, the brief builder,
the public API, the matching engine, agent presence, the offer handshake, the React workstation and
the database layer are all real services doing real work. Only the *edges* are still fakes: the
phone, the speech model, the LLM, the bank's data and the agent roster.

A caller keys their way to the right queue, is offered the pre-call recording and either takes it,
refuses it or ignores it — and all three answers reach the same agent, because the menu settled the
routing before any of it ran. They are deliberately **not** told a position in the queue: the matcher
re-solves the whole caller × agent matrix every tick, so there is no arrival order to report
(`docs/DECISIONS.md` `D91`).

**The audio path is real** (§7). A WAV file or a phone leg is normalised, endpointed by a voice
activity detector and transcribed by a Thai speech model into ordered `TranscriptTurn`s that reach
`IntakeService.on_turn`. The engine was picked on measurements over 20 real Thai call-centre calls
rather than argued about: **Typhoon ASR**, the only one that meets the 1.5 s utterance-to-turn
budget, on **20 of 20** calls (`docs/DECISIONS.md` `D104`).

**And a consented recording is kept, encrypted.** The same frames the transcriber reads are written
to object storage under AES-256-GCM, with a data key per recording wrapped by a master the process
never writes down, and a retention date set the moment it is stored (`docs/DECISIONS.md` `D110`).
A caller who declines the recording is transcribed in memory for the brief and **stored nowhere** —
which is a property you can check yourself, in the walkthrough below.

**And the agent sees what the caller said.** By the time somebody presses Accept, the sentences
spoken while the caller was waiting are on their screen, in order, each with the moment in the
recording it was said. That is the pitch in one paragraph, and it is the thing you can watch
happen in the walkthrough below.

**Everything below runs with no services, no API keys, no GPU and no database.** That is deliberate
(`docs/DECISIONS.md` `D3`): every external dependency sits behind a port with a working fake, so you
can clone this and have a call running end to end in about a minute.

---

## Quick start

Three commands, on a clean clone, on any OS:

```bash
uv sync --extra web
```

```bash
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml
```

```bash
uv run python -m readycall.entrypoints.api
```

Three front ends come up with it:

| | | |
|---|---|---|
| **`/sim`** | the customer simulator | one static page, no build step (`D47`) |
| **`/workstation`** | the broker's desk | React bundle — needs the one-off `npm run build` in §2 |
| **`/assist/<token>`** | the customer's **paired screen** (`D120`, `D121`) | one static page. You do not open this directly — during a live call the broker presses **สร้างลิงก์ให้ลูกค้า** in the *เครื่องมือช่วยลูกค้า* panel on the workstation's right rail, and the token in the path is what authorises it. (`POST /v1/agent/calls/<id>/assist/link` is the same thing without the screen.) |

Then open **<http://127.0.0.1:8000/sim>**. The agent workstation at `/workstation` needs one extra
build step — see [§2](#2-the-agent-workstation-react-bundle).

---

## Prerequisites

| Tool | Needed for | Required? |
|---|---|---|
| **[uv](https://docs.astral.sh/uv/)** | everything Python. Installs its own Python 3.11 | **Yes** |
| **Node 18+** | building the agent workstation bundle, **once** | Only for `/workstation` |
| **Docker** | Postgres, so a shift survives a restart — **or the whole system in one container** (§5b), which needs none of the rows above | Optional |
| **mermaid-cli** | re-rendering the docs diagrams to SVG | Only if you edit diagrams |
| **An NVIDIA GPU** | local Thai speech-to-text | Only for real STT (§7). Everything else, tests included, runs without one |

Python is pinned to **3.11** (`.python-version`); `uv` fetches it for you. Nothing here needs a
system Python.

---

## Setup, one piece at a time

Each piece below is independent. Do §1 and you have a working system; the rest add capability.

### 1. The Python package — *required for everything*

```bash
uv sync --extra web
```

Installs the runtime, the `web` extra (FastAPI, uvicorn, websockets) and the dev group (pytest,
ruff, mypy). Then, optionally:

```bash
cp .env.example .env
```

`.env` is gitignored and is the only place a real key belongs. `.env.example` beside it
carries the same **names with no values** and is committed, so adding a key means adding
its name there too. Every secret in it is marked `[READ]` — an adapter uses it today — or
`[SLOT]`, meaning the field exists and its adapter does not, so setting it changes nothing
yet. **Nothing in there is required**: the system runs with no keys at all, and each line
you fill in swaps one fake for one real thing. See [Configuration](#configuration).

<details>
<summary>Which extras exist, and when you need them</summary>

| Command | Gets you | Enough for |
|---|---|---|
| `uv sync` | runtime only | the scenario runner, the matching simulator, the doc scripts |
| `uv sync --extra web` | **+ FastAPI / uvicorn / websockets** | the API, the simulator, the workstation, **the full test suite** |
| `uv sync --extra ml` | **+ torch (CUDA) / transformers / faster-whisper / silero-vad** | Silero voice detection and the two Whisper engines. ~3 GB, wants an NVIDIA GPU (§7) |
| `uv sync --extra ml --extra asr` | **+ `nemo_toolkit[asr]`** | **Typhoon**, the shipped engine. Large; the CT2 fallback exists for a box where this will not install (`D103`, `D104`) |
| `uv sync --extra llm` | **+ `anthropic` / `openai`** | the two real LLM adapters (`D119`). Small. **Not needed to run anything**: the shipped provider is `rulebased`, which is a real adapter with no key, no network and no extra |
| `uv sync --extra s3` | **+ `boto3`** | MinIO or real S3 for the encrypted recordings (`D110`) |

Three test files import FastAPI, so **`--extra web` is the one to use** unless you have a reason not
to. ⚠️ `uv sync` **prunes** anything not named, so `uv sync --extra web` on a box that had the GPU
stack removes it — name every extra you want in one command. `ml` and `asr` are real extras now and both are large; neither is needed to run the system, to
replay a scenario or to pass the suite — the audio path is dependency-free by design, which is why
CI exercises it with no GPU at all.
</details>

### 2. The agent workstation (React bundle)

Needed only to open `/workstation`. **Node is needed once, to build — never at run time.**

```bash
cd apps/workstation && npm install && npm run build && cd ../..
```

That produces `apps/workstation/dist/`, which the API mounts automatically at `/workstation`. The
API checks whether that folder exists and simply omits the route if it does not, so **skipping this
step breaks nothing else**.

> ⚠️ `dist/` is gitignored, so a fresh clone has no workstation until this has run. On a venue with
> no internet, `npm install` is the step that fails — **build before you travel.** (Tracked as open
> question `Q17` in `docs/NEXT_SESSION.md`.)

### 3. Postgres — *optional, makes a restart survivable*

Without it the whole system runs in memory, which is the default and is fine for development. With
it, an in-flight call and an agent's declared state survive the server being killed (`D78`).

```bash
docker compose -f infra/docker-compose.yml up -d postgres
```

```bash
uv run alembic upgrade head
```

Then run anything with `STORAGE_BACKEND=postgres` set, in `.env` or the environment.

The compose file also defines **redis**, **minio** and **pgweb**. `minio` is used since `D110` —
see the next section — and the other two are not yet, standing ready so the day a phase needs one
is not also the day someone learns Docker networking. Name the service explicitly
(`up -d postgres`) rather than starting all four.

<details>
<summary>The test database, and why it is separate</summary>

The Postgres suite creates and drops its own tables, so it uses a **separate database**,
`readycall_test`, created automatically by `infra/postgres/init/02-test-database.sql`. Init scripts
only run on an empty data directory, so this exists on a fresh volume and survives a plain restart.

If the suite ever reports it missing:

```bash
docker compose -f infra/docker-compose.yml exec postgres psql -U readycall -d postgres -c "CREATE DATABASE readycall_test OWNER readycall"
```

Point it elsewhere with `READYCALL_TEST_DATABASE_URL`. **Never point it at the dev database** — the
teardown drops every table and leaves Alembic claiming the schema is current.
</details>

### 4. Object storage for recordings — *optional; without it recordings stay in memory*

The recording is written **encrypted, always** (`D110`) — `build_blob_storage` wraps every backend
in AES-256-GCM, so what reaches a bucket or a directory is ciphertext with the key nowhere near it.
The default `BLOB_STORAGE=memory` needs nothing and keeps recordings for the life of the process,
which is right for development and for the stage.

To keep them for real, you need a store **and** a master key. The key is not optional: a durable
store with a per-process key would write ciphertext nobody can ever read again, so the app refuses
to start in that combination.

```bash
python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
```

Put that in `.env` as `RECORDING_MASTER_KEY`. **Lose it and every recording written under it is
gone** — that is what envelope encryption means, and it is also what makes `D14`'s erasure
guarantee real.

The simplest durable option is a local directory, which needs no container:

```bash
BLOB_STORAGE=localfs
```

Objects land under `BLOB_ROOT` (`var/blobs`, gitignored). For the S3 path, install the extra and
start MinIO:

```bash
uv sync --extra s3
```

```bash
docker compose -f infra/docker-compose.yml up -d minio
```

```ini
BLOB_STORAGE=minio
BLOB_ENDPOINT_URL=http://127.0.0.1:9000
BLOB_ACCESS_KEY=readycall
BLOB_SECRET_KEY=readycall123
```

The bucket is created at startup if it is missing. The console is at http://localhost:9001.

> ⚠️ **Port 9000 is popular and something else may already hold it.** On the development laptop a
> stray Python server did, and the symptom was not a bind failure — Docker's proxy answered second,
> so boto3 reported *"the server committed a protocol violation"*. Publish it elsewhere and point
> the client to match:
>
> ```bash
> MINIO_PORT=19000 MINIO_CONSOLE_PORT=19001 docker compose -f infra/docker-compose.yml up -d minio
> ```

Recordings are deleted by retention with `scripts/purge_recordings.py`; see *Things you can run*.

### 5. Generated mock bank data — *optional*

Three hand-authored customers ship in `mock/bank_core/fixtures/` and are used by default. Generate
a larger set when you want load or matching realism:

```bash
uv run python mock/bank_core/generate.py --seed 42 --customers 2000
```

Writes to `mock/bank_core/generated/` (gitignored). Deterministic: the same seed produces
byte-identical files.

### 5b. The whole thing in one container — *optional, and it needs nothing else installed*

If you do not want to install anything — no `uv`, no Node, no Python — this is the one command
that gets you a running ReadyCall:

```bash
docker build -t readycall .
```

```bash
docker run --rm -p 8000:8000 readycall
```

Then <http://127.0.0.1:8000/sim> and <http://127.0.0.1:8000/workstation>, exactly as above. The
image builds the React workstation itself, so `/workstation` works with no `npm` step and no
internet at run time.

Or through compose, which is behind a **profile** so it does not disturb the Postgres/MinIO
commands in §3 and §4:

```bash
docker compose -f infra/docker-compose.yml --profile demo up --build
```

**What is real in the container and what is not.** Everything except the speech engine: the IVR,
the identity ladder, the matcher, the brief, the workstation, the paired customer screen, the plan
comparison, the handoff — all real, on fixture data, with no services and no keys.

⚠️ **The image ships on `STT_ENGINE=scripted`, on purpose.** A plain container cannot reach the
GPU without host setup that varies by machine, which is exactly what fails at a venue. The real
Typhoon engine (§7) runs on a host with a GPU; this image is the one that starts anywhere.

⚠️ **It also defaults to `LLM_PROVIDER=rulebased`**, a real adapter that needs no key (`D119`), so
the container runs with nothing configured. Pass keys to turn the models on:

```bash
docker run --rm -p 8000:8000 \
  -e LLM_PROVIDER=anthropic -e LLM_MODEL=claude-sonnet-5 -e ANTHROPIC_API_KEY=sk-ant-... \
  -e LLM_FAST_PROVIDER=openai_compatible -e LLM_FAST_MODEL=gpt-5.4-mini \
  -e LLM_FAST_BASE_URL=https://api.openai.com/v1 -e OPENAI_API_KEY=sk-proj-... \
  readycall
```

**With keys, the full AI story runs in the container with no microphone and no GPU**, because a
typed intake is a real turn rather than a stand-in (`D133`). Verified end to end in the image: three
sentences typed on `/sim` reached the broker's transcript panel, a `gpt-5.4-mini` preview of them
was on the offer card **before Accept**, `claude-sonnet-5` replaced it after, and the
*"ข้อมูลอื่นๆ ที่มีเกี่ยวกับลูกค้า"* panel carried its own model-written sentence (`D131`, `D134`,
`D135`). There is no audio in the image at all — every `*.wav` is excluded by `.dockerignore`.

The container runs as a non-root user and answers `/health`, which is also its `HEALTHCHECK`:

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok", ... "intents_loaded":33}
```

⚠️ **`intents_loaded` is the field that matters.** A container that started but loaded no domain
pack still returns `status: ok` on some failures; the count is what proves the config directory
came along.

**If you want the durable backend instead** (`Q21` is the open question of which the demo should
use), there is a second profile that brings Postgres up with it:

```bash
docker compose -f infra/docker-compose.yml --profile demo-postgres up --build
```

### 6. Diagram rendering — *only if you edit the docs*

```bash
npm install -g @mermaid-js/mermaid-cli
```

Or set `MMDC=/path/to/mmdc`. The renderer drives a headless browser; if it cannot find one, set
`PUPPETEER_EXECUTABLE_PATH` to an installed Chrome or Edge rather than downloading a second Chromium.

### 7. Speech-to-text — *optional, and it needs an NVIDIA GPU to be worth installing*

```bash
uv sync --extra ml --extra web
```

**Nothing else needs this**, and it is a ~3 GB download. Every test, all three scenarios and
the stage-safe demo path run on `STT_ENGINE=scripted`, which returns canned transcripts and
never touches a GPU. Install it only when you are working on the audio.

**`torch` comes from the CUDA index, not PyPI** (`D95`), which `pyproject.toml` already
configures — you do not have to pass an index URL. This matters because the PyPI wheel is the
**CPU build** and installing it fails silently: everything imports, everything runs, and
Whisper is roughly ten times too slow with `torch.cuda.is_available()` quietly `False`.

So check it, rather than assuming the install worked:

```bash
uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

You want `2.11.0+cu128 True`. A version with **no `+cuXXX` suffix** is the CPU wheel — remove
the venv (`uv sync --reinstall`) rather than trying to patch over it.

Verified on the dev laptop: RTX 3050 Laptop (sm_86), driver 581.08, **4.00 GiB total and about
3.2 GiB actually free** — the desktop compositor holds the rest. That is tighter than the
"4–6 GB" the older docs assumed, and it is why the engine choice is measured (`D30`) rather
than picked.

---

## Things you can run

Each entry says what the command does, what has to be **set up** first, and what has to be
**running alongside** it.

### The API server — *the main thing*

```bash
uv run python -m readycall.entrypoints.api
```

Serves the public API, the customer simulator and the agent workstation from **one process on
:8000**. Starts a background sweep that expires unanswered offers, re-matches abandoned callers and
drops agents whose heartbeat died.

- **Needs set up:** §1. §2 as well if you want `/workstation`.
- **Needs running:** nothing. Add Postgres (§3) only if you set `STORAGE_BACKEND=postgres`.

| URL | What it is |
|---|---|
| <http://127.0.0.1:8000/sim> | **the customer** — persona picker, plan list, *Contact* button |
| <http://127.0.0.1:8000/workstation> | **the agent** — presence, offers, the brief, the softphone shell |
| <http://127.0.0.1:8000/docs> | OpenAPI browser |
| <http://127.0.0.1:8000/health> | liveness |

> **A full demo is both tabs at once.** Sign an agent in at `/workstation` and set them ready, then
> place a call from `/sim`. One process serves both — there is no second server to start.

> ⚠️ The server does **not** auto-reload. Restart it after changing Python.

### Watch a caller's words reach the agent's screen

The demo the project is actually about, and it needs **no telephony, no GPU and no API key**.
Two terminals and a browser.

**1. Start the API** (above) and open **<http://127.0.0.1:8000/workstation>**. Sign in as an
agent whose skills match the call you are about to place — `A001`, `A002` or `A003` for a
motor claim — and press **พร้อมรับสาย** (*ready*). Nothing is offered to an agent who has not
said they are ready; that is `D59`, not a bug.

**2. Make the caller's audio.** A fresh clone has none: every `*.wav` is gitignored,
because the real corpus is customer speech with account numbers in it (`D97`, `D14`). This
synthesises one in a second, with no dependencies — and **sizes each utterance from the
script**, so the rate guard below cannot bite you by accident:

```bash
uv run python scripts/make_demo_audio.py
```

**3. Place the call.** `POST /v1/demo/calls` takes an `audio` filename, plays that WAV down
the call's media leg in 20 ms packets exactly as telephony will at P5, and the real
endpointer cuts it into utterances (`D107`). Any 16-bit mono WAV in `DEMO_AUDIO_DIR`
(`tests/audio` by default) will do — including one you record yourself:

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" -d "{\"intent_code\": \"motor.claim.notify\", \"caller_number\": \"0812345678\", \"intake_keys\": [\"1\"], \"audio\": \"demo_intake.wav\", \"ignore_hours\": true}"
```

`"intake_keys": ["1"]` is the caller accepting the recording offer. Press `2` instead and
there is no transcript at all, and the panel says *why* — a refusal and a silence are
different facts (`D88`).

**4. Press Accept.** The sentences the caller spoke while waiting are already on the screen,
under the brief, each with the moment in the recording it was said. Nothing was shown before
that point: during intake the call belongs to nobody, and an agent who *declines* an offer
never reads the caller's words (`D106`).

**What you are hearing, and what you are reading, are separate.** On the default
`STT_ENGINE=scripted` the words come from `config/demo_transcript.yaml`, one line per
utterance the detector found — so the **audio decides the timings and the number of turns**
and the file decides what they say. That is the stage-safe path, and it is why the demo does
not depend on a model loading in a noisy room. Set `STT_ENGINE=typhoon` (§7) and the same
walkthrough transcribes real Thai speech for real.

- **Needs set up:** §1 and §2. §7 only if you want real transcription rather than the
  scripted lines.
- **Needs running:** the API server. Nothing else.

> ⚠️ **The script and the audio have to be sized for each other.** `D98`'s rate guard
> refuses a turn carrying more than ~15 characters per second of the utterance it arrived
> on, measured from real Thai — and it does not care that the text came from a file. On too
> short an utterance every line is silently dropped and the panel is simply empty, with
> nothing in the log to explain it. `make_demo_audio.py` computes the length from the
> longest line in the script and warns if you force it below the floor; if you bring your
> own recording, that is the arithmetic to do.

### Compare plans across carriers, which is the broker's actual mandate

Journey step 3, and the brief marks it as the biggest leak. **No extra setup: §1 and §2.**

Sign in at `/workstation` as **A006**, press **พร้อมรับสาย**, then place an *advice* call
rather than a claim:

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" -d "{\"intent_code\": \"health.advice.compare\", \"caller_number\": \"0812345678\", \"intake_keys\": [\"2\"], \"ignore_hours\": true}"
```

Press **Accept**, then **เปิดข้อมูลแผน** in the right rail. The dialog has a line selector
and two tabs (`D127`):

- **เปรียบเทียบ** ranks three plans against what this customer already holds, each with a
  sentence and green ↑ / red ↓ chips for what is better and what is worse. Read the second
  row: it usually has the **best figure in most columns** and still ranks below the first,
  because of a deductible. That is the ranking doing real work rather than sorting by the
  biggest number.
- **แผนทั้งหมด** is the catalogue for that line, plan by plan, with the customer's own
  marked. Selecting one shows its figures and can push just that plan.

Switch the line selector to **ประกันรถยนต์**: the comparison re-bases onto the customer's
*motor* policy rather than the health one the call is about.

Everything about that order is arithmetic — weights live in `config/comparison.yaml`, the
figures come from `Product.coverages` through `CoreDataProvider`. **The model rewrites only
the reason sentence** (`D16`, `D126`, `D137`).

With an LLM key configured (§ [Configuration](#configuration)), a small **AI** badge appears
beside the sentences a model wrote; without one they are generated and the badge is absent.
The ordering is identical either way — no model ever sees a score. A real example, on the
fixture data above:

> **กรุงเทพ เฮลท์ อีลิท** — *เพิ่มค่าห้องและค่าอาหารจาก 3,000 เป็น 6,000 วงเงินผู้ป่วยในต่อปีจาก
> 1,000,000 เป็น 10,000,000 และค่ารักษาผู้ป่วยนอกจาก 1,500 เป็น 2,500 **แต่ค่าใช้จ่ายส่วนแรกสูงขึ้น
> จาก 0 เป็น 30,000***

That plan has the better figure in three of four rows and still ranks **second** — and the
sentence says why. Four guards stand behind it, and the first is the one that matters:
**every number in the sentence must be one we handed the model**, so a coverage figure can
never be written by a model (`D16`). It may not rank, price, or promise cover either; a
refused sentence falls back to the generated one and the table is unchanged.

⚠️ This is the **one** model call in the system that is awaited, bounded by
`LLM_COMPARISON_TIMEOUT_S` (3 s), because the panel is fetched once when you open it and is
never polled. Measured: ~2.4 s the first time, **4 ms** afterwards from a per-call cache.

Then press **สร้างลิงก์ให้ลูกค้า** in the tool panel, open the link, and press
**ส่งตารางนี้ให้ลูกค้า**. The table appears on the other screen — and note what it does
*not* contain:

⚠️ **A guest screen gets the market comparison without the customer's own column**, and
says why. Tapping a link proves somebody is holding that phone, not who they are (`D42`).
Sign in on the customer's screen and push again: **แผนปัจจุบันของคุณ** appears as the first
column. Same tool, same button — the *column* is gated, not the tool, because comparing what
the market offers is true for anybody (`D126`, `D121`).

### Send the customer something you typed yourself

Every other tool sends what the *system* composed. This one sends the broker's own sentence
— a hospital name, a spelling, three steps — so it does not have to be caught by ear
(`D128`). In the tool box, the **ส่งข้อความให้ลูกค้าอ่าน** control is a text box rather than
a button: type, press send, and it appears on the paired screen labelled
**ข้อความจากเจ้าหน้าที่** so the customer knows a person wrote it.

⚠️ Note the badge above the send button. It shows whether the customer's screen is signed
in, because this is the one tool whose content the system cannot classify — so the broker
gets the fact and makes the call (`D128`).

- **Needs set up:** §1 and §2.
- **Needs running:** the API server. Nothing else.

### Watch the AI summary land on the offer card, before Accept

The summary runs **twice** (`D131`): a fast **preview** while the offer card is ringing, and
the careful pass over the whole transcript once the broker accepts. That is what makes *"the
broker has the brief before they speak"* true of the AI half of the brief, and it is what
closes `Q35`.

**Needs set up:** §1, §2, and an API key — this is the one feature here that talks to a
hosted model and spends money. Everything works without one: with no key the provider stays
`rulebased` and the screen keeps the rule-based summary, which is the shipped default.

Put the keys in `.env` (gitignored). Either name works for OpenAI — `LLM_API_KEY` or
`OPENAI_API_KEY`, whichever your key arrived under (`D130`):

```ini
# the careful pass, after Accept
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-5

# the preview, during the offer window. Omit all three to use LLM_MODEL for both.
LLM_FAST_PROVIDER=openai_compatible
LLM_FAST_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-proj-...
LLM_FAST_MODEL=gpt-5.4-mini
```

Then install the SDKs and start the server. ⚠️ **Name every extra you want in one command —
`uv sync` prunes** (`B31`):

```bash
uv sync --extra web --extra llm
uv run python -m readycall.entrypoints.api
```

Sign in at `/workstation` as **A006**, but **do not press พร้อมรับสาย yet** — the caller has
to be talking before a desk rings, which is the real sequence. Place the call first:

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" \
  -d '{"intent_code":"motor.claim.notify","caller_number":"0812345678","intake_keys":["1"],"audio":"demo_intake.wav","ignore_hours":true}'
```

Wait about ten seconds for the caller to finish recording, **then** press พร้อมรับสาย. The
offer card appears carrying the rule-based summary, and one to two seconds later that text is
replaced by the model's. Accept, and it is replaced again by the whole-transcript version —
the **สรุประหว่างรอรับสาย** badge disappears at that moment.

⚠️ **`intent_code` and the scripted lines have to match.** `config/demo_transcript.yaml` is a
motor crash, so placing a `health.claim.notify` call makes the model correctly answer
*"unclear"* and refuse to summarise — and the screen then shows **no AI summary at all**,
which looks broken and is not (`D131`).

⚠️ **`demo_intake.wav` is gitignored.** A fresh clone regenerates it:
`uv run python scripts/make_demo_audio.py`.

### Compare models before choosing one

`scripts/compare_llm.py` runs the same four Thai intakes through every model you name,
through the real summariser and the real prompt file, and prints latency, cost and the actual
Thai each one wrote. **This is P4's "a comparison table produced by the harness, not by
opinion"** (`D130`).

```bash
uv run python scripts/compare_llm.py --list          # presets and cases, spends nothing
uv run python scripts/compare_llm.py --preset fast --dry-run   # count the calls first
uv run python scripts/compare_llm.py --preset full --repeat 3 --out var/llm_compare.md
```

- **Needs set up:** §1, `uv sync --extra llm`, and whichever keys the models you name need.
- **Needs running:** nothing. It calls the providers directly.

⚠️ **It spends real money** — `--preset full --repeat 3` is 63 model calls, which is cents
rather than dollars at these rates and is still a real invoice. `--dry-run` prints the matrix
and calls nothing.

⚠️ **The report goes to a file, not the console**, because it is full of Thai and the Windows
console is cp1252 (`B1`). **Read the "what each model actually wrote" section at the bottom** —
the latency and cost columns cannot see quality, and on the run that produced `D130`'s table
the cheapest model scored a clean sheet while writing *"crashed last night"* about a caller
who said *this morning*.

### See what the bank already knew about this customer

`ContextAssembler` has always fetched holdings, life events, claims and the conversation
history into the frozen snapshot — and the brief exposed two counts. Since `D134` the rest
of it is on the broker's screen, under **ข้อมูลอื่นๆ ที่มีเกี่ยวกับลูกค้า** in the brief panel.

⚠️ It **shows** and it does not **recommend** (`D116`). Every row names its source, an
inferred life-event signal carries its confidence and a stored record carries none, and a
model-written summary is refused outright if it states a figure or tells the broker what to
sell.

- **Needs set up:** §1 and §2. The facts need no key at all; only the summary sentence does.
- **Needs running:** the API server.

The persona to use is **C000002**, who holds the story the orientation deck describes — a
mortgage (2022, confidence 0.95, from `loan_origination`) and a new child (2024, 0.70):

```bash
uv run python -m readycall.entrypoints.api
```

Sign in at `/workstation` as **A003** — the renewal desk. ⚠️ `general.renewal` needs the
`renewal.retention` skill, and since `D117` a motor advisor is simply never offered it
(`D132`'s dialog marks intents you cannot receive, for exactly this reason). Press
**พร้อมรับสาย**, then:

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" \
  -d '{"intent_code":"general.renewal","caller_number":"+66898887777","intake_keys":["2"],"ignore_hours":true}'
```

Accept, and read the panel under the brief. **Press ดูข้อมูลดิบทั้งหมด** — the folded half is
the point: the summary above it is only trustworthy because every row underneath says where
it came from (`D18`).

### Let the customer type instead of speaking

`D133`. The pre-call intake needs a microphone, a media path from the browser and an ASR
engine — and **typing needs none of them**, because the intake seam takes turns rather than
frames (`D88`). So the whole AI story runs on a laptop with nothing plugged in.

- **Needs set up:** §1. A key only if you want the AI summary rather than the rule-based one.
- **Needs running:** the API server.

At `/sim`: sign in, tap a plan, **Contact us about this plan**, pick a reason — and the new
third sheet asks *"ก่อนต่อสาย — อยากเล่าเรื่องไว้ก่อนไหม?"*. Choose **พิมพ์เล่าให้ฟัง**, and a text box
appears on the call screen while you wait. Type a sentence, press send, repeat.

⚠️ **The question is asked before dialling, not during the wait**, and that is forced as
well as better: `INTAKE_ACTIVE` is reachable only from `QUEUED`, and a call becomes
`MATCHED` the instant the offer resolves — so there is no legal window mid-wait to open an
intake in (`Q39`). The voice option is a **labelled stub**; the typed one is real.

### Hand a claim to the insurer, which is what a broker actually does

A broker does not adjudicate claims — the insurer does (`D117`, from Krungsri's own duty
slide). Since `D124` that is an action rather than a banner. **No extra setup: §1 and §2.**

Start the API, sign in at `/workstation` as **A006** (health), press **พร้อมรับสาย**, then:

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" -d "{\"intent_code\": \"health.claim.notify\", \"caller_number\": \"0812345678\", \"intake_keys\": [\"2\"], \"ignore_hours\": true}"
```

Press **Accept**, then **ส่งต่อ** in the call bar beside วางสาย. The dialog carries:

- the banner saying this call ends with the insurer — the same `handoff_to_insurer` flag
  that drew it above the policy panel, so the two cannot disagree;
- **the carrier that actually wrote this customer's policy, as its own row above the
  market menu.** `config/insurers.yaml` is a *menu, not a whitelist* — on hackathon day
  the real extract arrives carrying carriers nobody has typed in, and a broker who cannot
  hand a claim to the company that wrote the policy has no product;
- six closed reasons, with the ones that only make sense about cover the customer holds
  greyed when we hold none. **The server refuses those independently** — the greying is
  the courtesy, not the gate (`D121`).

Choosing one ends the call and the wrap-up form is already filled in:
`ส่งต่อ เมืองไทยประกันภัย — การพิจารณาและจ่ายค่าสินไหม`. It is a **suggestion**: nothing is
saved until the broker presses save, because they are the one who made the promise (`D45`).

⚠️ The dialog's second tab, **โอนสายภายใน** (transfer to another broker), is a **labelled
stub**. `D63` designed it and it is not built; the tab says so rather than pretending.

- **Needs set up:** §1 and §2.
- **Needs running:** the API server. Nothing else.

### Prove the recording is encrypted, and that a refusal is honoured

Two claims worth checking yourself rather than believing: what lands in the bucket cannot be
played, and a caller who declines leaves nothing behind at all (`D110`, `D14`).

Set up §4 first, then start the API with a durable store — here MinIO, published on 19000 because
9000 was taken:

```bash
MINIO_PORT=19000 MINIO_CONSOLE_PORT=19001 docker compose -f infra/docker-compose.yml up -d minio
```

```ini
BLOB_STORAGE=minio
BLOB_ENDPOINT_URL=http://127.0.0.1:19000
BLOB_ACCESS_KEY=readycall
BLOB_SECRET_KEY=readycall123
RECORDING_MASTER_KEY=<your base64 key>
```

Run the walkthrough above twice — once with `"intake_keys": ["1"]` and once with `["2"]` — and
accept the first. Then look at what is actually there. The console at <http://localhost:19001>
(`readycall` / `readycall123`) shows **one** object, under the consenting call:

```bash
uv run python -c "import boto3,os; c=boto3.client('s3', endpoint_url=os.environ['BLOB_ENDPOINT_URL'], aws_access_key_id=os.environ['BLOB_ACCESS_KEY'], aws_secret_access_key=os.environ['BLOB_SECRET_KEY']); print([o['Key'] for o in c.list_objects_v2(Bucket='readycall-recordings').get('Contents', [])])"
```

Download it and it starts `RCE1`, not `RIFF`: the framing header, the key ref, the wrapped data
key, then AES-256-GCM ciphertext. It opens through `EncryptingBlobStorage` with the right master
key and refuses with any other — the same failure a tampered byte produces, because GCM
authenticates as well as encrypts.

- **Needs set up:** §1, §4.
- **Needs running:** the API server, and MinIO if `BLOB_STORAGE=minio`.

### Delete recordings past their retention

`D14` promises retention per artifact and an erasure job across both stores. This is that job for
the audio half. It is a script rather than a background task on purpose: deletion is the thing you
least want happening unattended on a demo machine.

```bash
uv run python scripts/purge_recordings.py --dry-run
```

It prints the store and the retention it is about to act on before anything else, and **refuses
outright on `STORAGE_BACKEND=memory`**, where a fresh process holds no rows and a clean run would
be a green tick over an untouched bucket. To honour a PDPA erasure request for one call, ignoring
retention entirely:

```bash
uv run python scripts/purge_recordings.py --call call_01ABCDEF
```

The object goes before the row. The other order can leave audio nobody knows they are holding.

- **Needs set up:** §1, §3 (rows) and §4 (objects).
- **Needs running:** whatever backends those are pointed at.

### The workstation dev server — *only while editing the React app*

```bash
cd apps/workstation && npm run dev
```

Vite with hot reload on **:5173**, proxying `/v1` to the API. Use this instead of rebuilding after
every edit.

- **Needs set up:** §2.
- **Needs running:** **the API server**, in another terminal. The dev server serves the UI only;
  every request it makes goes to :8000.

### Replay a scripted call, end to end

```bash
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml
```

Drives one call from arrival through the IVR, the queue, matching, the offer, the live call and
wrap-up — printing the state timeline, the assembled context, the brief and the events. No
telephony, no GPU, no database, no network. Deterministic: two runs are byte-identical.

- **Needs set up:** §1 (`uv sync` alone is enough).
- **Needs running:** nothing.
- `--quiet` suppresses the info logs. Three scenarios ship in `tests/scenarios/`:

| Scenario | What it exercises |
|---|---|
| `anonymous_declined.yaml` | the floor — unrecognised caller, no app, declines the recording, routed by keypad alone |
| `pattheera_ipd.yaml` | the app path — verified identity, both menu questions already answered |
| `roadside_motor_claim.yaml` | a cold call to a product-line number, identity guessed from caller ID |

  A scenario's `intake:` block drives the real offer: `declined: true` presses **2**, a scenario
  with `turns:` presses **1**, and one with neither says nothing and falls through to hold.

### Simulate the matcher under load

```bash
uv run python scripts/run_matching.py --calls 25 --compare
```

Throws synthetic callers at the matching engine and prints what it decided and why. `--compare`
runs the global solver and greedy over the **same** matrix and prints the score gap — so how much
global matching is worth is a measurement, not an assertion. The gap depends on the load: at low
volume the two often agree exactly, and it opens up as the queue gets contested.

- **Needs set up:** §1. **Needs running:** nothing.
- Options: `--calls N`, `--seed N`, `--solver hungarian|greedy`, `--quiet`.

### Prepare real Thai audio to measure against

```bash
uv run python scripts/prepare_dataset.py --n 20
```

- **Needs set up:** §1, and the dataset at `../data/` (22 GB, outside this repo — `D97`).
- **Needs running alongside:** nothing.

Selects calls reproducibly, writes `<call>.wav` + `<call>.txt` pairs the bake-off reads,
and emits `segments.tsv` with the dataset's own speech/noise spans — ground truth our
endpointer can be scored against.

⚠️ **The output is gitignored and must stay that way.** It is real customer speech; the
transcripts contain names and account numbers (`D14`).

### Convert a Whisper checkpoint for faster-whisper

```bash
uv run python scripts/convert_ct2.py
```

Thonburian publishes a transformers checkpoint only, and faster-whisper needs a CTranslate2
build, so it has to be converted once locally. It writes a quantised copy (~737 MB) to
`models/`, which is gitignored — so **a fresh clone has to run this**. About a minute if the
Hugging Face cache already holds the checkpoint, plus a ~1.6 GB download if it does not.

**The CT2 build is now the FALLBACK engine, not the first choice** (`D104` chose Typhoon).
Convert it anyway: it needs only the `ml` extra, where Typhoon needs NeMo, so this is what
runs on a box where that will not install. Turn it on with

```bash
STT_ENGINE=thonburian_ct2       # STT_MODEL is left UNSET on purpose (`B23`)
```

Leave `STT_MODEL` blank so the adapter's own default — the local `models/` directory —
applies. Setting it to a Hugging Face id is refused at startup, because faster-whisper reads
a CTranslate2 directory and an HF id fails at model load on the box with the GPU. Measured paced
over 12 real Thai calls against the fp16 original:

| | Thonburian fp16 | CT2 `int8_float16` |
|---|---|---|
| p95 utterance-end → turn, median | 19.5 s | **1.65 s** |
| p95, worst call | 58.7 s | **2.48 s** |
| VRAM | 2731 MB | **1106 MB** |
| CER median | 0.161 | 0.182 |

Twelve times faster at the median and twenty-four at the worst, for about 13% relatively
more character error. The budget is still missed (1.5 s), but by 1.1–1.7x rather than
13–39x.

### The chosen engine: Typhoon ASR

```bash
uv sync --extra ml --extra asr
STT_ENGINE=typhoon
```

- **Needs set up:** §1, plus **both** extras. `asr` is `nemo_toolkit[asr]`, a large install
  kept separate from `ml` on purpose (`D99`).
- **Needs running alongside:** nothing. Downloads the model on first use.

**It is the only engine that meets the latency budget** (`D104`). Measured paced over 20
real Thai calls, against a target of p95 < 1.5 s:

| | fp16 | CT2 int8 + hint | **Typhoon** |
|---|---|---|---|
| p95 median | 19.5 s | 1.68 s | **0.19 s** |
| p95 worst | 58.7 s | 2.53 s | **0.28 s** |
| calls inside budget | 0 of 12 | 7 of 20 | **20 of 20** |
| CER mean | **0.109** | 0.128 | 0.133 |

It is a **transducer**, not a Whisper model, so it has no 30-second window and does not pay
a full encode for a two-second utterance. That is the whole reason it is fast, and it was
predicted in `D99` before any of it was measured.

The trade: about 22% relatively more character error than fp16, and **it cannot take the
vocabulary hint** — a transducer has no prompt mechanism, so `SttHint.vocabulary` does
nothing for it.

### Measure the speech engines against each other

```bash
uv run python scripts/bake_off.py --list
```

- **Needs set up:** §1 for the harness itself; **§7 (the `ml` extra) for any real engine**.
- **Needs running alongside:** nothing.

`.gitignore` excludes `*.wav`, so a fresh clone has no test audio. Generate the synthetic
smoke file first (it is three tone bursts, **not speech** — see `B14`):

```bash
uv run python scripts/make_test_audio.py
```

The `scripted` engine needs no GPU and exists to prove the harness works:

```bash
uv run python scripts/bake_off.py --engines scripted --audio tests/audio/*.wav
```

A real one downloads its weights on first use (`tiny` is ~75 MB and is the cheap check that
CUDA is actually working end to end):

```bash
uv run python scripts/bake_off.py --engines thonburian --vad silero \
    --audio tests/audio/thai_calls/*.wav
```

**Rank on the CER column, not WER** (`B18`). Thai does not put spaces between words, so a
whitespace word error rate compares one arbitrary segmentation against another — it read
**0.94-1.12 on a model that was working fine**. Both are printed; CER is the real one.

`--vad silero` matters as much as the engine: the detector decides what the model is even
asked to transcribe, so a number measured with the dependency-free `energy` detector is
partly a measurement of that detector.

**To get a WER column, put a `.txt` next to each `.wav`** containing the true transcript.
Without one the harness reports latency and VRAM only, and prints `-` for WER rather than a
number computed against nothing.

**Always look at the text behind a CER before believing it** (`B18`, `B19`, `B20` — three
bugs found this way and none found by a test):

```bash
uv run python scripts/bake_off.py --engines thonburian --vad silero \
    --audio tests/audio/thai_calls/*.wav --dump transcripts.txt
```

`--dump` writes reference against hypothesis to a UTF-8 file. It never prints to the
console, because Thai on the Windows cp1252 console kills the process (`B1`).

**`--fast` is not a display option.** It stops pacing the feed, which measures throughput
(`rtf`) instead of latency and blanks the latency column. Every real measurement of the
**budget** — utterance end to turn, p95 < 1.5 s — has to run paced, which takes as long as
the audio does. `B20` hid inside that distinction for a session.

It has a second edge worth knowing: an unpaced feed ingests the whole file before the model
finishes the first utterance, so the stream holds the entire call in memory. Past **120 s**
of audio that hits the backlog cap (`D100`) and the oldest segments are abandoned — with a
warning in the log, but abandoned. **`--fast` is only safe for accuracy on files shorter
than two minutes.** The dataset's calls are 65–90 s, which is why it is safe here.

### Score the detector, which a CER cannot see

```bash
uv run python scripts/score_endpointer.py --vad silero
```

- **Needs set up:** §1, plus §7 (the `ml` extra) for `--vad silero`. `--vad energy` needs
  nothing at all.
- **Needs running alongside:** nothing. **No GPU is used** — the detector runs on CPU and no
  speech model is loaded.
- **Needs:** `tests/audio/thai_calls/segments.tsv`, written by `prepare_dataset.py`.

The endpointer decides what the model is ever *asked* to transcribe, so a sentence it never
emitted looks, in CER, exactly like a sentence the model got wrong — and the fixes are
opposite. This scores that half against the dataset's hand-annotated spans: **coverage**
(what fraction of annotated speech seconds reached the model), **span recall**, and
**false-alarm seconds** (audio detected where nobody spoke, which is the `B14` input).

```bash
uv run python scripts/score_endpointer.py --vad silero --sweep 0.15 0.35 0.5 0.65 0.8
```

sweeps `D9`'s speech threshold against **one** pass of the detector, so every row reads
identical probabilities and a difference between rows is the endpointer's alone.

Two things about this table that are easy to misread, and both are in the output:

- **audio is paced at wall-clock speed by default.** An unpaced feed queues every utterance
  at once and reports a *backlog* instead of a latency — measured, 11270 ms against 553 ms
  for identical work. `--fast` measures throughput instead and blanks the latency column.
- **WER over whitespace tokens is a phrase error rate on unsegmented Thai.** Fine for
  ranking engines against each other; not quotable as an absolute number.

⚠️ **`D30` is closed** — `D104` picked Typhoon on a balanced 20-call set, and the table is
in `docs/PLAN.md`'s P3 exit criteria. Read `B14`, `B20` and `B21` before believing any
number this prints: all three were measurement bugs that produced confident wrong figures,
and no accuracy figure from before 2026-09-04 is quotable at all.

### Preparing the test audio, and why `--mix` matters

```bash
uv run python scripts/prepare_dataset.py --n 20 --mix
```

Almost every call in this corpus ends with the customer reading out a phone number, so a
**random** sample is overwhelmingly digit-heavy. That was harmless until `B21` **loosened**
the repetition guard for digits — and a set full of digits is exactly the wrong set to
check that the loosening did not let real hallucinations back in (`Q30`). `--mix` caps the
digit-heavy share (default half) and the printed table gains a `digit%` column so the
balance is visible rather than assumed.


### Build the voice prompts

```bash
uv run python scripts/build_prompts.py
```

Renders every spoken line in `config/voice_prompts.yaml` to a cached clip and writes
`prompts/voice/manifest.json`. Cached by `hash(text, voice, engine)`, so editing one Thai line
re-renders exactly that line.

- **Needs set up:** §1. **Needs running:** nothing (the default TTS engine is `null`, which records
  what it would say and synthesises no audio).
- `--list` prints every distinct line without rendering · `--check` fails if the committed manifest
  is stale · `--force` re-renders everything.

**Run this after editing any Thai wording or any menu label**, and commit the manifest — a test
fails if it falls behind.

### Run the tests

```bash
uv run pytest -q
```

- **Needs set up:** §1 with `--extra web`.
- **Needs running:** nothing. Database cases **skip automatically** when Postgres is down.

| With Postgres | Result |
|---|---|
| stopped | 454 pass, 42 skipped |
| running (§3) | 493 pass, 3 skipped — the three are foreign-key cases the in-memory backend cannot have |

### Lint, format and typecheck

```bash
uv run ruff check src tests scripts mock
```

```bash
uv run ruff format src tests scripts mock
```

```bash
uv run mypy
```

All three must be clean; CI enforces them along with the tests and every scenario replay.
`docs/` is excluded from formatting on purpose — the explanations are verbatim records.

### Database migrations

```bash
uv run alembic upgrade head
```

Creates or updates our schema. Alembic takes its URL from `Settings`, **never** from `alembic.ini`,
so it always targets the same database the app opens.

- **Needs running:** Postgres (§3).
- `uv run alembic revision --autogenerate -m "..."` to add one. A correct run against an up-to-date
  database produces an **empty** migration — that is the check.

### Regenerate the docs

```bash
uv run python scripts/gen_diagrams.py
```

Rebuilds the 14 diagrams that are derived from source — the transition table, the loaded config, the
pydantic models, the event registry, the ORM metadata. Never hand-edit a `.mmd` with a
`%% GENERATED` banner.

```bash
uv run python scripts/render_diagrams.py
```

Renders every `.mmd` to an SVG. Needs mermaid-cli (§6); `--check` verifies freshness without
rendering, and takes diagram names to render just a few.

```bash
uv run python scripts/build_reading_data.py
```

Refreshes the real menu and prompt data embedded in `docs/reading/the_line.html`, the interactive
keypad page.

```bash
uv run python scripts/audit_docs.py
```

Checks every number the docs claim against the running code. Run it before finishing a phase.

---

## What needs what

```
uv sync --extra web ──┬─▶ scenario replay          (nothing else needed)
                      ├─▶ matching simulator       (nothing else needed)
                      ├─▶ prompt build + doc tools (nothing else needed)
                      ├─▶ tests                    (Postgres optional — cases skip without it)
                      └─▶ API server on :8000 ──┬─▶ /sim            works immediately
                                                ├─▶ /workstation    needs the npm build (§2)
                                                └─▶ vite dev :5173  needs the API running too

docker compose up -d postgres ──▶ alembic upgrade head ──▶ STORAGE_BACKEND=postgres
                                                            └─▶ a shift survives a restart

uv sync --extra ml ──┬─▶ STT_ENGINE=thonburian_ct2 / thonburian_hf   (needs an NVIDIA GPU)
                     ├─▶ VAD_ENGINE=silero        (CPU; energy is the default and needs nothing)
                     └─▶ scripts/bake_off.py with a real engine
                     (nothing else in the system needs any of this)

RECORDING_MASTER_KEY ──┬─▶ BLOB_STORAGE=localfs   (a directory; no container)
                       └─▶ uv sync --extra s3 ──▶ docker compose up -d minio
                                                    └─▶ BLOB_STORAGE=minio
     (without the key: BLOB_STORAGE=memory only — any durable store REFUSES to start,
      because ciphertext nobody can ever read is worse than no recording at all)
```

```
docker build -t readycall .  ──▶ docker run -p 8000:8000 readycall
                                   ├─▶ /sim, /workstation, /assist/<token>   all work immediately
                                   ├─▶ STT_ENGINE=scripted                   (no GPU reachable in a
                                   │                                          plain container, on purpose)
                                   └─▶ LLM_PROVIDER=rulebased                (pass -e keys for real models)
     (needs NOTHING else installed — not uv, not Node, not Python. The image builds the
      workstation bundle itself, so /workstation needs no npm step at run time.)
```

Nothing in the left column depends on anything in the right.

---

## Configuration

The audio path is two env vars and both default to needing nothing:

| Variable | Default | Notes |
|---|---|---|
| `STT_ENGINE` | `scripted` | `scripted` returns canned transcripts and never touches a GPU — every test, all three scenarios and the stage-safe demo path run on it. **`typhoon` is the engine this project ships** (`D104`) and needs the `asr` extra; `thonburian_ct2` (faster-whisper/CTranslate2) is the documented fallback; `thonburian_hf` is the transformers pipeline. `distill` and `cloud` are named but not built, and say so in the log rather than falling back silently. |
| `VAD_ENGINE` | `energy` | `energy` needs no dependencies and is also the degradation rung. `silero` is the real one (`D9`) and needs the `ml` extra. |
| `STT_DEVICE` | `auto` | Resolves to `cuda` when a GPU is genuinely usable, `cpu` otherwise — so a box with the extra installed and no working GPU falls back instead of dying at model load. |
| `STT_COMPUTE_TYPE` | `int8_float16` | int8 weights, fp16 compute. The default because of the measured 4.00 GiB / ~3.2 GiB free (`D95`). |
| `STT_WORKER` | `inline` | `subprocess` puts the engine in a child process, which is the only way a decode timeout can be enforced — `asyncio.wait_for` around a thread does not kill the thread (`D112`). Costs a spawn and a model load, so `inline` stays the default and is what the demo runs. |
| `STT_DECODE_TIMEOUT_S` | `8.0` | How long one utterance gets before the worker is **killed**, under `subprocess` only. The model *load* is not inside it. |
| `BUS_DRAIN_INTERVAL_S` | `0.05` | How often the API process runs the event bus's handlers (`D105`). **Do not raise this casually**: it is on the transcript's path to the screen, where the budget is 1.5 s end to end and the model already spends 0.19 s. `0` disables it, which is what a test wants when it drains explicitly — and with it disabled the live transcript never arrives. |
| `DEMO_AUDIO_DIR` | `tests/audio` | The only directory `POST /v1/demo/calls` will play a WAV out of (`D107`). The request sends a bare filename; this says where it may live, so the endpoint is never a way to read an arbitrary file. |
| `DEMO_TRANSCRIPT_FILE` | `config/demo_transcript.yaml` | The lines the `scripted` engine speaks, one per endpointed utterance (`D107`). Keep them short enough for the audio they play over — `D98`'s rate guard refuses more than ~15 characters per second and does not care that the text came from a file. |

The recording is four more, and they also default to needing nothing (`D110`):

| Variable | Default | Notes |
|---|---|---|
| `RECORDING_ENABLED` | `true` | `false` keeps audio out of storage entirely: analysed per utterance, in memory, never on a disk. |
| `BLOB_STORAGE` | `memory` | `localfs` writes to `BLOB_ROOT`; `minio` and `s3` are one adapter and need `uv sync --extra s3`. **Every one of them is wrapped in AES-256-GCM** — that is what `build_blob_storage` does, and it is why `localfs` is allowed at all. |
| `RECORDING_MASTER_KEY` | *(unset)* | Base64, 32 bytes. Unset means a master generated for this process only, and the app **refuses to start** with that against any durable store: ciphertext nobody can ever read is worse than no recording. |
| `RECORDING_RETENTION_DAYS` | `90` | Stamped onto each recording as `delete_after` **when it is stored**, so changing this never silently re-dates audio already held (`D14`). |



Settings come from the environment or a `.env` file, via `pydantic-settings`. Every one has a
default that works. The full surface is documented in `docs/INTEGRATIONS.md` §7; the ones that
change what actually runs:

| Variable | Default | Effect |
|---|---|---|
| `STORAGE_BACKEND` | `memory` | `postgres` makes state survive a restart |
| `TELEPHONY_PROVIDER` | `simulated` | a whole phone network in a dict; Asterisk lands at P5 |
| `STT_ENGINE` | `scripted` | canned transcripts, no GPU |
| `LLM_PROVIDER` | `rulebased` | no API key needed |
| `TTS_ENGINE` | `null` | records lines, synthesises no audio |
| `BLOB_STORAGE` | `memory` | recordings live and die with the process; `localfs`/`minio` keep them, encrypted |
| `CORE_DATA_PROVIDER` | `fixtures` | the three hand-authored customers |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | where the server binds |
| `DEMO_LOGIN_ENABLED` | `true` | the persona picker. **Must be false anywhere near real data** |

Adapter names are enums, so a typo fails at startup rather than mid-call. Some combinations are
rejected on boot — an Anthropic provider with no key, a deferral window that can never fire — for
the same reason.

---

## If something does not work

| Symptom | Cause |
|---|---|
| `/workstation` returns 404 | the bundle was never built — §2. The route only exists when `dist/` does |
| A Python change did nothing | the server does not auto-reload. Restart it |
| A Thai character crashes a script | Windows consoles are cp1252. The app calls `enable_utf8()`; ad-hoc scripts must too, or write to a UTF-8 file |
| 42 tests skipped | Postgres is not running. Expected — §3 if you want them |
| `alembic upgrade head` does nothing, but the app says *"relation does not exist"* | the version table is stamped with nothing behind it: `uv run alembic stamp base && uv run alembic upgrade head` |
| `Could not locate cudnn_ops64_9.dll` | CTranslate2 cannot find the cuDNN torch already ships. The adapter adds `torch/lib` to the DLL search path itself, so this should not happen — if it does, torch is missing or is the CPU wheel (§7) |
| The transcript contains insurance jargon the caller never said | `B14`. The model can echo `config/stt_vocabulary.yaml` back on a non-speech segment. `echoes_the_prompt()` is supposed to catch it; if something got through, that guard is the place to look — not the model |
| Transcription is very slow and the text looks invented | Almost certainly near-silence reaching the model (`B14`: 8.6 s for one second of digital silence). Check the level gate in `TranscriptionStream._transcribe` and the VAD threshold |
| `torch.cuda.is_available()` is `False` | you have the CPU wheel. The version string will lack `+cu128`. `uv sync --reinstall --extra ml` (§7) |
| A queue is closed and a call goes to voicemail | queue hours are real. Pass `ignore_hours` on the demo endpoint, or check `config/queue_hours.yaml` |
| `render_diagrams.py` cannot find a browser | set `PUPPETEER_EXECUTABLE_PATH` to an installed Chrome or Edge |

---

## Docs

`docs/` is the project's permanent memory. Start at `NEXT_SESSION.md`.

| File | Contains |
|---|---|
| [**`docs/FOR_THE_TEAM.md`**](docs/FOR_THE_TEAM.md) | **Start here if you are new.** Git and GitHub from zero, and how to change how the screens look without breaking how they work |
| [`docs/NEXT_SESSION.md`](docs/NEXT_SESSION.md) | Live state, next steps, open questions, landmines |
| [`docs/PLAN.md`](docs/PLAN.md) | Build phases P0–P8, exit criteria, risks, team tracks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, call lifecycle, data flows, latency budget, degradation |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Both stores and the bank-data swap strategy |
| [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) | Ports & adapters, models, libraries, the full config surface |
| [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) | Stack, folder map, feature status, constraints |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why it is built this way (`D1`…) |
| [`docs/BUG_HISTORY.md`](docs/BUG_HISTORY.md) | Solved bugs (`B1`…) — search here first |
| [`docs/diagrams/`](docs/diagrams/README.md) | 69 diagrams across 13 themed pages; 14 generated from source |
| [`docs/explanations/`](docs/explanations/) | One plain-language walkthrough per phase |
| [`docs/reading/`](docs/reading/) | Interactive pages — open in a browser, no build step |

---

## Keeping this file honest

**If you change how anything is set up or run, update this README in the same commit.** A new
dependency, a new entry point, a new service that has to be running, a new build step, a command
that changes its flags — all of it belongs here, because this is the first file anyone reads and a
setup guide that is wrong costs more than one that is missing.

The rule of thumb: if a teammate on a fresh clone would get stuck, it goes in a section above.

---

## Credits

Thai speech recognition ships on **Typhoon ASR** by SCB 10X — <https://huggingface.co/scb10x> — a
Thai NeMo transducer, chosen on measured latency and accuracy over the alternatives (`D104`; the
bake-off table is in `docs/NEXT_SESSION.md`).

**Thonburian Whisper** by Looloo Technology and the Biomedical and Data Lab, Mahidol University —
<https://github.com/biodatlab/thonburian-whisper> (ICNLSP 2024), fine-tuned from OpenAI Whisper —
remains a supported engine and the documented fallback for a machine where NeMo will not install
(`STT_ENGINE=thonburian_ct2` / `thonburian_hf`). It is also the engine every accuracy number in this
project was first measured against.

Voice activity detection uses **Silero VAD**. Summarisation runs on **Claude** (Anthropic) or any
OpenAI-compatible endpoint, and on neither by default — the shipped provider is rule-based.
