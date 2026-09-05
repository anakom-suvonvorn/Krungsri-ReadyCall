# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-09-05._

---

## If you have just been compacted, read this first

_Rewritten 2026-09-04 (night). Everything above the horizon of this block is settled; what
follows is what a fresh session needs and nothing it does not._

**P3 is complete, including the GPU half. `D30` is closed.** The audio path runs end to end
from a WAV file to `IntakeService.on_turn`, an engine is chosen on measurements, and it
meets `ARCHITECTURE` §15's latency budget on every call in the test set — which nothing had
ever done before 2026-09-04.

### The engine, in one table

Paced, 20 real Thai call-centre calls, same detector and guards throughout:

| | Thonburian fp16 | CT2 int8 + hint | **Typhoon (`D104`)** |
|---|---|---|---|
| p95 utterance-end -> turn | 19.5 s / 58.7 s worst | 1.68 s / 2.53 s | **0.19 s / 0.28 s** |
| inside the 1.5 s budget | 0 of 12 | 7 of 20 | **20 of 20** |
| `busy` worst | 1.25 | 0.11 | **0.020** |
| VRAM | 2716 MB | ~1000 MB | 1068 MB |
| CER **mean** | **0.109** | 0.128 | 0.133 |

**Ship Typhoon** (`STT_ENGINE=typhoon`, needs the `asr` extra). **CT2 is the fallback** for
a box where NeMo will not install (`STT_ENGINE=thonburian_ct2`, `STT_MODEL` unset, and run
`scripts/convert_ct2.py` first). Typhoon is ~22% relatively worse on CER than fp16, cannot
use the vocabulary hint at all, and is weaker on spoken digits while better on
conversation — all recorded in `D104` with the mitigation.

### The four days that produced it, and the lesson from each

Read the bug entries before touching the audio path. **Every one of these was found by a
measurement or by the user, and not one by a test.**

| | what it was | the lesson |
|---|---|---|
| `B20` | the buffer released a segment's audio before the model saw it, and `_slice` **clamped** so it returned the wrong moment at the right length | a slice that cannot be satisfied must **refuse**, never approximate |
| `B21` | the repetition guard deleted real phone numbers — a Thai number has **five** identical digit words in a row | ask *"what in this language legitimately repeats?"*, not *"does the guard work?"* |
| `B22` | `close()` freed the model object but not the GPU memory | found by **watching `nvidia-smi`**, not by a failure |
| `B23` | the engine a decision had just chosen **could not be selected by config**. Then it happened again with Typhoon | read the wiring while writing the instructions for it |
| `B19` | one adapter silently dropped the vocabulary hint, so an engine comparison was really a hinted-vs-unhinted one | an experiment returning *exactly no difference* is a broken experiment |

Two methodology traps that each cost a published number:

- **Rank on the CER MEAN, never the median.** At n=20 the median is unstable — two runs of
  an *identical* config gave 0.087 then 0.124 while the mean moved 0.128 -> 0.130. This
  cost `D103` a self-correction hours after it was written.
- **The test set moved the headline by 1.8x.** Same engine, same code: CER median 0.161 on
  the old digit-heavy set, 0.089 on the balanced one. The set is now `--mix --seed 7`.

### What is NOT built, precisely

**The live transcript on the agent's screen — this is the next slice.** Two pieces, not
three, and **less is missing than a first read suggests** — I got this wrong once while
writing this brief and checked before leaving it here:

- ✅ **The event already exists and is already published.** `ev.TranscriptTurnAdded`
  (`domain/events.py`, `"transcript.turn"`) carries the id, seq, speaker role, text,
  timings and confidence — and **`PassiveRecordIntake.on_turn` publishes it on the bus**
  for every turn (`services/intake/passive.py`). `IntakeService.on_turn` hands the turn to
  the strategy, and the strategy is what publishes. So there is nothing to add at the
  intake end.

1. **Nothing subscribes.** `api/realtime.py`'s `AgentHub` is a *push* mechanism —
   `send(agent_id, kind, payload)`, with per-agent sequencing and replay-on-reconnect
   already built (`D68`, `B7`). Nothing takes `transcript.turn` off the bus and calls it.
   That subscriber is the missing server piece, and it should use the existing hub rather
   than a second channel.

   **The design question it runs into, which is not plumbing:** during intake **the call
   is not assigned to anybody yet** — that is the whole point, the transcript is being
   built *before* an agent accepts. So a turn published at that moment has no `agent_id`
   to be sent to. Two halves are needed: buffer the turns against the call, and flush them
   to the agent on accept (the brief preview in the offer card is the precedent, `D69`),
   then stream live once the call is assigned. **Do not invent a second delivery path for
   the live half** — `B6` and `D68` are both about a client growing a second source of
   truth.

