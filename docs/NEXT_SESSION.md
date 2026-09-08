# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-09-08._

---

## If you have just been compacted, read this first

_Rewritten 2026-09-07, after `D117`–`D120`. Everything settled before this block is in the
sections below; what follows is what a fresh session needs and nothing it does not._

### ⚠️ FIRST: the notes arrived, and they are being worked through

The user gave a long set of notes on 2026-09-08. What they produced so far is `D121` (the
tool rail's screen, and the tier gate moving from KIND to PURPOSE), `B32`, `B33`, and a
corrected README credit. **The rest of the list is not done and is written down below** —
see *"The 2026-09-08 notes, and what each became"*. Read that before picking anything up.

The pattern held again: **the user's first two sentences of feedback contained one thing
that could not be reached at all (`D120`'s rail had no UI) and one rule that was wrong in
both directions (the kind-based gate).** Neither was visible to 830 passing tests.

### ⚠️ ZEROTH: two files carry facts you must not re-derive

1. **`docs/MARKET_FACTS.md`** — every figure the orientation supplied, with its source
   slide. Broker channel share, the renewal case, market sizes, real insurer names, the
   competitor list, and the logistics. **Do not invent a number that belongs here.**
2. **`docs/reading/the_assist_rail.html`** — what the 7 September work actually does, in
   plain language and drawn. **This is the shared vocabulary with the user now**; read it
   before explaining any of it to them again.

Three constraints from `MARKET_FACTS` that shape every plan:

- **The pitch is 5 MINUTES plus 5 of Q&A**, twelve teams, file submitted 11:00–12:00 on
  day 2 (13 Sep). This rules out a live multi-step walkthrough and is the single most
  important planning fact.
- **The Gen Z statement on insight p.35/48 is a WORKSHOP EXERCISE, not the brief.** The
  real challenge is open — right coverage, right customer, right time, right channel — and
  the team is **already a finalist on the submitted idea**. Refinement, **not a pivot**. A
  voice-first answer is fine.
- **Judging is Impact · Feasibility · Creativity · User insight.**

⚠️ Half those slides are images with no text layer, so `pypdf` returns empty pages. Render
with PyMuPDF at ~110 dpi and read the PNGs. An empty extraction is not an empty slide.

### The 2026-09-08 notes, and what each became

| the note | verdict | state |
|---|---|---|
| README credits still say Thonburian only | correct | **DONE.** Typhoon credited as the shipped engine, Thonburian kept as supported + the fallback, and named as the engine every accuracy figure was first measured on |
| *"there's no tool rail on the workstation at all"* | correct, and it was `B24`'s family | **DONE** (`D121`) |
| *"forms needs to be signed in is only half true — look at the purpose, not the type"* | correct, and wrong in BOTH directions | **DONE** (`D121`) |
| *"shouldn't the comparison data come from the company's database, not a yaml?"* | **correct, and it was a category error about to be made** | **NOT STARTED.** See below — this reshapes Track B |
| *"the tool rail doesn't unlock when i open the link"* | correct — a deadlock I built | **DONE** (`B35`) |
| *"why is there a เข้าสู่ระบบ on the link page?"* | correct; it read as mandatory | **DONE** (`D121` amendment) + `D122` moves the verified tier to the app, where it belongs |
| *"the contact options are weird — they already have the plan"* | correct, and `D48` was the cause | **DONE** (`D122`) |
| *"/sim is completely detached from the call queue"* | correct, and the app branch was **broken** | **DONE** (`D122`, `B36`) |
| *"the workstation gets into a stuck state if i don't touch it"* | correct, and it was the worst bug yet | **DONE** (`B34`) |
| *"end call doesn't end it on /sim and /assist"* | correct — and THREE dead methods behind it | **DONE** (`B37`) |
| tool dialog: no close button, two columns, scrolling groups | all three adopted | **DONE** (`D122` amendment) |
| `/sim` should look like an actual call screen | correct | **DONE** — call card, timer, mute/speaker/วางสาย, and the customer can now hang up at all |
| `/sim` instrumentation should be hide-able | correct | **DONE** |
| *"it keeps calling GET /v1/app/assist after the call ended"* | correct | **DONE** — polling stops once the call ends |
| free-text tool idea | good, and it is `D44` with a keyboard | **DONE** (`form.free_text`) |
| *"it feels like a hackathon without AI"* | half a misreading, half a real gap | **NOT STARTED.** See below |
| transfer button beside วางสาย, two tabs (internal / other company) | correct, and the second tab is new | **NOT STARTED.** Extends `D63` |
| register the customer via a pushed form, or push the app download? | their own second instinct is right | **DECIDED** in `D121`: push the download, registration belongs to the bank's app |
| where to put an OpenAI key; try several fast models | answered | **NOT STARTED.** `scripts/compare_llm.py` still does not exist |
| *"is it all still swappable?"* | yes, with two named exceptions | products data (fixed by the note above) and `NotifierPort`, which does not exist |

**The products correction, in one paragraph, because it changes Track B's shape.** A plan
catalogue is **live data owned by someone else**, not domain taxonomy — it changes without
us, and `config/` is *not reachable by the hackathon-day data swap*. So a `config/products.yaml`
would have put the one thing the comparison feature reads outside the seam built for
exactly this. It belongs behind `CoreDataProvider`, which **already has `get_product()` and
already has `mock/bank_core/fixtures/products.json`**. Track B's real work: add
`list_products(line=...)` to the port and every adapter + the contract suite, give `Product`
an `insurer` and comparable attributes as typed `Coverage` rows (typed precisely so a model
cannot invent a figure into one, `D16`), and refill the fixtures with the real carriers from
`MARKET_FACTS` §8 — they are currently all `KS-` codes with no carrier, i.e. still
insurer-shaped in the one place `D117` did not reach.

**The AI question, and the part of it that is a misreading.** *"Ship the container on
`STT_ENGINE=scripted`"* is about **one** model — the Thai ASR, which needs CUDA. The LLM is
an API call and runs fine in a plain container. The demo machine is ours and has the GPU, so
the honest split is: real engine on our box for the pitch and the recorded video, container
as the "any judge can run this" story. Nobody has to watch the scripted engine.

The real gap is that the AI already here is **invisible in the story**: a Thai ASR chosen on
a measured 20-call bake-off, an LLM writing the broker's brief, and a Hungarian solver. The
user's better idea is predictive models over the bank's own data, and they map one-to-one
onto the four ที่ใช่ — lapse propensity (right time), gap/next-best-product (right coverage),
life-event trigger (right customer), channel preference (right channel). **Agreed scope: ONE
model, lapse propensity, trained on a real public dataset (Kaggle/UCI/OpenML), never on
synthetic data** — the user was explicit about this and they are right, because "we
generated the data and then learned it" is circular and a judge can dismantle it in one
question. ⚠️ **Verify the dataset actually downloads before building anything on it.**

### Where the machine actually is

**P0 · P1 · P1b · P2a · P2b · P2c · P3 complete. P4 is PARTLY done, and one thing landed
that is in no phase at all.** The phase letters stopped describing the work on 2026-09-07 —
read this section, not a phase number.

A caller reaches the right **broker** queue through a real menu, is offered the pre-call
recording, is transcribed by an engine chosen on measurements, and their words are on the
broker's screen the moment Accept is pressed. Their audio is encrypted in object storage
or nowhere. The broker sees **which carrier** underwrote the policy, and on a claim call a
banner saying the call ends with the insurer. An **AI summary** upgrades the screen a few
seconds later, or silently does not. And the broker can **bind the call to the customer's
phone** and push a comparison or a form onto it.

### The four decisions of 2026-09-07, one line each

| | what it is | the one thing to know |
|---|---|---|
| `D117` | the domain is a **broker's** | claims are **handoffs**, renewal is its own desk, `*.advice.compare` is the mandate. Config + one field; **no service logic changed** |
| `D118` | playbooks in config | closes `Q19`. Guarded **both ways** at startup — a missing playbook and an unreachable one both refuse to boot |
| `D119` | the LLM actually runs | `build_llm` + two adapters. **Six paths end in "return None"** and that is the design. Measured: **4.5 s, $0.0085** |
| `D120` | the customer's paired screen | a link, not a code. **Guest sees anything true for anybody; personal content needs a sign-in**, and the refusal names the reason |

**Where they are written down**, in the order to read them:
`docs/reading/the_assist_rail.html` → `docs/diagrams/13_broker_and_assist.md` (five new diagrams)
→ `docs/explanations/P4_broker_and_assist.md` (**§5 is copy-pasteable commands**).

### What is NOT built, precisely

1. **The comparison DATA.** `D120` built the transport; the table renders and reaches the
   phone. What fills it — `products.yaml`, gap analysis against what the customer holds,
   ranking on real attributes with the model writing only the reason sentence — is
   **Track B and is not started**.
2. **Docker packaging** (Track E), and the rehearsal. ⚠️ Ship the container on
   `STT_ENGINE=scripted`: a plain container cannot reach the GPU without host setup that
   varies by machine, which is what fails at a venue.
3. **Sending the assist link.** `NotifierPort` is P5. The link comes back for the broker to
   read out.
4. **Signature, OCR, document upload.** `document_request` records the intent and stubs it.
5. **A golden set for the LLM.** Without one, "85% intent accuracy" is a claim, not a
   measurement — and it is a named P4 exit criterion.
6. **Everything P3 never had:** a real TTS voice (`Q22`), the agent's own leg (`D26`, P6),
   P7's real key management, a recording player on the screen.

### `Q24` — answered, and what it still gates

Answered by the **brief itself** (`D116`): *"ข้อมูลสุขภาพเป็น sensitive data ต้องขอ consent
แยก"*. The plan: name the health scope in the offer wording on health lines **and** gate
extraction in code so health entities cannot be pulled without it.

⚠️ **Neither half is built.** It is not blocking anything today because nothing extracts
entities — and it becomes blocking at **the first line of an entity extractor**, which is
the next P4 item after Track B. `D116` also moved the gate: it is not about whether we may
*hold* an affiliate-sourced field, it is about whether we may **recommend** from it.

### How to see the whole thing working

```bash
uv run python scripts/make_demo_audio.py    # a fresh clone has NO audio: *.wav is gitignored
uv run python -m readycall.entrypoints.api
#   /workstation, sign in as A006 (health), press พร้อมรับสาย, then:
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" \
  -d '{"intent_code":"health.claim.notify","caller_number":"0812345678","intake_keys":["2"],"ignore_hours":true}'
#   Accept: the carrier name, the handoff banner, and three L1-gated actions.
```

Then the paired screen — `explanations/P4_broker_and_assist.md` §5.3 has the full sequence.
**The step worth doing is pushing a form to a screen that has only tapped a link** and
watching it refuse with the reason.

### Before changing anything, know these

- **`B25`** — the availability filter is the only thing keeping an unavailable agent from
  being rung, and its absence was invisible for weeks.
- **`B28`** — since `D113` one agent can hold **two assignments for one call**. Anything
  asking "this agent's assignment for this call" must filter to `PENDING`/`ACCEPTED`.
- **`B30`** — a fixture with **one** of something tests nothing about choosing.
- **`B31`** — `uv sync` **prunes**. Name every extra in one command, or you will silently
  remove the GPU stack and expose latent CI failures.
- **`tests/integration/test_floor_under_load.py`** — seeded random walks over the real API
  asserting **invariants**. Add a scenario whenever a fault is found by clicking; that is
  now seven for seven.

### The five slices of 2026-09-06, in one line each

| | what it is | the one thing to know |
|---|---|---|
| `D110` | the encrypted recording | **one** wrapper does the crypto for every backend; consent is checked at the SEAL, not at the open; the upload never touches the accept path |
| `D111` | `_degradation()` answers for real | claims `stt_unavailable` only when the engine failed **and** nothing was transcribed — a quiet caller must never be blamed on the engine |
| `D112` | the decode timeout | needed `D2`'s worker process, because `wait_for` cannot kill a thread. The test asserts **the process is gone** |
| `D113` | `Q31` closed | exclusions cleared when everyone declines; cap is config and **0 = forever** is what ships; the card says the round and whether this agent is the last |
| `D114` | the durable transcript | a **fourth** subscriber, its own, so a storage failure cannot blank the screen |

**All five are explained in plain language for the user** at
`docs/reading/the_recording.html` (published:
https://claude.ai/code/artifact/c51ef926-0a13-45b7-b59f-6731be83255c). Read it before
explaining any of this to them again — it is the shared vocabulary now.

### Before changing the matcher, the workstation or the audio path

- **`B25`** — the availability filter is the only thing keeping an unavailable agent from
  being rung, and its absence was invisible for weeks.
- **`D113`** — `excluded_agents()` is now *who has declined in this round*, not a permanent
  record. The stress suite's invariant still holds; read why before changing it.
- **`tests/integration/test_floor_under_load.py`** — seeded random walks over the real API
  asserting **invariants**. **Add a scenario here whenever a fault is found by clicking or
  by reasoning about the system**; that is now six for six.
- **`D110`'s landmine list** — `build_blob_storage` is the only place a store may be built,
  and that is what makes `localfs` safe rather than forbidden.

### The engine, in one table

Paced, 20 real Thai call-centre calls, same detector and guards throughout (`D104`):

| | Thonburian fp16 | CT2 int8 + hint | **Typhoon** |
|---|---|---|---|
| p95 utterance-end -> turn | 19.5 s / 58.7 s worst | 1.68 s / 2.53 s | **0.19 s / 0.28 s** |
| inside the 1.5 s budget | 0 of 12 | 7 of 20 | **20 of 20** |
| CER **mean** | **0.109** | 0.128 | 0.133 |

**Ship Typhoon** (`STT_ENGINE=typhoon`, needs the `asr` extra). **CT2 is the fallback** for
a box where NeMo will not install. `scripted` is the default and is the stage-safe path —
its lines come from `config/demo_transcript.yaml` since `D107`.

### The bugs that shaped this codebase, and the one sentence each is worth

**Not one of them was found by a test.** Most were found by the user driving a running
workstation; the rest by tracing the call graph by hand. This table is why the landmine
list further down is as long as it is.

| | what it was | the lesson |
|---|---|---|
| `B24` | `TranscriptionService.open()` was called by nothing, so the running system had **never transcribed anything**; and nothing drained the event bus, so any subscriber was unreached | **follow the call graph from something a user does.** "It has tests" and "it runs" are different claims |
| `B25` | the matcher never asked whether an agent could take a call — `is_available()` was dead code. Unready agents were rung; a signed-out agent collected every caller | a **dead method is a claim nobody checked**; and two places answering one question will disagree in silence |
| `B26` | the wait on screen was frozen: `B12` unfroze it for the matcher and never asked who else read it | **a fix for one consumer is not a fix** |
| `B27` | the same wait was still sent as a *number* so nothing made it move; and `lastSeq` is per tab while `seq` is per agent, so a second agent in one tab discarded its own offer | **a duration on a screen is a clock and needs an anchor**; a per-session counter needs an owner for the transition |
| `B21`–`B23` | the digit guard ate phone numbers; `close()` freed the object not the GPU; two engines could not be selected by config | measure it, watch `nvidia-smi`, and read the wiring while writing the instructions for it |

Two methodology traps that each cost a published number: **rank on the CER mean, never the
median** (unstable at n=20), and **the test set moved the headline by 1.8x**.

## Where things stand right now

**P0 · P1 · P1b · P2a · P2b · P2c · P3 complete. P4 PARTLY DONE** — the LLM seam is built
and measured (`D119`), playbooks are in config (`D118`), and the domain is a broker's
(`D117`). Plus one thing in no phase at all: the customer's **paired screen** (`D120`).
`D30`'s bake-off is closed (`D104`), the live transcript is on the screen (`D106`), and the
encrypted recording is in object storage (`D110`). The system knows who is calling and how much to believe it, why they are
calling, everything we hold about them assembled before the phone is answered, which agent
should take it and why, the desk rings and a human accepts with the screen already right —
the caller keys their own way to the right queue through a real menu hearing real
(pre-rendered) Thai, is offered the pre-call recording and takes it or refuses it or ignores
it, all three reaching the same agent — **and what they said while they were waiting is on
that agent's screen the moment they press Accept**, in order, each sentence carrying the
moment in the recording it was said — **and if they consented, their audio is in object
storage encrypted, with the key ref and the retention date on an `audio_recordings` row.**
If they declined, it is nowhere.

Verified **2026-09-08**: **856 tests** — 844 pass + 12 skipped. `ruff check` +
`ruff format --check` clean over 218 files, `mypy --strict` clean over 151, all scenarios
replay, 69/69 diagrams current, prompt pack fresh, `audit_docs.py` clean on the live files.
**And by driving the workstation in a browser**: link minted from the panel, tool box
opened (11 tools, exactly the 5 personal ones locked), a blank quote form pushed to a
GUEST screen at 375 px, filled in, submitted, and read back on the broker's screen; then
sign-in, all 5 unlocked, and a claim form arriving prefilled with `HL-2024-000811` and
`เมืองไทยประกันภัย`. **And on a running server**: a broker call routes
to `q_claims` carrying its carrier and handoff banner, a real `claude-sonnet-5` summary
came back in 4.5 s for $0.0085, and a form pushed to a customer's phone came back filled. **And verified against a running server with a
real MinIO container**: the bucket holds `RCE1`-framed ciphertext, the right master key
returns the original 622,124-byte WAV, a wrong one refuses, and the caller who pressed 2
left nothing behind.

### The bugs the user found by using the thing, in one place

Almost every fault in this list came from somebody driving the screen and reporting what
looked wrong — not from the suite. The pattern is worth knowing before reading any of it,
because it is now **twenty** and they rhyme:

1. **`B6` (2026-08-24)** — six faults, **three of which were decisions the docs already
   contained**. The lesson is about reading `.mmd` sources, not about React.
2. **`B7`** — RONA, re-matching and heartbeat expiry were all written, all correct, and
   **called by nothing**. An ignored offer stranded the agent in `OFFERING` for the shift.
3. **`B8`** — the clock-skew fix from `D68` **froze every timer it was meant to correct**,
   because the correction was sampled in the same tick as the value it corrected.
4. **`B9`** — a bare `models/` in `.gitignore` meant the **entire ORM package was never
   committed**. A whole phase looked landed; a fresh clone had no tables.
5. **`B12` (2026-09-01)** — the matcher's anti-starvation was **fed a constant**. `waiting_s`
   was frozen at admit time, so no caller's urgency ever grew. Found by answering a question
   about a *prompt*, not by looking for a bug.

6. **`B24` (2026-09-05)** — `TranscriptionService.open()` was called by nothing, so the
   audio path had **never transcribed anything** in the running system; and nothing drained
   the event bus, so any subscriber would have been unreached.
7. **`B25`** — the matcher never checked whether an agent could take a call.
   `AgentPresence.is_available()` was **dead code**, so an unready agent was rung and a
   signed-out one collected every caller in the queue.
8. **`B26`** — the wait on screen was frozen. `B12` had unfrozen it *for the matcher* and
   nobody asked who else read it.
9. **`B27`** — the same wait was sent as a number rather than an anchor, so nothing made it
   move; and `lastSeq` is per tab while `seq` is per agent, so the second agent in one tab
   discarded its own offer.
10. **`Q31` (2026-09-06)** — not a `B` entry because it was *designed* in rather than
   introduced, but it belongs in this list: a caller every qualified agent declined waited
   for the life of the shift, with no error and no failing test, while the queue showed
   them as being handled. Found by the user asking what happens when everyone says no.
   Closed by `D113`.
12. **`B28` (2026-09-06)** — and this one is the sharpest of the lot, because it was found
   by using the feature shipped **that same day**. `D113` made a caller the whole floor
   declined come round again; the user declined, took the call on round 2, and could not
   end it. One agent now holds two assignments for one call, and every lookup that said
   "the first one" had been silently asserting there could only be one. It also let an
   agent who declined keep acting on a caller somebody else took.
13. **`B30` (2026-09-07)** — the cold-call path knew the product line and threw it away.
   Invisible since P1b because the demo customer held exactly ONE policy, so the "only one
   they have" fallback produced correct output for the wrong reason. Found by giving them
   a portfolio, not by a test. **A fixture with one of something tests nothing about
   choosing.**
17. **`B37` (2026-09-08)** — "end call doesn't end it on the customer's screen", and behind
   it **three methods called by nothing**: `AssistService.close()`, `AssistService.sweep()`
   and the `WRAP_UP` state counted as live. Two of the three were written in the same file
   on the same day as the feature that needed them — `B7`'s family arriving *within* one
   change rather than across months.
16. **`B34`/`B35`/`B36` (2026-09-08)** — the second wave from the same session, and `B34`
   is the most damaging fault in the project's history: an agent **on a live call** was
   signed out for not heartbeating, because browsers throttle a hidden tab's timers — and
   nothing picked the call up, so it became unendable and resurfaced after every later
   call. It would have fired on stage. `B35` was a deadlock I built into `D121` the same
   day. `B36` was an entry path that had been **broken since P1b** and that nothing had
   ever executed.
15. **`B32`/`B33` (2026-09-08)** — both found in ten minutes of *using* the tool rail, and
   neither visible to 856 tests. The catalogue was fetched on mount, before sign-in, so it
   401'd into a defensive `catch` and the rail was empty for the shift. And the dialog's
   2-second poll was rebuilt every render — the workstation re-renders every second — so
   it never fired once, which looked exactly like the server not returning the customer's
   answer.
14. **`B31` (2026-09-07)** — `uv sync` prunes, so installing the LLM extra removed the GPU
   stack and instantly exposed five suite errors and five mypy errors that had been latent
   for weeks and **would have been red on CI**. A deferred import relocates the failure,
   and every `except ImportError` around the import is then guarding an empty room.
11. **Two caught in the same session before shipping**, both by writing the test that
   would notice: `open_leg` replacing a leg and silently unsubscribing whoever opened
   first (`B24`'s shape, with two consumers now), and the STT worker's async stdin read
   that **works on Linux CI and fails on Windows** with a handle error — a worker that
   would have passed everywhere except the demo laptop.

All of them are the same family as `B3` and `B4`: *a confident, plausible, wrong result that
no test could see.* When something looks fine, check that it is actually running — and,
since `B9`, check that it is actually **committed**: every other verification in this
project is a statement about the working tree, not about the repository. `B12` adds a rung
below that: check that what is running is being handed **live** arguments.

**The 2026-09-01 session is worth reading as a pattern.** Three of its four changes came from
the user reasoning about the *system* rather than the code — "why tell them a queue number if
the matcher can reorder?", "does the enrichment influence who gets the call?", "why is repeat
on 9 now that 0 is free?". Each was right, and one of them uncovered `B12`. When the user
questions a behaviour, read the code that implements it end to end before answering.

```bash
uv sync --extra web
# Optional: the database path. Everything below works without it.
docker compose -f infra/docker-compose.yml up -d postgres && uv run alembic upgrade head
#   then STORAGE_BACKEND=postgres to make a restart survivable (D78)
uv run pytest -q
uv run python scripts/run_scenario.py tests/scenarios/anonymous_declined.yaml --quiet
uv run python scripts/run_matching.py --calls 25 --compare
uv run python scripts/build_prompts.py --list      # every line the caller can hear
cd apps/workstation && npm install && npm run build && cd ../..   # once; node not needed to RUN
uv run python -m readycall.entrypoints.api
#   http://127.0.0.1:8000/sim          the customer
#   http://127.0.0.1:8000/workstation  the agent
```

Everything still runs **in memory by default** — no services, no keys, no GPU. Postgres is
real, wired and optional: `STORAGE_BACKEND=postgres` switches one factory line
(`build_storage`, `D75`/`D78`) and the shift then survives a restart.

## What exists (cumulative)

**P0 — foundations.** Config with startup coherence checks · structured logging · injected
`Clock` + swappable ids (`D35`) · UTF-8 console (`B1`) · **15** call states, 19 event types
· 8 ports each with a fake (10 now: `vad` from `D96`, `keyring` from `D110`) · call state machine + orchestrator (single writer) · in-memory
event bus.

**P1 — context.** `domainpack.py` · `services/identity/` (the L0–L3 ladder) ·
`services/context/assembler.py` (parallel fan-out, per-field provenance, frozen snapshot) ·
`services/brief/builder.py` · `adapters/core_data/caching.py` (TTL + serve-stale + breaker).

**P1b — the HTTP layer.** `POST /v1/calls/intents` · app context events · contact reasons ·
**customer simulator** at `/sim`, one HTML file, no build step (`D47`). Identity comes from
a `SessionResolver`, never the request body (`D4`).

**P2a — matching.** 15-agent roster · tunable weights with startup validation · hard
filters in two groups, **capability** (skill, **graded** language) and **availability**
(`offline`, `not_ready`, `busy`, `at_capacity`), plus `already_offered` which is about the
caller rather than the agent — the availability group added by `B25`, 2026-09-05 · fit ×
urgency · **our own Hungarian solver** (`D49`) · guard rails · a `MatchingDecision` per
call **including non-assignments**, saying **which of four** unplaced reasons applies
(`D50`, `D108`).

**P2b — the workstation.** `services/agents/` (presence, the offer handshake, dispatch) ·
`services/queues/hours.py` + `queue_hours.yaml` · `services/capture/keypad.py` (`D44`) ·
`services/identity/attestation.py` (`D42`) · `api/realtime.py` (per-agent sequencing,
replay-on-reconnect) · `api/routers/agent.py` · **React workstation** at `/workstation`
(`D32`) · `POST /v1/demo/calls` standing in for telephony.

**P2c — persistence (done).** SQLAlchemy 2.0 async + Alembic (URL from `Settings`) ·
**9 tables** · Postgres stores behind the P0 interfaces, returning **domain models**
(`D77`) · **one contract suite across in-memory / SQLite / Postgres** (`D75`) ·
**write-through with an in-memory projection** (`D78`) — services keep their working set,
write durably, restore at startup · presence, the waiting pool and the live identity are
**derived, never stored twice** (`D76`, `D78`) · `Container` reads `STORAGE_BACKEND` ·
a restart is proved by **ending a process**, in pytest and again with real uvicorn.

**P3 (steps 1–3) — the line.** `config/voice_prompts.yaml`: **27 prompts**, declared slots,
and a `flow:` table mapping **15 roles** to ids so `services/` holds no prompt literals
(`D28`) · the guard that every referenced id resolves, **both directions**, as a startup
gate *and* a test · `scripts/build_prompts.py` hash-cached by (text, voice, engine), deduped
by rendered text to **54 clips**, committed manifest asserted fresh · **`services/ivr/`** —
greeting + notice → product menu (skipped when the DID or app said) → reason menu → queue,
with `0` the only reserved key (`D86`, `D90`), a wrong press never a strike (`D82`), and every
failure path ending in a queue rather than a hang-up · personalised ordering with its
evidence · **a menu is composed, not one clip** (`D80`) · **`menu_path` is canonical whatever
was pressed** (`D81`) · both fake IVR walks retired — `run_scenario.py`'s `# P1:` and
`demo.py`'s `# P2b:`.

**P3 step 4a — the offer (`D88`, `D91`).** `services/intake/`: **`hold.py`**, a second no-I/O
machine for everything *below* the "queue is now known" line — the press-1/press-2
offer, one re-offer, the recording and its four endings · **`strategy.py`**, the `D10` seam,
which takes **`TranscriptTurn`s rather than a media stream** so the whole thing is buildable
and testable with no GPU, no audio and no telephony · **`passive.py`**, `PassiveRecordIntake`
with an idempotent `finalize()` because the accept and the hang-up genuinely race ·
**`service.py`**, the driver — which **returns as soon as the caller answers** and leaves the
intake live, because `D21`'s offer window IS the grace period and the accept endpoint is what
ends it · one keypress grants both consent scopes and **a refusal is recorded, not nothing** ·
`reoffer_due()` runs in `sweep_once` (`B7`, pre-empted) · **no queue position is spoken**
(`D91`, reversing `D89` — there is no line to have a position in) · the scenario runner's
intake stand-in is retired · **`B12`**: a waiting caller's urgency never grew, because the
pool fed the matcher a frozen `waiting_s`.

**P3 step 4a review pass (`D90`–`D92`, `B12`), 2026-09-01.** `0` is the repeat key and `9`
is free again (`D90`) — and moving it exposed a prompt spelling a config value into its own
Thai, which nothing checked · **no queue position or wait estimate is ever spoken** (`D91`,
reversing `D89` two days later): there is no line to have a position in, and the pack lost
both prompts and both roles so it cannot come back by config · **speech may change WHO
answers and HOW SOON, never WHICH QUEUE** (`D92`) — the boundary `D23` implied and never
stated, written before P4 can cross it · **`B12`**: `WaitingCall` is frozen and `tick()`
rebuilt it naming only `excluded_agent_ids`, so every caller's `waiting_s` stayed at its
admit value and all of `D22`'s anti-starvation ran against a constant · **`D93`/`B13`**: the
wait ceiling lived inside a guard that only runs for calls the solver already placed, so it
could never rescue a starved caller; it is a pre-pass before the solver now.

**P3 review pass (`D82`–`D84`, `D86`, `D87`, `B10`, `B11`).** Driven by the user working the screen and
the menu. **A wrong keypress is never a strike** — no attempt limit, and `menu.invalid` now
speaks both escape keys, because without a ceiling "try again" stops being survivable advice
(`D82`) · **`0` walks the same queue ladder as everything else** (`D83`); it used to
short-circuit to the DID default and throw away a chosen product line · **the pre-call
identity step is gone** (`D84`) — promoting to L3 with no human in the loop is what `D44`
refuses · **`B10`**: ending ACW without saving left the call in `WRAP_UP` for the shift ·
**`B11`**: a saved wrap-up still looked like unfinished work, and had no visible
confirmation · **`D86` reverses `D83` the next day**: the operator key is gone entirely —
every menu already speaks its own "เรื่องอื่นๆ", so `0` was a shortcut to a destination the
menu already offered · **`D87`**: an unfiled wrap-up now goes to a **backlog** the agent
clears later, which is what makes `D45`'s "the person decides when ACW ends" survivable
instead of lossy.

**P2b review pass.** The opening line asks an **open question** below L2 (`D55`) · the
recommended-action chain written down (`D56`) · `other` challenge + a **named** third party
(`D57`) · the agent sees the digits, `mask()` is for logs (`D58`) · `agent_intent` is a
**standing instruction** with `declarable` / `awaiting_declaration` / `intent_reason`
computed server-side (`D59`) · attestation locks with an explicit amend (`D60`) · timers
anchored to server timestamps · the ACW bar lives outside the wrap-up form.

**P2b second review pass (`D61`–`D74`).** The attestation control **re-locks after every
amend** (`D61`) · third party **is** verified and the outcome keeps the log honest (`D65`,
reversing `D62`) · lookups walk a ladder and say **which rung matched**, both eras (`D66`,
`D67`) · the server says what it knows — `wrapup_saved`, clock skew, no client-side
thresholds (`D68`) · the offer card carries a **gated brief preview** (`D69`) · the queue
strip splits **mine / all** (`D70`) · `attestable` is a server decision and a rejection is
**reversible** (`D71`) · the challenge list moved to `config/challenges.yaml` and is served
(`D72`) · **assurance gates what the agent may SAY and DO, not what they may SEE** (`D74`,
reversing `D20`'s display gating).

**P3 step 4b — the audio (`D96`, `B14`).** `media/`: `audio.py` normalises **any** telephony
shape to 16 kHz mono float32 in pure Python (both G.711 laws, because Thailand is A-law and
decoding one as the other produces loud plausible garbage nobody would blame on the codec) ·
`gateway.py` fans frames per **leg** (`D26`) · `sources.py` replays a WAV as if it were a
phone, which is how endpointing gets tuned with no telephony · **`ports/vad.py` is the ninth
port**, returning a probability rather than a decision · `adapters/vad/`: `EnergyVad` (no
dependencies — the CI path *and* the degradation rung) and `SileroVad` (`D9`: from the
installed package, never `torch.hub`) · **`services/transcription/`**: `endpointer.py`, a
third no-I/O machine carrying `D9`'s inherited constants where they can be asserted against
a list of floats; `stream.py`, which never blocks ingestion on the model and keeps turns in
order with one consumer; `service.py`, which finally feeds `IntakeService.on_turn` and
drives both recording timeouts **from the sweep** (`B7`) · `adapters/stt/`: `faster_whisper`
(CTranslate2, `int8_float16`, with the Windows cuDNN discovery handled in the adapter),
`thonburian_hf` (hint applied via `prompt_ids` since `B19`) and `typhoon_asr` (NeMo
transducer, **the shipped engine** since `D104`) · `scripts/bake_off.py` with `busy`, `pad`,
`CERth` and a per-engine aggregate · `scripts/score_endpointer.py` · `scripts/convert_ct2.py`
· **`torch` comes from the CUDA index** (`D95`) — the PyPI wheel is CPU-only and installing
it fails silently.

**P3 step 4c — the engine chosen (`D102` → `D103` → `D104`, `B19`–`B23`), 2026-09-04.**
`D30` closed on measurements over a **balanced** 20-call set (`--mix --seed 7`, `Q30`):
Typhoon meets the 1.5 s budget on **20 of 20** calls at p95 0.19 s where fp16 managed 0 of
12 at 19.5 s · batch dispatch built and measured as a **no-op on this GPU** (`D101`) · the
digit exemption that stopped the guard deleting phone numbers (`B21`) · `close()` freeing
device memory (`B22`) · two engines that could not be selected by config (`B23`, then
Typhoon) · and two methodology corrections that each cost a published number — rank on the
**mean** not the median, and the **test set** alone moved the headline by 1.8x.

**P3 step 4d — the transcript on the screen (`D105`–`D107`, `B24`), 2026-09-05.**
`services/transcription/delivery.py` is `ARCHITECTURE` §14's "Agent Delivery" for
`transcript.turn`: it **holds** a call's turns while nobody owns it — the whole of intake,
which is the product rather than an edge case — and flushes them to whoever **accepts**,
never to whoever was merely offered (`D52`) · every push carries the **complete** transcript
so a dropped message cannot leave a gap (`D68`) · **not gated on assurance**, because it is
the caller's own speech rather than anything looked up, L0 included (`D106`) ·
`pump_once` in `api/app.py` is the bus's own driver at 0.05 s, without which no subscriber
in the process runs at all (`D105`) · `TranscriptionService.open()` is finally **called** —
by the demo endpoint now, by telephony at P5 — and a WAV can be played down the leg so the
whole path runs with no phone (`D107`) · the scripted engine has lines, from config, and
resets per recording through a capability protocol · `TranscriptPanel` draws it under the
brief, and says *why* when it is empty · **`B24`: two services were written, correct,
tested and called by nothing**, which is `B7`'s family with a fourth member.

**P3 step 4e — the encrypted recording (`D110`), 2026-09-06. P3 is now complete.**
`ports/keyring.py` is the **tenth port** and `LocalKeyRing` wraps a data key per object with
a master from `RECORDING_MASTER_KEY` — envelope encryption, the shape a KMS has, so P7's
vault is a second adapter · `adapters/blob_storage/`: `EncryptingBlobStorage` is **the only
cryptography in the repo** and wraps every backend, which is what makes `localfs` allowed
now where the in-memory store's docstring used to refuse it · `S3BlobStorage` covers MinIO
and real S3 (boto3 in a thread, in an `s3` extra, imported at point of use) ·
`build_blob_storage` is the enforcement point and the only place a store should be
constructed · `services/recording/` is a **sink on the gateway**, not part of it: it seals on
Accept and uploads from the sweep, so no object-store round trip ever sits between the agent
answering and the caller hearing them (`D12`) · **consent is checked at the seal**, so a
caller who pressed 2 is transcribed in memory and stored nowhere · `MediaGateway.open_leg` is
**idempotent**, because two consumers now open a leg and replacing it would have discarded
the first one's sinks — `B24`'s shape, caught by two tests that fail without the fix ·
`audio_recordings` is the **tenth table**, written only after the store confirms ·
`scripts/purge_recordings.py` is `D14`'s erasure job, object before row, refusing to run
against `memory` · verified against a real MinIO container, not from tests alone.

**P3 step 4f — the two audio-path gaps (`D111`, `D112`), 2026-09-06.**
`IntakeService._degradation()` answers for real: `TranscriptionStream` reports every
utterance an engine failure cost, `TranscriptionService` forwards it, and an intake with a
failure and **no** turns renders `stt_unavailable` — the sentence `panels.tsx` has had
since `D106` and nothing could reach · the rule is narrow on purpose, because most callers
who take the recording and then wait quietly produce no turns and must not be told the
system failed · **`entrypoints/stt.py` is `D2`'s worker**, and `SubprocessSttEngine` is the
adapter that owns it: `STT_WORKER=subprocess` puts the engine in a child process so a
runaway decode can be **killed** on `STT_DECODE_TIMEOUT_S`, which is the preventer `D98`
designed and refused to fake · the deadline covers the send as well as the reply · the
model load is outside it, and the engine is warmed at startup · verified on a running
server: six Thai sentences transcribed across a process boundary and onto the screen, and
no orphaned child after the parent was hard-killed.

**P3 step 4g — the durable transcript (`D114`), 2026-09-06.** `transcript_turns` is the
**eleventh table** and `TranscriptRecorder` is a **fourth subscriber** to `transcript.turn`
— its own, not a line inside delivery, so a storage failure cannot reach the agent's screen
(`D12`); a test publishes through a store that raises and asserts the screen still got the
turn · written **incrementally, per turn**, which is `ARCHITECTURE` §6's actual promise:
six rows on a live Postgres for a call **nobody accepted** · `turn_id` is the primary key
and both stores upsert, because the bus is at-least-once (`D15`) and a doubled sentence is
a false statement about what somebody said · the event had to grow `engine`,
`engine_version`, `is_final` and `intake_id`, because a subscriber can only persist what it
is handed.

## Designed but NOT built (read before touching these areas)

- **`D63` — call transfer.** One filtered roster menu covering all three needs (named agent /
  department / seniority), with *"let the system choose"* walking the same list in fit order.
  The caller moves **last**: an acceptance notifies the *original* agent, who wraps up with the
  customer and presses Release. A busy receiver can *accept and queue at the front*. Lands with
  P6; reuses the offer handshake, `D52` exclusion, presence and the brief almost wholesale.
- **`D64` — the live matching board.** Callers left, agents right, edges coloured by fit,
  hard-filter exclusions drawn differently from low scores. All the data is already in
  `matching_decisions`. Unscheduled; it would have caught `B4` on sight.

## What to do next (in order)

_Rewritten 2026-09-07, after `D117`–`D120`. Tracks A, D and most of C are done; do not
redo them._

⚠️ **The user has notes waiting** (see the top of this file). Read them before starting
Track B or E — twelve of the last fourteen faults came from them driving the screen.

**0. DONE — the orientation notes became `D115`/`D116`, and Tracks A, C and D became
`D117`–`D120`.** The plan is `docs/reading/the_broker_turn.html`; what was built against it
is `docs/reading/the_assist_rail.html`.

| track | days | what | who |
|---|---|---|---|
| ~~**A**~~ | ~~1-2~~ | ✅ **DONE 2026-09-07** (`D117`, `D118`, `B30`). **Broker domain pack** — intents/skills/queues around renewal, enquiry, quote, service, **handoff**; `insurer` on `Policy`; fixtures where one customer holds policies from three insurers. **Blocks everything else** | 1 |
| **B** | 2-5 | **Compare & best-fit** — `products.yaml` with comparable attributes, gap analysis, rule-based ranking first, model writes only the reason sentence (`D16`) | 2 |
| ~~**C**~~ | ~~1-6~~ | ◐ **PARTLY DONE 2026-09-07** (`D120`) — pairing, the tool rail and push-a-form all work end to end; the comparison DATA is Track B's. **Customer app v2 + the tool rail** — the 375-line static page becomes a Vite app; push-a-form working end to end, the rest as labelled stubs | 2 |
| ~~**D**~~ | ~~2-4~~ | ✅ **DONE 2026-09-07** (`D119`). **The LLM, actually running** — `build_llm` FIRST (there is no adapter and no factory), then summary + intent into `summary_th`, then a labelled set | 1 |
| **E** | 5-7 | **Package, freeze, rehearse** — Dockerfile + compose profile, **feature freeze end of day 5**, and record a video of the demo working | all |

**If the week collapses, three things:** the broker domain pack · compare & best-fit on the
workstation · **one** tool working end to end. One real tool proves the rail; five
half-built ones prove less.

⚠️ **Ship the container on `STT_ENGINE=scripted`.** A plain container cannot reach the GPU
without host setup that varies by machine, which is exactly what fails at a venue. Keep the
real engine as a documented host-run option.

**1. TRACK B — compare & best-fit.** The biggest remaining item, and the brief's biggest
leak. The **transport already exists** (`D120` pushes a comparison to the customer's phone
and it renders); what is missing is the data behind it:

- `config/products.yaml` with genuinely comparable attributes — room & board, deductible,
  co-pay, exclusions, OPD, sum insured — across the real carriers in `MARKET_FACTS` §8.
- Gap analysis: the customer's current cover against each candidate, **differences ranked
  by size**. `Policy.insurer` and the multi-carrier fixtures are already there for this.
- **Rank on facts, explain with the model** (`D16`, `D115`): the ordering comes from real
  attributes, the model writes only the reason sentence, and a figure that cannot be traced
  to a field does not render.
- Surface and compare — **never quote or underwrite**. The brief puts pricing out of scope.

**2. TRACK E — package, freeze, rehearse.** Dockerfile + a compose profile, feature freeze,
and **record a video of the demo working**. Live demos fail at venues.

**3. What is left of P4 proper.** Intent classification wired to the blend, entity
extraction, brief versioning, confidence calibration, the suggested opening, and a golden
set so "85% intent accuracy" is a measurement. Two things point at it: `D92` draws the line
P4 must not cross (speech may change WHO answers and HOW SOON, never WHICH QUEUE), which
should be written as a test *with* the blend rather than after it; and `Q24` — a health-line
caller speaking health data into a recording nobody consented to hold *as such* — has to be
decided **before** an entity extractor exists, because that is the first code that can
breach it.

**4. Then, roughly in this order:** P5 real telephony, which is what replaces
`POST /v1/demo/calls` and `D107`'s WAV player — and which is also what turns
`RecordingService` on for the *live* leg (`D26`, P6) · `D85`'s `acw_stats` into
`expected_free_in()` as a **score, never a filter** (`D73`) · the matcher inputs still fed
by nothing (`B12`: `is_vulnerable`, `last_agent_id`, `last_contact_at` are set on the
Customer and the brief but never on the `WaitingCall`, so `customer_priority` and
`continuity` score 0 on every real call) · a **player** for the recording on the agent's
screen, which is a UI job now that the audio exists and decrypts · `call_intents` /
`app_context_events` still in memory · `D64`'s live matching board · `Q26`'s env var that
changes nothing.

**A note on the LLM, now that it exists.** `build_llm` is wired, both adapters are real,
and the key is in `.env` (`D119`). `LLM_PROVIDER=rulebased` remains the shipped default and
is a real adapter needing no key, no network and no extra — so everything still builds and
tests with nothing configured. The SDKs are the **`llm` extra**: `uv sync --extra llm`, and
⚠️ name every other extra you want in the same command because **`uv sync` prunes** (`B31`).

The exit criterion that still needs a key is *"a Claude-vs-Typhoon table produced by the
harness, not by opinion"* — and `scripts/compare_llm.py` does not exist yet.

**Before the hackathon**, separately from the build: `Q21` (which storage backend the demo
runs on — and now also which **blob** backend, since `memory` is the one that needs no key
and no container), `Q23` (personalised menus renumber, and a human reading a script off
paper will press what the script says), `Q17` (whether `apps/workstation/dist/` is
committed, which decides whether a venue with no internet can build the workstation at all).

### Settled this session, so nobody re-opens them

- **The CER is explained** — it was `B20`, not the model, not the reference, not the
  detector, not the audio. ⚠️ **The figure that used to be quoted here (0.09-0.50, median
  0.29) is withdrawn**: it was measured on the digit-heavy set, before `B21`, and with only
  one engine hinted. The current numbers are `D104`'s table above, and they are the only
  ones to quote. Rank on the **mean**.
- **The endpointer is scored and it is fine.** `scripts/score_endpointer.py` (the first
  thing to read `segments.tsv`): coverage **0.797**, span recall **0.902**, 4.7 s of false
  alarm over 940 s. Dropping `D9`'s threshold from 0.65 to 0.15 buys only 0.86 coverage,
  and the seconds it "misses" are **86% near-silent and 72% within half a second of an
  annotated boundary** — an annotator rounding outward. Real speech lost: **3%**.
  **Leave `D9`'s inherited constants alone; they are right for this audio.**

## The constraints that still bind the audio path — do not re-litigate

_This section used to be a plan for P3 step 4b. **Step 4b landed** (`D96`, `D104`,
`D105`-`D107`), so what is left here is only the part that is still a live constraint.
`explanations/P3_voice.md` is the narrative; `diagrams/07_voice_and_ai.md` §7.x and §7.y
draw it end to end._

- **`D12`: the call is never blocked on AI**, and **`D37`**: routing is settled before any
  of this runs. Every failure degrades to a call routed correctly with a menu-derived
  brief — which `anonymous_declined` replays.
- **`D9`: STT is re-implemented streaming-first.** The reference project
  (`…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`) is **read-only** and is
  reference for *how the model behaves*, never code to copy. Keep: VAD threshold 0.65, min
  speech 500 ms, min silence 100 ms, ~120/60 ms padding. `silero-vad` as a dependency,
  never a runtime `torch.hub.load` — a network fetch during a live call is unacceptable.
- **`D21`: the offer window IS the intake grace period.** Intake records until the agent
  presses Accept. Proved on a running server. ⚠️ And the **order** at the accept is
  load-bearing (`B24`): close the transcriber *before* finalising the intake, or the
  caller's last sentence is dropped.
- **`D26`: both legs are forked separately** — speaker labels come from the topology, not
  from a diarisation model. The agent's own leg is P6.
- **`D10`: intake is a STRATEGY**, and **`D88`**: the strategy takes **turns, not frames**,
  so the transcriber sits on the far side of the seam. That is why the audio half could be
  built and tested three phases before the GPU — and also why nobody was appointed to
  *open* the recording, which was `B24`.

### The hardware reality, stated plainly

RTX 3050 laptop: **4.00 GiB total, ~3.2 GiB free** (`D95` — the older docs' "4–6 GB" was
wrong; the compositor holds the rest). **Do not plan to run a local LLM and the STT engine
on the same card** — the default split is STT local, LLM via API. Whoever has the strongest
GPU should own the demo machine. Typhoon uses 1068 MB, so P4's model is the question.

## Still open

| # | Question | Current default |
|---|---|---|
| **Q32** | **Should there be a "decline and show me a different caller" button?** The user proposed it and then talked themselves out of it, and they were right to. Two reasons. **It already exists implicitly:** declining re-solves the matrix immediately, and the caller you get next is the best remaining match *for you* — fit is scored per call×agent, so it is not "a worse call", it is the best of what is left. **And the explicit version is harmful:** a button that lets an agent skip a caller and keep their place is cherry-picking, which is the well-known contact-centre pathology the Hungarian solver exists to prevent — the hard cases would circulate while the easy ones got taken, and `matching_decisions` would record it as the system's choice rather than as a person's. `D109`'s *decline + pause* covers the legitimate need underneath the idea ("not now"), and costs the agent their place in the rotation, which is what makes it honest. | **Decided: not building it.** `D109` covers the real need |
| **Q33** | **Ring every qualified agent at once and give the call to whoever answers first?** The user's "random idea", and it is a real pattern — it is what a room full of desk phones does. Worth keeping because it is a genuine **degradation rung**: if nothing has been accepted after N seconds, broadcasting beats a caller waiting. As the *primary* mechanism it deletes everything the matcher buys — fit, continuity, load balance, the anti-starvation ceiling — and replaces them with *who clicked fastest*, which systematically rewards the least busy rather than the best suited and gives N-1 agents an interruption for every call. `AgentHub.broadcast()` already exists, so the mechanism is cheap; the policy is what needs deciding. **Revisit after P5**, when there is real telephony to measure a real accept latency against. | **Parked.** Not for the hackathon build |
| ~~Q7~~ | ~~Intent taxonomy + menu wording~~ | **RESOLVED 2026-09-07 by `D117`.** The orientation was the domain review, and the taxonomy is broker-shaped now: 33 intents, advice/compare and renewal first-class, claims as handoffs |
| Q8 | Typhoon model ids / licence / pricing | **Still open, and now cheap to close** — `OpenAiCompatibleLlm` is built (`D119`), so pointing `LLM_BASE_URL` at Typhoon is a config line. What is missing is the model id and the pricing, verified against live docs rather than memory |
| Q9 | `OFFER_TIMEOUT_S=20`, ACW thresholds | Guesses; tune against how a real agent works |
| Q11 | Language menu wording when English lands | `preferred` vs `acceptable` modelled (`D38`) |
| Q12 | Which challenges count for promotion to L3 | 4 named + `other` free text (`D57`); confirm the named list with Krungsri |
| Q13 | Does a third-party caller need a named representative | Assume yes; `Policy` has no `representatives` field yet |
| Q15 | Matching weights are guesses | Tune against real volumes; `--compare` exists to re-measure |
| **Q34** ⚠️ **NEW 2026-09-07** | **What the customer submits through a pushed form is not stored anywhere** (`D120`). The broker reads it off their screen and types it into the wrap-up. That is honest for a demo and wrong for a product: the customer filled in a form and the system kept no record of it. It wants a real table and a retention rule (`D14`), not a longer-lived dict — and the moment a signature or an upload lands, it stops being optional. | In memory, dies with the call |
| **Q35** ⚠️ **NEW 2026-09-07** | **The AI summary takes 4.5 s, against a 1 s brief budget** (`D119`, `ARCHITECTURE` §15). Survivable only because it is fire-and-forget after Accept, so nobody waits. But the pitch says "the agent has the brief before they speak", and 4.5 s is after. Options: accept it and describe it accurately, move to a smaller model, shorten the prompt, or stream. **Do not quietly restate the budget as met.** | Accepted, because nobody waits |
| **Q16** | **A keypad lookup confirms a policy number at L1.** The caller supplied the digits and the agent must not read them aloud below L2 — but it is a confirmation oracle. Designed this way in `D44`; worth a second look. | Allowed |
| **Q17** | **Commit `apps/workstation/dist/`?** It is gitignored, so a fresh clone has no workstation until `npm run build` runs — and on a venue with no internet, `npm install` is what fails. | Not committed |
| **Q18** | **"Not this person" is a one-way door.** It clears the customer exactly as `D42` asks, but leaves the agent with nobody to attach the call to, and customer search does not exist (`D32` defers lookup). A rejected call stays anonymous for its duration. A test asserts this so it fails the day search lands. **Now visible rather than silent (`D61`)**: the two forward outcomes are disabled with the reason in the tooltip instead of answering 400. | Accepted for now |
| ~~Q19~~ | ~~`config/playbooks/` does not exist~~ | **RESOLVED 2026-09-07 by `D118`.** `config/playbooks.yaml` holds all 24, guarded both ways at startup; `_PLAYBOOKS` is deleted |
| **Q20** | **Should a reveal-on-click with a per-field audit entry come back at P7**, for the most sensitive fields only? `D74` opened display to the agent; the honest answer depends on Krungsri's own agent-desktop policy, which we do not have. | Not for now; every read is logged |
| **Q21** | **Which storage backend does the DEMO run on?** `memory` is the default and needs nothing; `postgres` is what survives a restart, and it is what makes the persistence work visible on stage at all. Running it on the day adds a container to the list of things that can fail, against `PLAN.md`'s risk register — *never depend on the venue*. Leaning: **rehearse on `postgres`, keep `memory` as the one-keystroke fallback**, since both pass the same suite. | Not decided |

| **Q22** | **Does the committed prompt pack carry actual audio once a real voice is chosen?** `D24` calls the checked-in pack the offline fallback, which is the whole reason the IVR works with no internet — but `CLAUDE.md` says never commit audio. That rule means *call recordings*, not TTS output of our own sentences, so the two are probably compatible; 63 short Thai clips is a few MB. Undecided because there is no audio yet. | Manifest only, for now |
| **Q30** | **The prepared test set is number-heavy.** Almost every call in this corpus ends with a phone number read aloud, so the 12 prepared calls over-represent digits and under-represent ordinary conversation. That was harmless until `B21` **loosened** the repetition guard for digits — the set that would catch a regression from that loosening is exactly the speech-heavy set we do not have. Re-prepare with a deliberate mix (the user raised this; they are right). | **RESOLVED 2026-09-04.** `prepare_dataset.py --mix --seed 7` built the balanced 20-call set (digit share 0-49%), and it is the set every number in `D103`/`D104` is measured on. The re-prepare alone moved the headline CER by **1.8x**, which is why no figure from before that date may be quoted |
| **Q28** | **The reference mixes scripts, and CER charges us for being right.** The dataset's transcripts write brand and place names in **Latin** (`True move`, `Mezzox Drip Cafe`, `Frosen Khaoyai`, `Router`, `L O S`) while Thonburian correctly transliterates them into Thai (`ทูมู`, `เมโซเอ็กซ์ดิสกาแฟ`, `โฟร์เซนต์ เขา ใหญ่`). Every character of those differs, so a *correct* transcription is scored as a total miss, and on the two worst files that is most of the residual CER. Options: normalise both sides through a transliteration map before scoring (real work, and it can flatter); report CER with those spans excluded and say so; or accept it and treat the number as a floor. **Do not quietly "fix" the reference** — editing ground truth to match the model is how a metric stops meaning anything. | **Decided 2026-09-04: one headline + one diagnostic.** `bake_off.py` reports `CER` (the only ranking metric) and `CERth` (Latin spans stripped from both sides). The GAP between them is the answer; three competing scores would just move the argument. Not ranked on `CERth` because that excuses every engine from the words it is most likely to get wrong. **And it does not block the engine choice** — the mismatch hits every engine equally, so it distorts the absolute number, not the ranking |
| **Q29** | **The p95 latency runs from 4.5 s to 58.7 s against a 1.5 s budget**, and the spread tracks throughput: at rtf <= 0.31 it is 4.5-8 s, at rtf >= 0.65 it is 31-59 s, because once decode is slower than speech the backlog compounds for the rest of the call. `D30`'s table is the thing that decides what to do. Thonburian medium fp16 takes ~3 s per utterance on this card and one consumer serialises them, so three short phrases in four seconds queue up. Candidates, and they are not exclusive: the **CT2 int8_float16 build** (`scripts/convert_ct2.py`, this is the row that was always meant to decide it), **Typhoon** (a transducer, so no 30 s padding — `D99` says exactly why this might be structural rather than incremental), a **smaller Thonburian**, or accepting a slower transcript because `D12` means the call is never waiting on it. | **RESOLVED by `D104`.** Typhoon, a transducer with no 30 s window: p95 **0.19 s median / 0.28 s worst**, **20 of 20** calls inside the budget, `busy` worst 0.020. `D99` predicted the structural reason before it was measured. CT2 int8 is the documented fallback |
| **Q27** | **The dataset is all `Government` domain, not insurance.** All 3189 calls (`D97`). It measures Thai telephone ASR honestly and says nothing about insurance jargon — and our `stt_vocabulary.yaml` hint is *wrong* for it, which makes it a fair test of whether the hint hurts when it does not apply. An insurance-domain set would still be worth having, and the hackathon may supply one. | Use it, and label the numbers as general Thai |
| **Q26** | **`Settings.max_wait_before_any_agent_s` is an env var that changes nothing.** The matcher reads `config/matching_weights.yaml`, never `Settings`, so `MAX_WAIT_BEFORE_ANY_AGENT_S=30` in `.env` silently does nothing — and since `D94` it also describes a shape (one number) the system no longer has. It survives only as the bound for a startup coherence check against `target_wait_s`. Delete it, or wire the weights loader to it. Found while writing `D94`. | Left in place, documented |
| **Q24** ⚠️ **ASKED 2026-09-06, AWAITING AN ANSWER — BLOCKS P4's ENTITY EXTRACTOR** | **A health-line caller speaks health data into a recording nobody consented to hold as such.** `D14` makes `health_data` a separate scope; the offer grants only `recording` and `ai_processing` (`D88`). Under PDPA (and GDPR) health information is a **special category** needing *specific* consent, and we have the general one. **It is harmless today** — nothing extracts anything, so we hold audio and a transcript of what they said. **It stops being harmless at the first line of an entity extractor**, which turns speech into `condition: cardiac`: that is the difference between holding a recording and building a health record. Three options: (a) **a third keypress** — cleanest legally, and it lengthens the longest prompt in the system on the line where callers are least able to wait; (b) **name the health scope in the offer's wording on health lines** — one keypress, honest disclosure, weaker if a regulator reads "specific" strictly; (c) **gate the extraction** so health entities are never pulled without the scope — costs the caller nothing, but on its own just means we never extract. **RECOMMENDED, and put to the user: (b) + (c)** — two independent protections, one keypress, and if the wording ever regresses the code refuses rather than silently over-collecting. Rejected (a) for the same reason `D113` defaults to circling rather than cutting off: the system should not spend a distressed person's time on our paperwork. | **Awaiting the user.** Do not write the extractor first |
| **Q23** | **Personalised menus renumber, and a human on a real keypad has no `ScriptedChoices`.** Every automated caller presses canonical keys and is translated (`D81`), so nothing in the suite or the demo endpoint can get this wrong. But at P5 a person reading a rehearsal script off paper will press what the script says, and for a recognised persona the numbers may have moved. Either rehearse with the persona that will actually be used, or set `personalisation.enabled: false` for the demo. | Enabled; decide before the day |

Resolved: **`Q31` — the caller everyone declined now goes round again (`D113`)**, with
the cap as config defaulting to *no cap* (the user's call: a cut-off caller has to start
again from the menu, a holding caller can hang up whenever they choose), RONA counting as a
decline, and the card saying both *"round N"* and *"you are the only agent who can take
this"* · **`Q29` the latency and `Q30` the test set** (both `D104`, and `Q30` is the one that moved the number) · **`Q28` one headline plus one diagnostic** · **`Q32` no cherry-picking button, `Q33` ring-all parked** (2026-09-05) · **`Q25` — the wait ceiling is now per urgency tier (`D94`)**, so an emergency reaches its guarantee at 60 s while a routine caller is still 120 s from theirs; when both are past their own, the more urgent goes first · rating is an event (`D46`) · single project (`D34`) · Asterisk · RTX 3050 · Claude
+ Typhoon compared · React workstation with the softphone in it · web customer simulator ·
menu-first flow (`D37`).

## The machine, as left on 2026-09-06

Facts about *this laptop* rather than the repo, so a fresh session does not rediscover them.

- **Docker works** (v29.2.0) and **Docker Desktop has to be started by hand** — it was not
  running on 2026-09-06 and `docker compose` failed with a named-pipe error rather than
  anything about Docker being down. `Start-Process "C:\Program Files\Docker\Docker\Docker
  Desktop.exe"`, then wait about a minute.
- **Two containers are now up: `readycall-postgres-1` and `readycall-minio-1`.** Everything
  runs without either; with both up the suite is 765 pass / 12 skip, and with neither the
  count of skips rises and nothing fails.
- ⚠️ **MinIO is published on 19000/19001 here, not 9000/9001.** Another process on this
  laptop (a system Python, PID varies) holds `127.0.0.1:9000`, and the symptom was not a
  bind failure — Docker's proxy answered second and boto3 reported a *protocol violation*.
  `MINIO_PORT=19000 MINIO_CONSOLE_PORT=19001 docker compose -f infra/docker-compose.yml up
  -d minio`, and `BLOB_ENDPOINT_URL=http://127.0.0.1:19000` to match.
- **The demo master key used while verifying `D110`** was
  `ZGVtby1tYXN0ZXIta2V5LTMyLWJ5dGVzLWxvbmchISE=` (the ASCII string
  `demo-master-key-32-bytes-long!!!`). It is in no file and is not a secret — it exists so
  the bucket's one object can still be opened. Generate a real one for anything else.
- **`.env` EXISTS ON THIS LAPTOP AND IS GITIGNORED.** Created 2026-09-06 at the user's
  request as the safe place for keys, and **rewritten later the same day** (`B29`) into
  four sections — what must be filled (both already are), the stage-safe demo profile,
  the real-engine profile commented beside it, and the knobs. One assignment per name.
  It carries a generated `RECORDING_MASTER_KEY` and a real `ANTHROPIC_API_KEY` (inert
  until P4 builds an adapter), so switching `BLOB_STORAGE=localfs` works with no setup.
  ⚠️ **Never print its contents, never commit it, and never regenerate that key** —
  everything written under it becomes unreadable. `.env.example` is the committed twin and
  carries names only.
- ⚠️ **The venv was pruned and restored on 2026-09-07** (`B31`). It now carries **every**
  extra: `uv sync --extra web --extra llm --extra s3 --extra ml --extra asr`. Verified
  after: `torch 2.11.0+cu128`, `cuda.is_available() True`, `nemo` imports, `anthropic` and
  `openai` import. **Any `uv sync` naming fewer extras will silently remove the rest.**
- **A stray ReadyCall server was left on port 8000 by an earlier session** and is still
  there (a uv-managed python, not the project venv). Port 8077 belongs to the user's
  music-backlog project. When verifying, pick a port and **check the route list to confirm
  it is ReadyCall** — one verification round was spent querying the wrong app entirely.
- **The GPU stack is installed and working**: `torch` + `cu128`, `nemo_toolkit[asr]`, and
  the HF cache holds four Thai checkpoints (~11.8 GB) paid for by the earlier project. So
  `STT_ENGINE=typhoon` runs here with no download. A fresh machine does not have any of it.
- **`tests/audio/demo_intake.wav` exists but is gitignored** (`*.wav`). Any fresh clone
  regenerates it with `uv run python scripts/make_demo_audio.py`, which sizes each
  utterance from the demo script so `D98`'s rate guard cannot silently eat the lines.
- **`readycall_test` exists inside that container's volume.** It was created by hand *and*
  added to `infra/postgres/init/02-test-database.sql` for fresh setups — init scripts only
  run on an empty data directory, so a `docker compose down -v` re-creates it and a plain
  restart keeps it. If the suite ever reports it missing:
  `docker compose -f infra/docker-compose.yml exec postgres psql -U readycall -d postgres -c "CREATE DATABASE readycall_test OWNER readycall"`.
- **`mermaid-cli` is now installed globally** (`npm i -g @mermaid-js/mermaid-cli`, ~190
  packages, a few minutes). `scripts/render_diagrams.py` finds it on `PATH`; nothing else
  needs it, and `--check` works without rendering.
- **Node is on `PATH`**, so `apps/workstation` can be rebuilt. `dist/` is gitignored and
  still uncommitted (`Q17`).
- The **dev database currently holds one call** from the live restart check, plus a handful
  from the 2026-08-31 browser verification of the intake offer. Harmless; the suite no longer
  touches that database at all.
- **Port 8000 gets left holding a stale server.** Twice now a `uv run python -m
  readycall.entrypoints.api` from an earlier session was still bound, and the new one failed
  to bind while its log still printed `api ready` — which reads exactly like a working start.
  `Get-NetTCPConnection -LocalPort 8000 -State Listen` finds it; the process must be stopped
  before the new code is being served. **The server does not reload Python changes**, so a
  stale process also serves stale code.
- **The in-app browser pane cannot open `file://` URLs or a signed-in `claude.ai` artifact.**
  It stays on whatever it had. To eyeball a `docs/reading/*.html` page, navigate to the repo
  path and screenshot the tab it opens (it lands in a NEW tab id — check `tabs_context`),
  and expect the first `screenshot` to time out once and succeed on retry.

## Things to be careful about (live landmines)

- **SINCE `D113`, ONE AGENT CAN HOLD TWO ASSIGNMENTS FOR ONE CALL** (`B28`). The round-1
  decline and the round-2 accept both exist, both belong to that agent, and `for_agent`
  yields them in insertion order — the **decline first**. Anything asking "this agent's
  assignment for this call" must filter to `PENDING`/`ACCEPTED` and take the newest;
  `_assignment_for_call` is the one place that does it, and `end_call`, `save_wrapup`,
  `attest_identity` and `start_capture` all reach the call through it. Returning the first
  match made End call answer *"was never accepted"* on a call the agent was on, **and**
  let an agent who declined keep acting on a caller somebody else had taken (`D52`).
- **THE SUITE MUST NEVER READ `.env`, AND A FIXTURE NOW ENFORCES IT** (`B29`).
  `Settings.model_config` names `env_file=".env"`, so before 2026-09-06 every test that
  built `Settings(...)` inherited whatever this laptop's **gitignored** file happened to
  say — `STT_ENGINE=typhoon` sent five transcript tests to the GPU. The autouse
  session fixture in `conftest.py` neutralises the file and restores it afterwards.
  Environment *variables* are left alone on purpose (`READYCALL_TEST_MINIO=1`,
  `STT_ENGINE=scripted uv run pytest`). **The dangerous direction is green, not red:** a
  permissive `.env` would hide a real fault rather than invent one.
- **ONE NAME, ONE ASSIGNMENT IN `.env`** (`B29`). The user's file set `STT_ENGINE`,
  `VAD_ENGINE` and `LLM_PROVIDER` twice each — the adapter block at the top and the
  behaviour block below. **The later one wins**, so the top block was decorative and the
  machine was really running typhoon + silero + anthropic while the file's first screen
  said scripted + energy + rulebased. Rewritten 2026-09-06 into four labelled sections
  with one assignment per name; `.env.backup-2026-09-06` is the original.
- **A TEST HELPER THAT DISCARDS A STATUS CODE CANNOT FAIL** (`B28`). `Floor.hang_up` posted
  to `/end`, threw the response away, and every invariant afterwards was true — of a call
  that had never ended. The stress suite walked this exact scenario and stayed green.
  Helpers in `test_floor_under_load.py` assert their status now; keep it that way.
- **A BROKER HANDS CLAIMS OVER; IT DOES NOT ADJUDICATE THEM** (`D117`). There is no
  `*.claim` skill — `claims.assist` takes the notification and hands to the insurer, and
  every such intent carries `handoff_to_insurer: true` which the agent's screen shows
  BEFORE they speak. If you find yourself adding a step that says "approved", "is
  covered", or quotes a premium, it belongs to the insurer and the brief names it as out
  of scope. `health.ipd.preauth` was deleted for exactly this reason.
- **ONE AGENT CAN NO LONGER BE ASSUMED INTERCHANGEABLE WITH ANOTHER ON THE SAME LINE**
  (`D117`). Advice and service are separate skills per line, and `renewal.retention` and
  `claims.assist` are cross-line. A test that signs in an advisor and places a claim will
  simply never be offered it — which is correct, and cost about an hour to recognise the
  first time. `mock/agents/agents.json` is the map.
- **A FIXTURE WITH ONE OF SOMETHING TESTS NOTHING ABOUT CHOOSING** (`B30`). The demo
  customer held exactly one policy, so `_pick_relevant_policy`'s "decline to guess"
  branch was unreachable and a missing `product_line` argument produced correct output
  for the wrong reason from P1b until 2026-09-07. They hold three now, from three
  carriers. Cardinality is part of a fixture's design.
- **A PERSONAL PUSH TO A LINK-ONLY SCREEN MUST STAY REFUSED** (`D120`, `D121`, `D42`).
  Tapping a link proves somebody holds that phone; it is not identity. **The gate is
  `personal` in `config/assist_tools.yaml`, per TOOL, not per kind** (`D121` — the old
  `_NEEDS_VERIFIED = {FORM, DOCUMENT_REQUEST}` was wrong in both directions). Two rules
  keep it coherent and both refuse at startup: `personal` is read from the file and
  **never from a request body**, and a non-personal tool may not declare `prefill` —
  prefilling is exactly what makes a form a statement about one customer. Widening it to
  make a demo smoother would put a stranger's policy on whoever holds the handset.
- **THE RAIL'S `disabled` ATTRIBUTE IS A COURTESY, NOT THE GATE** (`D121`, `D71`). A
  personal tool renders greyed with its reason so the broker learns why before clicking,
  which is `D71`'s pattern. The server refuses independently, and the test that matters
  asserts the **server's** refusal — never the client's `disabled`. If you ever find
  yourself testing the greying instead, the gate has moved to the wrong side of the wire
  (`B5`).
- **THE CUSTOMER SCREEN POLLS AND MUST NOT RE-RENDER ON EVERY POLL** (`D120`). It compares
  a content signature first, because a re-render wipes a form the customer is halfway
  through typing — the optimistic-UI hazard `D44`'s keypad panel already taught, in the
  one place it would be most infuriating.
- **`uv sync` PRUNES, AND A CHECK THAT NEEDS AN OPTIONAL EXTRA IS CHECKING THE MACHINE**
  (`B31`). `uv sync --extra web --extra llm` removed the `ml` stack from this laptop and
  instantly produced five suite errors and five mypy errors that had been latent for weeks and
  would have been red on CI. Name every extra in one command. And when you defer an
  import into a constructor to keep a module importable, **every `except ImportError`
  written around the import is now guarding an empty room.**
- **THE LLM READ PATH MUST NEVER CALL A MODEL** (`D119`). `Container.brief_snapshot` runs
  on every `/me` and every socket push; summarising there would hit the provider dozens of
  times per call. `summarise_call` computes once, in the background, from `accept_offer`,
  and the read path only *prefers* what is already cached.
- **THE SUMMARY IS FIRE-AND-FORGET AND MUST STAY THAT WAY** (`D119`, `D12`). `accept_offer`
  starts the task and never awaits it — the agent is connected the moment that endpoint
  returns. Measured on the real provider: **4.5 s, $0.0085 a call** on `claude-sonnet-5`,
  which is well outside §15's 1 s brief budget and survivable only because nobody waits
  for it. If a summary is ever wanted BEFORE accept, that number says it needs a smaller
  model, a shorter prompt or streaming.
- **A PROMPT IS A FILE WITH ITS VERSION IN THE NAME, AND EDITING ONE IN PLACE IS A BUG**
  (`D119`, `D18`). `analyses.prompt_version` names the file a result came from. A new
  prompt is a new file. Rendering refuses a missing slot AND an undeclared one, because a
  prompt that silently loses its transcript still returns a confident summary of nothing.
- **A SECRET MAY BE DECLARED IN `Settings` BEFORE ITS ADAPTER EXISTS; A BEHAVIOUR KNOB MAY
  NOT.** That looks like a contradiction of `Q26` and is the opposite of one. A knob nothing
  reads is a lie about what the system does. A *secret* slot is redacted from every log line
  by name the moment somebody sets it, documented in one place, and stops a live key being
  pasted somewhere with no obvious home. The declared-but-unread ones are marked `[SLOT]` in
  `.env.example`; setting one changes nothing yet, and that is said out loud.
- **THE TRANSCRIPT HAS TWO COPIES NOW, AND THEY ANSWER DIFFERENT QUESTIONS** (`D114`).
  `TranscriptDeliveryService`'s in-memory list is the **live** path — what the socket
  pushes and what `GET /v1/agent/me` renders while the call is happening.
  `transcript_turns` is the **record**, written per turn by `TranscriptRecorder`. Do not
  make the screen read from the table on a hot path, and do not make the recorder the
  thing the screen depends on: they are separate subscribers precisely so a storage
  failure cannot blank a panel.
- **THE EVENT IS THE ONLY CARRIER A SUBSCRIBER HAS** (`D114`). `TranscriptTurnAdded` grew
  `engine`, `engine_version`, `is_final` and `intake_id` because `transcript_turns` has
  those columns and a store that filled them from anywhere else would be inventing them.
  If you add a column, add the field to the event in the same change.
- **`turn_id` IS THE PRIMARY KEY AND BOTH STORES UPSERT** (`D114`, `D15`). The bus is
  at-least-once by design. A transcript with a sentence in it twice is not a formatting
  problem — it is a false statement about what the caller said.
- **`stt_unavailable` MEANS "THE ENGINE FAILED **AND** NOTHING WAS TRANSCRIBED"** (`D111`).
  Not "the transcript is empty" — most callers who take the recording and then wait quietly
  produce no turns at all, and a rule that looked only at emptiness would put a system
  failure on the agent's screen about every one of them. A *partly* lost transcript is
  logged loudly and reported as `NONE`; if it ever needs to reach the screen it wants its
  own `DegradationReason`, not this one stretched (`D50`/`D108`'s argument again).
- **THE STREAM REPORTS A LOSS; IT DOES NOT CONCLUDE ONE** (`D111`).
  `TranscriptionStream` only ever sees what it dispatched, so it cannot tell a silent
  caller from a dead engine. It counts and hands the count up through `on_transcription_
  lost`. Putting the judgement in the stream is the version that guesses.
- **A FAKE ENGINE MUST IMPLEMENT `transcribe_utterance`, NOT `transcribe`** (`D111`). The
  first broken-engine fake had the wrong method name, so the stream failed with an
  `AttributeError` and the test passed for a reason that had nothing to do with a broken
  model. The test asserts the engine was actually **asked** now.
- **`STT_WORKER=subprocess` IS THE ONLY WAY THE DECODE TIMEOUT IS REAL** (`D112`, `D98`).
  Under `inline` — the default, and what every test and the stage demo run —
  `STT_DECODE_TIMEOUT_S` does nothing at all, because there is nothing to kill. That is not
  a bug and it is not a fallback to add later: `asyncio.wait_for` around `to_thread` does
  not kill the thread, and a guard that looks like one and is not is `B7`'s whole family.
- **THE DEADLINE COVERS THE SEND, NOT JUST THE REPLY** (`D112`). A wedged worker stops
  reading as well as answering, and whether `drain()` then blocks depends on the OS pipe
  buffer, the utterance length and how much the child consumed first. It was seen to hang
  once on an ~80 KB utterance and **does not reproduce reliably** — which is the argument
  for the deadline covering it, not against. Do not "simplify" the exchange back into two
  separate awaits.
- **THE MODEL LOAD IS DELIBERATELY OUTSIDE THE DEADLINE** (`D112`). `_ensure()` has its own
  180 s bound, because a cold Typhoon load is not a runaway decode and killing it as one
  would make the worker unable to start at all. `api/app.py` warms the engine at startup,
  in the background, for the same reason `SttEngine.warmup` exists.
- **THE STT WORKER'S stdout IS THE PROTOCOL** (`D112`). `readycall.logging` writes to
  stderr — a happy accident this design depends on — and the child rebinds `sys.stdout` to
  stderr before building the engine, because model libraries print. A stray `print` in a
  transformers import corrupts the stream and looks like a protocol bug.
- **THE CHILD READS WITH BLOCKING I/O IN A THREAD, AND THAT IS NOT A STYLE CHOICE**
  (`D112`). `loop.connect_read_pipe(..., sys.stdin)` raises
  `OSError: [WinError 6] The handle is invalid` under the Proactor loop, which is the
  Windows default — so the async version would have passed on Linux CI and failed on the
  demo laptop. Caught by the round-trip test, not by review.
- **A SMALL TEST PAYLOAD TESTS THE BUFFER, NOT THE PROTOCOL** (`D112`). 8 KB of samples
  fits in a pipe buffer, so every send completes instantly and the wedged-worker path is
  never exercised. `tests/unit/test_stt_worker.py` uses 160 KB — 2.5 s of 16 kHz audio,
  an entirely ordinary Thai sentence.
- **`build_blob_storage` IS THE ONLY PLACE A STORE MAY BE CONSTRUCTED** (`D110`). It always
  wraps the backend in `EncryptingBlobStorage`, and that wrapper is the entire reason
  `localfs` is allowed at all — the in-memory store's docstring used to refuse a local one
  precisely because nothing encrypted. Building `LocalFsBlobStorage(...)` or
  `S3BlobStorage(...)` directly opts out of the guarantee. Only tests do it, and the test
  that matters reaches through `.inner` to assert the backend holds ciphertext: asserting
  on `store.get()` would pass just as happily on a store writing plaintext.
- **AN EPHEMERAL MASTER KEY AGAINST A DURABLE STORE IS REFUSED AT STARTUP** (`D110`), and
  that refusal is the feature. Without `RECORDING_MASTER_KEY` the ring generates a master
  per process; combined with `localfs`/`minio`/`s3` that writes ciphertext nobody will ever
  read again — a recording that exists, costs money, satisfies an audit on paper, and plays
  nothing. Against `memory` it is exactly right, because the objects die with the key. If
  you find yourself deleting that check to make something start, set the key instead.
- **`open_leg` IS IDEMPOTENT, AND UNDOING THAT SILENTLY UNSUBSCRIBES SOMEBODY** (`D110`).
  Two consumers open a leg now — the transcriber and the recorder — and neither may depend
  on the other running (`D12`). Replacing the leg discards the first opener's `sinks`, so
  it stays correct, running, and fed nothing: `B24`. Two tests in `test_recording.py` fail
  without it; both were checked by disabling the fix.
- **THE RECORDING IS SEALED ON THE ACCEPT PATH AND UPLOADED FROM THE SWEEP** (`D110`,
  `D12`). `close()` moves a list onto a queue and returns; `flush_pending()` does the I/O.
  Moving the upload into `close()` puts an object-store round trip between the agent
  pressing Accept and the caller hearing them, and `test_close_does_not_touch_storage`
  fails rather than merely getting slower.
- **CONSENT IS CHECKED AT THE SEAL, NOT AT THE OPEN** (`D110`, `D14`). The offer window IS
  the recording window (`D21`), so a caller who presses `2` has had frames flowing the
  whole time. Checking at `open()` would be too early — the keypress can land after the leg
  does — and would mean either recording them anyway or losing the audio of everyone whose
  consent arrived a second late.
- **NOTHING IS RECORDED UNTIL THE STORE CONFIRMS** (`D110`). The `audio_recordings` row is
  written after `put()` returns, never before. A reference to an object that was never
  written is a recording that looks retrievable, satisfies an audit, and plays nothing. The
  purge does the same thing in the other direction: **object first, then row** — the other
  order leaves audio nobody knows they are holding.
- **`delete_after` IS STAMPED AT UPLOAD TIME AND NEVER RECOMPUTED** (`D110`, `D14`).
  Lowering `RECORDING_RETENTION_DAYS` must not silently shorten the life of audio already
  held, and raising it must not extend it. The promise that binds is the one that was true
  when the caller said yes.
- **PORT 9000 IS POPULAR AND THE FAILURE DOES NOT LOOK LIKE A PORT CONFLICT** (`D110`). On
  this laptop a stray Python server holds `127.0.0.1:9000`, so MinIO bound fine, Docker's
  proxy answered second, and boto3 reported *"the server committed a protocol violation"*.
  `MINIO_PORT=19000 MINIO_CONSOLE_PORT=19001 docker compose ... up -d minio`, and set
  `BLOB_ENDPOINT_URL` to match. `Get-NetTCPConnection -LocalPort 9000 -State Listen` lists
  every listener; there were three.
- **THE MINIO ROW OF THE BLOB CONTRACT SUITE IS OPT-IN** (`READYCALL_TEST_MINIO=1`), the
  same shape as the Postgres one. A socket answering on 9000 is not necessarily this
  project's MinIO, and a contract suite that quietly writes into somebody else's bucket is
  worse than one that skips.
- **A DURATION IN A PAYLOAD NEEDS AN ANCHOR OR IT WILL NOT MOVE** (`B27`, `D68`, `B8`).
  A number only changes when a snapshot arrives, and for a caller sitting in a queue
  nothing ever happens to cause one. Send the *instant* alongside it — `waited_since`,
  `longest_wait_since`, `acw_since`, `call_answered_at` — and let `useSecondTicker` +
  `elapsedSince` count. Compute the anchor **beside** the number, in `_live()`, so the two
  cannot end up describing different moments.
- **`seq` IS PER AGENT; THE CLIENT'S `lastSeq` IS PER TAB** (`B27`). Signing out does not
  unmount `useSocket`, so without a reset the second agent to use a tab silently discards
  every push below the first agent's high-water mark — including their offer. `lastSeq`
  resets when `enabled` flips true, and **only** then: a reconnect must keep its position
  or the outbox replay in `D68` has nothing to replay against.
- **AN INTERVAL WHOSE EFFECT DEPENDS ON A CALLBACK IDENTITY NEVER FIRES HERE** (`B33`).
  The workstation re-renders **once a second** to drive its timers, so any
  `useEffect(... , [.., onSomething])` holding a `setInterval` longer than 1 s is torn
  down and rebuilt before it can fire. Hold the callback in a ref and depend on the real
  inputs. The symptom points at the server: the data is correct, present, and not on
  screen.
- **"FETCH ONCE" MUST MEAN ONCE AFTER THE PRECONDITION** (`B32`). An empty dependency
  array means "as early as possible", which for anything authenticated is *before sign-in*
  — and a defensive `.catch()` then turns a loud 401 into a permanently empty panel. Key
  the effect to the thing that had to happen first.
- **IF THE UI UPDATES ON A TEN-SECOND CADENCE, IT IS THE HEARTBEAT** (`B27`).
  `HEARTBEAT_MS = 10_000` is the only ten in the client, and `heartbeat_ack` falls through
  `onSocketMessage` to `quietRefresh()`. Anything that appears to refresh every ten seconds
  is really not refreshing at all — that is just the next unrelated redraw.
- **THE HARD FILTER IS THE ONLY THING THAT KEEPS AN UNAVAILABLE AGENT FROM BEING RUNG**
  (`B25`). `hard_filter` checks `offline` / `not_ready` / `busy` and nothing else does —
  `PresenceView.offerable` is for the *screen*. Until 2026-09-05 neither of them was
  consulted by the matcher and `AgentPresence.is_available()` was dead code, so a caller
  was offered to an agent whose own screen said `offerable: false`. If you touch
  `hard_filter`, the test that matters is the **HTTP** one: this bug is invisible unless
  something drives the matcher.
- **`OFFERING` COUNTS AS BUSY, AND THAT IS LOAD-BEARING** (`B25`). Without it one ringing
  desk is handed every waiting caller in a single tick, and none of them reaches anybody
  else until each offer times out — twenty seconds per caller. It looks exactly like the
  queue being stuck, and that is what it looked like.
- **A FIX FOR ONE CONSUMER IS NOT A FIX** (`B26`). `B12` unfroze `waiting_s` for the
  matcher and left the two places a human reads it frozen for another four days. When you
  correct a value, `grep` for every reader before believing you are done.
- **THE POOL'S STORED `waiting_s` IS THE ADMIT-TIME VALUE AND ALWAYS WILL BE.** Read it
  through `DispatchService.waiting()` / `waiting_call()`, which derive the live wait from
  `call_sessions` (`B26`, `D78`). Never write it back — that creates the second copy the
  derivation exists to avoid.
- **THERE IS A FLOOR-LEVEL STRESS SUITE NOW — USE IT** (`tests/integration/test_floor_
  under_load.py`). Seeded random walks over the real HTTP API with several agents and
  several callers, asserting **invariants** after every step rather than outcomes: nobody
  unavailable is rung, no caller rings two desks, no desk holds two callers, nobody is
  re-offered a call they declined, no wait goes backwards. Verified to catch `B25` by
  disabling the fix. **Add a scenario here whenever a bug is found by clicking** — that is
  the class of fault the rest of the suite cannot see, and it is now two for two.
- **`D52`'s EXCLUSION IS CLEARED WHEN EVERY QUALIFIED AGENT HAS DECLINED** (`D113`). The
  caller goes round again, on the next tick, with a round counter on the offer card. So
  `excluded_agents()` is **not** a permanent record of who turned this caller down — it is
  who has turned them down *in this round*. The stress suite's invariant still reads
  "nobody currently excluded is holding an offer", which is still true; what is no longer
  true is "nobody is ever rung twice about one call", which it never said.
- **`max_offer_rounds: 0` MEANS FOREVER, AND THAT IS THE SHIPPED DEFAULT** (`D113`). It is
  in `matching_weights.yaml`, not `Settings` (`Q26`). The user's reasoning, which is
  business logic rather than an engineering property: a caller who is cut off has to start
  again from the menu, while a caller still holding can hang up whenever they choose. If a
  cap is ever set and fires, it deliberately does **not** move the call to `VOICEMAIL` —
  `D25`'s path is P6 and does not exist, and a state nothing handles is `B7`'s shape.
- **THE OFFER CARD NEVER SAYS HOW MANY OTHER AGENTS COULD TAKE THE CALL** (`D113`), only
  whether this agent is the **last** one. "Three others could take this" is a
  diffusion-of-responsibility prompt on a card whose other button is decline, and it is not
  actionable. Show a fact when it increases responsibility, never when it diffuses it.
- **`sole_candidate` IS COUNTED FROM THE DECISION'S OWN CANDIDATES** (`D113`), where
  `hard_filter_failed is None` means the agent passed **the same filter the matcher used**.
  Never compute availability a second way — that is precisely how `B25` happened.

- **`publish()` DOES NOT RUN ANYTHING** (`D15`, `D105`). The in-memory bus enqueues; the
  handlers run on `drain()`. In the API process that is `pump_once` every
  `BUS_DRAIN_INTERVAL_S` (0.05 s) — **and there was no such driver at all until 2026-09-05**,
  so a subscriber was correct, tested and unreached (`B24`). If you add one: write a test
  that drives the *driver*, not the handler. Setting `BUS_DRAIN_INTERVAL_S=0` disables it,
  which is right for a test that drains explicitly and silently breaks the live transcript.
- **THE SWEEP MUST NOT BECOME THE BUS DRIVER** (`D105`). It runs every 1.0 s and the whole
  utterance-to-screen budget is 1.5 s, of which the model already spends 0.19 s. Two
  drivers, two deadlines; a test asserts the sweep does not drain, so nobody tidies them
  back together.
- **CLOSE THE TRANSCRIBER BEFORE FINALISING THE INTAKE** (`B24`). `stream.finish()`
  transcribes the segment still open and drains the queue, and those turns go to
  `IntakeService.on_turn`, which passes them on **only while the strategy is running**.
  Finalise first and the caller's last sentence — the one they were saying as the agent
  picked up — is logged and dropped. `accept_offer` has the order right; keep it.
- **THE TRANSCRIPT IS FLUSHED ON ACCEPT, NEVER ON THE OFFER** (`D106`). An offer can be
  declined or time out and the call is re-matched (`D52`); an agent who declines must not
  have read the caller's words verbatim for a call they never took. The offer card's gated
  *summary* (`D69`) is a different disclosure and stays.
- **EVERY TRANSCRIPT PUSH CARRIES THE WHOLE LIST, NEVER A DELTA** (`D106`). The client
  REPLACES. If you make it append, one dropped message leaves a sentence missing from the
  middle with nothing on screen to say so — `D68`'s rule in the place it matters most.
- **ONE STT ENGINE PER PROCESS IS WRONG FOR A SCRIPT** (`D107`). `ScriptedSttEngine` carries
  a cursor, so without `open()` resetting it the second demo call shows an empty panel — the
  stage-safe fallback failing exactly as it exists to prevent. Every test passed while this
  was broken because each placed **one** call. Any test about the demo path places at least
  two.
- **THE SCRIPTED LINES AND THE DEMO AUDIO MUST BE SIZED FOR EACH OTHER** (`D107`, `D98`).
  The rate guard refuses more than ~15 characters per second of the utterance a turn arrived
  on and does not care that the text came from a file, so a long Thai line on a short
  utterance is **silently dropped** and the panel is simply empty.
  `scripts/make_demo_audio.py` computes the length from the script; do that arithmetic if
  you bring your own recording.
- **A GREEN ASYNC TEST CAN BE GREEN ON SCHEDULING LUCK** (`B24`). The transcriber's worker
  runs on the application's loop, which under `TestClient` only advances while a request is
  in flight. `tests/unit/test_transcript_over_http.py::settle` polls with cheap requests
  rather than sleeping. A test that passes for a reason you cannot name is not yet a test.

- **THE ENGINE IS TYPHOON** (`D104`), not the CT2 build — `D103` chose CT2 and `D104`
  replaced it the same day, so a landmine list written between the two says the wrong
  thing. `STT_ENGINE=typhoon`, and it needs the `asr` extra on top of `ml`.
- **THE FALLBACK IS THE CT2 BUILD, AND IT IS A LOCAL DIRECTORY** (`D103`) produced by
  `scripts/convert_ct2.py`, not a Hugging Face id — inventing one was `B17`. A fresh
  clone has no `models/` (gitignored), so the conversion is a setup step. It takes about a
  minute when the HF cache is warm and downloads ~1.6 GB when it is not. Convert it even
  when shipping Typhoon: it is what the demo falls back to if NeMo will not install.
- **RANK ON THE CER *MEAN*, NOT THE MEDIAN** (`D103`). At n=20 the median is unstable:
  two runs of an IDENTICAL configuration moved it **0.087 -> 0.124** while the mean went
  0.128 -> 0.130, because int8 inference is not bit-reproducible and a couple of calls
  crossing the middle drags a median a long way. `bake_off.py` prints both plus the worst;
  a big mean-vs-median gap means one call is doing the talking, so go read it in `--dump`.
  **This cost `D103` a self-correction hours after it was written.**
- **THE TEST SET MOVED THE HEADLINE NUMBER BY 1.8x** (`Q30`, now fixed). Same engine, same
  code: CER median 0.161 on the digit-heavy set, 0.089 on the balanced one. Phone numbers
  are the hardest thing in this corpus. **Every accuracy figure recorded before 2026-09-04
  was measured on the pessimistic set.** The set is now `--mix --seed 7`, 20 calls, digit
  share 0-49%.
- **THE VOCABULARY HINT IS LOAD-BEARING NOW** (`B19` fixed, `Q27` answered). It is mildly
  negative on fp16 (+0.010 CER) and strongly positive on int8 (**-0.043**), and it steadies
  the decoder enough to move CT2's worst `busy` from 0.46 to 0.11. So
  `config/stt_vocabulary.yaml` is no longer a nicety: changing it costs accuracy AND still
  risks `B14`'s echo. Re-measure after touching it.
- **STT_MODEL IS DELIBERATELY BLANK** (`B23`). The two Thonburian engines want different
  things from that one field - an HF id for `thonburian_hf`, a local CT2 directory for
  `thonburian_ct2` - so the only correct default is empty, and a startup check refuses an
  HF-looking id with the CT2 engine.
- **`close()` MUST FREE THE DEVICE MEMORY, NOT JUST THE OBJECT** (`B22`). Dropping the
  reference leaves the weights in torch's caching allocator, so the driver still counts
  them: a second engine in the same process is measured against a polluted baseline and,
  on a 4 GiB card, may not fit at all. This invalidated a CT2 VRAM figure (585 MB, real
  answer 1106 MB) before it was caught by **watching `nvidia-smi`, not by a failure**.
- **BATCHING MUST NEVER WEAKEN A GUARD** (`D101`). `_transcribe_batch` splits into
  prepare / dispatch / publish precisely so that the level gate still runs per segment
  BEFORE the model and all four guards still run per segment AFTER it. Only the inference
  in the middle is shared. A hallucination that arrives alongside three good utterances is
  exactly as dangerous as one that arrives alone.
- **A BATCH IS NEVER WAITED FOR** (`D101`). `_consume` takes what is *already* queued with
  `get_nowait()`. Waiting to fill a batch would trade latency for throughput on a path
  that does not always need throughput, and would add delay to the common case where the
  model is keeping up. If you ever change this to wait, you have built packing without
  the design in `D101`.
- **RESULTS COME BACK INDEX-FOR-INDEX, AND EMPTY UTTERANCES KEEP THEIR SLOT** (`D101`).
  `ThonburianHfEngine.transcribe_batch` gives an empty utterance a placeholder rather than
  filtering it out, because filtering and re-appending would shift every later result onto
  the wrong segment — `B20`'s fluent-sentence-on-the-wrong-moment failure in a new place.
- **THAI DIGIT WORDS LEGITIMATELY REPEAT, AND THE GUARD USED TO EAT THEM** (`B21`).
  `เก้า` is nine; `0989999934` is five of them in a row and it is a real phone number.
  Nine of the twelve prepared calls contain a run of 3+ identical digit words, so the old
  `min_repeats=3` was deleting phone numbers in most calls. `DIGIT_MIN_REPEATS = 10` now
  applies to digits only. **This is a LOOSENING of a safety guard**, so any change near
  `looks_like_a_loop` has to re-check both directions: `B16`'s three word-loops must still
  be caught, and the answer-key phone numbers must still survive. Fixing it moved median
  CER 0.292 -> 0.161 and recovered 8 lines.
- **NEVER "COMPRESS" A REPEATED DIGIT RUN** (`B21`). Collapsing `เก้า เก้า เก้า เก้า` to one
  turns 9999 into 9 — a different, plausible, wrong phone number. A dropped line is
  visibly missing; a wrong number is not. If the de-duplication idea is built, digits are
  fenced out of it and so is the case where the loop IS the whole utterance (`B14`: the
  whole thing was invented, so there is no real prefix to recover).
- **`rtf` IS MEANINGLESS IN A PACED RUN, AND `busy` IS WHAT IT WAS STANDING IN FOR.**
  Pacing makes wall time equal the audio length by construction, so rtf would read ~1.00
  whatever the engine did — the column blanks rather than lie. `busy` (seconds inside the
  model per second of audio) is meaningful in both modes and **above 1.00 the transcriber
  can never catch up**. Measured: 0.16-1.48, median 0.45, one call over 1.00.
- **WHISPER ENCODES A FIXED 30 s WINDOW WHATEVER YOU GIVE IT**, so a 1.5 s clip costs what
  a 25 s one does. The `pad` column measures it: **2.2-3.0, median 2.7** — we ask the GPU
  to encode 2.7x more audio than the call contains. That is the size of the prize for
  batching, and the reference project's speed came from exactly this (`batch_size=4` in
  `…/STT_Thonburian_Whisper/main.py`). **Batching (several clips, one GPU pass) is safe
  and changes no output. PACKING (several utterances glued into one clip) is the bigger
  win and loses per-utterance boundaries — do batching first.**
- **THE PREPARED TEST SET IS NUMBER-HEAVY AND THAT IS NOW A RISK** (`Q30`). Almost every
  call in this corpus ends with a phone number read aloud, so the 12 prepared calls
  over-represent digits. Since `B21` loosened a guard, the set that would catch a
  regression is exactly the set we do not have: speech-heavy calls. Re-prepare with a
  deliberate mix before trusting the loosened guard.
- **THE BUFFER OWES AUDIO TO EVERY QUEUED SEGMENT, NOT JUST TO THE OPEN ONE** (`B20`).
  `_trim` released history 30 s behind the NEWEST segment the moment it was queued, which
  is correct only while the consumer keeps up — and `feed()` never yields, so an unpaced
  feed ingests the whole call before one segment is transcribed. `_awaiting` now holds
  every outstanding claim. If you touch `stream.py`, the invariant is: **nothing below the
  oldest queued segment's start may be released**, and past `_MAX_BACKLOG_SAMPLES` it is
  abandoned with a WARNING rather than silently.
- **`_trim` RUNS ON A SEGMENT CLOSE, SO THE QUIET PATH NEEDED ITS OWN TRIM** (`B20`). A
  leg where nobody speaks closes no segment, so nothing trimmed and the buffer grew about
  30 MB a minute — 16,000,000 samples after 1000 s, measured. Bounded today only by
  `hold.py`'s silence timeout, which belongs to a different object and will not be there
  for `D26`'s agent leg. `_TRIM_WHEN_IDLE_SAMPLES` covers it. **Found by re-reading the
  diff of another fix before committing it.**
- **A SLICE THAT CANNOT BE SATISFIED MUST REFUSE, NEVER APPROXIMATE** (`B20`). `_slice`
  clamped with `max(0, start - base)`, so when the audio was gone it returned the right
  LENGTH from the wrong MOMENT — which in Thai is a fluent sentence attributed to an
  instant the caller was not speaking. `D16`'s hazard exactly: invented text that looks
  credible is the dangerous kind. A gap is recoverable; a confident wrong sentence is not.
- **`--fast` IS NOT A DISPLAY OPTION, IT CHANGES HOW THE SYSTEM IS DRIVEN** (`B20`). It
  was added in `B14` to stop the harness reporting a backlog as a latency, and then every
  subsequent measurement used it — so it hid `B20` *and* hid the fact that the p95 budget
  is missed by up to **40x** (4.5-58.7 s against 1.5 s). Both halves of a measurement flag
  are load-bearing.
- **A `# pragma: no cover` IS A CLAIM THAT A BRANCH CANNOT HAPPEN** (`B20`). The one on
  `if not samples: return` read "only if trimming raced a very long segment" — a correct
  description of the bug, written before it happened, and then not believed. Treat one as
  a hypothesis to test, not a note to yourself.
- **"THREE TURNS ARRIVED" IS A MUCH WEAKER ASSERTION THAN "TURN THREE CARRIED SEGMENT
  THREE'S AUDIO"** (`B20`). Every test in `test_transcription_stream.py` made the first
  kind, which is why a fake engine returning canned text could not see this. `MarkerStt`
  returns the amplitude of what it was handed, so the audio itself is assertable.
- **WHEN AN EXPERIMENT RETURNS EXACTLY NO DIFFERENCE, SUSPECT THE EXPERIMENT** (`B19`).
  Hint vs no-hint gave CER identical to three decimals on four files. That is not a
  finding about domains, it is an argument that was never used - `ThonburianHfEngine`
  reads `hint.language` and drops `hint.vocabulary`. It warns now; it is still not
  implemented. **An engine comparison where the engines disagree about whether they read
  a parameter is not a comparison.**
- **RANK THAI ACCURACY ON CER, NEVER WER** (`B18`). Whitespace WER on unsegmented Thai
  compares one arbitrary segmentation against another and read **0.94-1.12** on a model
  that was working perfectly. `bake_off.py` reports both and ranks on CER.
- **A MODEL ID IS A FACT, NOT A NAMING CONVENTION** (`B17`). `...-combined-ct2` was
  invented from the pattern and does not exist. `D30` already said to re-verify model
  names at implementation time. One HTTP request to the HF API settles it.
- **The DETECTOR is half of any accuracy number.** It decides what the model is even
  asked to transcribe, so a CER measured with `energy` says as much about the VAD as
  about the engine. Real measurements use `--vad silero`.
- **THAI HAS NO SPACES, so any text rule that calls `.split()` is broken by default**
  (`B16`). The repetition guard shipped doing exactly that and caught **0 of 3** real
  Thonburian loops the user had actually seen. It is character-level now. Before writing
  any rule about transcript text, ask what it does on one 200-character token — because
  that is what real Thai output looks like.
- **There are THREE guards between the model and the agent, of three different kinds**
  (`B14`, `B16`, `D98`): the text's shape, its overlap with our own vocabulary hint, and
  whether that much speech was physically possible in the time. They are different kinds
  on purpose — a failure that dodges one rarely dodges all three. `MAX_CHARS_PER_SECOND`
  is **measured** (real Thai: median 7.6, max 15.0; real loops: 39-53), not chosen.
- **NEVER hand Whisper near-silence** (`B14`). One second of digital silence costs **8.6
  seconds** on this GPU — 55x a real utterance — and comes back with invented Thai. Three
  guards exist and none is optional: a level gate before dispatch, `echoes_the_prompt()`,
  and the punctuation-stripping repetition check. The warmup tone is part of this too; both
  adapters warmed on `[0.0] * 16000` and were paying the worst input the model has.
- **`config/stt_vocabulary.yaml` can come back out of the model's mouth** (`B14`). Fed a
  non-speech segment, faster-whisper returned three of its terms, in that file's order, as
  if the caller had said them. Adding a common word there would make `echoes_the_prompt()`
  start eating real sentences; keep it to genuinely rare domain terms.
- **`torch` must come from the CUDA index, never PyPI** (`D95`). The PyPI wheel is CPU-only
  and installing it fails **silently**: everything imports, everything runs, Whisper is ten
  times too slow and `cuda.is_available()` is quietly `False`. Check the version string
  carries `+cu128`.
- **The real GPU figure is 4.00 GiB total and ~3.2 GiB free**, not the "4-6 GB" the older
  docs assumed — the compositor holds the rest.
- **A broad `except Exception` around a thing that is supposed to work turns a failure into
  a skip.** The VAD contract suite did exactly that: `filterwarnings=error` made a
  third-party `DeprecationWarning` raise during model load, and five tests reported a green
  skip while the production detector was tested by nothing. The warning is now ignored by
  name, and the suite only skips on `ImportError`.
- **The media layer must work with NO `ml` extra.** CI runs `uv sync --frozen`. That is why
  `media/audio.py` is pure Python and why `EnergyVad` exists — a path only exercised on the
  one laptop with a GPU is a path nobody runs (`B7`).
- **If you add an optional extra that a test imports, the CI install line is part of the
  change** (`B15`). CI ran `uv sync --frozen` with no extras while `test_api.py` imports
  fastapi at module level, so it could not COLLECT the suite - `B9`'s gap, moved from
  working-tree-vs-repo to working-tree-vs-CI. Fixed to `--extra web`; `--extra ml` stays
  out on purpose (3 GB, no GPU on the runner, and the audio path is dependency-free by
  design so CI still exercises it).
- **The dataset is 22 GB at `krungsri/data/`, outside the repo, and must stay there**
  (`D97`). `Copy of audiofiles.zip` is a second 22 GB and is deletable once the
  extraction is trusted. The prepared subset and `/models/` are gitignored — the
  transcripts are real customer speech with account numbers in them (`D14`).
- **The HF cache already holds FOUR Thai checkpoints, from the user's earlier project**,
  so several bake-off rows cost **no download at all**. Checked 2026-09-04:

  | cached model | size | why it matters |
  |---|---|---|
  | `biodatlab/whisper-th-medium-combined` | 1.6 GB | the current baseline |
  | `biodatlab/whisper-th-large-v3-combined` | 3.1 GB | the large row, free to try |
  | **`biodatlab/distill-whisper-th-large-v3`** | 3.1 GB | **a DISTILLED model — built for speed, and it was not on our list.** Distil-Whisper cuts the decoder to a couple of layers, which is a far bigger latency lever than int8 quantisation. Try it before packing |
  | `Thaweewat/whisper-th-medium-ct2` | 1.5 GB | somebody else's pre-built Thai CT2; different weights from ours, worth a row as a sanity check |

  Total cache ~11.8 GB at `~/.cache/huggingface/hub`.
- **Thonburian medium fp16 uses ~2.75 GiB and peaks near 3.8 of 4.0 GiB** with Silero
  alongside. It fits, with very little room. large-v3 probably will not.
- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`).
- **Assurance gates what the agent may SAY and DO, never what they may SEE** (`D74`).
  The agent sees the whole record from L1 — they need it to verify the caller at all, and
  showing a bank employee the record they were routed is internal processing, not
  disclosure. **L0 renders nothing**, because at L0 there is nobody to render. The controls
  that bind: no name in the opening below L2 (`D55`), playbook steps gated by
  `requires_assurance` (`D56`), `may_act_on_policy` false below L2, every read audited.
  Reverses `D20`'s display gating; `D53`'s DTO **stays** — it was the mechanism, not the
  policy.
- **Gate a rendered sentence on the IDENTITY, not on the payload.** After a rejection the
  frozen snapshot still holds the old policy, so `summary_th` will happily print a number
  the structured fields correctly withheld — a live leak caught mid-change, and `B5` with
  its two halves swapped.
- **NEVER serialise a domain model where a permission boundary exists** (`D53`, `B5`). It
  shipped a real leak: `CaseBrief.model_dump()` sent policy numbers and coverage figures to
  a call at L1. Use a wire DTO, and **test the raw bytes** — an assertion on rendered text
  cannot see a field the renderer never mentions.
- **`enable_utf8()` before printing domain text** — Thai + cp1252 kills the process (`B1`).
  **This bit twice more on 2026-08-25**, both times from a throwaway `python - <<PY` that
  printed a box-drawing character or Thai. The rule is not only about the app: any ad-hoc
  script that prints non-ASCII dies on this laptop. Write to a UTF-8 file and `cat` it, or
  print nothing and check the result separately.
- **The browser preview pane does not composite, so CSS transitions never advance** and
  `getComputedStyle` reads the *start* value forever. Two colours looked identical when they
  were not. This is `B8`'s second lesson in a new place — anything animated cannot be measured
  through that pane. Inject `transition: none !important` first, or test the logic directly.
- **`event` is not usable as a structlog kwarg** (`B2`).
- **`time.monotonic()` is useless for stage timings on Windows** (`B3`) — use `perf_counter`.
- **`ManualClock()` defaults to New Year's Day**, so every `business` queue is CLOSED under
  a default test clock and a placed call returns `queue_closed:holiday`. Pass an explicit
  `start` when hours matter. The default is not changed: scenario golden output depends on it.
- **`zoneinfo` has no tz database on Windows** — `tzdata` is a declared dependency. Without
  it `Asia/Bangkok` raises *only* on the demo laptop, never on CI.
- **The client never sends `customer_id`** (`D4`) — **nor `agent_id`** on the staff side.
  Both come from their own (separate!) session cookie and store.
- **Routing must never depend on the AI** (`D37`); **never let AI delay the call** (`D12`);
  **never let the model produce coverage numbers** (`D16`).
- **An ANI match is probable, not verified** (`D20`). **Assurance goes UP and DOWN mid-call**
  (`D42`) — promotion is a **re-render, not a re-fetch**.
- **Keypad capture is UNTYPED** (`D44`). A lookup returns evidence; only the agent attests.
- **`mask()` is for logs and transcripts, NOT for the agent** (`D58`). The agent asked the
  caller to key those digits and has to read them back. Masking their own screen deletes the
  feature and protects nothing.
- **Never speak a name below L2** (`D55`). The screen shows it; the opening line asks an
  **open** question. A leading question is weaker verification — anyone can answer "yes".
- **Third party DOES promote to L3** (`D65`, reversing `D42`/`D62`). The button asserts the
  agent checked the caller may act for the policyholder, so the level follows the
  attestation. What keeps the log honest is the **outcome**, which stays `third_party` with
  a name and relationship — the record has never said the policyholder was verified, and
  still does not. The earlier reading confused "keep the log truthful" with "keep the level
  low", and only the first was ever the requirement.
- **The identity control re-locks after EVERY attestation** (`D61`). The client learns a new
  one landed from `attestation_count`, which only grows. Watching `attested_outcome` misses a
  confirmed→confirmed correction, and a lock that opens once is worse than no lock.
- **`agent_intent` is a standing instruction, never a live status** (`D59`). It is not
  deselected around a call. Mid-call only `ready`/`last_call`/`draining` are declarable.
  `LAST_CALL` is **spent** when that call ends. Use `intent_reason` to tell the three routes
  into `not_ready` apart — they need different screens.
- **`git add -A` does not mean it is committed** (`B9`). A bare directory name in
  `.gitignore` matches at **every depth**, so `models/` swallowed `src/readycall/db/models/`
  and a whole phase shipped with no ORM package. Every other check this project runs — tests,
  mypy, ruff, the live database — reads the *working tree*. After adding a package, run
  `git ls-files <dir>` and confirm it is not empty.
- **Services write through and read from memory** (`D78`). The store is the durable record;
  the working set is a projection rebuilt by `restore()` at startup. Do **not** add a read
  path that queries the database on a hot path — `excluded_agents()` runs inside the matcher
  tick, and `D39`'s trigger (two processes) has not fired.
- **Half the state is derived on purpose** (`D78`): current presence ← `agent_state_log`, the
  waiting pool ← `call_sessions` in `queued`/`matched`, the live identity ←
  `CallSession.identity`. Adding a table for any of them creates a second answer to a
  question that already has one.
- **A restart never restores `system_state`, `READY` or `LAST_CALL`** (`D78`). The platform
  has given a reconnecting agent nothing to do, and the two intents that invite a call must
  come from the person. *Lunch* does carry across, because they said it.
- **Running the Postgres suite drops its tables** — which is why it has **its own database**
  (`D79`). If `alembic upgrade head` ever no-ops while the app says *"relation does not
  exist"*, the version table is stamped with nothing behind it:
  `uv run alembic stamp base && uv run alembic upgrade head`.
- **Alembic never compares foreign keys** (`D79`). They are unqualified in the model and
  qualified when reflected, so every run proposed churning every FK — with broken DDL. New
  tables still get their FKs inline. A correct autogenerate run produces an **empty**
  migration; that is the check.
- **A fast test path that enforces LESS than production is a fast path that lies** (`D75`).
  SQLite ignores foreign keys unless asked, so the same contract test passed on SQLite and
  failed on Postgres. `PRAGMA foreign_keys=ON` is set on every SQLite connection now.
- **Presence is a projection of `agent_state_log`, not a stored table** (`D76`). Two places
  recording the same fact will disagree, and the log is the one that answers *"what was
  true at 14:03"*. A restart rebuilds standing state from `latest_per_agent()`.
- **Repositories return domain models, never ORM rows** (`D77`). A row carries a session
  lifetime, and the first thing that breaks is a background sweep whose session has closed.
- **Alembic takes its URL from `Settings`, never `alembic.ini`.** A migration against a
  different database than the app opens fails as "table does not exist" and costs an hour.
  Generated migrations also need `import readycall.db.base` — the template does it now,
  because autogenerate emits custom types by full path without importing them.
- **Anything that must happen because TIME PASSED needs a driver, and needs a test in
  which only time passes** (`B7`). `expire_offers`, `dispatch.tick` and `presence.sweep`
  were all written, all correct, and all called by nothing — an ignored offer stranded the
  agent in `OFFERING` for the shift. `sweep_once()` in `api/app.py` now runs them every
  `agent_sweep_interval_s`. Every test drove the system through endpoints, and endpoints
  tick the dispatcher on the way through, so the suite proved the ticking worked without
  proving anything caused it.
- **If the server knows it, the server says it** (`D68`). A client that re-derives a server
  fact creates a second source of truth, and the client's copy is the wrong one. Three
  shipped at once: `savedCalls`, an unread `server_time`, and a hardcoded ACW threshold.
- **`active_call_session_id` stops at `WRAP_UP`.** It goes `null` the instant a wrap-up is
  saved. For "which call am I wrapping up" use `wrapup_call_session_id`, which outlives the
  record because ACW runs to the agent's declaration (`D45`).
- **A lookup reports WHICH rung matched** (`D66`), not just true/false — the whole number
  and four trailing digits are very different evidence for an attestation. Both eras are
  accepted wherever a year is compared (`D67`).
- **The offer card carries a gated brief preview** (`D69`), built from `BriefOut`, never
  from `CaseBrief`. Reaching into the domain model there would reintroduce `B5` in a new
  place; a test asserts the raw bytes of an L1 offer carry no policy number.
- **The workstation renders permissions, it never computes them.** `declarable`, `offerable`
  and what the brief may show are all server decisions. A client that decides will
  eventually disagree, and the client's copy is the wrong one.
- **A control that cannot be pressed must LOOK disabled.** Silently refusing a click reads
  as a broken button.
- **Saving the wrap-up is NOT "done"** (`D45`). ACW runs from media disconnect until the
  agent declares *any* next state. Nothing is auto-saved and nothing auto-readies.
- **A rating is NOT a call state** (`D46`).
- **Hard filters exclude, they do not down-rank** (`D22`). A failed filter is not a low score.
- **A call can go unplaced for two opposite reasons** (`D50`) — roster gap vs capacity. And
  `chosen_agent_id is None` covers a **third** case: a deliberate `DEFER`.
- **Don't make UI state optimistic where the value is read aloud.** The keypad panel did, and
  invented a digit the server did not have.
- **Background refreshes must not set the `busy` flag.** Routing socket pushes through the
  same helper as user actions greyed the whole UI once a second — that was the "flicker",
  and it was not React's fault (`B6`).
- **Timers are `now − server_timestamp`, never "since mount".** Otherwise a refresh mid-call
  restarts the call clock at zero.
- **The server must be restarted to pick up Python changes** — the launch config runs
  uvicorn without `--reload`. Twenty minutes went into "why is `intent_reason` empty".
- **A menu is NOT one clip** (`D80`). Personalised ordering makes a single baked recording
  impossible, so a menu is a lead-in plus one rendered line per option plus the reserved-key
  hint. The labels live only in `menus.yaml`; `voice_prompts.yaml` contains no menu options
  at all, and adding some would create the second copy the whole design avoids.
- **What the caller pressed is NOT what gets stored** (`D81`). With an option promoted, `1`
  means something different on that call. Every press is resolved to canonical the instant it
  arrives, so `menu_path` always means the same thing. If you add a new caller-input path,
  route it through `MenuPresentation.resolve()` — and if you write a scenario, give
  `ScriptedChoices` **canonical** keys, never the ones a caller would hear.
- **A prompt id is a string in one file and a definition in another.** The join is checked in
  both directions, as a startup gate *and* a test, because the failure is silent in the worst
  place: everything passes and the caller hears a gap. Add a `PromptRole` and you must map it
  in `voice_prompts.yaml`, or the app will not boot.
- **`services/` asks for a prompt ROLE, never a prompt id** (`D28`). Same reason as every
  other literal: the id belongs in `config/`.
- **A personalisation fixture that promotes options already in position proves nothing.** One
  test asserted a reordering that never happened, because motor and health are canonically
  keys `1` and `2` and promoting them changed nothing. Use travel/life (`3`/`4`) when you
  want the numbering to actually move.
- **The intake driver STOPS at the answer, on purpose** (`D88`). `run_offer` returns the
  moment the caller presses 1 or 2, and the hold stays in `IntakeService._live`. The three
  things that end a recording — a VAD silence, the max duration, an agent pressing Accept —
  all arrive from **outside**, and the last one arrives on another connection seconds later
  (`D21`). If you "finish" that loop you will be inventing what a media layer that does not
  exist would have reported, which is `B3`/`B4`/`B7`/`B8`'s entire family. The first version
  did exactly this and a scripted caller exposed it: press `1`, run out of script, and the
  loop hears a silence that never happened.
- **A strategy takes TURNS, not frames** (`D88`). The media gateway, the resampler, the VAD
  and the STT worker are one concern and they sit on the far side of the seam. That is the
  only reason the seam has a real implementation, and its own suite, three phases before
  the GPU it will run beside.
- **`declined` and `ignored` are different facts** (`D88`). Pressing 2 writes a
  `granted=False` consent row; saying nothing writes none. An agent looking at a thin brief
  needs to know which — the first means do not raise it, the second means you may. Do not
  "simplify" the refusal row away.
- **The strategy never guesses `degraded`.** No turns can mean the caller said nothing or
  that the transcriber was down, and only the driver can tell. A strategy inventing
  `stt_unavailable` puts a claim on the agent's screen that nothing checked.
- **NEVER speak a queue position or a wait estimate** (`D91`, reversing `D89`). There is no
  line to have a position in: the matcher solves the whole call x agent matrix every tick, so
  arrival order is not an input, and both overtaking directions happen by design — a
  200-second waiter beats a fresh CRITICAL caller on urgency alone, and a fresh CRITICAL
  caller with a better-fitting agent beats the waiter. `queue.position` and
  `queue.position_only` were **deleted** from the pack and from `PromptRole` so the line
  cannot come back by config alone. The mistake `D89` made is worth remembering: *we can
  count a pool* is not *we can rank it*.
- **Speech may change WHO answers and HOW SOON, never WHICH QUEUE** (`D92`). The keypad owns
  `queue_id` and `required_skill`; the intent blend `D23` describes may move `intent_urgency`
  and the fit signals only. P4 is the first code that can break this — write the test with
  the blend, not after it.
- **The matcher's inputs are a frozen snapshot unless something refreshes them** (`B12`).
  `WaitingCall` is frozen and `tick()` rebuilds it with `replace()`; anything not named there
  keeps its admit-time value for the whole call. `waiting_s` is now recomputed from
  `session.queued_at` every tick. **Still fed by nothing:** `is_vulnerable` (it is set on the
  Customer and on the brief, never on the `WaitingCall`), `last_agent_id` / `last_contact_at`
  (set on the context snapshot, never on the `WaitingCall`), and `waiting_credit_s` outside
  the demo path. So `customer_priority` and `continuity` currently score 0 on every real
  call, and `run_matching.py` hides it by generating them synthetically.
- **The wait ceiling is a PRE-PASS, not a guard rail** (`D93`, fixing `B13`), and it is
  **one ceiling PER URGENCY TIER** (`D94`): critical 60 s, high 120 s, normal 180 s, low
  270 s, in `guards.max_wait_before_any_agent_by_urgency`. Never compare a wait against
  `max_wait_before_any_agent_s` directly — that is only the default for an unnamed tier.
  Use `weights.ceiling_for(call.intent_urgency)`. A table whose ceilings RISE with urgency
  fails the boot, because getting it backwards is otherwise silent: every call still
  routes and the crash-scene caller simply waits. When two callers are both past their own
  ceiling the **more urgent** is rescued first, wait breaking ties inside a tier. It runs
  before the solver, in `MatchingEngine._rescue()`. Do not move it back into `_guard`: `_guard` only
  runs for a call the solver **already chose an agent for**, so a ceiling checked there can
  never fire for the caller who lost the matrix — which is the only caller it is for. The
  rescue takes the **lowest**-fit qualified agent on purpose, so specialists stay free.
- **A guard that runs after a selection can only veto that selection, never rescue what it
  skipped** (`B13`'s lesson). Worth applying to any future rule phrased as "past X, do Y":
  ask whether Y is about the *chosen* agent or about the *pool*.
- **Never test a contention rule without contention** (`B13`, `B12`, `B4`). The old ceiling
  test gave one caller an entire free floor; the solver picked them anyway and the assertion
  passed on a decision the rule had not caused.
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `datetime.now()` or a raw random id** outside `clock.py`/`ids.py` (`D35`).
- **There is NO operator key, and `0` is the REPEAT key** (`D86` removed the operator,
  `D90` moved repeat onto it). It is the only reserved key. The way out of a menu is its own
  spoken "เรื่องอื่นๆ" option, which is why `test_every_reason_menu_still_ends_in_a_catch_all`
  is load-bearing rather than tidy. Do not re-add an operator on "it is the convention": it
  routed exactly where the catch-all routes — and `0` is not free any more anyway.
- **Never spell the repeat key into a prompt's text** (`D90`). `menu.invalid` said "กด 9"
  as a literal while `menus.yaml` owned the real value, so moving the key would have left
  the apology naming a digit the IVR no longer honoured — and nothing would have failed.
  It is a declared `{repeat_key}` slot now, and a test asserts it stays one.
- **Never block the state buttons to force a wrap-up** (`D87`). It re-couples what `D45`
  separated, traps an agent who needs to leave, and *incentivises garbage* — forced to file
  before they can go, somebody types "." and saves, and now the record looks filed and is
  worthless. An honest gap beats a dishonest entry. The backlog is the answer.
- **The wrap-up backlog is DERIVED** (`D78`): "ACW ended and no `call_wrapups` row exists".
  Do not add a flag for it — filing clears the entry by construction, and a second copy of
  the fact is a second thing to forget to reset.
- **A wrong keypress is NEVER a strike; only silence is bounded** (`D82`). They are opposite
  evidence — a wrong key proves somebody is there, silence does not — so do not "tidy" them
  back into one symmetrical rule. `runaway_press_guard` is for a stuck DTMF sender, and a
  test asserts it stays ≥ 20 so it cannot quietly become an attempt limit again.
- **`0` must never short-circuit the queue ladder** (`D83`). That was the actual bug behind
  the "operator introduces a path where we know nothing" objection: it jumped to the DID
  default and discarded a product line the caller had already chosen.
- **There is no pre-call identity step, on purpose** (`D84`). Anything that promotes assurance
  with no human in the loop contradicts `D44`. The resolver's `ivr_verified_customer_id` rung
  is deliberately fed by nothing; it is reserved for `D25`'s voicemail path, which has no
  agent at all, and wiring it up owes a decision entry.
- **Ending ACW must close the call** (`B10`). `D45` decouples "I finished the form" from "I am
  done with this call", but a call left in `WRAP_UP` stays the agent's ACTIVE call and the
  screen keeps rendering that customer — then relapses the moment a later call closes. Do not
  fix this by blocking the state buttons: that re-couples exactly what `D45` separated.
- **When the only feedback is something DISAPPEARING, there is no feedback** (`B11`). Success
  and a silently-failed request look identical. Anything whose success is invisible needs an
  acknowledgement.
- **The prose rots the same way the diagrams do.** The `D86` sweep fixed the `.mmd` files and
  missed five *prose* claims that `0` reaches a human — in `ARCHITECTURE.md`'s degradation
  table, `PLAN.md`'s P3 summary, `DATA_MODEL.md`'s note on `pressed`, `DECISIONS.md`'s own
  `D37`, and `diagrams/12_the_menu.md`'s reserved-key table. Grep for the *behaviour*, not
  just the decision id: `grep -rn '`0`' docs/`.
- **A `%% HANDWRITTEN` banner is a CLAIM, and hand-drawn diagrams rot.** The generated ones
  cannot drift; the other 46 can, and a sweep on 2026-08-26 found two still teaching decisions
  that had been **reversed** (`brief_gating` on `D74`, `identity_promotion` on `D65`) plus a
  dozen simply overtaken. When you reverse or amend a decision, `grep -n "D74" docs/diagrams/src/*.mmd`
  and fix every diagram citing it **in the same commit**. A wrong diagram is more convincing
  than wrong prose.
- **A semicolon inside a mermaid `Note` breaks a sequence diagram** — it is parsed as a
  statement separator, and the error points at the *following* line, so it reads like an
  unrelated syntax problem. Use a full stop.
- **`README.md` is the setup/run contract and must not rot.** It is the first thing anyone sees on
  GitHub, and it was badly stale once already (it still said *"Status: planning. No code yet."*
  after six phases had landed, and pointed at the abandoned `DemoProject/`). Anything that changes
  how a piece is installed or started belongs there in the same commit — one section per piece,
  each saying what else has to be running alongside it.
- **`docs/` is excluded from `ruff format`** — the explanations are verbatim records.
- Python is pinned **3.11**: PEP 695 generics are a syntax error; use `Generic[T]`.
- Windows: paths have spaces (quote them); heredocs with apostrophes fail — use the Write tool.

## Diagrams (visual walkthroughs)

`docs/diagrams/` — 69 diagrams with explanations, in 13 themed pages. Start at
`docs/diagrams/README.md`. Two pages cover the parts with no screen: **`11_persistence.md`**
(what survives a restart) and **`12_the_menu.md`** (what the caller actually hears — the
prompt pipeline, why a menu is not one clip, and every path that does not end in a route).
**`the_offer.html`** is the plain-language explanation of everything below the queue line —
written because the terminal summary of `D88` left the user with no idea what had been
built. When a slice is hard to see, write the readable page as part of finishing it, not
after being asked. Both diagram pages have readable twins in `docs/reading/` you can click
through: `persistence.html` has a
**restart simulator**, `the_line.html` has a **working keypad** that walks the real menu and
shows every press resolving back to canonical. Each must be updated with its diagram page —
and `the_line.html`'s data is generated (`scripts/build_reading_data.py`), with a test that
fails when it is stale. **14 of the 69 are generated from source**,
so they cannot drift; `tests/unit/test_diagrams.py` fails if a committed one falls behind.
**`decision_map` was extended to `D1-D109` on 2026-09-06** — it had stopped at `D81`, which is
what a hand-drawn map does when extending it is a redraw. Its banner now states the rule it
follows: a reversed decision is never drawn as though it still stands, it gets a leaf naming
what replaced it (`D1`, `D62`, `D83`, `D89`, `D102`, `D103`). Add a `D#`, add a leaf, same commit.

```bash
uv run python scripts/gen_diagrams.py      # rebuild derived .mmd sources
uv run python scripts/render_diagrams.py   # render all .mmd -> .svg  (needs mermaid-cli)
uv run python scripts/render_diagrams.py --check   # content-hash staleness check
uv run python scripts/build_prompts.py --check     # same idea, for the spoken lines
uv run python scripts/build_reading_data.py       # refresh the keypad page's menu data
```

`mermaid-cli` is not installed globally: `npm i -g @mermaid-js/mermaid-cli`, or set `MMDC`.

## Explanations (plain-language walkthroughs)

`docs/explanations/` — teaching notes, one per phase. **Snapshots, not specifications**;
each has a "changes since" section. Write one per phase as it lands.

- `P0_foundations.md` · `P1_context.md` · `P1b_http_layer.md` · `P2a_matching.md`
- `P2b_workstation.md` — the two axes, the handshake, after-call work, the socket, queue
  hours, the disclosure leak and the invented digit — plus a **"changes since"** section
  covering the `D55`–`D60` review pass and where recommended actions come from.
- `P2c_persistence.md` — the database layer end to end: the seam, three backends against
  one contract suite, **what is stored versus what is derived** (`D78`), the redaction rule
  at rest, the migration papercuts, and how a restart is actually proved. Read §8 before
  assuming something is or is not durable.
- `P3_voice.md` — the prompt pipeline and the IVR: why the guard was written before the
  prompts, the two decisions that only appeared once it was built (`D80`, `D81`), and the two
  test bugs that would have passed review. **§9 says exactly what step 4 has left to do.**
- `P4_broker_and_assist.md` — **the 2026-09-07 work, in one place**: why the domain was an
  insurer's and what changed, playbooks moving to config, the LLM seam with its measured
  4.5 s / $0.0085, and the customer's paired screen. **§5 is a copy-pasteable "try it
  yourself"** including how to watch a personal push get refused, which is the rule worth
  seeing rather than reading. Diagrams: `diagrams/13_broker_and_assist.md`.
- `P2b_workstation_client.md` — **the browser tab itself**: its two channels, the full
  server-owned vs client-owned ledger, every endpoint, the socket contract, and the
  audit that produced `B7`, `B8`, `D68` and `D71`. Read this before changing
  `apps/workstation/`. A **readable, illustrated twin** lives at
  `docs/reading/workstation_wiring.html` — open it in a browser, no build step. Both
  must be updated together.

**Before changing identity, capture or presence, open the `.mmd` sources**, not just the
prose. `diagrams/src/identity_promotion.mmd` carries the open-question rule and the
three-parallel-paths shape, and neither is restated anywhere else. Skipping that cost three
of the six faults in `B6`.

## Before any `/compact`

```bash
uv run python scripts/audit_docs.py
```

Mismatches in `NEXT_SESSION` / `PROJECT_STATE` / `diagrams/*.md` are real bugs. Mismatches
in `explanations/*.md` and `BUG_HISTORY` verification notes are **expected** — those are
dated snapshots, and the fix is a "changes since" entry, never an edit to the body.

## Published artifacts (the readable twins, live)

Update by republishing the **same file path**, which keeps the URL. Republishing without the
URL from another conversation creates a duplicate instead.

| Page | URL |
|---|---|
| `reading/the_line.html` — the keypad | https://claude.ai/code/artifact/5953f7a7-d0c4-4829-8cc7-fec2b6f5b856 |
| `reading/2026-09-03_what_happened.md` — **markdown, not a page.** The 3 September session from zero, with a glossary and the two open decisions. Written for the user after three summaries failed to land | _(a file, no URL)_ |
| `reading/2026-09-05_the_words_on_the_screen.md` — **markdown, not a page.** How the transcript reached the agent's screen, and the two services that turned out to be running nowhere (`B24`). Ends in three commands that put six Thai sentences on a real screen | _(a file, no URL)_ |
| `reading/the_assist_rail.html` — **what the 2026-09-07 work actually does**, in plain language: the broker/insurer duty split drawn, the five call shapes and where each ENDS, the pairing and its one gate drawn, the six ways the AI summary does nothing, and a fifteen-minute "try it" that ends with watching a personal push get REFUSED. Written because a terminal summary of `D117`-`D120` tells the reader nothing | _(a file, no URL)_ |
| `reading/the_broker_turn.html` — **the plan of record from 2026-09-06.** Why the pitch extends from the service call into comparison (step 3, the brief's biggest leak) and document handling (step 4), the broker-vs-agent reframe, the consent correction, the persona recommendation and the seven-day track plan. `D115`/`D116`. **Artifact publishing was blocked, so this one is a file** | _(a file, no URL)_ |
| `reading/the_recording.html` — **what happens to the caller's voice**: the encrypted recording explained from a phone packet to a file in a bucket (envelope encryption drawn, not asserted), the killable decode worker, `Q31`'s circle-back, and the durable transcript. Written because *"the encrypted recording landed"* tells the reader nothing | https://claude.ai/code/artifact/c51ef926-0a13-45b7-b59f-6731be83255c |
| `reading/the_audio_path.html` — the wait ceiling, the GPU, and the audio path, in plain language, **ending in commands that verify each claim** | https://claude.ai/code/artifact/722ddf9f-77bc-410b-8057-7f90a693ec4c |
| `reading/the_offer.html` — what the intake offer is, in plain language | https://claude.ai/code/artifact/2c6fa04d-089f-4f4c-bfa6-fb4abc95a0af |
| `reading/persistence.html` — the restart simulator | https://claude.ai/code/artifact/b2a03f9c-01f2-42bc-ae55-3860d901374d |
| `reading/workstation_wiring.html` | https://claude.ai/code/artifact/23107f9a-17e5-4011-b206-b2e5809a138f |

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock p.7). Brief:
  `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
