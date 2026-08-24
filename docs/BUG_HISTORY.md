# BUG_HISTORY

_Solved bugs and the lessons they bought. **Search this file FIRST when debugging** — the answer may already be here._
_Last updated: 2026-08-24._

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