2. **Draw it** in `apps/workstation/`. Read `explanations/P2b_workstation_client.md`
   first; `B6` was six faults and three of them came from not reading the `.mmd`/prose
   sources before changing that app.

Two smaller things in the same area:

- **`IntakeService._degradation()` returns `NONE` unconditionally.** That was a *wait*
  until `D96` and is a *gap* now: `TranscriptionService` knows whether the engine failed and
  nothing carries it back. Do not guess `stt_unavailable` — wire it.
- **The decode timeout** (`D98`'s missing half) still needs `D2`'s killable worker process.
  Do not fake it with `asyncio.wait_for`: that does not kill the thread, and a guard that
  looks like one and is not is `B7`'s whole family.

Then: the encrypted recording to object storage (P7 key management), and P4.

## Where things stand right now

**P0 · P1 · P1b · P2a · P2b · P2c complete. P3 complete except the recording-to-storage
and the live transcript on the screen — `D30`'s bake-off is CLOSED (`D104`).** The system knows who
is calling and how much to believe it, why they are calling, everything we hold about them
assembled before the phone is answered, which agent should take it and why, the desk rings
and a human accepts with the screen already right — the caller keys their own way to the
right queue through a real menu hearing real (pre-rendered) Thai — and now, **once the queue
is settled, they are offered the pre-call recording, and take it or
refuse it or ignore it, all three reaching the same agent**.

Verified **2026-09-04 (night)**: **676 tests** — 634 pass + 42 skipped without the Postgres
container (the 42 are the database cases). `ruff check` + `ruff format --check` clean over 178 files,
`mypy --strict` clean over 125, all scenarios replay, 62/62 diagrams current, prompt pack
fresh (54 clips), `audit_docs.py` clean on the live files.

### The four sessions of review since P2b, in one place

Most of the recent work came from the user driving the screen and reporting what was wrong.
The pattern is worth knowing before reading any of it:

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

All five are the same family as `B3` and `B4`: *a confident, plausible, wrong result that
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
· 8 ports each with a fake · call state machine + orchestrator (single writer) · in-memory
event bus.

**P1 — context.** `domainpack.py` · `services/identity/` (the L0–L3 ladder) ·
`services/context/assembler.py` (parallel fan-out, per-field provenance, frozen snapshot) ·
`services/brief/builder.py` · `adapters/core_data/caching.py` (TTL + serve-stale + breaker).

**P1b — the HTTP layer.** `POST /v1/calls/intents` · app context events · contact reasons ·
**customer simulator** at `/sim`, one HTML file, no build step (`D47`). Identity comes from
a `SessionResolver`, never the request body (`D4`).

**P2a — matching.** 15-agent roster · tunable weights with startup validation · hard filters
(skill, **graded** language, capacity, `already_offered`) · fit × urgency · **our own
Hungarian solver** (`D49`) · guard rails · a `MatchingDecision` per call **including
non-assignments**, saying **which** of the two unplaced reasons applies (`D50`).

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

## Designed but NOT built (read before touching these areas)

- **`D63` — call transfer.** One filtered roster menu covering all three needs (named agent /
  department / seniority), with *"let the system choose"* walking the same list in fit order.
  The caller moves **last**: an acceptance notifies the *original* agent, who wraps up with the
  customer and presses Release. A busy receiver can *accept and queue at the front*. Lands with
  P6; reuses the offer handshake, `D52` exclusion, presence and the brief almost wholesale.
- **`D64` — the live matching board.** Callers left, agents right, edges coloured by fit,
  hard-filter exclusions drawn differently from low scores. All the data is already in
  `matching_decisions`. Unscheduled; it would have caught `B4` on sight.

## Next steps (in order)

**Read `B20` first if you have not.** It rewrote what the rest of this list is about: the
accuracy problem was ours and is fixed, and the problem that was underneath it is latency.

1. **~~THE LATENCY~~ — SOLVED. `Q29` is CLOSED** (`D104`). Typhoon ASR meets
   `ARCHITECTURE` §15's 1.5 s budget on **20 of 20 calls**: p95 **0.19 s median, 0.28 s
   worst**, `busy` worst **0.020**. Nine times faster than the CT2 build that superseded
   fp16 the same morning, and ~100x faster than fp16.

   | paced, balanced set | fp16 | CT2 + hint | **Typhoon** |
   |---|---|---|---|
   | p95 median | 19.5 s | 1.68 s | **0.19 s** |
   | inside 1.5 s | 0 of 12 | 7 of 20 | **20 of 20** |
   | `busy` worst | 1.25 | 0.11 | **0.020** |
   | CER mean | **0.109** | 0.128 | 0.133 |

   **`D99` predicted this before it was measured** — a transducer has no 30 s window, so it
   does not pay a full encode per utterance. It is the one prediction on this project that
   was written down first and then confirmed.

   `STT_ENGINE=typhoon` (needs the `asr` extra). **CT2 is the documented fallback** for a
   box where NeMo will not install.

   **Both of the things left open on it are now answered** (`D104`):
   - **`D99`'s silence prediction is CONFIRMED.** 1 s of digital silence: Typhoon **149 ms,
     empty**; Whisper (`B14`) **8578 ms and invented Thai**, including our own hint terms.
     Same for hiss and a tone. That removes `D16`'s dangerous failure mode rather than
     catching it downstream. **The three guards stay anyway** — microseconds, and they are
     the degradation path if the engine is swapped back to the CT2 fallback.
   - **The 3 missing turns are endpointer-cut fragments, not a short-utterance weakness.**
     Tested against real annotated spans at every duration: Typhoon returned empty on
     **0 of 39**. It declines fragments whose boundaries are *ours*; Whisper guesses at
     them. Which is better is genuinely open.
   - **The real weakness is digits.** Split by digit share: speech-heavy calls Typhoon
     **0.137** vs CT2 0.171 (better); digit-heavy calls Typhoon 0.132 vs CT2 **0.099**
     (worse). A 0.067 swing. Mitigated by architecture rather than by the model — `D44`'s
     keypad is how a policy number actually arrives and `D20`'s ANI gives the calling
     number, so spoken digits are corroboration, not the record. **If that stops being
     true, re-open `D104` and re-check the digit-heavy row.**

2. **~~The CT2 build~~ — now the fallback** (`D103`, superseded as first choice).
   `D30` has its answer, measured paced on the **balanced** 20-call set with both engines
   treated the same:

   | | Thonburian fp16 | **CT2 int8 + hint** |
   |---|---|---|
   | p95 median | 19.5 s | **1.68 s** |
   | p95 worst | 58.7 s | **2.53 s** |
   | inside the 1.5 s budget | 0 of 12 | **7 of 20** |
   | `busy` worst | 1.25 (over 1.00!) | **0.11** |
   | VRAM | 2716 MB | **~1000 MB** |
   | CER **mean** | **0.109** | 0.128 (17% worse) |

   **It is a trade, not a free win** — that is the correction `D103` had to make to itself.
   Ship it anyway: `D12` means a transcript arriving 58 s late is an empty screen.

   **The stability problem is gone**: nothing is near `busy` 1.00, so the compounding
   backlog that produced the 23-59 s latencies has no case that triggers it. The 1.5 s
   budget is still missed, but by **1.1-1.7x instead of 13-39x**. Cost is CER 0.161 ->
   0.182, which is worth paying — a slightly less accurate transcript is still a
   transcript; one that arrives a minute late is an empty screen.

   **Convert it before anything else on a fresh machine:**
   ```bash
   uv run python scripts/convert_ct2.py     # ~1 min, no download if the HF cache is warm
   ```

   **To close the last 1.1-1.7x, in cost order:**
   - **the `distill-whisper-th-large-v3` checkpoint is ALREADY in the HF cache** (3.1 GB,
     paid for by the earlier project). A distilled model cuts the decoder to a couple of
     layers, which is a bigger lever than quantisation and costs nothing to try.
   - **Typhoon** (`D99`, NeMo installed): a transducer with **no 30 s window at all**, so
     it is the one candidate that could make packing unnecessary.
   - **packing** (`D101`), in the user's minimum-threshold form. Now a refinement rather
     than a rescue.

2. **THE OLD LATENCY ITEM, kept for the reasoning** (`Q29`, `D101`).
   **Batching is built and measured as a NULL RESULT on this GPU** — `busy` median
   0.45 -> 0.45, worst 1.48 -> 1.46, per-call change -7% to +13% averaging zero, with CER
   and turn counts identical. It is kept (correct, tested, 1 MB of VRAM, changes no output,
   and the demo machine may have the spare capacity that makes it pay) but **do not expect
   it to help here, and do not repeat the inference that the earlier project's
   `batch_size=4` explains its speed** — that was my guess and the measurement refuted it.
   Batching *overlaps* work; on a card with no spare capacity there is nothing to overlap.
   **PACKING is therefore the front-runner, and `D101` records its design** in the user's
   improved form: pack past a configurable **minimum** speech duration (~1.5-5 s) rather
   than toward the 30 s maximum. It is the only lever in this design that removes
   arithmetic rather than rescheduling it — seven 30 s windows per call become one or two.
   It also keeps the delay and boundary loss to a fraction of the naive full-window
   version, and — the part that matters more than throughput — **stops us handing the model
   single-word clips**, which is the input `B14` measured at 8.6 s and invented Thai.
   **Run the CT2 and Typhoon rows first** (cheaper, and a cheaper 30 s window is the other
   real lever); build packing if `busy` is still near 1.00 after them.

   The original problem, for reference:
   (`Q29`). Paced over all 12 real calls, p95 utterance-end to turn ranges
   **4.5 s to 58.7 s** against `ARCHITECTURE` §15's **1.5 s**. It was invisible because
   every measurement used `--fast`, which blanks the latency column — the flag added in
   `B14` *to stop the harness lying about latency* became the reason nobody measured it.

   **The distribution is bimodal and it tracks throughput exactly**, which is the
   actionable part:

  | throughput (rtf) | files | p95 utterance-end -> turn |
  |---|---|---|
  | 0.19 - 0.31 | 6 | **4.5 - 8.0 s** |
  | 0.65 - 0.90 | 5 | **31 - 49 s** |
  | 1.50 | 1 | **59 s** |

   **This is not "a bit over budget", it is a stability threshold.** Thonburian medium
   fp16 on this card is at or beyond real time for half these calls, and once decode is
   slower than speech the transcriber can never catch up — the backlog compounds for the
   rest of the call and the last utterance arrives a minute late. One consumer serialises
   by design (`D100` says why a second one is not the fix). **The engine has to get
   faster**; nothing else in this design can absorb rtf > 1.
   - `D100`'s 120 s backlog cap **never fired** and no segment was abandoned, so these are
     honest end-to-end latencies, not truncated ones.
   - **Do not read the low-rtf rows as the answer.** The same engine produced both halves;
     what varies is the call.
   - **Measure paced.** It takes as long as the audio does, and that is the point.
3. **Finish `D30`'s table.** The engine is chosen (`D102`); what is missing are the rows
   that could close the last 1.1-1.7x. Everything they need is on this machine.
   ```bash
   uv run python scripts/bake_off.py --engines thonburian --vad silero \
       --audio "tests/audio/thai_calls/*.wav" --out bakeoff.txt --dump transcripts.txt
   ```
   **Rank on CER, never WER** (`B18`), and **read the dump** — three bugs have now been
   found by looking at the text behind a number and none by a test. Missing rows, in order
   of what they settle:
   - **the CT2 row.** `uv run python scripts/convert_ct2.py` converts Thonburian once
     (it publishes no CT2 build — that was `B17`), then
     `--engines faster_whisper:models/whisper-th-medium-combined-ct2`. `int8_float16` on a
     4 GiB card is the most likely fix for the latency, and this row prices its accuracy
     cost.
   - **the Typhoon row** (`D99`): a **transducer**, so no 30 s padding — the structural
     reason it might not have this problem at all rather than merely less of it. Needs
     `nemo_toolkit[asr]` as its own `asr` extra — **ask first**, it is another large
     download.
   - **the large-v3 row**: `--engines thonburian:biodatlab/whisper-th-large-v3-combined`.
     Probably will not fit in 3.2 GiB alongside anything; finding that out is the point.
   - record the table in `PROJECT_STATE` §8 and pick the engine on it.
4. **Decide `Q28` before quoting a CER to anyone.** The reference writes brand and place
   names in **Latin** while the model correctly transliterates them into **Thai**, and CER
   charges every character of a right answer. On the two worst files that is most of the
   residual. Either normalise both sides, or report the number with those spans excluded
   and say so — but **do not edit the ground truth to match the model.**
5. **`B19`: implement the vocabulary hint in `ThonburianHfEngine`**
   (`processor.get_prompt_ids()` -> `prompt_ids`) and re-measure. It warns loudly now, so a
   run can no longer be quietly unhinted, but an engine comparison where the engines
   disagree about whether they read a parameter is not a comparison.
6. **The decode timeout** (`D98`'s missing half). The rate guard *detects* a runaway; only
   a killable worker process can *stop* one, and `D2` already plans `entrypoints/stt.py`.
   Do not fake it with `asyncio.wait_for` — that does not kill the thread, and a guard that
   looks like one and is not is `B7`'s whole family.
7. **The encrypted recording to object storage.** `ARCHITECTURE` §6 asks the gateway for it;
   it needs MinIO wired and per-recording key refs, which is P7's key management.
8. **The live transcript on the workstation.** Turns exist and are published; nothing draws
   them.
9. **P4** — analysis and brief v2+ with Claude and Typhoon compared on the golden set.
10. **`D85` is implemented and parked.** Wire `acw_stats.py` into `expected_free_in()` when
   P6 brings real ACW data, as a **score, never a filter** (`D73`).
11. **Wire the matcher inputs that are fed by nothing** (`B12`): `is_vulnerable`,
    `last_agent_id`, `last_contact_at` are set on the Customer / brief / snapshot but never
    on the `WaitingCall`, so `customer_priority` and `continuity` score 0 on every real call.
12. **Small:** `call_intents` / `app_context_events` still in memory · `D64` the live
    matching board · `Q26` the env var that changes nothing.

### Settled this session, so nobody re-opens them

- **The CER is explained** — it was `B20`, not the model, not the reference, not the
  detector, not the audio. Corrected: **CER 0.09-0.50, median 0.29** over 12 real calls.
- **The endpointer is scored and it is fine.** `scripts/score_endpointer.py` (the first
  thing to read `segments.tsv`): coverage **0.797**, span recall **0.902**, 4.7 s of false
  alarm over 940 s. Dropping `D9`'s threshold from 0.65 to 0.15 buys only 0.86 coverage,
  and the seconds it "misses" are **86% near-silent and 72% within half a second of an
  annotated boundary** — an annotator rounding outward. Real speech lost: **3%**.
  **Leave `D9`'s inherited constants alone; they are right for this audio.**

## Starting P3 step 4b — read this before opening anything else

Steps 1–3 and **step 4a (the offer)** are done and needed no GPU. **What is left is the GPU
half**, and it is the only part of the whole project with hardware risk.
`explanations/P3_voice.md` covers what was built and why; `diagrams/12_the_menu.md` draws it,
including §12.6 on the hold.

**The socket is already there.** `IntakeService.on_turn(call_session_id, turn)` accepts a
`TranscriptTurn` and publishes it; `on_silence` and `on_max_duration` end a recording. Today
only tests and the scenario runner call them. Step 4b is the thing that turns audio into
those calls — and nothing above it has to change when it lands.

### What now exists that step 4 plugs into

| Already there | Why it matters |
|---|---|
| **`services/ivr/`** | the walk is real and drives the simulated telephony adapter. Step 4 adds what happens *after* the queue, not another menu. |
| **`voice_prompts.yaml`** | `intake.offer`, `intake.start`, `intake.done`, `intake.declined`, `intake.reoffer`, `voicemail.*` and `rating.request` are **written, rendered and mapped to roles**. They are text waiting for the machinery that plays them; nothing calls those roles yet. |
| **`ports/stt.py`** | streaming-first (`D9`): `AudioFrame` is **16 kHz mono float32, always**, and the media gateway normalises before anyone sees it. Per-utterance and in-memory, so no PII lands on disk. |
| **`services/intake/`** | the offer, the consent, the strategy seam and the live-hold registry all exist (`D88`). `on_turn` / `on_silence` / `on_max_duration` are the three entry points the media side drives. |
| **`adapters/stt/scripted.py`** | the fake that keeps every test and the stage-safe demo path off the GPU. It honours `warmup()`, so swapping to Thonburian is one env var. |
| **`IvrResult`** | already carries `queue_id`, the intent and its `intent_source`. Whatever runs intake reads a finished routing decision rather than making one. |

### What does NOT exist (checked against disk 2026-08-25)

`prompts/th/` (the LLM prompt tree — `prompts/voice/manifest.json` is the *audio* one and
does exist) · `services/transcription/` · `services/analysis/` · `media/` · `workers/` ·
`observability/` · `config/core_mapping.yaml` · `tests/golden/`
*(`services/intake/` existed on this list until 2026-08-31. It exists now.)*

**And the `ml` extra is still commented out in `pyproject.toml`**, so `uv sync --extra ml`
fails today. The intended set is on that commented line: `torch`, `transformers`,
`faster-whisper`, `onnxruntime`, `silero-vad`. Declaring it is step zero and it is not free
— this is the install that takes a while on a metered connection, and it lands on the STT
box only (`D2`). It was deliberately **not** done this session, because nothing in steps 1–3
needed it and an unused multi-gigabyte dependency in the lockfile is a cost with no payer.

### The decisions that already constrain step 4 — do not re-litigate

- **`D12`: the call is never blocked on AI**, and **`D37`**: routing is already settled by
  the time any of this runs. Every failure here degrades to a call that is routed correctly
  with a menu-derived brief — which is exactly what `anonymous_declined` already replays.
- **`D9`: STT is re-implemented streaming-first.** The reference project
  (`…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`) is **read-only** and is
  reference for *how the model behaves*, never code to copy. Keep: VAD threshold 0.65, min
  speech 500 ms, min silence 100 ms, ~120/60 ms padding, and the repetition guard for
  Whisper's silence-loop. Change: `silero-vad` as a dependency, never a runtime
  `torch.hub.load` — a network fetch during a live call is unacceptable.
- **`D21`: the offer window IS the intake grace period.** Intake keeps recording until the
  agent presses Accept; nobody waits longer and no sentence is lost. **Already wired**:
  `accept_offer` calls `intake.on_agent_accepted` before `assignments.accept`, and it
  finalises as `is_partial=True`. Proved on a running server, not just in a test.
- **`D26`: both legs are forked separately** — speaker labels come from the topology, not
  from a diarisation model.
- **`D30`**: Thonburian stays default; Typhoon ASR is benchmarked against it on the same
  audio rather than argued about.
- **`D10`: intake is a STRATEGY.** `PassiveRecordIntake` **is built**, same `IntakeResult` as
  the future conversational one. Do not inline it into the orchestrator — and note `D88`: the
  strategy takes **turns, not frames**, so the transcriber sits on the far side of the seam.

### Suggested order

1. **`uv add --optional ml …`** first — it is the slow one, and everything else can be
   written while it downloads. **Ask before running it**: it is a multi-gigabyte download on
   the user's laptop, and the docs' own note applies — an unused dependency in the lockfile
   is a cost with no payer until something needs it.
2. ~~The intake offer~~ — **done** (`D88`).
3. **Media gateway + VAD**, against a WAV file rather than a phone, so endpointing can be
   tuned without telephony. It ends by calling `IntakeService.on_turn` / `on_silence`.
4. **The STT worker and the bake-off**, which is where the 3050 risk actually is.

### The hardware reality, stated plainly

RTX 3050 laptop, 4–6 GB. Whisper pads every chunk to 30 s, so `faster-whisper`/CTranslate2 at
`int8_float16` is probably required to hit the p95 < 1.5 s budget. **Do not plan to run a
local LLM and Whisper on the same card** — the default split is STT local, LLM via API.
Whoever has the strongest GPU should own the demo machine.

## Still open

| # | Question | Current default |
|---|---|---|
| Q7 | Intent taxonomy + menu wording | **User: leave as-is, revisit during the hackathon.** |
| Q8 | Typhoon model ids / licence / pricing | Verify against live docs when writing the adapter |
| Q9 | `OFFER_TIMEOUT_S=20`, ACW thresholds | Guesses; tune against how a real agent works |
| Q11 | Language menu wording when English lands | `preferred` vs `acceptable` modelled (`D38`) |
| Q12 | Which challenges count for promotion to L3 | 4 named + `other` free text (`D57`); confirm the named list with Krungsri |
| Q13 | Does a third-party caller need a named representative | Assume yes; `Policy` has no `representatives` field yet |
| Q15 | Matching weights are guesses | Tune against real volumes; `--compare` exists to re-measure |
| **Q16** | **A keypad lookup confirms a policy number at L1.** The caller supplied the digits and the agent must not read them aloud below L2 — but it is a confirmation oracle. Designed this way in `D44`; worth a second look. | Allowed |
| **Q17** | **Commit `apps/workstation/dist/`?** It is gitignored, so a fresh clone has no workstation until `npm run build` runs — and on a venue with no internet, `npm install` is what fails. | Not committed |
| **Q18** | **"Not this person" is a one-way door.** It clears the customer exactly as `D42` asks, but leaves the agent with nobody to attach the call to, and customer search does not exist (`D32` defers lookup). A rejected call stays anonymous for its duration. A test asserts this so it fails the day search lands. **Now visible rather than silent (`D61`)**: the two forward outcomes are disabled with the reason in the tooltip instead of answering 400. | Accepted for now |
| **Q19** | **`config/playbooks/` does not exist** but is in the folder map. Actions live in `_PLAYBOOKS` in `builder.py` (`D56`). Moving them out is a P4 task. | Deferred to P4 |
| **Q20** | **Should a reveal-on-click with a per-field audit entry come back at P7**, for the most sensitive fields only? `D74` opened display to the agent; the honest answer depends on Krungsri's own agent-desktop policy, which we do not have. | Not for now; every read is logged |
| **Q21** | **Which storage backend does the DEMO run on?** `memory` is the default and needs nothing; `postgres` is what survives a restart, and it is what makes the persistence work visible on stage at all. Running it on the day adds a container to the list of things that can fail, against `PLAN.md`'s risk register — *never depend on the venue*. Leaning: **rehearse on `postgres`, keep `memory` as the one-keystroke fallback**, since both pass the same suite. | Not decided |

| **Q22** | **Does the committed prompt pack carry actual audio once a real voice is chosen?** `D24` calls the checked-in pack the offline fallback, which is the whole reason the IVR works with no internet — but `CLAUDE.md` says never commit audio. That rule means *call recordings*, not TTS output of our own sentences, so the two are probably compatible; 63 short Thai clips is a few MB. Undecided because there is no audio yet. | Manifest only, for now |
| **Q30** | **The prepared test set is number-heavy.** Almost every call in this corpus ends with a phone number read aloud, so the 12 prepared calls over-represent digits and under-represent ordinary conversation. That was harmless until `B21` **loosened** the repetition guard for digits — the set that would catch a regression from that loosening is exactly the speech-heavy set we do not have. Re-prepare with a deliberate mix (the user raised this; they are right). | **Tool built 2026-09-04**: `prepare_dataset.py --mix` caps the digit-heavy share and prints a `digit%` column. **The set itself has not been re-prepared yet** |
| **Q28** | **The reference mixes scripts, and CER charges us for being right.** The dataset's transcripts write brand and place names in **Latin** (`True move`, `Mezzox Drip Cafe`, `Frosen Khaoyai`, `Router`, `L O S`) while Thonburian correctly transliterates them into Thai (`ทูมู`, `เมโซเอ็กซ์ดิสกาแฟ`, `โฟร์เซนต์ เขา ใหญ่`). Every character of those differs, so a *correct* transcription is scored as a total miss, and on the two worst files that is most of the residual CER. Options: normalise both sides through a transliteration map before scoring (real work, and it can flatter); report CER with those spans excluded and say so; or accept it and treat the number as a floor. **Do not quietly "fix" the reference** — editing ground truth to match the model is how a metric stops meaning anything. | **Decided 2026-09-04: one headline + one diagnostic.** `bake_off.py` reports `CER` (the only ranking metric) and `CERth` (Latin spans stripped from both sides). The GAP between them is the answer; three competing scores would just move the argument. Not ranked on `CERth` because that excuses every engine from the words it is most likely to get wrong. **And it does not block the engine choice** — the mismatch hits every engine equally, so it distorts the absolute number, not the ranking |
| **Q29** | **The p95 latency runs from 4.5 s to 58.7 s against a 1.5 s budget**, and the spread tracks throughput: at rtf <= 0.31 it is 4.5-8 s, at rtf >= 0.65 it is 31-59 s, because once decode is slower than speech the backlog compounds for the rest of the call. `D30`'s table is the thing that decides what to do. Thonburian medium fp16 takes ~3 s per utterance on this card and one consumer serialises them, so three short phrases in four seconds queue up. Candidates, and they are not exclusive: the **CT2 int8_float16 build** (`scripts/convert_ct2.py`, this is the row that was always meant to decide it), **Typhoon** (a transducer, so no 30 s padding — `D99` says exactly why this might be structural rather than incremental), a **smaller Thonburian**, or accepting a slower transcript because `D12` means the call is never waiting on it. | Not decided; measure before choosing |
| **Q27** | **The dataset is all `Government` domain, not insurance.** All 3189 calls (`D97`). It measures Thai telephone ASR honestly and says nothing about insurance jargon — and our `stt_vocabulary.yaml` hint is *wrong* for it, which makes it a fair test of whether the hint hurts when it does not apply. An insurance-domain set would still be worth having, and the hackathon may supply one. | Use it, and label the numbers as general Thai |
| **Q26** | **`Settings.max_wait_before_any_agent_s` is an env var that changes nothing.** The matcher reads `config/matching_weights.yaml`, never `Settings`, so `MAX_WAIT_BEFORE_ANY_AGENT_S=30` in `.env` silently does nothing — and since `D94` it also describes a shape (one number) the system no longer has. It survives only as the bound for a startup coherence check against `target_wait_s`. Delete it, or wire the weights loader to it. Found while writing `D94`. | Left in place, documented |
| **Q24** | **A health-line caller speaks health data into a recording nobody consented to hold as such.** `D14` makes `health_data` a separate scope; the offer grants only `recording` and `ai_processing` (`D88`). Three options: a third keypress (honest, and it lengthens the longest prompt in the system on the line where callers are most distressed); name the scope in the offer's wording on health lines (one keypress, three scopes); or gate the *extraction* at P4 so health entities are never pulled without it. **Leaning: the second plus the third.** Decide before P4 writes an entity extractor — that is the first code that can breach it. | Not asked for |
| **Q23** | **Personalised menus renumber, and a human on a real keypad has no `ScriptedChoices`.** Every automated caller presses canonical keys and is translated (`D81`), so nothing in the suite or the demo endpoint can get this wrong. But at P5 a person reading a rehearsal script off paper will press what the script says, and for a recognised persona the numbers may have moved. Either rehearse with the persona that will actually be used, or set `personalisation.enabled: false` for the demo. | Enabled; decide before the day |

Resolved: **`Q25` — the wait ceiling is now per urgency tier (`D94`)**, so an emergency reaches its guarantee at 60 s while a routine caller is still 120 s from theirs; when both are past their own, the more urgent goes first · rating is an event (`D46`) · single project (`D34`) · Asterisk · RTX 3050 · Claude
+ Typhoon compared · React workstation with the softphone in it · web customer simulator ·
menu-first flow (`D37`).

## The machine, as left on 2026-09-01

Facts about *this laptop* rather than the repo, so a fresh session does not rediscover them.

- **Docker works** (v29.2.0) and the Postgres container is **stopped**, not removed — it was
  brought up on 2026-08-25 to verify the P3 numbers and stopped again. Bring it back with
  `docker compose -f infra/docker-compose.yml up -d postgres`. Everything runs without it; with the
  container down the database cases skip (**454 pass, 42 skipped**) and with it up they all
  run (**493 pass, 3 skipped** — the three are FK cases the in-memory backend cannot have).
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

`docs/diagrams/` — diagrams with explanations, in themed pages. Start at
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
fails when it is stale. **14 of the 60 are generated from source**,
so they cannot drift; `tests/unit/test_diagrams.py` fails if a committed one falls behind.

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
| `reading/the_audio_path.html` — the wait ceiling, the GPU, and the audio path, in plain language, **ending in commands that verify each claim** | https://claude.ai/code/artifact/722ddf9f-77bc-410b-8057-7f90a693ec4c |
| `reading/the_offer.html` — what the intake offer is, in plain language | https://claude.ai/code/artifact/2c6fa04d-089f-4f4c-bfa6-fb4abc95a0af |
| `reading/persistence.html` — the restart simulator | https://claude.ai/code/artifact/b2a03f9c-01f2-42bc-ae55-3860d901374d |
| `reading/workstation_wiring.html` | https://claude.ai/code/artifact/23107f9a-17e5-4011-b206-b2e5809a138f |

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock p.7). Brief:
  `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
