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

**P0 · P1 · P1b · P2a · P2b · P2c complete. P3 done except the audio.**

The identity ladder, the keypad IVR, the intake offer, the context assembler, the brief builder,
the public API, the matching engine, agent presence, the offer handshake, the React workstation and
the database layer are all real services doing real work. Only the *edges* are still fakes: the
phone, the speech model, the LLM, the bank's data and the agent roster.

A caller keys their way to the right queue, is offered the pre-call recording and either takes it,
refuses it or ignores it — and all three answers reach the same agent, because the menu settled the
routing before any of it ran. They are deliberately **not** told a position in the queue: the matcher
re-solves the whole caller × agent matrix every tick, so there is no arrival order to report
(`docs/DECISIONS.md` `D91`). What is missing is the audio itself: no media gateway, no
voice activity detection and no speech-to-text worker, so the transcript socket
(`IntakeService.on_turn`) is real and nothing feeds it.

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

Then open **<http://127.0.0.1:8000/sim>**. The agent workstation at `/workstation` needs one extra
build step — see [§2](#2-the-agent-workstation-react-bundle).

---

## Prerequisites

| Tool | Needed for | Required? |
|---|---|---|
| **[uv](https://docs.astral.sh/uv/)** | everything Python. Installs its own Python 3.11 | **Yes** |
| **Node 18+** | building the agent workstation bundle, **once** | Only for `/workstation` |
| **Docker** | Postgres, so a shift survives a restart | Optional |
| **mermaid-cli** | re-rendering the docs diagrams to SVG | Only if you edit diagrams |
| **An NVIDIA GPU** | local Thai speech-to-text | Not yet — P3 step 4 |

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

**You do not need a `.env`** — every setting has a working default. Copy it when you want to change
one. See [Configuration](#configuration).

<details>
<summary>Which extras exist, and when you need them</summary>

| Command | Gets you | Enough for |
|---|---|---|
| `uv sync` | runtime only | the scenario runner, the matching simulator, the doc scripts |
| `uv sync --extra web` | **+ FastAPI / uvicorn / websockets** | the API, the simulator, the workstation, **the full test suite** |
| `uv sync --extra ml` | **+ torch (CUDA) / transformers / faster-whisper / silero-vad** | local Thai STT. ~3 GB, needs an NVIDIA GPU, needed by nothing else (§6) |

Three test files import FastAPI, so **`--extra web` is the one to use** unless you have a reason not
to. The `ml` extra is deliberately still commented out in `pyproject.toml`: it is several gigabytes
and nothing uses it yet.
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

The compose file also defines **redis**, **minio** and **pgweb**. None of them is used yet — they
are there so the day a phase needs one is not also the day someone learns Docker networking. Name
the service explicitly (`up -d postgres`) rather than starting all four.

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

### 4. Generated mock bank data — *optional*

Three hand-authored customers ship in `mock/bank_core/fixtures/` and are used by default. Generate
a larger set when you want load or matching realism:

```bash
uv run python mock/bank_core/generate.py --seed 42 --customers 2000
```

Writes to `mock/bank_core/generated/` (gitignored). Deterministic: the same seed produces
byte-identical files.

### 5. Diagram rendering — *only if you edit the docs*

```bash
npm install -g @mermaid-js/mermaid-cli
```

Or set `MMDC=/path/to/mmdc`. The renderer drives a headless browser; if it cannot find one, set
`PUPPETEER_EXECUTABLE_PATH` to an installed Chrome or Edge rather than downloading a second Chromium.

### 6. Speech-to-text — *optional, and it needs an NVIDIA GPU to be worth installing*

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

Renders every `.mmd` to an SVG. Needs mermaid-cli (§5); `--check` verifies freshness without
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

uv sync --extra ml ──▶ STT_ENGINE=thonburian  (needs an NVIDIA GPU; nothing else needs this)
```

Nothing in the left column depends on anything in the right.

---

## Configuration

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
| `torch.cuda.is_available()` is `False` | you have the CPU wheel. The version string will lack `+cu128`. `uv sync --reinstall --extra ml` (§6) |
| A queue is closed and a call goes to voicemail | queue hours are real. Pass `ignore_hours` on the demo endpoint, or check `config/queue_hours.yaml` |
| `render_diagrams.py` cannot find a browser | set `PUPPETEER_EXECUTABLE_PATH` to an installed Chrome or Edge |

---

## Docs

`docs/` is the project's permanent memory. Start at `NEXT_SESSION.md`.

| File | Contains |
|---|---|
| [`docs/NEXT_SESSION.md`](docs/NEXT_SESSION.md) | Live state, next steps, open questions, landmines |
| [`docs/PLAN.md`](docs/PLAN.md) | Build phases P0–P8, exit criteria, risks, team tracks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, call lifecycle, data flows, latency budget, degradation |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Both stores and the bank-data swap strategy |
| [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) | Ports & adapters, models, libraries, the full config surface |
| [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) | Stack, folder map, feature status, constraints |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why it is built this way (`D1`…) |
| [`docs/BUG_HISTORY.md`](docs/BUG_HISTORY.md) | Solved bugs (`B1`…) — search here first |
| [`docs/diagrams/`](docs/diagrams/README.md) | 61 diagrams across 12 themed pages; 14 generated from source |
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

Thai speech recognition uses **Thonburian Whisper** by Looloo Technology and the Biomedical and Data
Lab, Mahidol University — <https://github.com/biodatlab/thonburian-whisper> (ICNLSP 2024), fine-tuned
from OpenAI Whisper. Voice activity detection uses **Silero VAD**.
