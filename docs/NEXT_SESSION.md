# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-25._

---

## Where things stand right now

**P0 · P1 · P1b · P2a · P2b · P2c complete. P3 steps 1–3 done.** The system knows who is
calling and how much to believe it, why they are calling, everything we hold about them
assembled before the phone is answered, which agent should take it and why, the desk rings
and a human accepts with the screen already right — and now **the caller keys their own way
to the right queue through a real menu, hearing real (pre-rendered) Thai**.

Verified **2026-08-25**: **496 tests** — 454 pass + 42 skipped without the Postgres
container, all 496 with it. `ruff check` + `ruff format --check` clean over 141 files,
`mypy --strict` clean over 106, all scenarios replay, 60/60 diagrams current.

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

All four are the same family as `B3` and `B4`: *a confident, plausible, wrong result that
no test could see.* When something looks fine, check that it is actually running — and,
since `B9`, check that it is actually **committed**: every other verification in this
project is a statement about the working tree, not about the repository.

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

**P3 (steps 1–3) — the line.** `config/voice_prompts.yaml`: **32 prompts**, declared slots,
and a `flow:` table mapping **19 roles** to ids so `services/` holds no prompt literals
(`D28`) · the guard that every referenced id resolves, **both directions**, as a startup
gate *and* a test · `scripts/build_prompts.py` hash-cached by (text, voice, engine), deduped
by rendered text to **63 clips**, committed manifest asserted fresh · **`services/ivr/`** —
greeting + notice → product menu (skipped when the DID or app said) → reason menu → queue,
with `0` always reaching a human, `9` free, and every failure path ending in a queue rather
than a hang-up · personalised ordering with its evidence · **a menu is composed, not one
clip** (`D80`) · **`menu_path` is canonical whatever was pressed** (`D81`) · both fake IVR
walks retired — `run_scenario.py`'s `# P1:` and `demo.py`'s `# P2b:`.

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

1. **P3 step 4** — media, VAD and the STT worker. The only part with hardware risk, and the
   only part of P3 not started. Full briefing below.
2. **P4** — analysis and brief v2+ with Claude and Typhoon compared on the golden set.
3. **Small and worth doing when convenient:**
   - `call_intents` and `app_context_events` are still in memory. Neither loses anything a
     restart cares about — an intent expires in 15 minutes and screen events are TTL-pruned
     — which is why they were left, but the tables are trivial if the demo ever needs them.
   - **`D64`, the live matching board.** All the data is now durable *and* queryable, which
     is most of the work; it would have caught `B4` on sight.

`grep -rn "# P2b:" src/` lists the steps a real IVR will drive that the demo endpoint fakes.

## Starting P3 step 4 — read this before opening anything else

Steps 1–3 are done and needed no GPU. **Step 4 is the GPU half**, and it is the only part
of the whole project with hardware risk. `explanations/P3_voice.md` covers what was built
and why; `diagrams/12_the_menu.md` draws it.

### What now exists that step 4 plugs into

| Already there | Why it matters |
|---|---|
| **`services/ivr/`** | the walk is real and drives the simulated telephony adapter. Step 4 adds what happens *after* the queue, not another menu. |
| **`voice_prompts.yaml`** | `intake.offer`, `intake.start`, `intake.done`, `intake.declined`, `intake.reoffer`, `voicemail.*` and `rating.request` are **written, rendered and mapped to roles**. They are text waiting for the machinery that plays them; nothing calls those roles yet. |
| **`ports/stt.py`** | streaming-first (`D9`): `AudioFrame` is **16 kHz mono float32, always**, and the media gateway normalises before anyone sees it. Per-utterance and in-memory, so no PII lands on disk. |
| **`adapters/stt/scripted.py`** | the fake that keeps every test and the stage-safe demo path off the GPU. It honours `warmup()`, so swapping to Thonburian is one env var. |
| **`IvrResult`** | already carries `queue_id`, the intent and its `intent_source`. Whatever runs intake reads a finished routing decision rather than making one. |

