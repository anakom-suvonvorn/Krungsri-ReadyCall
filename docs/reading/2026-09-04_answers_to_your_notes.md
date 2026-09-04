# Answers to your notes, 4 September

_Your note was not a mess. It contained one live bug, one correct diagnosis of why the old
project felt faster, and one architectural idea that is right. Apologies are not needed —
this is the most useful feedback I have had on this project._

Read [2026-09-03_what_happened.md](2026-09-03_what_happened.md) first if you have not; it
explains the vocabulary this file uses.

---

## The headline: your phone-number catch halved the error rate

You asked:

> _"these are just flagging the thing where the customer will say the number of something,
> like telephone number… so we can't be sure if it's a hallucination from the model or not?
> which is not right?? right?"_

**Right. And it was worse than you thought.**

`เก้า` means **nine**. I checked the dataset's own answer keys and converted the spelled-out
Thai numerals back into digits:

| call | the real phone number | longest run of one repeated digit |
|---|---|---|
| `d9b_1670931464` | 0989**9999**34 | **5 × เก้า** |
| `d9b_1670931950` | 0989**9999**35 | **5 × เก้า** |
| `ecb_1670853026` | 0817**99999**8 | **5 × เก้า** |
| `c3a_1670959100` | 0989**9999**449 | **4 × เก้า** |
| `547_1670935733` | 0989**9999**197 | **4 × เก้า** |

**Nine of the twelve calls contain a run of three or more identical digit words.** Our guard
refused at three. It was deleting customers' phone numbers in the majority of calls, by
construction — and a phone number is the one thing on an agent's screen that has to be exact
and cannot be guessed from context.

### What fixing it did

Same model, same audio, only the guard changed:

| | before | after |
|---|---|---|
| median CER | 0.292 | **0.161** |
| mean CER | 0.302 | **0.171** |
| lines of transcript | 74 | **82** |
| calls improved | — | **8 of 12, none worse** |

One call went to **CER 0.009** — essentially a perfect transcript. And the causal check
holds: the four calls that did *not* improve are precisely the four where no digit line had
been dropped.

The fix gives a **digit word** a threshold of 10 repeats instead of 3. Ten is the arithmetic
ceiling on a real run, since a Thai mobile number is ten digits; a hallucination repeats
dozens of times, so nothing real is given up. Seven new tests, and I checked them against
the old threshold first — they fail there, which is the only way to know a test is doing
anything. Their data is not invented: the "must survive" strings are the phone numbers from
the answer keys, and the "used to be eaten" strings are pasted from your terminal.

> **Why it survived the last review.** `B16` rebuilt this guard around three real Thonburian
> loops you gave me — and all three repeated a *word*. I broadened the fixture from one
> example to three and all three were the same **kind** of example. The question that finds
> this on day one is not "does the guard work?" but **"what in this language legitimately
> repeats?"** — and that is a question the person who has heard the calls asks, not the
> person reading the code.

---

## "why is rtf all n/a???"

Because you ran it **paced** (without `--fast`), and in a paced run `rtf` cannot mean
anything. Pacing sleeps to match real talking speed, so wall-clock time equals the audio's
length *by construction* — it would read ~1.00 on every row no matter how fast the model is.
Printing it would be a fake number, so the column blanks.

But you were right to be annoyed, because the thing rtf was standing in for **does** matter
and there was no way to see it. So I added it. Two new columns, both meaningful in either
mode:

**`busy`** — seconds spent inside the model, per second of audio. This is *the* number.
Below 1.00 the transcriber keeps up. **Above 1.00 it can never catch up** and falls further
behind every sentence for the rest of the call. Measured on your 12 calls:

```
busy   0.16 – 1.48,  median 0.45,  one call over 1.00
```

**`pad`** — how many seconds Whisper actually *encoded* per second of real call:

```
pad    2.2 – 3.0,  median 2.7
```

Which brings us to your big idea.

---

## The 30-second padding — yes, you have it exactly right

> _"btw im understanding the 30s stuff right right? like it pads whatever the audio is into
> 30s with empty quite audio?"_

