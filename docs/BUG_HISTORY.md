# BUG_HISTORY

_Solved bugs and the lessons they bought. **Search this file FIRST when debugging** — the answer may already be here._
_Last updated: 2026-09-08._

Format per entry:

```
## B<n>. <short symptom-level title>
- **Symptoms:** what was actually observed (error text, wrong output, timing)
- **Root cause:** the real reason, not the first guess
- **Investigation:** how it was found (so the next person can reuse the technique)
- **Fix:** what changed, which files
- **Verification:** what was run and what was observed — actual numbers/output
- **Lesson:** the generalisable rule (and any `D#` it created)
```

---

## B1. Printing a Thai transcript killed the process (`UnicodeEncodeError`, cp1252)
- **Symptoms:** A smoke test of the scripted STT engine printed the transcribed text and
  died with `UnicodeEncodeError: 'charmap' codec can't encode characters in position
  0-23`. The partial line `turn 1:` appeared, then the traceback. The *transcription
  itself was fine* — the Thai string was correct in memory.
- **Root cause:** The Windows console defaults to cp1252, which has no Thai codepoints.
  Any `print()` of Thai raises, and the exception propagates out of whatever was running.
- **Investigation:** The give-away was that the prefix printed and the Thai did not, and
  that the traceback bottomed out in `encodings/cp1252.py` rather than anywhere in our
  code. Re-running with `PYTHONIOENCODING=utf-8` produced the Thai correctly.
- **Fix:** `readycall/console.py::enable_utf8()` reconfigures stdout/stderr to UTF-8 with
  `errors="replace"`, called by every entrypoint that can print domain text (starting
  with `scripts/run_scenario.py`). `errors="replace"` rather than `strict` on purpose: a
  mangled glyph in a log line is cosmetic, a killed process mid-call is not.
- **Verification:** All three scenarios print Thai transcripts correctly with no env var
  set.
- **Lesson:** This was already written down as a landmine and it *still* bit within an
  hour of writing the first line of code. Anything that can print domain text calls
  `enable_utf8()` first — and never rely on the ambient console encoding.

## B2. `structlog` raised `TypeError: got multiple values for argument 'event'`
- **Symptoms:** `test_a_failing_handler_is_recorded` failed with
  `TypeError: meth() got multiple values for argument 'event'` from inside the event bus's
  own error handler. Only reachable when a *handler* raised — so the logging line meant to
  explain a failure was itself a second failure.
- **Root cause:** `structlog` reserves the keyword `event` for the log message. The bus
  called `log.error("event handler failed", event=event.name, ...)`, so the message was
  passed twice.
- **Fix:** renamed the kwarg to `event_name` in
  `readycall/adapters/event_bus/memory.py`, with a comment saying why.
- **Verification:** 84 tests pass, including the lenient-bus test that exercises that path.
- **Lesson:** `event` is not available as a structured-logging key anywhere in this
  codebase. Worth knowing because an event-driven system wants to log about events
  constantly. Also: error paths need their own test, or the handler that only runs when
  something is already broken stays broken.

---

## B3. Every stage timing recorded as exactly `0.0 ms` on Windows

- **Symptoms:** the customer simulator's headline metric read **"0.0 ms to assemble
  context"**. Not missing, not an error — a confident zero. The same zero appeared in
  `context_build_ms` on the intent status endpoint and in every `stage_timings_ms` entry
  written by the scenario runner, which is why nobody had noticed: a plausible-looking
  number that happens to be wrong is much easier to miss than a crash.
- **Root cause:** `SystemClock.monotonic_ms()` used `time.monotonic()`. On Windows that is
  backed by the ~15.6 ms system tick, so anything faster than a tick measures as zero.
  Measured on the dev laptop:

  ```
  monotonic    resolution: 0.015625 s   -> 2 distinct values in 200,000 samples
  perf_counter resolution: 1e-07 s      -> 200,000 distinct values
  ```

  Every stage in the system so far — context assembly against fixtures, brief building,
  the domain-pack walk — completes in well under 15 ms, so *all* of them read as `0.0`.
- **Investigation:** the giveaway was that the number was exactly `0.0` rather than small
  and noisy. A real measurement of something fast jitters; a clean zero across every
  unrelated stage means the instrument, not the code. Confirmed with
  `time.get_clock_info()` and a distinct-values count on both clocks.
- **Fix:** `SystemClock.monotonic_ms()` now uses `time.perf_counter()`. Both clocks are
  monotonic, which is the property `Stopwatch` needs; only `perf_counter` also has the
  resolution. One line, plus a docstring saying why so nobody "simplifies" it back.
- **Verification:** the same request now reports `context_build_ms = 0.400…`. Full suite
  green; scenario replays stay byte-identical because they use `ManualClock`, which was
  never affected.
- **Lesson:** this quietly gutted `D18` — *every stage writes a timing record* — and the
  pitch's own claim that the demo can **show** "context ready before the phone rang". A
  timing story told with an instrument that cannot resolve the thing being timed is worse
  than no story, because it looks like it works. **`time.monotonic()` is for measuring
  elapsed time coarsely; `time.perf_counter()` is for measuring performance.** They are not
  interchangeable on Windows, which is the demo platform.
- **Also worth noting:** this was found by *running the thing and looking at it*, not by a
  test. No assertion would have caught it — `0.0` is a perfectly valid float and the code
  was doing exactly what it said. Some bugs only show up when a human reads the output.

## B4. The matcher told supervisors "nobody is qualified" while listing a qualified agent

- **Symptoms:** `run_matching.py --calls 25` reported `no_candidates` on 17 of 25 callers, with
  the Thai rationale *"ไม่มีเจ้าหน้าที่ที่มีทักษะ/ภาษาที่ตรงและว่างอยู่"* — "no agent with matching
  skill/language is available". **Twelve of those seventeen printed a qualified candidate on the
  very next line**, e.g. `call_sim_002 -> no_candidates … A006 score=2.276 skill=0.90`.
- **Found by:** reading the output aloud while explaining it to the user. Nothing failed, nothing
  was slow, and every test passed. The contradiction is only visible to someone who reads the
  rationale and the candidate list *together* — which is exactly what a supervisor does.
- **Root cause:** `MatchingEngine.match` treated "the solver returned no column for this row" as a
  single condition and always emitted `MatchKind.NO_CANDIDATES`. The solver returns no column for
  **two unrelated reasons**: the row is entirely `IMPOSSIBLE` (every agent failed a hard filter),
  or the row had qualified agents who were all won by higher-scoring calls in the same tick. One
  is a roster gap, the other is a capacity shortfall.
- **Why it mattered more than a cosmetic label:** the two produce **opposite operational
  responses**. "No agent with this skill is online" tells a supervisor to fix the roster — hire or
  retrain for a skill they may already have on the floor. "They are all busy" tells them the
  caller is next as soon as someone frees. Acting on the wrong one wastes the day.
- **A second instance of the same fault, in the summary:** `run_matching.py` printed
  *"all of these failed a HARD filter — so waiting longer cannot help them"* unconditionally for
  every caller past the wait ceiling. That was an **assertion, not a measurement**. It happened to
  be true on the default seed. On `--seed 123` it is provably false: two callers are past the
  ceiling and one of them (`health.ipd.preauth`, waiting 200 s) has a qualified agent who is
  merely busy.
- **Fix:** `MatchKind.NO_CANDIDATES` split into `NO_QUALIFIED_AGENT` and `ALL_QUALIFIED_BUSY`,
  chosen by whether any candidate in the row passed every hard filter, each with its own rationale
  (the busy one names the count). The summary now reads the reason off the stored decisions and
  splits the starved callers into `ROSTER gap` and `CAPACITY` (`D50`).
- **Verification:** 228 tests pass. Three new tests: a contrived one-agent/two-caller contest where
  the loser must be `ALL_QUALIFIED_BUSY` *and* must list a candidate that passed every filter; a
  roster-gap case; and an invariant over a contended load asserting the kind can never contradict
  its own candidate list — with a guard that fails if the fixture stops exercising both branches.
  On the real 25-call run the 17 became **12 `all_qualified_busy` + 5 `no_qualified_agent`**, and
  the five are the `life` calls, correct because no life-skilled agent is online in that draw.
- **Lesson:** the same shape as `B3` and the Hungarian bug — **a confident, plausible, wrong answer
  is far more dangerous than a crash**, and none of the three were caught by a test. What made this
  one catchable is that the decision record carries its own evidence: the candidate list
  contradicts the label, so a *test* can check the two against each other. Where an explanation is
  the product, assert that the explanation agrees with the data it was derived from.
- **Writing the first version of that invariant test also surfaced a third meaning** of
  `chosen_agent_id is None`: a deferred call, deliberately held. Worth remembering before treating
  that field as a binary.

## B5. The brief leaked everything it was supposed to be hiding

- **Symptoms:** none visible. The workstation rendered correctly: at `l1_probable` the policy
  row said *ปกปิดจนกว่าจะยืนยันตัวตน*, the identity panel said disclosure was locked, and the
  Thai summary carefully said "มีกรมธรรม์ที่เกี่ยวข้อง (ยังไม่ยืนยัน)" instead of a number.
  **The bytes told a different story.** The same response body contained
  `HL-2024-000811`, `sum_insured`, every `coverages` entry, and the customer's `dob`.
- **Found by:** dumping the raw JSON while driving the UI and grepping it for the policy
  number, rather than reading the screen. Nothing on the screen was wrong.
- **Root cause:** `Container.render_brief` returned `CaseBrief.model_dump(mode="json")`.
  `CaseBrief` carries `snapshot: ContextSnapshot`, which carries the whole `Customer360` —
  every policy, every coverage figure, the customer record. `BriefBuilder` gates the
  *rendered Thai lines* by assurance, which is exactly what it was asked to do; nothing
  gated the object graph hanging off the side of them. Serialising the domain model shipped
  both.
- **Why this one is the worst kind:** every safeguard around it was working. The ladder was
  right, the builder was right, the flag in the payload was right, the UI was right. The
  leak lived in the gap between "the brief is gated" and "the brief *object* is gated", and
  every test asserted the first.
- **`D42` predicted it, in as many words:** *"Sending the full brief and hiding fields in
  React would put someone's coverage one devtools panel away."* The decision was written; the
  implementation still did the thing the decision forbade, because the forbidden thing is
  what `model_dump()` does by default.
- **Fix:** a wire DTO (`BriefOut` and friends in `api/schemas.py`) built by `_brief_out()`.
  The customer block requires `L1_PROBABLE`; the policy block requires `L2_STRONG`;
  recommended actions are filtered by their own `requires_assurance`; the raw snapshot never
  crosses at all, only field-level provenance. **The gate is now the shape of the payload** —
  there is nowhere to put a policy number until the level permits one.
- **Verification:** a test that serialises the whole workstation snapshot and searches the
  raw string for `HL-2024-000811`, `policy_no`, `sum_insured`, `coverages` and `dob` at L1;
  a second that promotes to L3 and asserts the policy *is* now present with a bumped brief
  version (`D7`); a third that rejects the identity and asserts it goes away again. 292 tests
  pass, and the browser shows the same thing: locked at L1, full coverage table at L3.
- **Lesson:** **never serialise a domain model onto a wire that has a permission boundary.**
  A DTO is not ceremony there — it is where the boundary is enforced, because a DTO can only
  leak what it has a field for. And test the *bytes*: an assertion about rendered text cannot
  see a field the renderer never mentions.

## B6. Six workstation faults, one root cause: the docs were not read closely enough

- **Symptoms:** a page of user-reported problems after the first workstation demo. Listed
  because the *pattern* matters more than any single one:
  1. the suggested opening greeted an **unverified** caller by name;
  2. *Third party* required pressing *Confirmed* afterwards, making it two steps;
  3. keypad digits were **masked from the agent** who had just captured them;
  4. identity buttons stayed live after an attestation;
  5. the ACW timer sat inside the wrap-up form and vanished when the form was saved, while
     the agent was still in after-call work;
  6. the call timer restarted at `00:00` after a browser refresh, and the whole UI flickered
     roughly once a second.
- **Root cause — for 1, 2 and 3, the same one: the answer was already written down.**
  - `diagrams/src/identity_promotion.mmd` is handwritten and marked *"checked against D42"*.
    It contains a node reading *"agent asks an **OPEN** question — ขอทราบชื่อผู้ติดต่อด้วยค่ะ"*
    and a note: *"never ask a leading question. 'ใช่คุณ X ไหมคะ' both leaks that the number
    belongs to them AND is weaker verification — anyone can answer yes."* It also draws
    **three parallel edges** from `agent presses one of THREE`, not a two-step path.
  - `D44` says raw captures are *"masked in transcripts and logs"*. It was implemented as
    "masked everywhere", including from the agent.
  - `D44` also says *"'the agent handles it' is the default and the only mode we build
    first"*. The named challenges shipped and the free-text escape hatch did not — exactly
    inverted.
- **The other three are ordinary UI faults, and each had a specific cause:**
  - The ACW timer was a prop of `WrapupPanel`, so saving the wrap-up unmounted the component
    and took the only visible clock with it. It now lives in its own bar in the shell.
  - Both timers counted from **component mount** rather than from a server timestamp, so a
    refresh reset them. `call_answered_at` and `acw_since` are now sent and the client
    computes `now - anchor`.
  - The flicker was **not** React. Every socket push was routed through the same helper as
    user actions, which sets a `busy` flag that disables every control — so during an active
    call the entire UI greyed out and came back about once a second. Background refreshes now
    use a separate path that touches no flag. Measured after: **0 disabled-state changes over
    3 s idle**, previously roughly one per second.
- **Investigation:** the user reported all six from ten minutes of using the screen. Every
  one was reproducible immediately; none was subtle. What made them expensive was that three
  of them were re-litigating decisions that already existed in `docs/`, which is precisely
  what that directory is for.
- **Fix:** `D55` (open question), `D57` (other + named third party), `D58` (masking is for
  logs), `D59` (standing-instruction presence model), `D60` (attestation locks and amends),
  plus the UI corrections above. 307 tests pass, including new ones asserting the opening
  line contains no name below L2 and that action 0 is prepended.
- **Verification:** driven by hand in a browser. The opening reads *"…ขอทราบชื่อผู้ติดต่อ
  ด้วยค่ะ"* at L1 and greets by name only after attestation; the three identity buttons
  disable together with an amend button; digits show as `2024000811` on screen while the log
  line carries `••••••••11`; the ACW bar survives saving and keeps ticking; the call timer
  read `01:02` after a mid-call refresh instead of `00:00`.
- **Lesson, and it is a process one rather than a technical one.** `CLAUDE.md` says to read
  the relevant doc **before** changing an area, and states the reason: *"if a future you with
  no memory would need it to avoid re-learning, it belongs in the docs."* The docs held the
  answers; the implementation was written from a summary of them. **A decision that exists
  but is not read is worth the same as one that was never written** — and it is worse than
  never writing it, because the team believes the question is settled.
- **Concretely, for next time:** when touching identity, read `03_identity.md` **and open the
  `.mmd` sources**. The generated diagrams carry rules that are not restated in prose
  anywhere else, and `identity_promotion.mmd` alone would have prevented three of these six.

## B7. An ignored offer stranded the agent for the rest of the shift — nothing drove RONA

- **Symptoms:** reported by the user. Let a test call ring without answering it. At zero the
  offer card disappears, and from then on the agent is stuck: the status panel reads
  *กำลังเสนอสาย*, no further call can be offered, `+ สายทดสอบ` is disabled, and there is no
  control that gets out of it. Signing out and back in is the only escape. The caller,
  meanwhile, is never re-matched to anybody.
- **Root cause:** **nothing in the running process ever called `expire_offers()`.** Nor
  `DispatchService.tick()`, nor `PresenceService.sweep()`. All three were written, tested in
  isolation, and given no driver:

  ```
  $ grep -rn "expire_offers" src/ scripts/ tests/ | grep -v "def expire_offers"
  (nothing)
  ```

  `expire_offers` even documents itself as *"Runs on a timer in the API process"*. There was
  no timer. `create_app`'s lifespan built the container, logged `api ready`, and yielded.
- **Why it looked handled, which is why it survived P2b:** the workstation hides the offer
  card the moment its countdown reaches zero — deliberately, so an agent cannot press Accept
  on a call that has already gone elsewhere. So the visible behaviour of a timed-out offer
  was *exactly right*, and the invisible half never happened. The client was counting down a
  deadline the server was not enforcing.
- **What else was silently dead:**
  - **re-matching.** Even once an offer expires, the caller only moves when a dispatch tick
    runs. So a decline was re-matched (the endpoint ticks explicitly) but a timeout was not.
  - **heartbeat expiry.** `agent_presence_ttl_s` exists so a closed laptop drops out of
    presence. Without a sweep it never did: a dead browser stayed `AVAILABLE` and kept
    winning matches, which is the *precise* failure RONA exists to prevent, one level down.
- **Investigation:** the user's description named the state exactly (*"the system still
  thinks the status is กำลังเสนอสาย"*), so this was one grep. The instructive part is what a
  test would have needed to catch it: every existing test drove the handshake through HTTP
  endpoints, and each endpoint ticks the dispatcher itself. Only *time passing with no
  request* exposes it, and nothing simulated that.
- **Fix:** `sweep_once()` in `api/app.py`, running `expire_offers` → `presence.sweep` →
  `dispatch.tick`, driven by a background task started in the lifespan and cancelled on
  shutdown. Interval is `agent_sweep_interval_s` (default 1.0); `0` disables it, which is
  what the tests set so they can drive the sweep deterministically against a `ManualClock`.
  Exceptions inside the loop are logged and swallowed — the loop must survive a bad tick,
  because it is the thing that recovers from bad ticks.
- **Verification:** two new tests, plus a live run. The tests advance a `ManualClock` past
  `offer_timeout_s` and assert the agent leaves `OFFERING`, lands `not_ready` with
  `intent_reason=rona_missed_offer`, and that a second agent is offered the same caller. A
  test helper heartbeats the "alive" agents first, because otherwise the presence sweep
  correctly drops an agent with no socket and masks the thing under test. Live, against the
  real server with the real timer:

  ```
  ignoring the offer for 25s (timeout 20s, sweeper every 1s)

  offer         None
  system_state  available      <- was stuck on offering, forever
  agent_intent  not_ready
  intent_reason rona_missed_offer
  ```

- **Lesson.** A service whose docstring says *"runs on a timer"* is asserting that somebody
  else does something, and nothing checks that claim. This is the third bug in this project
  of the same family as `B3` and `B4`: **the code was correct and simply never ran**, and
  the observable behaviour was plausible enough that nobody looked. The generalisable rule:
  *anything that must happen because time passed needs a test in which only time passes.*
  Every test here drove the system through endpoints, and every endpoint helpfully ticked
  the dispatcher on the way through — so the suite proved the ticking worked without ever
  proving anything caused it.

## B8. The clock-skew fix froze every timer it was meant to correct

- **Symptoms:** reported by the user immediately after `D68` shipped. Both the in-call timer
  and the after-call-work timer updated *"only after every 10s or so again"* rather than
  every second — the same symptom the server-anchored rewrite had already fixed once, back
  when the cause was something else entirely.
- **Root cause: the correction cancelled out the thing it was correcting.** `D68` added a
  clock-skew term so a laptop with a drifted clock would not show wrong durations. The
  arithmetic was right and the *placement* was wrong:

  ```ts
  const now  = useSecondTicker(signedIn);          // returns Date.now(), fresh each render
  const skew = clockSkewMs(snapshot.server_time);  // = server_time - Date.now(), EACH RENDER
  elapsed    = now + skew - started;
  ```

  Both `now` and the `Date.now()` inside `clockSkewMs` are read during the *same* render, so
  they are the same instant and cancel:

  ```
  now + skew  =  Date.now() + (server_time - Date.now())  =  server_time
  ```

  The elapsed time therefore became `server_time - started` — a value that changes only when
  a **new snapshot arrives**. Snapshots arrive on socket pushes and background refreshes,
  which is roughly every ten seconds. The 4 Hz ticker kept re-rendering faithfully and kept
  computing the identical number.
- **Why it is worth a full entry:** the timers were re-rendering, the ticker was firing, the
  server timestamps were correct, and the skew maths was correct. Every component behaved
  exactly as designed. **The bug lived in the composition** — the same shape as `B5`, where
  the ladder, the builder, the flag and the UI were each right and the leak was between
  them. A correction term must be sampled from a *different moment* than the value it
  corrects, or it is not a correction, it is an identity.
- **Fix:** sample the skew **once per snapshot** and hold it in state
  (`useEffect` keyed on `snapshot.server_time`), so `now` moves while `skew` stays put.
  The code carries the explanation, because the broken version looks more correct than the
  fixed one — recomputing "current" skew every render reads like the careful choice.
- **Verification.** Measuring through the rendered DOM was misleading: the automated browser
  pane runs the tab hidden, and Chrome throttles `setInterval` in background tabs, so the
  displayed timer *legitimately* freezes there regardless of this bug. Both versions of the
  computation were therefore replayed directly, simulating two renders five seconds apart
  against one snapshot:

  ```
  OLD:  at T+0s = 60.0   at T+5s = 60.0   advanced 0.0s     <- frozen between snapshots
  NEW:  at T+0s = 60.0   at T+5s = 65.0   advanced 5.0s     <- moves with the clock
  ```

- **Lesson, and it generalises past this bug:** *a value derived from `Date.now()` cannot be
  combined with another value derived from `Date.now()` in the same tick and still describe
  elapsed time.* One of the two has to be anchored earlier. Sample offsets at the moment the
  reference arrives, never at the moment you use them.
- **Second lesson, about testing:** the automated browser cannot verify per-second UI
  timing, because the pane is hidden and hidden tabs are throttled by the browser itself. A
  timing claim about the DOM measured that way is worthless; test the arithmetic directly
  instead, or watch it with human eyes.

## B9. A phase shipped with its database models never committed — `.gitignore` said `models/`

- **Symptoms:** none, locally, for a whole phase. P2c's first half was written, tested
  against a live Postgres, documented, and committed as
  *"p2c: persistence for call sessions and the agent state log"*. Every test passed, the
  migration ran, `psql` showed the rows. **A fresh clone had no `src/readycall/db/models/`
  at all** — so `import readycall.db.repositories` raises `ModuleNotFoundError`, every
  `alembic` command fails, and `STORAGE_BACKEND=postgres` cannot start. The default
  in-memory path keeps working perfectly, which is why nothing complained.
- **Found by:** running `git status` before committing the *next* phase and noticing that
  four files I had just edited under `db/models/` were not listed. Not by a test, not by CI
  — by reading a list and spotting an absence, which is the hardest kind of thing to spot.
- **Root cause:** `.gitignore` line 31, under a heading that reads `# Models / caches`:

  ```
  models/
  ```

  Intended for ML weights. A gitignore pattern containing no slash except a trailing one
  **matches a directory of that name at any depth**, so it also matched
  `src/readycall/db/models/` — the ORM package. `git add -A` silently skipped it, and
  `git status` showed nothing to report, because an ignored file is not "untracked", it is
  invisible.
