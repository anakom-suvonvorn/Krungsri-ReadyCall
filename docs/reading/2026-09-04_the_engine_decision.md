# Picking the speech engine, in plain language

_Written 2026-09-04, after your notes. Continues
[2026-09-03_what_happened.md](2026-09-03_what_happened.md) and
[2026-09-04_answers_to_your_notes.md](2026-09-04_answers_to_your_notes.md), which have the
glossary._

**One sentence:** starting from your instruction to fix the test set first, we now have a
speech engine that **hits the speed target on every single call**, where yesterday nothing
came close — and getting there required correcting three of my own numbers along the way.

---

## Part 1 — You were right to make me do the test set first

I re-prepared it with a deliberate mix: 20 calls, digit share from **0% to 49%**, where
before every call was somebody reading out a phone number.

Then I ran the *same engine* on the old set and the new one:

| Thonburian fp16, identical code | CER median | mean |
|---|---|---|
| old, digit-heavy set (12 calls) | 0.161 | 0.171 |
| **new, balanced set (20 calls)** | **0.089** | 0.109 |

**The test set alone moved the headline number by 1.8×.** Phone numbers are the hardest
thing in this corpus — each digit is its own word with no sentence context to help. Every
accuracy figure this project had recorded was measured on the pessimistic set.

That is a bigger effect than any engine change I made afterwards. Doing it first was the
right call and I would not have done it in that order.

---

## Part 2 — The comparison I'd already committed was unfair

Remember `B19` — one of the two speech engines silently ignored the "vocabulary hint" (a
list of insurance words we give the model to nudge it)?

I fixed it, and then realised the comparison I'd committed that morning had **one engine
hinted and the other not**. The exact thing `B19`'s own write-up warned about. I wrote that
warning and then walked into it.

So I measured all four combinations properly:

| CER (lower is better) | **without** the hint | **with** the hint |
|---|---|---|
| Thonburian fp16 | **0.109** | 0.119 |
| CT2 (compressed) | 0.171 | 0.128 |

Two things fall out:

- **Compression really does cost accuracy** — 0.109 → 0.171 unhinted. My earlier table hid
  this, because the compressed engine had the hint and the other didn't.
- **The hint repairs most of it**, and it *steadies* the model: the compressed engine's
  worst-case workload dropped from 0.46 to 0.11 with the hint. A model that isn't
  wandering emits fewer words, and fewer words is less time. Two separate measurements
  pointing the same way is worth more than either alone.

**And it answers a question we'd had open (`Q27`):** does an *insurance* word list hurt on
these government-service calls? **No.** Slightly negative on one engine, strongly positive
on the other. Keep it.

---

## Part 3 — I had to correct myself again, and this one is a methodology lesson

I then wrote up "the compressed engine wins on every axis", based on it scoring 0.087
against 0.089.

**That was wrong**, and here's how I caught it. I ran the *identical configuration* twice:

| same engine, same audio, same settings | CER median | CER mean |
|---|---|---|
| run 1 | **0.087** | 0.128 |
| run 2 | **0.124** | 0.130 |

The median moved by 40%. The mean barely moved at all.

The reason: this model's output isn't perfectly repeatable run-to-run, and with only 20
calls spread widely, **two calls crossing the middle drag the median a long way** while
leaving the average alone. I'd been quoting the unstable one.

On the *mean*, the most accurate setup is plain fp16 (0.109), not the compressed one
(0.128). So compression is a **trade**, not a free win.

The measuring tool now prints mean, median and worst together, so this can't be quoted
carelessly again.

---

## Part 4 — Then Typhoon, and it is not close

Typhoon is the other Thai model you approved installing. It is **not a Whisper model** —
different architecture entirely.

| paced, on the balanced set | fp16 | compressed CT2 | **Typhoon** |
|---|---|---|---|
| delay before words appear, typical | 19.5 s | 1.68 s | **0.19 s** |
| delay, worst call | 58.7 s | 2.53 s | **0.28 s** |
| **calls inside the 1.5 s target** | **0 of 12** | 7 of 20 | **20 of 20** |
| accuracy (CER mean) | **0.109** | 0.128 | 0.133 |
| memory | 2716 MB | ~1000 MB | 1068 MB |

**Every call meets the target. Nothing else has ever met it once.**

### Why — and this is your 30-second insight, confirmed

Remember you worked out that Whisper pads everything to 30 seconds, so tiny clips waste the
model? **Typhoon doesn't have a 30-second window at all.** It processes exactly what it's
given. So it never pays for the padding that was costing every Whisper variant 2.5–3× the
audio actually spoken.

Compressing a Whisper makes each 30-second window *cheaper*. Typhoon doesn't have the
window.

We had written this down as a prediction back when the adapter was first sketched — *"if
the latency budget turns out to be the problem on this GPU, this is the structural reason it
might not be"* — and this is the one prediction on this project that was written first and
then confirmed. Most have gone the other way.

### The cost, honestly

**Typhoon is about 22% less accurate than plain fp16** (0.133 vs 0.109), and it sits
*between* the two Whisper setups rather than beating them. It also **cannot use the
vocabulary hint** at all — that mechanism only exists in Whisper-family models, so the
points the hint buys aren't available here.

It's still right, for the same reason as before: a transcript a few points less accurate is
one the agent reads more carefully. A transcript that misses the target is one they **don't
have** when they pick up.

---

## Part 5 — Two more things that would have broken on demo day

Both are the same shape, and neither could be caught by a test:

1. **`B22`** — releasing a model didn't release its GPU memory. Found by watching the card
   during a run and noticing the number was too big for what was loaded. It made one VRAM
   figure wrong (585 MB reported, 1106 MB real).
2. **`B23`, then again with Typhoon** — *twice today* the engine a decision had just chosen
   **could not actually be switched on**. Once because a config default pointed at the wrong
   kind of model file; once because the engine wasn't wired into the selector at all and
   silently fell back to the fake one.

Twice is a pattern, so there's now a test asserting that every engine the docs recommend can
actually be selected.

---

## Where it stands

- **Engine: Typhoon** (`STT_ENGINE=typhoon`, needs the NeMo extra you approved).
- **Fallback: the compressed CT2 build**, which needs only the standard extra — for a
  machine where NeMo won't install. Its numbers are recorded so that choice is informed.
- **Rejected: the distilled large model** that was already in your cache. Free to try, and
  worse than CT2 on every axis. Recorded so nobody spends the download again.
- **Speed target: met.** 20 of 20.

**676 tests**, everything green, tree clean.

## What I'd do next

1. **Explain Typhoon's 3 missing turns.** It produces 143 where the Whisper engines produce
   146. Small, unexplained, and "the fast one also says less" is exactly the sort of thing
   that turns out to matter.
2. **Check whether Typhoon hallucinates on silence** the way Whisper does (`B14`). We
   predicted it shouldn't. We predicted the speed correctly, but that's not evidence for
   this one.
3. **Your packing idea is now unnecessary** — it existed to reduce the number of 30-second
   windows, and the chosen engine has none. The design stays written down in case we ever
   go back to a Whisper.