### What does NOT exist (checked against disk 2026-08-25)

`prompts/th/` (the LLM prompt tree — `prompts/voice/manifest.json` is the *audio* one and
does exist) · `services/intake/` · `services/transcription/` · `services/analysis/` ·
`media/` · `workers/` · `observability/` · `config/core_mapping.yaml` · `tests/golden/`

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
  agent presses Accept; nobody waits longer and no sentence is lost.
- **`D26`: both legs are forked separately** — speaker labels come from the topology, not
  from a diarisation model.
- **`D30`**: Thonburian stays default; Typhoon ASR is benchmarked against it on the same
  audio rather than argued about.
- **`D10`: intake is a STRATEGY.** `PassiveRecordIntake` for v1, same `IntakeResult` as the
  future conversational one. Do not inline it into the orchestrator.

### Suggested order

1. **`uv add --optional ml …`** first — it is the slow one, and everything else can be
   written while it downloads.
2. **The intake offer in the IVR** (press 1 / press 2, re-offer once). Pure keypad, no
   audio, and it finishes the flow the prompts already describe.
3. **Media gateway + VAD**, against a WAV file rather than a phone, so endpointing can be
   tuned without telephony.
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
| **Q23** | **Personalised menus renumber, and a human on a real keypad has no `ScriptedChoices`.** Every automated caller presses canonical keys and is translated (`D81`), so nothing in the suite or the demo endpoint can get this wrong. But at P5 a person reading a rehearsal script off paper will press what the script says, and for a recognised persona the numbers may have moved. Either rehearse with the persona that will actually be used, or set `personalisation.enabled: false` for the demo. | Enabled; decide before the day |

Resolved: rating is an event (`D46`) · single project (`D34`) · Asterisk · RTX 3050 · Claude
+ Typhoon compared · React workstation with the softphone in it · web customer simulator ·
menu-first flow (`D37`).

## The machine, as left on 2026-08-25

Facts about *this laptop* rather than the repo, so a fresh session does not rediscover them.

- **Docker works** (v29.2.0) and the Postgres container is **stopped**, not removed. Bring it
  back with `docker compose -f infra/docker-compose.yml up -d postgres`. Everything runs
  without it; with the container down the database cases skip (**399 pass, 42 skipped**)
  and with it up they all run (**438 pass, 3 skipped** — the three are FK cases the
  in-memory backend cannot have).
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
- The **dev database currently holds one call** from the live restart check. Harmless; the
  suite no longer touches that database at all.

## Things to be careful about (live landmines)

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
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `datetime.now()` or a raw random id** outside `clock.py`/`ids.py` (`D35`).
- **`docs/` is excluded from `ruff format`** — the explanations are verbatim records.
- Python is pinned **3.11**: PEP 695 generics are a syntax error; use `Generic[T]`.
- Windows: paths have spaces (quote them); heredocs with apostrophes fail — use the Write tool.

## Diagrams (visual walkthroughs)

`docs/diagrams/` — diagrams with explanations, in themed pages. Start at
`docs/diagrams/README.md`. Two pages cover the parts with no screen: **`11_persistence.md`**
(what survives a restart) and **`12_the_menu.md`** (what the caller actually hears — the
prompt pipeline, why a menu is not one clip, and every path that does not end in a route).
Both have readable twins in `docs/reading/` you can click through: `persistence.html` has a
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
| `reading/persistence.html` — the restart simulator | https://claude.ai/code/artifact/b2a03f9c-01f2-42bc-ae55-3860d901374d |
| `reading/workstation_wiring.html` | https://claude.ai/code/artifact/23107f9a-17e5-4011-b206-b2e5809a138f |

## Handy references

- Pitch deck: `../Krungsri.pdf` (workstation mock p.7). Brief:
  `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`.
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