- **Why it survived a full phase, and this is the instructive part:** every check that could
  have caught it ran against the **working tree**, not against what was committed. Tests,
  `mypy`, `ruff`, the migration, the live Postgres verification — all of them read files
  that exist on this laptop. Nothing in the loop ever asked *"does this repository contain
  what I think it contains?"* CI would have caught it on a fresh checkout; CI was not run on
  that commit.
- **Fix:** anchor the pattern to the repository root, where the ML weights actually live:

  ```
  /models/
  ```

  Then commit the package that should have been there. Also audited every other source file
  for the same fault — `find` piped through `git check-ignore --stdin` across `src`,
  `tests`, `scripts`, `config`, `mock`, `infra` and `apps` — and only
  `mock/bank_core/generated/*.json` came back, which is correctly ignored.
- **Verification:** `git check-ignore -v src/readycall/db/models/calls.py` now reports
  nothing, `git ls-files src/readycall/db/models/` lists all five files, and the six-table
  migration plus its models are in the same commit as the code that uses them.
- **Lesson, and it is a new one for this project.** The whole `B3`/`B4`/`B6`/`B7`/`B8`
  family is *a confident, plausible, wrong result nobody looked at*. This is the same family
  with a new hiding place: **the gap between the working tree and the repository.** Every
  verification this project runs is a statement about local files. "It is committed" is a
  separate claim, and `git add -A` does not make it — an ignore rule outranks it silently.
- **Concretely, for next time:** after adding a new package, run
  `git ls-files <dir>` and confirm it is not empty. And treat a bare directory name in
  `.gitignore` as a bug: `models/`, `data/`, `dist/`, `build/` all match at every depth, and
  every one of those is also a plausible name for real source. Anchor them.
---

## Areas where bugs are expected (write them up when they happen)

Recording these up front so the first person to hit one recognises it as a known-risky area rather
than a mystery:

- **Audio pipeline** — sample-rate/format mismatches (8 kHz µ-law vs 16 kHz float32), frame
  misalignment, clock drift between the telephony clock and our buffer, silent channels caused by
  mono/stereo confusion.
- **VAD endpointing** — cutting the first syllable (hence the padding in `D9`), never firing on
  continuous background noise, or firing constantly on a noisy line.
- **Whisper failure modes** — repetition loops on silence/noise, hallucinated text on empty audio,
  language drift to English on code-switched Thai.
- **Concurrency** — two writers of `call_sessions.state`; duplicate event handling from an
  at-least-once bus; a race between "queue pop" and "intake finalise".
- **Telephony** — NAT/ICE failures, codec negotiation, WebRTC certificate issues, ARI reconnection
  losing in-flight calls.