**Yes.** Whisper's encoder always processes exactly 30 seconds. Hand it 1.5 seconds of speech
and it zero-pads to 30 and does the full amount of work. A 1.5-second clip and a 25-second
clip cost the *same*.

That is why `pad` reads 2.7: we are asking the GPU to encode **2.7× more audio than the call
contains**, because we send 6–9 separate short clips per call and each one costs a full
window.

And your inference from that is also right: this is not only a speed problem. Feeding tiny
clips is exactly the condition that produces `B14`'s hallucination — a fragment surrounded by
29 seconds of silence is close to the "near-silence" input that made the model invent Thai.

### But there are two different versions of your idea, and one is much safer

You suggested both without quite separating them, so let me:

**A. Packing** — glue several utterances together into one ≤30s clip and send it as *one*
request. This is what cuts the padding waste: 7 windows per call becomes 1–2.
- **Prize:** roughly **3–7× less GPU work**. Would take `busy` from median 0.45 to ~0.1, and
  the worst call from 1.48 to ~0.3 — comfortably solving the latency problem.
- **Cost:** you get one blob of text back and lose the per-utterance boundaries and
  timestamps. And you must wait to fill the window, which *adds* delay by construction.

**B. Batching** — send several *separate* clips through the GPU in one forward pass. Each
keeps its own transcription and its own boundaries. Nothing about the output changes.
- **Prize:** better GPU utilisation, no semantic change, no added delay.
- **Cost:** none, really, beyond memory.

**And here is the thing I found in your old project:**

```python
prediction_gen = pipe(
    KeyDataset(audio_dataset, "audio"),
    batch_size=4,          # <-- this
)
```

> _"maybe it worked very well for me in the past bc that implementation does the thing in
> big chunks"_

**That is exactly it, and now we know the mechanism.** The old project ran four clips through
the GPU at once. It also had the easy version of the problem — a file on disk, so every chunk
is available up front and batching is free. We are streaming, so a clip only exists once the
caller has said it.

**My recommendation: do B first, then measure, and only reach for A if B is not enough.**
Batching is the change that cannot make anything worse. And it fits the streaming case
naturally in the exact place it is needed: when the model is keeping up, the queue has one
item and we send one; when it falls behind, the queue has several and we send them together.
The speed-up arrives precisely when there is a backlog, which is the only time it matters.

Packing stays on the table as the bigger hammer, and your "cut at the closest pause before
30s" rule is the right shape for it.

---

## "why are a lot of these just numbers??"

Two separate things here, and you spotted both.

**1. The guard was eating the number lines** — that is `B21` above, now fixed.

**2. The test set genuinely is number-heavy, and you are right that this is a problem.**
`prepare_dataset.py` picks calls at random from the dataset, and it happens that almost every
call in this corpus ends with the customer reading out a phone number. So our 12 calls
over-represent digits and under-represent ordinary conversation.

> _"don't just use ONLY number saying heavy audio, also have a bunch of speech heavy audio
> too"_

Agreed, and it matters more now than before: the digit exemption I just added is a *loosening*
of a safety guard, and a test set full of digits is the wrong set to check that loosening did
not let real hallucinations through. **This is on the list below.**

---

## "if repeat > currently drops right? why don't we just… 'un-repeat' it?"

Good idea, and I want to take the useful half while flagging the trap.

**The trap: never do this to digits.** Compressing `เก้า เก้า เก้า เก้า` to `เก้า` turns
`9999` into `9` — it would silently corrupt a phone number into a *different, plausible*
phone number. That is worse than dropping it, because a dropped line is visibly missing and a
wrong number is not.

**Where it is genuinely right:** a *word* loop with a real sentence in front of it —
`คนเชื่อถือในการการการการ…`. The prefix is real speech and we currently throw it away.

**But there is a case where it is wrong even for words**, and it is the one from `B14`: fed
near-silence, the model produced a loop where *the entire thing was invented*. Compressing
that surfaces invented text on the agent's screen instead of nothing.

