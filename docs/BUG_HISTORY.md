# BUG_HISTORY

_Solved bugs and the lessons they bought. **Search this file FIRST when debugging** — the answer may already be here._
_Last updated: 2026-08-23._

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