- **Thai text** — cp1252 console crashes on Windows, Buddhist-era dates, `+66`/`0` phone
  normalisation, tokenisation for entity extraction.
- **Data mapping** — a hackathon-day CSV whose columns don't match assumptions; nulls where the model
  requires values; duplicate customer ids.

## B10. A call could sit in `WRAP_UP` for the rest of the shift
_Found by the user driving the workstation, 2026-08-26._

- **Symptoms**, exactly as reported: end a call, then press a presence state **without**
  saving the wrap-up. The report panel vanishes, but the customer's details stay in the
  middle column and the identity controls stay live on the right — a "half finished state".
  Take another call afterwards and everything behaves perfectly. Then save *that* call's
  wrap-up, and **the first call's details come back and never leave**, no matter how many
  calls follow.
- **Root cause.** Declaring a next state ends after-call work (`D45`) and marks
  `acw_ended_at`, but **nothing closed the call**. It stayed in `CallState.WRAP_UP`, and
  `_active_call_id()` returns the most recent assignment whose call is `IN_CALL` **or**
  `WRAP_UP`. So:
  1. call A is stranded in `WRAP_UP` → it is still "this agent's active call" → its identity
     and brief keep rendering;
  2. `_wrapping_call_id()` correctly returns `None` (ACW *did* end), so the wrap-up panel
     unmounts — which is why the screen looked half-dressed rather than obviously wrong;
  3. call B arrives, is newer, and wins the sort → everything looks fixed;
  4. B is saved and goes `CLOSED` → the sort falls through to A, still in `WRAP_UP` → A
     returns and stays, because nothing will ever close it.
- **Investigation.** The report reads like a UI bug and it is not; the client renders exactly
  what the snapshot says. Following `_active_call_id` to its `live_states` set made it
  immediate. The tell was step 4 — a bug that *heals* and then *relapses* is almost always a
  fallback in a sort, not a rendering fault.
- **Fix.** Ending ACW now closes the call when no wrap-up was filed, with the transition
  reason `acw_ended_without_wrapup`.
- **What was deliberately NOT done: blocking the state buttons until the form is saved.**
  That was the reported instinct and it would have worked, but it re-couples the two things
  `D45` exists to separate — ACW ends when the *person* says so, not when our form is
  satisfied — and it traps an agent who needs to step away. `D45`'s other half also survives
  intact: **no wrap-up is invented.** The absence of a `call_wrapups` row is still exactly
  the record that none was filed, which is what `11_persistence.md` promises.
- **Verification.** Three regression tests, each confirmed to **fail with the fix reverted**,
  including the user's precise A-then-B-then-relapse sequence.
- **Followed by `D87`.** Closing the call stopped it haunting the screen and left the
  customer's record with a permanent hole — the agent had no way to finish what they had
  stepped away from. The unfiled wrap-up now goes to a **backlog** they can clear later,
  which is what makes "the person decides when ACW ends" survivable rather than lossy.
- **Lesson.** *A bug that fixes itself and then comes back is a fallback, not a redraw.* And
  more generally: two functions that answer nearly the same question (`_active_call_id` and
  `_wrapping_call_id`) will eventually disagree about an edge case, and the edge case is
  where the state machine was left unfinished.

## B11. A saved wrap-up still looked like outstanding work
_Found by the user in the same pass._

- **Symptoms:** pressing บันทึก (save) left the whole form on screen, fully editable, with
  only a small badge to say it had worked — whereas บันทึกแล้วพร้อมรับสาย cleared it
  immediately. Same underlying action, two completely different-looking outcomes.
- **Root cause.** The panel renders while `system_state == after_call_work`, which is
  **correct** (`D45`: saving closes the record, ACW keeps running). The second button also
  declares a state, which ends ACW and unmounts the panel as a side effect. So the
  inconsistency the user saw was real, but only one of the two behaviours was wrong: the
  saved form should not still look like a form.
- **Fix.** Once saved, the panel collapses to a **read-only summary that reads the record
  back** — disposition, notes, follow-up — with the next-state action still offered. The ACW
  bar stays either way, because after-call work genuinely has not ended, and the panel now
  says so in a sentence.
- **And the missing feedback.** Saving closes a record the agent can no longer see, so
  success and a silently-failed request looked identical. A green confirmation now appears
  on both save paths (`aria-live`, 3.2 s, `prefers-reduced-motion` respected).
- **Lesson.** When the only feedback for an action is something *disappearing*, there is no
  feedback at all — the failure case looks the same. Anything whose success is invisible
  needs a visible acknowledgement, and one disposition list serving both the form and the
  summary means the two cannot drift.

## B12. A waiting caller's urgency never grew, so nothing about starvation actually worked
_Found 2026-09-01, while checking whether announcing a queue position made sense. Nobody was
looking for this._

- **Symptoms:** none visible. Every test passed, `run_matching.py --calls 25` produced
  sensible-looking assignments, and the anti-starvation behaviour `D22` describes was
  documented in three places. What nothing showed was that a caller who had been holding for
  ten minutes scored exactly the same as one who had just arrived.
- **Root cause:** `WaitingCall` is a frozen dataclass admitted once into
  `DispatchService._waiting`. Each tick rebuilds it — but with `replace()` naming **only**
  `excluded_agent_ids`. Every other field, `waiting_s` included, kept its admit-time value
  for the entire life of the call. On the demo path that value was `body.waited_s`, defaulting
  to `0.0`.
- **What that disabled, measured against the real scoring code:**

  ```
  the pool's frozen view                 what it should have been
  t=  0s  wait_pressure=0.00  u=1.300    t=  0s  0.00  u=1.300
  t= 60s  wait_pressure=0.00  u=1.300    t= 60s  0.50  u=1.800
  t=120s  wait_pressure=0.00  u=1.300    t=120s  1.00  u=3.000   <- SLA breach
  t=600s  wait_pressure=0.00  u=1.300    t=600s  2.00  u=3.000   <- any-qualified fallback
  ```

  So `wait_pressure` was pinned at 0, `sla_risk` never fired, the 180 s
  `max_wait_before_any_agent_s` fallback never fired, and neither did the 60 s cap that stops
  a caller being deferred indefinitely. **The entire mechanism `D22` exists for was written,
  correct, and driven by a constant.**
- **Investigation:** the question was not "is there a bug" — it was the user asking whether
  telling a caller their queue position made sense given a Hungarian matcher. Reading
  `score_urgency` to answer that showed `total_wait_s` mattered enormously; reading `tick()`
  to see where it came from showed it never changed.
- **Fix:** `tick()` now also refreshes `waiting_s` from `session.wait_seconds(now)` — the
  pool already holds the session, and `CallSession.wait_seconds` already existed. The demo's
  "arrives having already waited N seconds" flag moved onto **`waiting_credit_s`**, which is
  the field for accrued wait that survives being re-scored, so it composes with live elapsed
  time instead of being overwritten.
- **Verification:** two tests in which **only the clock moves**. One asserts a caller's
  `total_wait_s` reaches 200 s and crosses both the SLA and the any-qualified ceiling; the
  other asserts the demo's pre-accrued 95 s still adds to live elapsed time. Both were
  confirmed to fail when the fix is reverted.
- **Lesson — and it is `B7`'s, one level down.** `B7` was *nothing called the driver*. This is
  *the driver ran and was handed a stale argument*, which is strictly harder to see: the code
  executes, the logs look healthy, and the value is merely wrong. **When a frozen object is
  rebuilt with `replace()`, the fields you did not name are a decision.** Write them down or
  they rot silently.
- **What this found next to it.** Three more matcher inputs are still fed by nothing on the
  live path: `is_vulnerable` (set on the `Customer` and on the brief DTO, never on the
  `WaitingCall`), and `last_agent_id` / `last_contact_at` (set on the context snapshot, never
  on the `WaitingCall`). So `customer_priority` and `continuity` score **0 on every real
  call** today. `run_matching.py` generates all three synthetically, which is exactly why the
  simulator looks like it exercises them. Not fixed here — it is a wiring job with its own
  decisions about where continuity data should come from — but it is now written down.

## B13. The wait ceiling could never fire for the caller it existed to rescue
_Found 2026-09-01 by the user asking how a past-ceiling caller is handled. Fixed by `D93`._

- **Symptoms:** none. `test_past_the_wait_ceiling_takes_anyone_qualified` passed, the config
  documented the behaviour, and three separate docs described it. The test gave one caller a
  whole free floor — with nobody to lose to, the solver picked them anyway and `_guard`
  stamped `FALLBACK` on a decision it had not actually caused.
- **Root cause:** ordering. `MatchingEngine.match()` builds the matrix, solves it, and *then*
  calls `_guard()` — but only for a call the solver returned an agent for:

  ```python
  chosen_index = assignment[index]
  if chosen_index is None:
      ...                       # ALL_QUALIFIED_BUSY / NO_QUALIFIED_AGENT
      continue                  # <- the guard is never reached
  agent = agents[chosen_index]
  kind, reason = self._guard(...)   # <- the ceiling lived in here
  ```

  A starved caller who lost the matrix took the `continue`. The ceiling was checked only for
  callers who had *already been given someone*, where all it did was suppress the deferral
  and the hot-spot bounce.
- **Why it survived review.** Within a single queue the bug is invisible: every caller on a
  queue needs the same `required_skill`, so fit against a given agent is identical for all of
  them, urgency alone decides, and the longest waiter wins. The bug needs **cross-queue
  contention for a multi-skilled agent** — and 6 of the 15 roster agents span more than one
  product line, so it is not exotic.
- **Fix:** `D93` — a `_rescue()` pre-pass before the solver.
- **Verification:** five tests, every one of them putting a second caller in the way, and all
  five confirmed to fail with the rescue disabled. The headline case: a health caller 600 s in
  now takes the shared agent from a fresh CRITICAL motor caller, and the loser is correctly
  reported `ALL_QUALIFIED_BUSY` rather than as a roster gap.
- **Lesson.** *A guard that runs after a selection can only ever veto that selection — it can
  never rescue whoever the selection skipped.* The ceiling was written as a filter on the
  chosen agent when it needed to be a claim on the pool. Worth checking the other guard rails
  for the same shape: `defer_*` and the hot-spot check are both genuinely about a chosen
  agent, so they are in the right place — the ceiling was the odd one out.
- **And the test that hid it is the same family as `B4` and `B12`:** a scenario with no
  contention proves nothing about a contention rule.

## B14. Whisper on near-silence: eight seconds, and it invents the words you gave it
_Found 2026-09-02 by running the bake-off on the real GPU and disbelieving the numbers._

- **Symptoms.** Three of them, and only the first looked like a bug:
  1. the bake-off reported a p95 latency of **0 ms** for every engine, including a real one;
  2. it reported **0 MB** of VRAM for a model that had visibly loaded onto the card;
  3. once both of those were fixed, `faster_whisper_tiny` reported **10-11 s** p95 on 4.5 s
     of audio — for the *smallest* Whisper there is.
- **Root causes — four separate ones, which is why this entry is long.**
  - **The latency instrument never fired.** It keyed a dict on the exact packet-boundary
    position and looked it up by `turn.t_end_ms`. The endpointer adds `pad_after_ms`, so a
    turn's end is 60 ms past any boundary and the lookup missed **every time**. No latency
    was ever recorded, and an empty list printed a clean `0`. A round zero across unrelated
    things is `B3` exactly: the instrument, not the code.
  - **The VRAM instrument was blind.** `torch.cuda.max_memory_allocated()` only sees torch's
    own allocator, and CTranslate2 allocates through its own — so it never saw a byte of
    faster-whisper's model. `mem_get_info()` asks the driver, which sees everything.
  - **The feed was not paced.** Pushing a whole file in a tight loop queues every utterance
    at once, so the third waits behind two inferences. That is a *backlog*, not a latency.
    Measured on identical audio and identical work: **11270 ms unpaced against 553 ms paced.**
  - **And then the real finding.** With honest instruments, a direct measurement:

    ```
    tone 0.9 s      155 ms    text: ""
    tone 2.0 s      195 ms    text: ""
    SILENCE 1.0 s  8578 ms    text: "ช่วงที่สุดได้โลงในอ้าอีก นี่"
    ```

    **One second of digital silence costs 8.6 seconds and comes back with invented Thai** —
    55x the cost of real speech. `D9` documented the hallucination and never mentioned the
    price. So a VAD false positive is not merely a junk turn on the agent's screen: it is a
    latency bomb that blocks every genuine utterance queued behind it.
- **Worse, it hands back our own vocabulary hint.** Fed a non-speech segment with
  `initial_prompt` set from `config/stt_vocabulary.yaml`, the model returned
  *"โอ้โอ้โอ้โอ้โอ้ เจ้า สินไหม? กรมธรรรม์, ผู้เอาประกัน, ผู้เอาประกัน, ผู้เอาประกัน"* — three of
  our own terms, in our own file's order, from audio containing no speech. **That is worse
  than an ordinary hallucination**, because the invented words are precisely the domain
  terms that make a brief look credible, and an agent reading them has no way to know the
  caller never said them. It is `D16`'s hazard — the model must not produce facts — one
  layer below where `D16` guards it.