**So the rule I would implement is:** compress only when the repeating run does **not** cover
the whole text — i.e. there is a genuine non-repeating prefix to recover — and never for
digits. When the loop *is* the whole utterance, keep dropping it. That is your instinct ("the
first part might be useful, only the after part is weird") with the two failure modes fenced
off. Not built yet; it is on the list.

---

## "the time to transcribe is way too long > cut off"

You are re-deriving something already decided, and I want to confirm you are right rather
than just point at the entry.

We already have a guard that catches a runaway *after the fact*, by asking whether more
characters came back than a human could physically have said. What you are describing — stop
it **while it is running** — is the missing half, and it is genuinely better because it saves
the wasted seconds rather than merely labelling them.

The reason it is not built is boring and real: stopping a computation mid-flight needs the
model to run in a **separate process** that can be killed. You cannot cancel a thread in
Python. That separate worker process is already planned; the timeout belongs there. Faking it
with a timer that does not actually stop anything would be a guard that looks like one and
is not, which is a mistake this project has made before.

---

## Typhoon: installed

Done — `nemo_toolkit[asr]` is in, as its own optional extra so it does not land on anything
that does not need it, and I have verified it imports. It is not yet benchmarked.

Worth restating why it is interesting, because it connects to everything above: Typhoon is a
**transducer**, not a Whisper model. It has **no 30-second window**. If the padding waste is
the root of our speed problem, Typhoon does not have that problem *structurally* rather than
having less of it.

---

## Brand names — you asked what I think

> _"or like show all 3? so that i can get the idea from looking at all 3 scores?? but have 3
> variations of a score would also be a lot too??? so idk?"_

**Your instinct — "so I can get the idea by comparing" — is right. Three scores is the wrong
way to get it.** Three numbers for one thing means every future conversation opens with
"which one do you mean?", and the point of a metric is to end arguments, not start them.

**What I did instead: one headline score, plus one diagnostic.** The table now has `CER`
(the headline, the only thing engines are ranked on) and `CERth` — the same score with
Latin-script spans removed from both sides. `CERth` is not a competing ranking; **the gap
between the two columns is the answer to your question.** A big gap means the headline is
pessimistic and roughly by how much. A small gap means the headline is what it looks like.

**Why not rank on `CERth`:** it would excuse every engine from the words it is most likely to
get wrong — and an insurance line will hear brand names too. That hides a real weakness
rather than measuring it.

**And the part that means this does not block us:** the mismatch hits every engine roughly
equally, so it distorts the absolute number but **not the ranking**. Since the decision we
actually need to make is a ranking one — Thonburian vs CT2 vs Typhoon — we can get on with it
today and settle the cosmetics later.

---

## One thing I got wrong while writing this

While comparing before/after for the digit fix, I read the wrong column out of the old
results file — the layout had changed when I added the new columns — and briefly had a
spectacular improvement that was really CER-vs-WER. I caught it because the numbers were
*too* good, re-ran it properly, and the real improvement is the one in the table above.

Mentioning it because it is the same failure this project keeps hitting, and the third time
this week: **a number is not a measurement until you know what produced it.**

---

## What is next, in order

1. **Batching (`B` above).** The safe version of your idea, and it is aimed straight at the
   one call with `busy` 1.48.
2. **A better test set.** Re-prepare with a deliberate mix of speech-heavy and number-heavy
   calls, and re-check the loosened digit guard against it. Your point, and it is now
   load-bearing.
3. **Typhoon on the bake-off**, with `busy`, `pad`, `CER` and `CERth` side by side against
   Thonburian and the CT2 build.
4. **Compression with the two fences** (never digits; only with a real prefix).
5. The decode timeout, once the worker process exists.

---

## Try it yourself

The digit fix, on the strings from your own terminal:

```bash
uv run pytest tests/unit/test_transcription_stream.py -q -k "phone_number or digit_lines"
```

The new columns:

```bash
uv run python scripts/bake_off.py --engines thonburian --vad silero --fast --audio "tests/audio/thai_calls/*.wav" --dump transcripts.txt
```

Drop `--fast` for real latency numbers — it takes as long as the audio does (~16 minutes).
