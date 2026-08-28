# BUG_HISTORY

_Solved bugs and the lessons they bought. **Search this file FIRST when debugging** — the answer may already be here._
_Last updated: 2026-08-25._

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