- **My own warmup was triggering it.** Both adapters warmed on `[0.0] * 16000`: one second
  of digital silence. A warmup written to take the first-use cost *off* the call path was
  itself paying the single most expensive input the model has.
- **Fix, four parts:** the two instruments corrected and the pacing added, with `--fast`
  blanking the latency column rather than printing a wrong one; a **level gate before
  dispatch**, so a segment with no energy never reaches the model; warmup on a tone;
  `echoes_the_prompt()` refusing a transcription that is mostly hint vocabulary; and the
  repetition guard taught to strip punctuation, because the observed loop was
  `"ผู้เอาประกัน, ผู้เอาประกัน, ผู้เอาประกัน"` and a comma per word hid it from a whitespace split.
- **Verification.** Four new tests carrying the measured numbers in their docstrings, plus
  the two guard suites. The silence gate is asserted by counting calls that reach a fake
  engine, so it fails if the gate is removed.
- **Lesson, and it is a new one for this project.** Every previous entry in this family —
  `B3`, `B4`, `B7`, `B8`, `B12`, `B13` — is *a confident, plausible, wrong result nobody
  looked at*. This one is that **three times over in a single afternoon, in the measuring
  apparatus itself**, and it would have been very easy to accept: a 0 ms latency looks like
  a fast system, and 10 s looks like "well, it is a small GPU". **The moment to be most
  suspicious of a number is when it agrees with what you already expected.** Neither zero
  nor ten seconds was ever real.

## B15. CI installed no extras, so it could not collect the test suite at all
_Noticed 2026-09-02 while checking what constraints the audio layer had to work under._

- **Symptoms:** none locally, ever. Every check passes on this laptop, because the laptop
  has been synced with `uv sync --extra web` since P1b.
- **Root cause:** `.github/workflows/ci.yml` ran **`uv sync --frozen`** with no extras.
  `fastapi` lives in the `web` extra, and `tests/unit/test_api.py` imports it at module
  level — so pytest fails during **collection**, before a single test runs. Not "some tests
  skipped": the whole suite, plus `mypy`, which also needs the import to resolve.
- **How it was found:** not by looking at CI. By reading the workflow to answer a different
  question — *what can the audio layer depend on?* — and noticing the answer implied the
  suite could not be running there either.
- **Fix:** `uv sync --frozen --extra web`. `--extra ml` is deliberately **not** added: it is
  ~3 GB, the runner has no GPU, and everything in the audio path except the two real model
  adapters is dependency-free precisely so that CI exercises it (`D96`).
- **Lesson, and it is `B9`'s exactly.** `B9` was *the gap between the working tree and the
  repository*; this is the gap between **the working tree and CI**. Every verification this
  project runs — tests, mypy, ruff, the scenarios — is a statement about *this machine*.
  "CI is green" is a separate claim, and nobody had checked it. The generalisable rule:
  **when you add an optional extra that any test imports, the CI install line is part of
  the change.** Adding the `ml` extra today would have made this worse without fixing it.
- **Still unverified, and stated rather than assumed:** whether CI has actually been failing
  or has simply never run on this repository. The remote exists; the workflow's history was
  not checked from here. Either way the line was wrong.

## B16. The repetition guard did not work on Thai, which is the only language it is for
_Found 2026-09-02 by the user pasting three real transcriptions from their earlier
Thonburian project and asking whether we needed to watch out for this._

- **Symptoms:** none, and that is the whole problem. `looks_like_a_loop()` shipped in `B14`
  with eight passing tests. Every one of those tests used **spaced** text, because the
  hallucination I had personally observed came back punctuated — the model had put commas
  in its own nonsense.
- **What the user supplied**, unprompted, as things they had actually seen:

  ```
  "คนเชื่อถือในการการการการการการการการ…"        195 chars, ONE token, no spaces
  "เพื่อช่วยช่วยช่วยช่วยช่วยช่วยช่วย…"              209 chars, ONE token
  "ความต้องการของลูกค้าความความความความ…"        264 chars, ONE token
  ```

- **Root cause:** `text.split()`. **Thai does not put spaces between words.** On real
  Thonburian output the whole utterance is a single token, so `len(words) < min_repeats`
  was true immediately and the function returned `False` before examining anything.
  Measured against all three strings before the fix: **caught 0 of 3.**
- **Why the tests did not catch it:** every fixture was written from the one sample I had
  seen, and that sample was spaced. The suite was internally consistent and tested the
  wrong language — a guard for Thai, validated only on text shaped like English.
- **Fix:** a character-level detector (`_longest_repeated_run`) that finds the longest
  immediately-repeating substring for periods of 1-12 characters, and calls it a loop when
  the run covers **≥45% of the utterance** with **≥3 repeats**. The whitespace check is
  kept as a fallback for the spaced case. Catches 3 of 3.
- **The half that took the care:** *coverage*, not presence. Thai reduplicates as a genuine
  grammatical feature (เร็วๆ, ค่อยๆ), and politeness particles repeat legitimately
  ("ครับ ผม เข้าใจ ครับ"). A guard tuned for recall alone would silently delete real
  sentences, which is strictly worse than passing a loop through: nobody ever finds out
  what the caller said. Four "must survive" cases are asserted alongside the three "must
  catch" ones, including an ordinary sentence written **without spaces**, which is how Thai
  is actually written.
- **Lesson.** `B14`'s lesson was *be most suspicious of a number that agrees with you*.
  This is its sibling: **be most suspicious of a test fixture that was derived from the one
  example you happened to see.** The eight tests were real, they passed, and they described
  a failure mode shaped like the wrong language. The fix came from someone who had run this
  model on real audio for months — which is worth more than any amount of reasoning about
  what a model "would" do.
- **Standing consequence:** the repetition guard is now the only part of the audio path
  with test data drawn from real Thai output rather than from synthesis. Everything else in
  `B14`'s guards is still validated against tones and against one observed hallucination.
  The dataset the user supplied (`Q27`) is what changes that.

## B17. The default STT model id was invented, and would have failed at load
- **Symptoms:** none yet — nothing had asked for that engine. `FasterWhisperEngine`'s
  `DEFAULT_MODEL` was `"biodatlab/whisper-th-medium-combined-ct2"`.
- **Root cause:** I wrote it from the pattern *"the CT2 build of a model is the model name
  plus `-ct2`"*, which is a real convention and is not a fact. **That repository does not
  exist.** One HTTP request to the Hugging Face API — the request I eventually made when
  the user asked why I was talking about `faster_whisper_tiny` instead of the planned Thai
  models — returned 401/not-found for it while confirming
  `biodatlab/whisper-th-medium-combined`, `biodatlab/whisper-th-large-v3-combined` and
  `scb10x/typhoon-asr-realtime` all exist.
- **What it would have looked like:** a download error naming a repository nobody could
  find, on demo day, in an adapter that had "worked" in every test — because every test
  used the scripted engine, and the one real run I did used `tiny`, which exists.
- **Fix:** the default is now a **local directory** (`models/whisper-th-medium-combined-ct2`)
  produced by `scripts/convert_ct2.py`, because Thonburian publishes a transformers
  checkpoint only and faster-whisper cannot read one. A path that does not exist now raises
  a `ConfigError` naming the conversion command, rather than a library error about a
  repository.
- **Lesson.** `D30` already said *"model names, licences and API pricing must be
  re-verified at implementation time rather than trusted from memory"* — written in August,
  ignored in September by the person who had read it that morning. A model id is a fact
  about the world, not a naming convention, and checking one costs a single request.

## B18. The bake-off's accuracy metric could not work on Thai, and read 100% error on a working model
_Found immediately by running the first real measurement and disbelieving it. Again._

- **Symptoms:** the first bake-off against real Thai call-centre audio, with Thonburian
  loaded on the GPU and visibly producing sensible Thai:

  ```
  thonburian   ...1669023294_human.wav   1 turn    WER 1.000
  thonburian   ...1670989598_human.wav   2 turns   WER 1.118
  thonburian   ...1670150269_human.wav   5 turns   WER 1.071
  thonburian   ...1670931464_human.wav   2 turns   WER 0.941
  ```

  **Over 100% error on two of four files**, which is only arithmetically possible when
  almost nothing aligns.
- **Root cause:** `word_error_rate` tokenises on whitespace. **Thai does not use
  whitespace.** Our transcriptions come back as one continuous string; the reference is
  the dataset's segments joined with spaces *we inserted ourselves*. So the comparison was
  one arbitrary segmentation against another, and the edit distance was roughly "replace
  everything".
- **This was written down in the file's own docstring, by me, two days earlier:** *"Thai
  does not put spaces between words, so on unsegmented Thai this is really a phrase error
  rate and will read pessimistically high."* I documented the trap, shipped the trap, and
  then read its output as a result about the model.
- **Fix:** `character_error_rate` — Levenshtein over characters with whitespace stripped
  from both sides — is now the primary metric and what the table ranks on. WER is retained,
  reported second, and labelled as meaningful only against a *segmented* reference
  (pythainlp, if it is ever wanted).
- **Lesson, and it is `B14`'s inverted.** `B14` was *be suspicious of a number that agrees
  with you*. This is: **be equally suspicious of one that disagrees.** A 100% error rate is
  not a bad result, it is an impossible one, and the impossible reading is almost always
  the ruler. Both times the tell was the same — a number at a suspiciously round limit
  (0.0 ms, 1.000 error) rather than a plausible messy one.
- **Also visible in that run, not yet explained:** only 1-5 turns were detected on calls
  whose ground truth has 4-6 speech segments, using the **energy** detector on real noisy
  phone audio. That is what the energy detector is for (`D96`: it is the no-dependency
  fallback), and the real measurement wants Silero. `--vad silero` is now a flag, because
  **the detector decides what the model is even asked to transcribe and is therefore half
  of any accuracy number.**

## B19. One STT adapter silently threw away the vocabulary hint, and a measurement found it
- **Symptoms:** none that looked like a bug. Running Thonburian over four real Thai calls
  **with** the vocabulary hint and **without** it produced CER identical to three decimals
  on every file:

  ```
                      with hint    without hint
  call 1                0.616         0.616
  call 2                0.759         0.759
  call 3                0.709         0.709
  call 4                0.468         0.468
  ```

- **Why that is a bug and not a result.** The measurement was set up to answer `Q27` — does
  an insurance vocabulary hurt on government-domain audio? A *result* would have been "a
  little better" or "a little worse". **Byte-identical output is not a finding about
  domains, it is the signature of an argument that was never used.**
- **Root cause:** `ThonburianHfEngine.transcribe_utterance` reads `hint.language` and
  ignores `hint.vocabulary` entirely. `FasterWhisperEngine` honours it via `initial_prompt`;
  the HF `pipeline` API does not accept one, and needs `processor.get_prompt_ids()` passed
  as `prompt_ids` instead. I wrote both adapters, wired one, and did not notice the other
  took the parameter and dropped it.
- **Why it matters beyond one number.** `ports/stt.py` advertises `SttHint.vocabulary` as
  *"domain nudges that measurably help on insurance jargon"*. A port whose adapters disagree
  about whether a parameter does anything is worse than one without the parameter: every
  comparison between those two engines is then partly a comparison of *which of them read
  the argument*, and `D30`'s whole purpose is a fair table.
- **Fix, and its limit.** The adapter now **logs a warning the first time** a vocabulary it
  will not use is handed to it, so a bake-off row can never again be quietly unhinted. The
  actual `prompt_ids` implementation is **not** done — it changes generation behaviour on a
  path that currently works, and doing it under time pressure at the end of a session is how
  a working engine becomes a broken one. It is written into the next steps.
- **Lesson.** This is the third time in two sessions that **a measurement found a bug that
  no test could** (`B14`'s instruments, `B18`'s metric, this). The pattern is worth naming:
  *when an experiment returns exactly no difference, suspect the experiment before
  concluding "no effect".* Identical to three decimals is a plumbing result, not a
  scientific one.

## B20. The audio buffer was released out from under segments still queued, and the CER investigation was chasing it
_Found 2026-09-03 while working the four CER hypotheses in `NEXT_SESSION` step 2. None of
the four was the answer._

- **Symptoms.** The recorded first real Thai numbers were **CER 0.47-0.76**, called "poor,
  and not yet explained" in five documents. Behind them, one detail nobody had looked at:
  the bake-off reported **3 turns** for a call the endpointer had cut into **6 segments**,
  and **4 turns** for one cut into 7. No drop was logged by any of the three guards.
