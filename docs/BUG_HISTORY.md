# BUG_HISTORY

_Solved bugs and the lessons they bought. **Search this file FIRST when debugging** — the answer may already be here._
_Last updated: 2026-08-17._

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

_No entries yet — the project is in the planning phase and no code exists._

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