- **How it was found — by elimination, cheapest first, exactly as the plan said.**
  1. **The reference-mismatch hypothesis died first.** The `.txt` covers only annotated
     speech, which is 15-29% of each file, so the obvious theory was that we transcribe
     ~75% more audio than the reference covers and every word of it is an insertion.
     Measured the un-annotated remainder in 1-second buckets: **median RMS 0.0000, and 1%
     of it reaches a third of that call's speech level.** It is digital silence — the
     human leg while the other side talks. No insertions available.
  2. **The detector was next, and `scripts/score_endpointer.py` was written to ask.** It
     reported **coverage 0.797** — 20% of annotated speech never reaching the model, which
     looked like the answer. It was not: measuring the *energy* of those missed seconds
     showed **86% of them below a third of speech level and 72% within 0.5 s of an
     annotated boundary.** That is an annotator rounding a span outward, not a lost
     sentence. Real speech lost: **5.9 s of 198 s, about 3%.**
  3. **Then the thing nobody had done: look at the text.** `bake_off.py --dump` prints
     reference against hypothesis. The hypothesis was **fluent, correct Thai matching the
     TAIL of the reference**, with the first half absent — 112 reference characters
     against 48, and the 48 all correct.
  4. **Transcribing each segment on its own, synchronously, produced all six**, every one
     correct. So the model, the detector, the endpointer and the guards were all fine, and
     the loss was in `TranscriptionStream` itself.
- **Root cause.** `_trim()` ran the moment a segment was **queued**, keeping 30 s of
  history behind that segment's end. That is correct only while the consumer keeps up.
  `feed()` never yields to the event loop — an unbounded `asyncio.Queue.put` has no await
  point in it — so under an unpaced feed the **entire call is ingested before the consumer
  transcribes anything**, and by then `_base` had advanced past the early segments. Their
  audio was gone before the model was ever shown it.
- **The second half is worse than the first, and is the reason this is a `warning` and not
  a footnote.** `_slice()` clamped with `max(0, start - base)`. When the audio had been
  trimmed that did **not** return empty — it returned `_buffer[0:n]`, a slice of roughly
  the right length **taken from the wrong moment in the call**. In Thai that transcribes
  into a fluent, plausible sentence attributed to an instant the caller was not speaking,
  and it reaches the agent's screen with nothing to mark it. `D16` bans the model from
  producing coverage figures because invented text that looks credible is the dangerous
  kind; this was that hazard again, one layer down, and this time in shipping code.
- **Why every instrument missed it.** Three separate `return`s left no trace: `if not
  samples: return` (marked `# pragma: no cover - only if trimming raced a very long
  segment`, which is exactly what happened), `if not text: return`, and the clamp itself,
  which produced no error because it produced *plausible output*. The three guards logged
  nothing because nothing reached them. The suite passed because every existing test feeds
  a few seconds of audio, which never crosses the 30 s window.
- **Fix, in four parts.**
  1. `_awaiting` holds the start sample of every queued-but-unconsumed segment, and
     `_trim` keeps everything from the oldest of them. One consumer draining FIFO makes
     the head of that deque the oldest sample anything can still reach.
  2. The backlog is **bounded and loud**: past `_MAX_BACKLOG_SAMPLES` (120 s) the oldest
     claim is abandoned with a `warning`. `D12` forbids backpressure on ingestion — the
     caller keeps talking whatever the GPU is doing — so the choice is between losing the
     oldest sentence and growing until the process dies, and it is made explicitly.
  3. `_slice` **refuses** a segment whose audio has been trimmed instead of clamping. A
     gap is recoverable; a confident wrong sentence is not.
  4. Both silent returns now log.
- **Verification.** Three tests, and they were checked against the *old* code before being
  trusted — on it, three utterances go in and the assertion reads `300 != 100`, one turn
  out. `MarkerStt` returns the **amplitude of the audio it was handed** rather than canned
  text, because an engine that says the same thing whatever it is given cannot see the half
  of this bug that matters. On the real GPU, the two calls that motivated it went from
  3 and 4 turns to **6 and 7**, and:

  | | before | after |
  |---|---|---|
  | call 1 | 3 turns, CER 0.616 | **6 turns, CER 0.188** |
  | call 2 | 4 turns, CER 0.709 | **7 turns, CER 0.139** |

  and `--fast` and paced now agree exactly, which they never did — **all 12 files agree
  on turn count** between the two modes, and no segment was abandoned or lost in either.

  The paced run that confirmed it also produced the finding the CER had been hiding: p95
  latency of **4.5 s to 58.7 s**, tracking throughput, against a 1.5 s budget (`Q29`).
- **Every recorded CER in this project was measured through this bug** and is wrong. The
  numbers in `PLAN`, `PROJECT_STATE`, `NEXT_SESSION`, `ARCHITECTURE`, `D97` and
  `reading/the_audio_path.html` are corrected in the same commit.
- **A second leak, found by reading the fix's own diff.** `_trim` runs only when a segment
  **closes**, so a leg on which nobody ever speaks never trims at all and the buffer grows
  for the length of the call — about **30 MB a minute**, measured at 16,000,000 samples held
  after 1000 s of silence. It is bounded today only by `hold.py`'s
  `INTAKE_SILENCE_TIMEOUT_S`, which is a bound belonging to a different object and will not
  be there when `D26`'s agent leg is transcribed for a whole call. `_TRIM_WHEN_IDLE_SAMPLES`
  now trims on the quiet path too, with its own test. Worth noting *how* it was found:
  re-reading a change before committing it, which is the cheapest review this project has.
- **Lessons, and there are three.**
  - **`--fast` was added in `B14` to stop the harness lying about latency, and then every
    measurement was taken with it.** It fixed one instrument and quietly became the
    condition under which a different one broke. A flag that changes how the system is
    *driven* is not a display option.
  - **A `# pragma: no cover` on a branch is a claim that it cannot happen.** This one said
    "only if trimming raced a very long segment" — a correct description of the bug,
    written before it, by me, and then not believed.
  - **"Three turns arrived" is a much weaker assertion than "turn three carried the audio
    of segment three".** Every test in this file asserted the first kind. That is why the
    new ones make the audio itself legible.

## B21. The repetition guard was deleting real Thai phone numbers
_Found 2026-09-03 by the user reading the log of their own bake-off run and asking whether
those drops were really hallucinations._

- **Symptoms.** Every call in the prepared set logged one or two lines like:

  ```
  dropped a repetition-loop transcription  sample='เก้า เก้า เก้า แปด'
  dropped a repetition-loop transcription  sample='เก้า เก้า เก้า'
  dropped a repetition-loop transcription  sample='เย์า เย์า เย์า เย์า เย์า สาม ย์'
  ```

  `เก้า` is **nine**. The user's question was exactly right: *"these are just flagging the
  thing where the customer will say the number of something, like telephone number... so we
  can't be sure if it's a hallucination from the model or not?"*
- **Root cause, and it is measurable rather than arguable.** Reading the *answer keys* of
  the 12 prepared calls and converting the spelled-out Thai numerals back to digits:

  | call | phone number in the reference | longest run of one digit |
  |---|---|---|
  | `d9b_1670931464` | 0989999934 | **5 x 9** |
  | `d9b_1670931950` | 0989999935 | **5 x 9** |
  | `ecb_1670853026` | 0817999998 | **5 x 9** |
  | `c3a_1670959100` | 0989999449 | **4 x 9** |
  | `547_1670935733` | 0989999197 | **4 x 9** |

  Nine of the twelve calls contain a run of **three or more identical digit words**, and
  three contain a run of **five**. `looks_like_a_loop` refuses at `min_repeats = 3`. It was
  therefore refusing real phone numbers, in the majority of calls, by construction.
- **Why this is the worst false positive available to this system.** A phone number is the
  one item on an agent's screen that has to be exact and cannot be inferred from context. A
  dropped sentence costs nuance; a dropped number costs the callback. And it was dropped
  **silently from the agent's point of view** — the log records it, the screen simply has
  one fewer line.
- **How it survived `B16`'s review.** `B16` rebuilt this guard around three real Thonburian
  loops the user supplied, and every one of those loops repeated a *word* (`การ`, `ช่วย`,
  `ความ`). The fixture was correct and the threshold derived from it was correct **for
  words**. Nobody asked what else in Thai legitimately repeats, and the answer — digits —
  is the single most important category of content in a call-centre transcript. `B16`'s own
  lesson was *"be most suspicious of a fixture derived from the one example you happened to
  see"*, and this is that lesson recurring one level up: the fixture was broadened from one
  example to three, and all three were the same *kind* of example.
- **Fix.** The repeating unit is now identified, not just counted. `_longest_run` returns
  the start index so the unit can be recovered; `_repeats_needed()` gives a digit word a
  threshold of **`DIGIT_MIN_REPEATS = 10`** instead of 3. Ten is the arithmetic ceiling on a
  legitimate run — a Thai mobile number is ten digits — so it is generous on purpose, and a
  Whisper loop repeats dozens of times, so nothing real is given up. Both the character-level
  and the whitespace-token paths consult it, including the 2- and 3-word cycle check.
- **`โท` is in the digit list** even though the word for 2 is `สอง`. Thai speakers say `โท`
  for 2 specifically when reading digits aloud, to keep it distinct from `สาม`.
- **The digit words live in `stream.py`, not in `config/`, and that is deliberate.** `D28`
  bans *domain* literals in `services/`; a numeral is a fact about the **language**, in the
  same category as the punctuation list and the "Thai has no spaces" rule that already live
  in this file. Retargeting this system to a hospital line does not change how Thai counts.
- **Verification.** Seven new tests, checked against the old threshold first: with the
  exemption disabled they fail, exactly as they should. Their data is not invented — the
  "must survive" strings are the phone numbers read out of the dataset's own answer keys,
  and the "used to be eaten" strings are pasted from the user's terminal. Two tests hold the
  other direction: `เก้า` thirty times is still a loop, and `B16`'s word loops are untouched.
- **Lesson.** *A guard tuned on one category of false positive will have a different
  category of false negative, and the categories are domain knowledge, not code.* The
  question that would have found this on day one is not "does the guard work?" but **"what
  in this language legitimately repeats?"** — and it was the user, who has heard these
  calls, who asked it.

## B22. `close()` released the model object but not the GPU memory
_Found 2026-09-04 while running the first two-engine bake-off, by watching `nvidia-smi`
rather than by anything failing._

- **Symptoms.** Running `--engines thonburian faster_whisper:<ct2>` in one table, the card
  sat at **3848 MiB of 4096** with the GPU at 100% — after the first engine had been
  closed and while only the second was supposed to be loaded. The run also took far longer
  than either engine had taken alone.
- **Root cause.** Both adapters' `close()` was one line:

  ```python
  async def close(self) -> None:
      self._pipe = None
  ```

  Dropping the Python reference frees the *object*. It does not return the device memory:
  torch keeps freed blocks in its own caching allocator, so the driver — which is what
  `mem_get_info()` asks, and what `nvidia-smi` reports — still counts them as in use.
- **Two consequences, and the second is worse than the first.**
  1. **The bake-off's VRAM column stops meaning anything in a multi-engine run.**
     `baseline = vram_in_use_mb()` is sampled per engine, before its weights load. For the
     second engine that baseline already contains the first engine's model, so the
     reported delta is not that engine's cost — it is whatever the allocator happened to
     reuse. `D30`'s whole purpose is a fair table, and a VRAM column that depends on run
     order is not one.
  2. **On a 4 GiB card it is most of the way to an out-of-memory failure**, and `D2`
     explicitly plans for the STT engine to be swappable. An engine swap that leaks a
     model's worth of VRAM works exactly once.
- **Fix.** `close()` drops the reference, then `gc.collect()` and `torch.cuda.empty_cache()`,
  wrapped so that releasing memory can never fail a call. Applied to all three real
  adapters (`thonburian_hf`, `faster_whisper`, `typhoon_asr`).
- **`empty_cache()` is normally a smell** — it fights the allocator that exists precisely
  to avoid re-allocating, and sprinkling it through a hot path is a classic way to make
  something slower. It is right *here* because the meaning of `close()` is "this process is
  finished with the model and something else needs the card". That is the one situation the
  call is for.
- **How it was found is the reusable part.** Nothing failed. No test could have caught it —
  the suite runs on the scripted engine and never loads a model at all. It was found by
  **watching the card while a long run was in progress** and noticing the number was too
  big for what was supposed to be loaded. The same family as `B14` and `B18`: an
  instrument disagreeing with the story, noticed only because somebody looked.
- **The measurement it invalidated has to be re-run**, which is the honest cost of finding
  it late: the first CT2-vs-Thonburian table was produced under this bug, so its VRAM
  column — and possibly its timings, since the card was contended — cannot be quoted.

## B23. The engine we just chose to ship could not be selected by config
_Found 2026-09-04 immediately after `D103` picked the CT2 build, by reading `build_stt`
to write down the env vars that turn it on._

- **Symptoms:** none, and none were possible yet — nothing had ever run the audio path
  through `Settings`. Every measurement so far went through `bake_off.py`, which
  constructs engines directly.
- **Root cause.** `Settings.stt_model` defaulted to `"biodatlab/whisper-th-medium-combined"`,
  a **Hugging Face id**, and `build_stt` reads `settings.stt_model or DEFAULT_MODEL`. The
  `or` only falls through on an empty string, so the default always won:

  ```python
  stt_model: str = "biodatlab/whisper-th-medium-combined"   # never empty, so...
  model=settings.stt_model or DEFAULT_MODEL                 # ...DEFAULT_MODEL is dead code
  ```

  `thonburian_ct2` is faster-whisper, which reads a **CTranslate2 directory**. Handing it a
  transformers checkpoint id fails at model load. So `STT_ENGINE=thonburian_ct2` on its own
  — the documented way to turn on the engine `D103` selects — could never have worked.
- **The field's own comment described the correct behaviour**: *"leave this blank to take
  whichever default the chosen adapter declares"*. The default was not blank. A comment
  documenting an intention the value contradicts is worse than no comment, because it stops
  the next reader from checking.
- **Why the two engines need different values.** They are the same weights and not the same
  artefact: `thonburian_hf` wants the HF id, `thonburian_ct2` wants a local directory that
  only exists after `scripts/convert_ct2.py` runs, because **Thonburian publishes no CT2
  build** (`B17`). One field cannot have one correct default for both — but it can have one
  correct *empty* default, which is what it has now.
- **Fix, two parts.**
  1. `stt_model` defaults to `""`, so each adapter's own `DEFAULT_MODEL` applies and the
     dead `or` branch becomes live.
  2. A **startup coherence check**: selecting `thonburian_ct2` with a value that looks like
     an HF id (contains `/`, does not exist on disk) refuses to boot, and the message names
     the conversion command. It is engine-specific, so `thonburian_hf` is untouched, and it
     accepts a real local path that happens to contain a slash.
- **Verification.** Four tests: the blank default, the refusal, a real directory being
  accepted, and the HF engine being unaffected.
- **Lesson, and it is the same one as `B17` and `B22`.** All three are *the configuration
  around a model rather than the model*, all three would have failed on the machine with the
  GPU on demo morning, and none of them could be caught by a test suite that runs on the
  scripted engine. The habit that finds them is **reading the wiring while writing the
  instructions for it**: `B17` came from checking a model id before publishing it, `B22`
  from watching a card during a run, this one from writing down two env vars and following
  them into the code.


## B24. Two services written, correct, tested, and called by nothing
_Found 2026-09-05 by tracing the path from a WAV file to a browser tab. Neither would ever
have failed a test._

- **Symptoms.** The audio path had a bake-off, a chosen engine, its own suite and an entry in
  every architecture document. It had never transcribed anything in the running system, and
  nothing said so. Separately, a `transcript.turn` subscriber written the obvious way would
  have been green in its unit suite and silent in production.

- **Root cause, and there were two.**
  1. **`TranscriptionService.open()` was called only by its own tests.** `IntakeService`
     deliberately does not know about media (`D88`: a strategy takes turns, not frames), so
     *somebody else* has to open the leg when a caller consents — and nobody did. The
     `HoldReport` said `recording=True` into the void.
  2. **Nothing drained the event bus periodically.** `publish()` enqueues; handlers run on
     `drain()`; the only drain in the live process was a background task on
     `POST /v1/calls/intents`. See `D105` for the measurement.

- **Investigation.** Not a debugging session — a **reading** one. The path was walked hop by
  hop asking *what calls this?*, which is `B7`'s question, and two of the answers were
  "nothing". `grep -rn "transcription.open" src/ tests/` was the whole investigation for the
  first; a twelve-line probe that published a turn, ran five sweeps and checked a spy was
  the whole investigation for the second.

- **Fix.** `D107` opens and closes the recording from the call lifecycle; `D105` adds the
  pump. Both have tests that drive the *driver* rather than the thing driven — the shape
  `B7` established, because a test that calls the service directly proves the service and
  says nothing about whether anything calls it.

- **Verification.** A real browser against a real server: a caller consents, a WAV plays
  down the leg, six Thai sentences appear on the agent's screen after Accept, each carrying
  the moment in the recording it was said. Then a test that places **three** calls, because
  the first version of the fix worked once — see below.

- **A third one, found by the running server after both fixes.** The container builds one
  STT engine per process. `ScriptedSttEngine` carries a cursor through its lines, so the
  first demo call consumed all of them and the second showed an empty panel. **Every test
  in the new file passed**, because each placed a single call. `D107` fixes it with a
  capability protocol; the regression test places three and asserts they are identical.

- **And a fourth, in the fix itself, caught before it shipped.** `accept_offer` finalised
  the intake *before* closing the transcriber. `stream.finish()` transcribes the segment
  still open and drains the queue, and those turns reach `IntakeService.on_turn` — which
  hands them to the strategy **only while it is running**. Finalising first meant every one
  of them arrived after the intake closed, where `PassiveRecordIntake.on_turn` correctly
  logs and drops them: the caller's last sentence, the one they were saying as the agent
  picked up, silently missing from the brief. `D21` says the offer window *is* the grace
  period; the order is what makes that true rather than merely intended.

- **Lessons.**
  - **"It has tests" and "it runs" are different claims**, and this project keeps
    confusing them. `B7` was three services with no driver, `B9` was a package with no
    commit, `B12` was a live service fed a frozen argument. Add: `B24`, a whole subsystem
    with no caller. The check that finds all four is the same — follow the call graph from
    something a user does, not from the module you are working on.
  - **A test that exercises one of something proves nothing about the second.** One call,
    one agent, one utterance. The scripted cursor, the shared engine and the offer/decline
    split are all invisible at n=1, and this is the same lesson as *"never test a contention
    rule without contention"* (`B13`, `B12`, `B4`) in a new place.
  - **A green async test can be green on scheduling luck.** The HTTP tests here passed
    before there was anything making the transcriber's worker run — under `TestClient` the
    application's loop only advances while a request is in flight. They now poll with cheap
    requests until the turns exist. A test that passes for a reason you cannot name is not
    yet a test.


## B25. The matcher never asked whether an agent could take a call
_Found 2026-09-05 by the user working the workstation and noticing that declining a call
made the queue behave in ways nothing explained. The fifth member of `B7`'s family._

- **Symptoms**, all reported as *"is this a bug or just the demo?"* and all the same bug:
  1. Sign in and do **not** press *พร้อมรับสาย*. A caller is offered to you anyway, a few
     seconds later — while your own screen says `offerable: false` and greys out the
     test-call button for exactly that reason.
  2. Sign out and close the tab. Callers keep being offered to you.
  3. Place several callers with one agent signed in. **None of them is offered.** Then,
     after a while, they all arrive at once.

- **Root cause, and it is one line missing rather than three bugs.** `hard_filter` checked
  `already_offered`, `skill`, `language`, `at_capacity` and `inactive`, and **never asked
  whether the person was available**. `AgentPresence.is_available()` — which says exactly
  that, and was written for exactly this — was called by **nothing in the repository**:

      $ grep -rn "is_available" src/ | grep -v torch
      src/readycall/domain/models.py:508:    def is_available(self, agent, *, within_schedule=True)

  So symptom 1 is `agent_intent` never being read; symptom 2 is `system_state` never being
  read after `sign_out` correctly sets `OFFLINE`; and symptom 3 is `OFFERING` never being
  read — one ringing desk was handed **every** waiting caller in a single tick, and each of
  them was invisible to everybody else until that offer timed out. Twenty seconds per
  caller, per ghost.

- **Investigation.** The user's third symptom was the one that looked most like magic, so
  it got a probe rather than a theory: their exact click sequence, with the matcher's own
  decision record printed at each step. The output named it immediately —

      -- A001 logs out
         presence still known for A001? True
        place call2: offered_to='A001'
        place call3: offered_to='A001'
        place call4: offered_to='A001'

  A signed-out agent collecting four callers. `at_capacity` was the only thing that had
  ever stopped an unavailable agent being chosen, and it only fires once somebody is
  actually *on* a call.

- **Fix.** Three named filters in `hard_filter` — `offline`, `not_ready`, `busy` — rather
  than one, because `D50` is about a supervisor being told *which* thing is wrong.
  `not_ready` covers `BREAK`, `LUNCH`, `TRAINING`, `ADMIN` and also `LAST_CALL` and
  `DRAINING`, which mean *finish what I have, give me nothing new*. `busy` covers
  `ON_CALL`, `AFTER_CALL_WORK` and `OFFERING`.

- **The fix could not land alone**, and that is `D108`: with availability filtered, a floor
  where everybody is on lunch would have reported `no_qualified_agent` — *"nobody with the
  skill is online"* — while four qualified people sat signed in. Two new `MatchKind`s carry
  the difference. See `D108`.

- **Verification.** The user's sequence, replayed: the unready agent gets nothing, the
  signed-out agent gets nothing, exactly one caller is offered to the one available agent
  and the rest wait with `no_agent_available` on the record. Then 20 new tests, of which
  the load-bearing ones are the HTTP ones — this bug is only visible when something
  *drives* the matcher.

- **Lessons.**
  - **A dead method is a claim nobody checked.** `is_available` reads as though the system
    uses it. `grep` for the definition of any rule you are relying on and confirm somebody
    calls it — the same move that found `B24` the same day.
  - **Two sources of truth agreed to disagree in silence.** `PresenceView.offerable` and
    the matcher computed *the same question* from the same data and only one of them was
    consulted. The screen said "not offerable" and the phone rang. If two places answer one
    question, one of them must be derived from the other, or a test must assert they match
    — there is now one that does.
  - **655 tests passed through all of it**, because every single one of them set its agents
    `ready` first. A fixture that always sets up the happy path tests the happy path.

## B26. The wait on the agent's screen was frozen at the moment the caller arrived
_Found in the same session, by the user noticing the number never moved._

- **Symptoms.** The offer card's *รอมาแล้ว* and the queue strip's *รอนานสุด* both showed the
  same value for the life of the call — 40 s on the demo path, because that is what the
  test-call button credits. Every other timer on the screen ticks.

- **Root cause.** `B12` fixed exactly this for the **matcher** and only for the matcher.
  `DispatchService.tick()` rebuilds each `WaitingCall` with `waiting_s` recomputed from
  `call_sessions.queued_at` — into a *local list* that it hands the engine. The copy in
  `self._waiting` is never written back, so it keeps the value `admit()` was given. Every
  read path for a screen (`waiting()`, `waiting_call()`) reads that copy. **Two answers to
  "how long has this person waited", and the human could only see the wrong one.**

- **Fix.** `DispatchService._live()` derives the wait from the session at read time, and
  `tick()` now goes through it as well, so there is one piece of arithmetic instead of two.
  Deliberately **not** written back into the pool: `D78`'s rule is that a second copy kept
  in step by remembering to update it is a second copy that will one day disagree — which
  is precisely what this bug was.

- **Verification.** Two tests, one per surface, because they read the same pool through
  different code and only one of them was ever going to be checked otherwise.

- **Lesson.** **A fix aimed at one consumer is not a fix.** `B12` correctly identified that
  `waiting_s` was frozen and correctly unfroze it for the thing it was investigating. The
  question it did not ask is *who else reads this?* — and the answer was the only two
  places a person ever sees the number.


## B27. The screen was late twice over: a duration sent as a number, and a sequence counter that outlived its session
_Found 2026-09-05 by the user, one day after `B26`, on the page `B26` was supposed to have
fixed. Two independent faults with the same symptom — **the workstation updating on a
ten-second cadence** — and ten seconds is the heartbeat interval, which is the clue that
named both._

### Symptom 1: the wait moved once every ten seconds instead of every second

- **Root cause.** `B26` made the server compute the wait correctly. It still sent it as a
  **number**. A number can only change when a new snapshot arrives, and snapshots arrive
  when *something happens* — which, for a caller who is sitting in a queue, is precisely
  never. The only thing refreshing the page at all was the socket's `heartbeat_ack`, which
  falls through `onSocketMessage` to `quietRefresh()` every `HEARTBEAT_MS` = 10 s.
- **Every other timer on that screen already had the fix.** The call timer counts from
  `call_answered_at`; the ACW timer counts from `acw_since`; both are **anchors**, both are
  ticked client-side by `useSecondTicker` + `elapsedSince`, and both got that way in `D68`
  and `B8`. The wait was the one duration still shipped pre-computed.
- **Fix.** `OfferOut.waited_since` and `QueueOut.longest_wait_since`, computed in
  `DispatchService._live()` **beside** the number they anchor so the two cannot describe
  different instants — and for the strip, the *earliest* anchor, which is the same caller
  as the *longest* wait seen from the other end. The scalars stay, exactly as
  `acw_seconds` stays next to `acw_since`.
- **And one thing found while doing it:** `OfferCard` was the only timer that never applied
  the clock skew. On a laptop whose clock has drifted, the ring countdown was wrong by the
  offset with nothing on screen pointing at the clock — `B8` again, in the one component
  that had been missed.

### Symptom 2: after switching agents, an offer took ten seconds to appear

- **Symptom, exactly as reported:** sign in as A, press ready, place a call, decline, sign
  out; sign in as B, press ready — and the offer arrives late, with the ring countdown
  already down to about **10** of its 20 seconds.
- **Root cause, and it is a scoping mismatch.** `seq` is per **agent** on the server;
  `lastSeq` in `useSocket.ts` is a `useRef` per **tab**. Signing out does not unmount the
  hook, so B's socket opened with A's high-water mark, sent it as `hello {last_seq: n}`,
  and then discarded every live message at or below it:

      if (message.seq <= lastSeq.current) return;

  B's channel starts at 1. **So B's offer push was thrown away by B's own client**, along
  with everything else until B's sequence climbed past A's. The card appeared when the
  10-second heartbeat happened to trigger an unrelated refresh — which is why the countdown
  was always around 10.
- **Fix, two halves, and only one of them is optional.**
  - **Client (required):** reset `lastSeq` when `enabled` flips true — a fresh sign-in.
    A *reconnect* goes through `onclose` -> backoff -> `connect()` without re-running the
    effect, so it keeps its position and the outbox still replays the gap, which is the
    whole point of `D68`.
  - **Server (correct, not sufficient):** `AgentHub.reset()` on sign-in. A sign-in is not a
    reconnect; whatever is in that outbox belongs to a previous session at that desk, and
    replaying it hands the new arrival offers that were resolved before they sat down.
- **A third thing, which is not a bug but was part of the same complaint.** Pressing ready
  with somebody already queued did not match until the next sweep — correct, but up to a
  second of a caller waiting in front of a free agent. `POST /v1/agent/state` now ticks the
  dispatcher, unconditionally: any declaration can change the matrix, and the matcher costs
  under 50 ms.

### Verification

In a browser, in a tab that had just held A001's session:

* the offer card appeared **109 ms** after the caller was placed, with no user action —
  before the fix that push was discarded and the card waited for the heartbeat;
* pressing ready with a caller already queued rang the desk **immediately**, countdown at
  16 of 20 rather than ~10;
* the wait ticked `00:41 -> 00:43` on the card and `02:24 -> 02:26` on the strip over two
  seconds. (Those are two different callers, correctly: the strip shows the *longest* wait
  in the queue, the card shows *this* caller's — that difference briefly looked like a
  third bug and is not one.)

### Lessons

- **Ten seconds is a number, not a mystery.** Both faults presented as "the UI updates
  every ten seconds", and `HEARTBEAT_MS = 10_000` is the only ten in the client. When a
  symptom has a period, find the constant with that period first.
- **A duration on a screen is a clock, and a clock needs an anchor.** `D68` and `B8`
  established this for two timers and the pattern was never applied to the third. When you
  add a duration to a payload, ask whether anything will make it move.
- **A per-session counter has to be reset by the thing that starts a session.** The client
  and the server were both right about their own scope and wrong about each other's: `seq`
  per agent, `lastSeq` per tab, and nothing owned the transition between them.
- **`B26` and `B27` are the same fix arriving twice**, one day apart, because the first one
  stopped at "the server now computes the right number". Fixing a value is not the same as
  making it visible.

---

## B28. A call taken on `D113`'s second round could never be ended

_Found by the user, on the day `D113` shipped, by doing the one thing `D113` exists to make
possible. **A new feature made an old lookup ambiguous, and nothing in the lookup knew.**_

### Symptoms

As the only qualified agent: place a call, **decline** it, watch it come round again
(`D113`, correctly — the floor was exhausted), **accept** it, work it, then press
**End call**:

```
assignment asgn_01M1T7NSB2X6JSP8 was never accepted
```

The agent is on a live call the system refuses to end. `WRAP_UP` is unreachable, so ACW
never starts, the call sits in `IN_CALL` for the life of the shift, and the agent cannot
sign out either — `logout` refuses while a call is active.

### Root cause

`_assignment_for_call` in `api/routers/agent.py` answered *"which assignment is this
agent's, for this call"* by returning **the first match the store yielded**:

```python
for assignment in container.assignments.for_agent(who.agent_id):
    if assignment.call_session_id == call_session_id:
        return assignment
```

That was correct for as long as an agent could hold **one** assignment per call, which was
every day until `D113`. `D52` excluded a declining agent for the life of the call, so a
second offer to the same desk was impossible by construction.

`D113` deleted that guarantee on purpose: when every qualified agent has declined, the
exclusions are cleared and the caller goes round again. On a floor of one — every demo,
every rehearsal, and the user's own screen — round 2 comes straight back to the same agent.
Now `for_agent` returns **two** assignments for the call, in dict insertion order: the
round-1 **decline** first, the round-2 **accept** second. `end_call` got the decline,
checked `outcome is ACCEPTED`, and refused.

### The second half, which the user did not see

The same function **authorises** `attest_identity` and `start_capture`. While a stale
decline counted as this agent's assignment, an agent who declined a call kept the right to
act on that caller *while somebody else handled them* — attesting their identity, or
starting a keypad capture on their live call. Reproduced on three agents:
A001 declines, A002 is offered the call, and the log still reads
`keypad capture started agent_id=A001`. That is `D52`'s disclosure boundary, breached by
the same line.

### Investigation

`grep` for the error string reached `AssignmentService.end_call` in four steps, and the
guard there is right — the interesting question was immediately *why is it holding an
assignment that was declined*, not *why is it refusing*. From there the whole call graph of
`for_agent` was read rather than just the one caller (`B26`'s lesson, applied on purpose):
the other five consumers all filter by a state only the live assignment can be in
(`PENDING`, `acw_started_at is not None`, `acw_ended_at is not None`) or sort by
`offered_at` descending. `_assignment_for_call` was the only one that assumed uniqueness.

### Fix

Select only assignments that are still live — `PENDING` or `ACCEPTED` — and take the newest
of those. A declined, timed-out or cancelled offer is history and stops being a key to the
call, which fixes `end_call` and closes the `D52` hole in the same line. The sort is kept
even though `D113` can leave only one un-superseded, so the answer never depends on dict
insertion order again.

### Verification

Both directions, on the fix and with the fix disabled:

* `test_a_call_taken_on_the_second_round_can_still_be_ended` — the user's exact path as one
  agent, through HTTP. Fails without the fix with the reported message.
* `test_a_declined_offer_stops_being_a_key_to_the_caller` — the disclosure half; the
  capture must answer 404 once the offer is declined.
* `test_a_call_taken_on_a_later_round_completes_normally` in the stress suite — declines
  round the floor, accepts on round 2, and runs the call to `CLOSED`.

### Why the stress suite missed it

`test_a_caller_the_whole_floor_declines_comes_back_round` (added *with* `D113`) walks this
exact scenario. It stops at "the caller was offered again", which was the whole of `Q31` —
nothing then tried to **finish** the call. And `Floor.hang_up` discarded its response
without checking the status, so even a walk that did end a call would have swallowed the
400: every invariant still held, because the call it described had simply never ended.
`hang_up` and `wrap_up` assert their status now.

### Lessons

- **A feature that removes a uniqueness guarantee has to name everything that relied on
  it.** `D113`'s own landmine entry says `excluded_agents()` is no longer a permanent
  record. It did not say the other thing that changed: *one agent can now hold two
  assignments for one call*. That second sentence is where this bug lived.
- **"The first match" is only an answer while there can be one match.** A lookup that
  returns the head of an unordered collection is asserting uniqueness without saying so —
  and it fails silently the day that stops being true, on whichever element the dict
  happens to yield first.
- **A test helper that ignores a status code cannot fail.** `hang_up` posted, discarded a
  400, and the invariants afterwards were all true of a call that had never ended. This is
  `B24`'s family in a test: correct code, running, proving nothing.
- **Check the whole call graph of the thing you changed, not the caller you came in
  through** (`B26` again). Coming in through `end_call`, the disclosure half was two
  callers away and would not have been found by fixing the button the user pressed.

---

## B29. The test suite read the developer's `.env`, so one laptop's private file decided whether it passed

_Found while answering a question about `.env`, not while debugging — which is the only
reason it was found at all. It had been true since P0._

### Symptoms

Five tests in `tests/unit/test_transcript_over_http.py` failed on this laptop and on no
other, with a message about the audio path rather than about configuration:

```
the audio path produced no turns - nothing opened the recording, the detector found
no speech, or the scripted lines were refused by `D98`'s rate guard
```

Nothing in the repository had changed. `STT_ENGINE=scripted uv run pytest` passed.

### Root cause

`Settings.model_config` declares `env_file=".env"`. Tests build `Settings(...)` with the
handful of fields they care about and let everything else default — and "default" silently
included **whatever this machine happened to have in an untracked, gitignored file**.

The user had uncommented `STT_ENGINE=typhoon` and `VAD_ENGINE=silero` to try the real
engine. Both leaked into every test that did not pin them, so the suite loaded a real Thai
ASR model on the GPU and asked it to transcribe the synthetic tones the fixtures generate.
It returned nothing, correctly, and five tests failed.

The same `.env` also assigned `STT_ENGINE`, `VAD_ENGINE` and `LLM_PROVIDER` **twice** each
— once in the adapter block at the top and again in the behaviour block below. The later
assignment wins, so the top block was decorative, and reading the file top-down gave
exactly the wrong answer about what the machine was running.

### Why this is worse than five red tests

`tests/conftest.py` opens by promising *"nothing here touches a network, a GPU, or a
database."* That was a claim nothing enforced. The failure direction that actually matters
is the opposite one: a developer whose `.env` happens to be permissive gets a **green** run
for code that would fail on CI or on anyone else's machine. `B9` was working-tree versus
repository and `B15` was working-tree versus CI; this is the third member — the suite
described one machine's private configuration and called it the system.

### Fix

A session-scoped autouse fixture in `tests/conftest.py` sets `Settings.model_config`'s
`env_file` to `None` for the duration of the run and restores it afterwards.

Environment **variables** are deliberately left alone: `READYCALL_TEST_MINIO=1` and
`READYCALL_TEST_POSTGRES` are how the opt-in contract rows are selected, and
`STT_ENGINE=scripted uv run pytest` has to keep working. It is the *file* nobody expects to
change a test result.

The user's `.env` was also rewritten to assign every name exactly once, with the stage-safe
demo profile live and the real-engine profile sitting commented beside it. Both secrets were
carried across byte-for-byte and verified by hash.

### Verification

* The five tests fail with the fix removed and the same `.env` in place, and pass with it.
* `STT_ENGINE=scripted uv run pytest` still selects the scripted engine, so the
  environment-variable override is untouched.

### Lessons

- **A gitignored file must never be able to change a test result.** Config precedence is a
  feature for an application and a hazard for a suite: the suite's whole job is to describe
  the repository, and anything machine-local that reaches it is describing something else.
- **The dangerous direction is green, not red.** Five failures got investigated in minutes.
  A permissive `.env` producing a pass would have hidden a real fault for as long as it
  took somebody else to clone the repo.
- **A docstring that promises a property is worth turning into a fixture.** The claim
  "nothing here touches a GPU" had been sitting at the top of `conftest.py` since P0,
  believed and unenforced.
- **Assigning one name twice in an env file is `Q26`'s lie in reverse:** not a knob nothing
  reads, but a knob read from a line you did not think was the live one.

---

## B30. The cold-call path knew the product line and threw it away

_The reasoning and the lesson live with `D117` in `DECISIONS.md`; this entry exists so a
search of the bug file finds it._

- **Symptoms.** `relevant_policy: None` on the agent's screen for a caller whose policy we
  hold, with an empty policy panel. Appeared the moment the demo customer was given a
  second policy (`D117`) and four workstation tests turned red together.
- **Root cause.** `api/routers/demo.py` passes `product_line` to the context assembler on
  the app path and **not** on the cold-call path twelve lines below, although
  `body.intent_code` is in scope there and the expression is identical.
  `_pick_relevant_policy` falls back to "the only policy they have" when it has no line
  signal, so a one-policy fixture produced correct output for the wrong reason and hid the
  gap from P1b until now.
- **Investigation.** The instinct was that the fixture change had broken something. It had
  not: it had removed a fallback that was concealing a real omission. Reading
  `_pick_relevant_policy` before touching either side is what turned "my new fixture broke
  four tests" into "four tests were passing for the wrong reason".
- **Fix.** Pass the line on both paths, with the reason written at the call site.
- **Verification.** On a running server: a cold call with `health.claim.notify` now returns
  `relevant_policy` with the carrier on it. The four tests pass for the right reason.
- **Lessons.**
  - **A fixture with one of something tests nothing about choosing.** The branch that
    declines to guess between unrelated policies was unreachable, because no customer had
    two. Cardinality is part of a fixture's design — `B13`'s "never test a contention rule
    without contention", one layer down.
  - **When two code paths do the same job, diff them.** The app path and the cold-call
    path sit twelve lines apart and one of them was missing an argument the other passes.
    That is the cheapest possible review and nobody had done it.
  - **A green test on a degenerate fixture is a claim about the fixture, not the code.**
    This is `B24`'s family from the data side: the code was correct-looking, running, and
    exercised — by an input that could not distinguish right from wrong.

---

## B31. Two checks that were passing because of what happened to be installed

_Found by `uv sync --extra web --extra llm`, which **prunes** anything not named and so
removed the `ml` extra from the dev machine. Both faults had been latent since the audio
work landed, and both would have shown on CI, which installs no `ml` at all._

### Symptoms

One command turned a green tree into:

* `tests/contracts/test_vad.py` — **five errors** where the file's own docstring promises the
  Silero rows are *"skipped, loudly"* when the extra is absent.
* `mypy` — **five errors** across `silero.py`, three STT adapters and `api/deps.py`, all
  `Cannot find implementation or library stub for module named "torch"`.

### Root causes, and they are the same shape

**The skip guard was watching the wrong thing.** It caught `ImportError` around
`from readycall.adapters.vad.silero import SileroVad` — but that module imports cleanly.
`silero.py` imports `torch` *lazily inside the constructor* and re-raises it as a
`ConfigError` with a friendly message, so the failure happens at `SileroVad()`, one line
below the `try`. The guard could never fire.

The fix had to be careful, because the same file already carries a warning against
widening it: an earlier version wrapped the construction in `except Exception` and turned
a real load failure into five green skips. So the guard now asks the precise question —
`importlib.util.find_spec("torch") is None` — which is exactly *"the extra is genuinely
absent"* and leaves the construction unguarded, so a real load failure still errors.

**The mypy override list had never included `torch`.** `silero_vad`, `faster_whisper`,
`transformers` and `nemo` were all listed; `torch` was not, and the check passed anyway
because every machine that ran it had `torch` installed as a side effect of the `ml`
extra.

### Lessons

- **A check that depends on an optional extra being present is checking the machine.**
  Both of these were green for weeks on the only box anybody ran them on, and both would
  have been red on CI. This is `B15` again — working tree versus CI — with the dependency
  set as the variable rather than the install command.
- **`uv sync` prunes.** `uv sync --extra web` on a box with the GPU stack silently removes
  it. Name every extra you want in one command; the README says so now.
- **A lazy import moves where the failure happens, and guards do not follow it.** Deferring
  `import torch` into a constructor is right — it keeps the module importable on a CI box
  — but it relocates the error from import time to construction time, and every `except
  ImportError` written around the import is then guarding an empty room.

## B32. The tool catalogue was fetched once, before there was anybody to fetch it for
_Found on 2026-09-08 by opening the tool box and seeing four empty headings._

- **Symptoms.** The rail dialog opened, drew its four group headings, and contained no
  tools at all. `GET /v1/agent/assist/tools` returned all 11 correctly when called by hand
  from the same browser, with the same cookie.
- **Root cause.** The catalogue is static for the life of the process, so it was fetched in
  a `useEffect` with an empty dependency array — once, on mount. On mount the agent has not
  signed in yet, so the request went out with no session cookie, came back **401**, and hit
  a `.catch(() => setTools(null))` written to keep a rail failure from breaking the call
  screen. The catch worked exactly as designed and swallowed the only attempt that would
  ever be made. The rail was then empty for the whole shift.
- **Fix.** Key the effect to the signed-in agent (`[agentId]`) instead of to mount. The
  catalogue is still fetched once per sign-in rather than per call.
- **Lesson.** **"Fetch once" has to mean once *after the precondition*, not once ever.** An
  empty dependency array encodes "as early as possible", which is the wrong moment for
  anything needing authentication — and pairing it with a defensive `catch` converts a
  loud 401 into a permanently empty panel. When a swallow-and-continue is right, ask what
  makes the *next* attempt happen; here there was no next attempt.

## B33. A 2-second poll that was destroyed every second, so it never fired
_Found in the same session, one screen later, and it looked exactly like a server bug._

- **Symptoms.** The customer filled in the pushed form and submitted it; the page confirmed
  it had been sent, and `GET /v1/agent/calls/{id}/assist` showed `responded: true` with the
  values. The broker's dialog never updated. Everything server-side was correct, which is
  what made it look like the response was not reaching the API.
- **Root cause.** The dialog polls with `setInterval(onRefresh, 2000)` in an effect
  depending on `[open, callId, onRefresh]`. The parent passes an **inline arrow**, so
  `onRefresh` has a new identity on every render — and the workstation re-renders **once a
  second** to drive its call timers (`B8`'s ticker). So the effect tore the interval down
  and rebuilt it every second, and a 2000 ms interval that is destroyed at 1000 ms **never
  fires at all**.
- **Fix.** Hold the callback in a ref, have the interval read the ref, and depend on
  `[open, callId]` alone.
- **Lesson.** **An interval whose effect depends on a callback identity is a bet that the
  component re-renders more slowly than the interval.** This one re-renders every second
  *by design*, so the bet was always lost. It belongs with `B8` and `B27`: a whole family
  where the timer machinery is correct and something upstream stops it advancing — and
  where the visible symptom points at the server, because the data really is right there
  and really is not on screen.

## B34. An agent talking to a customer was signed out for not moving their mouse
_Reported by the user on 2026-09-08: **"i don't even interact with the workstation … is
there a timer that forces you out?"** There was, in effect. This is the most damaging bug
found so far, because it fires hardest in exactly the situation a demo creates._

- **Symptoms.** Leave the workstation on a live call and look at another window for a
  minute. Come back to a screen showing `ออกจากระบบ`, `พร้อมรับสาย`, `ยังไม่รับสาย` and
  `เชื่อมต่อแล้ว` **at the same time**; the caller's brief, identity panel and keypad all
  still rendered; the state buttons greyed out; and **no End call button**, so the call
  could not be ended at all. A reload changed nothing. Signing out and back in fixed the
  badges but left the dead call's panels on screen — and the moment a *later* call ended,
  the stale one came back and stuck.
- **Root cause, in two halves.**
  1. `PresenceService.sweep()` dropped **any** agent whose heartbeat was older than
     `agent_presence_ttl_s` (30 s), including one whose `system_state` was `ON_CALL`. The
     client heartbeats every 10 s from a `setInterval` — and **browsers throttle a hidden
     tab's timers to roughly one a minute**, so backgrounding the workstation for a minute
     silently exceeded the TTL. The "timer that forces you out" was the browser's.
  2. `sweep()`'s docstring says it returns the dropped ids *"so the caller can decide what
     to do about any call they were holding"*. Its one caller, `sweep_once` in
     `api/app.py`, **only logs the count**. So the call stayed `IN_CALL` with an ACCEPTED
     assignment belonging to an `OFFLINE` agent: unendable, unroutable, and permanently
     attached to that desk. `B10`'s exact shape reached by a different road.
- **The sharpest detail.** `test_signing_out_mid_call_is_refused` has asserted since P2b
  that an agent *may not* sign out mid-call. The rule existed, was tested, and was enforced
  only on the path the human drives. The platform's own path had no such check — so the
  system refused to let a person do the very thing it then did to them automatically.
- **Fix.** `sweep()` never drops an agent who is `ON_CALL`, and logs a warning naming how
  long they have been silent. ACW stays droppable on purpose: the customer has already
  gone and an unfiled wrap-up lands in `D87`'s backlog. Client-side, the workstation now
  also beats on `visibilitychange`, so a returning tab is correct at once.
- **Verified** by disabling the fix: the new test fails, and passes with it.
- **Lesson.** **A missing heartbeat is a claim about the tab, not about the person** — and
  browsers actively make that claim false. More generally: when a rule is worth enforcing
  against the user's own action, check whether the platform's automatic path enforces it
  too. Half the bug was the missing guard; the other half was a function that hands back
  a consequence its only caller drops on the floor.

## B35. The pairing status could only be learned by a poll the pairing had to unlock first
_Found by the user two minutes after `D121` shipped: **"when i open the generated link, it
doesn't unlock the tool rail … it just continues to say ยังไม่ได้เชื่อมหน้าจอ"**._

- **Symptoms.** The broker mints the link, the customer opens it and is looking at the
  page — and the workstation goes on saying the screen is not connected, so
  *เปิดกล่องเครื่องมือ* stays disabled and nothing can be pushed. Only a full page reload
  broke it.
- **Root cause.** The customer tapping the link is an event on **their** device, so the
  only way the broker's screen learns of it is by polling. The poll ran
  `if (!open || !callId) return;` — i.e. **only while the tool dialog was open** — and the
  button that opens that dialog is `disabled` until `paired` is true. Each half is
  defensible alone; together they are a deadlock, and I wrote both.
- **Why my own browser walkthrough missed it.** Every push I verified came after a reload
  or a fresh navigation, each of which re-runs the one-shot refresh. The deadlock needs
  somebody to *sit and wait* on a screen that never updates — which is what a real user
  does and a scripted walkthrough never does.
- **Fix.** Poll whenever a link exists **or** the dialog is open. A workstation that has
  minted no link still polls for nothing, which is the case the condition preserves.
- **Lesson.** **When state changes on somebody else's device, ask what makes this screen
  find out — and check that the answer is not gated on the state itself.** This is the
  third client-side fault in two days (`B32`, `B33`) where the server was entirely correct
  and the screen simply never asked again.

## B36. The app path could not place a call at all, and nothing had ever tried
_Found while wiring `/sim` to the queue (`D122`). It had been broken since P1b._

- **Symptoms.** `POST /v1/demo/calls` with a `correlation_token` returned **500**, every
  time. `IllegalTransition: intent_created -> ivr`.
- **Root cause.** `start_from_intent` leaves the call in `INTENT_CREATED`, which is
  correct: tapping Contact in the app produces a dial target, and the customer has not
  rung yet. The only legal move from there is `CONNECTING` — the dial. The demo endpoint
  went straight on to run the IVR, which requires `CONNECTING`, so the app branch could
  never complete.
- **Why nothing caught it.** Every scenario, every test and every manual demo that reaches
  an agent arrives as a **cold call**, and `start_cold_call` begins in `CONNECTING`
  already. The one caller who would have used the app branch — the simulator — minted a
  token and stopped, so the branch was written, reviewed, committed, and executed by
  nobody for six phases.
- **Fix.** Transition to `CONNECTING` after `start_from_intent`, with a comment saying it
  is the simulated dial that telephony performs at P5.
- **Verified** by disabling the fix: the new test fails, and passes with it.
- **Lesson.** **Two entry paths need two tests, and the one with no demo behind it is the
  one that rots.** This is `B7`/`B24`'s family again, with a twist worth naming: the code
  was not merely uncalled, it was *unreachable through the only UI that would have called
  it*, because that UI stopped one step short. A branch guarded by an `if` that nothing in
  the repo ever satisfies is dead code wearing a disguise.

## B37. The customer's screen stayed "in a call" for the whole of after-call work
_Reported by the user: **"pressing end call does not end the call on /sim and /assist —
only ending acw/save record does"**. Three dead methods behind one symptom._

- **Symptoms.** The broker presses **วางสาย**. The customer's screen goes on saying
  *"กำลังสนทนากับเจ้าหน้าที่"*, keeps its forms, and only clears when the agent finishes
  their wrap-up — which `D45` deliberately lets run for as long as the agent needs.
- **Root cause, in three parts.**
  1. Both "is this call live" sets counted `WRAP_UP`. But `D45`'s whole point is that
     after-call work is the **agent's** paperwork; the customer hung up when the media
     stopped. The customer is on the far side of that line and their screen should say so.
  2. **`AssistService.close()` was called by nothing.** It exists to shorten the pairing
     to `PAIRING_GRACE` when a call ends — the documented behaviour in `D120` — and no
     code path invoked it.
  3. **`AssistService.sweep()` was called by nothing either**, so expired pairings were
     never dropped.
- **Fix.** `end_call` calls `close()`; `sweep_once` calls `sweep()`; `WRAP_UP` comes out of
  both live sets. The app shows *"สายสิ้นสุดแล้ว"* and keeps what is on screen for the
  grace window, so a half-typed form is not blanked under the customer's fingers.
- **And a second fault, found by pressing the button rather than reading the diff.** The
  grace covered the *display* and not the *send*: `POST /v1/app/assist/respond` looked
  only at live calls, so the form survived the broker ringing off, sat there fully typed,
  and then 404'd on submit. **That is worse than clearing it** — the customer is told
  nothing and believes it went. Both paths use the grace pairing now.
- **Lesson.** Two of the three causes were methods written in the same file, on the same
  day, as the feature that needed them — `B7`'s family arriving *within* a single change
  rather than across months. Writing `close()` and calling `close()` are separate acts, and
  only the second one does anything. And when you add a grace window, walk every operation
  it is supposed to cover: a half-covered grace is a promise the UI keeps and the API
  breaks.
