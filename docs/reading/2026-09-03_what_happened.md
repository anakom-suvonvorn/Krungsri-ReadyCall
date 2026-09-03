# What happened on 3 September, explained from zero

_Written 2026-09-03, for you, deliberately assuming **nothing**._

You have told me three times now that my summaries do not land. That is my fault, not
yours: I write in shorthand (`B20`, `D100`, "CER", "rtf") that only makes sense if you
already know the story. This file starts from nothing and builds up. There is a
**glossary at the bottom** — if a word looks like jargon, it is defined there.

**The one-sentence version:** the speech-to-text was scoring badly, I assumed it was the
AI model's fault, it turned out to be a bug in **our own code** that was silently throwing
away half of every caller's sentences — and fixing it revealed a *different*, bigger
problem underneath, which is that the transcript can arrive up to a minute late.

---

## Part 0 — Which part of the system this is about

ReadyCall does a lot of things. Today touched exactly **one** of them.

When somebody phones in, the system figures out who they are, why they are calling, and
which agent should take it. While they are waiting for that agent, we offer to record
what they say, so the agent can see it written on their screen the moment they pick up.

**Turning that recorded speech into text on a screen is "the audio path".** That is the
only thing this document is about. Nothing else in the system changed today.

The audio path has four stages, in order:

```
  1. the phone line     raw audio arrives
  2. the DETECTOR       "somebody is talking right now" / "silence right now"
  3. the ENDPOINTER     cuts the audio into sentence-sized pieces at the pauses
  4. the MODEL          turns one piece of audio into one line of Thai text
                            ↓
                        the agent's screen
```

Every stage is something that can be wrong, and a big part of today was working out
**which one** was.

---

## Part 1 — Where we were when today started

Yesterday you gave me the Kaggle dataset of real Thai call-centre recordings. That was
the first time this system had ever been tested on real human speech instead of on
synthetic beeps. I ran the first measurement and it came back **bad**.

The score is called **CER** — character error rate. It works like this:

- **0.00** = the machine's text is character-for-character identical to what a human wrote
  down as the truth. Perfect.
- **1.00** = totally wrong.
- **0.10** = about one character in ten is wrong. That is a good result for real phone audio.

We were getting **0.47 to 0.76**. Roughly *half to three-quarters of the characters wrong*.
That is not "needs tuning", that is "something is broken".

I wrote down four possible explanations and, honestly, said I did not know which:

1. **The answer key doesn't cover everything.** The human-written transcript only covers
   the bits somebody bothered to annotate. If we transcribe *more* audio than that, every
   extra word is scored as an error even if it is correct.
2. **The detector is throwing away speech**, so the model never gets to hear it.
3. **A vocabulary hint isn't being passed to the model**, so it does not know it is
   listening for insurance words.
4. **Real phone audio is just harder** than the clean audio the model advertises its
   scores on.

---

## Part 2 — How I worked through those four

The principle I used, and it is the reusable bit: **do the check that is cheapest to run
and most likely to kill a theory, first.** Do not start with the theory you like.

### Theory 1: the answer key doesn't cover everything → **dead**

The human answer key covers only **15–29%** of each recording. So on the face of it this
looked very promising: we transcribe the whole file, they only wrote down a fifth of it,
so four-fifths of our output would be counted as wrong.

But that only matters **if there is anything to hear in the other 75%.** So I measured how
loud that audio actually is, second by second.

**It is digital silence.** Not "quiet" — actually nothing. Median loudness: 0.0000. Only
1% of it even reaches a third of normal speaking volume.

The reason is obvious in hindsight: these recordings are **one side of a phone call**. The
customer's microphone is silent while the agent is talking. There were never any extra
words available to be wrong.

> **Theory 1 killed, and it cost about 10 minutes.**

### Theory 2: the detector is throwing away speech → **looked guilty, was innocent**

To check this I had to write a new tool, because nothing in the project could measure the
detector at all. That tool is now `scripts/score_endpointer.py`. It compares what our
detector *thinks* is speech against the spans a human annotated by hand.

Its first answer looked damning: **20% of annotated speech never reached the model.**

That is a big number and it was very tempting to stop there and say "found it". But 20% is
not 60%, so it could not be the whole story — and something felt off. So I measured the
same thing a second way: instead of counting the *seconds* we missed, I measured **how
loud those missed seconds were**.

- **86% of them are near-silent.**
- **72% of them sit within half a second of the edge of an annotated span.**

That is not us losing sentences. That is **a human annotator drawing a box slightly too
big** — they marked "speech from 9.45 to 15.75" and the person actually started talking at
9.6 and stopped at 15.5. We correctly ignore the silence at the edges; the scorer counted
it as a miss.

Actual speech lost: **3%**, not 20%.

I also swept the detector's sensitivity from its current setting all the way to very
twitchy, and it barely helped. **So the detector's settings are right for this audio and
should be left alone.** That is a useful thing to now know for certain.

> **Theory 2 killed. And a genuinely useful measurement tool exists now that didn't before.**

### Theory 3: the vocabulary hint → **real, but far too small**

This one is a real bug (I'd found it yesterday) but it could never explain a score that
bad. Parked.

### Theory 4: real audio is just harder → **never needed**

I deliberately put this last, and here is why that matters: **it is the one theory that
cannot be acted on.** If the answer is "the audio is hard", there is nothing to do. That
makes it the most comfortable explanation and the least useful one. Comfortable
explanations should always be tested last, because it is far too easy to stop there and
feel finished.

---

## Part 3 — What actually found it: looking at the words

At this point all four theories were dead or too small, which meant my whole framing was
wrong.

So I did the obvious thing that **nobody had done in this entire project**: I looked at the
actual text. Not the score — the words.

I could not, because there was no way to see them. So I added one (`--dump`). Here is what
came out, for one call:

**What the human wrote down (112 characters):**
> สนใจซื้อมือถือค่ะ · โปรมือถือสำหรับเดือนนี้ มีตัวไหนบ้างคะ · ขอบคุณค่ะ · โปรโมชั่นเป็นยังไงคะ · มีเครื่องพร้อมส่งไหมคะ · สนใจค่ะ

**What our system produced (48 characters):**
> โปรโมชั่นเป็นยังไงคะ · เครื่องพร้อมส่งมั้ยคะ · สนใจ ค่ะ

Look at that carefully, because it is the whole story:

- The text we produced is **completely correct Thai**.
- It matches the **last three sentences** of the answer key.
- **The first three sentences are simply not there.**

The model was not making mistakes. **It was never being asked about the first half of the
call.** And a missing sentence, in CER terms, counts as every one of its characters being
wrong — which is exactly how "correct output" produced a score of 0.6.

To be certain, I then transcribed each piece of that call **one at a time, by hand**. All
six came back, all six correct.

> So: the model was fine. The detector was fine. The safety guards were fine.
> **The loss was in the plumbing between them.**

---

## Part 4 — The bug, in plain terms

Here is how the audio path actually works internally.

Audio arrives continuously and goes into a **buffer** — think of it as a conveyor belt
holding the last stretch of the call. When the endpointer decides "that was a sentence",
it does not hand over the audio itself; it hands over a **note saying "the sentence is at
positions 7.7s to 9.6s"**, and that note goes into a **queue**. A single worker (the AI
model) works through that queue one note at a time, and for each note it goes back to the
belt and fetches that stretch of audio.

The belt cannot grow forever — a long call would eat all the memory — so it throws old
audio away.

**And here is the bug: it decided what counted as "old" by measuring backwards from the
newest audio that had arrived, rather than from the oldest note the worker still had to
do.**

That is fine as long as the worker keeps up. But when the worker fell behind, the belt
carried on binning things — **including the audio for notes still sitting in the worker's
in-tray.** By the time the worker got to note #1, the audio it pointed at was gone.

That is why we lost the *beginning* of calls specifically, and why the parts that survived
were perfect.

### And now the genuinely dangerous half

When the worker went to fetch audio that had been binned, the code did **not** say "that's
gone". It had a line in it that quietly clamped the position back to zero — so it returned
**whatever happened to be at the start of the belt now**, at roughly the right length.

That is a different moment of the call.

In Thai, a random couple of seconds of real conversation transcribes into **a perfectly
fluent, plausible sentence**. It would have appeared on the agent's screen, attached to the
wrong moment, looking exactly as trustworthy as a real one.

**A missing sentence is a gap you can see. A confident wrong sentence is not.** This is the
single most dangerous kind of failure this system can have, and it was live in the code.

---

## Part 5 — What I changed

Four things:

1. **Every note in the queue now holds a claim on the belt.** Nothing older than the
   oldest outstanding claim can be thrown away. This is the actual fix.
2. **The backlog is capped at two minutes, and giving up is loud.** If the model falls more
   than two minutes behind, we do abandon the oldest audio — because the alternative is
   the buffer growing until the program dies — but it now writes a **warning** in the log.
   It cannot happen silently, which is what made this bug survive.
3. **If a piece's audio really is gone, we now refuse it** rather than approximating it
   from whatever is lying around. This kills the dangerous half permanently.
4. **Two places that used to discard things without a word now say so.** Those silent
   discards are the reason my instruments showed nothing for a whole session.

I also found a **second, unrelated leak** while re-reading my own fix before committing it:
the cleanup only ever ran when a sentence *finished*, so on a call where nobody speaks at
all, nothing was ever cleaned up and the buffer grew about **30 MB a minute**. Measured at
16 million samples held after ~17 minutes of silence. Fixed, with its own test.

> Worth noting *how* that was found: **re-reading a change before committing it.** That is
> the cheapest review this project has and it caught a real bug today.

---

## Part 6 — How I proved the fix actually works

This matters, because "I fixed it" is easy to say.

**I ran the new tests against the OLD, broken code first.** A test that passes on code you
have already fixed proves nothing at all — it might be testing nothing. On the old code,
these tests fail exactly as they should: three sentences go in, one comes out.

I also made the tests assert something **stronger than they used to**. Every existing test
in that file checked "three results arrived". That can never catch the dangerous half,
because in that failure three results *do* arrive — they are just the wrong audio. So the
fake model in the new tests **reports the loudness of the audio it was handed**, which lets
the test insist that *result three carried segment three's audio specifically*.

Results on the real GPU:

| | before | after |
|---|---|---|
| call 1 | 3 sentences, CER 0.616 | **6 sentences, CER 0.188** |
| call 2 | 4 sentences, CER 0.709 | **7 sentences, CER 0.139** |

Across all 12 real calls the score is now **0.09 to 0.50, typically 0.29**. Same model, same
audio, same day.

---

## Part 7 — The problem that was hiding underneath, which is now the real one

There is a flag on the measuring tool called `--fast`. It shoves the audio in as fast as
possible instead of at the speed a person actually talks.

I added that flag *yesterday, to fix a different measurement problem* — and then used it
for every measurement afterwards. **That is what let the bug above survive**, because
shoving audio in at maximum speed is exactly the condition that makes the worker fall
behind.

So today I ran it properly for the first time, at real talking speed. It takes as long as
the audio does — about 16 minutes — which is why nobody had done it.

**The target is that a sentence appears on screen within 1.5 seconds of the caller
finishing it.** Here is what actually happens, across all 12 calls:

| how fast the model got through the audio | calls | delay before the words appear |
|---|---|---|
| comfortably faster than real time | 6 | 4.5 – 8 seconds |
| close to real time | 5 | **31 – 49 seconds** |
| slower than real time | 1 | **59 seconds** |

**The shape of that table matters much more than any number in it.**

This is not "a bit slow" — it is a **tipping point**. As long as the model gets through one
sentence faster than the caller can say the next one, it keeps up, and the delay stays
small. The moment it does not, **it can never catch up**: it falls further behind with every
sentence for the rest of the call, and the caller's last words land a minute later.

**On your laptop's graphics card, half of these real calls are on the wrong side of that
line.**

So the fix is not tuning. **The model has to be faster.** Two options were already planned
and now have a number saying what they are for:

- a **compressed build** of the same Thai model (smaller, faster, slightly less accurate);
- a **different model** (Typhoon) that does not pad every clip out to 30 seconds the way
  the Whisper family does — which may be a structural fix rather than an incremental one.

### One thing to be clear about the stakes

**None of this delays the actual phone call.** The caller is connected to a human on a path
that never waits for any of this — that rule is baked into the architecture. What a slow
transcript costs is *how much of the caller's own words the agent has in front of them at
the moment they pick up*. A 45-second delay means the agent answers with a mostly-empty
screen.

---

## Part 8 — Two things I got wrong today

I want these written down rather than buried.

1. **I told you the delay was "4.4 – 7.7 seconds".** That was measured on **two** calls, and
   those two happened to be the fast ones. The real range across all twelve is **4.5 to
   58.7 seconds**. I corrected it in all six places it had been written, including the
   published page.
2. **Yesterday I recorded the accuracy problem as "poor, not yet explained" and listed four
   theories.** All four were wrong, and the actual cause was code I had written myself two
   days earlier. The theories were not unreasonable, but I framed it as "the model is
   underperforming" when I should have asked "is the model even being shown the audio?"

The pattern in both: **I trusted a number without looking at what was behind it.** That is
now the third time on this project (the other two are written up as `B18` and `B19`), and
the tool I built today (`--dump`) exists specifically so it is harder to do again.

---

## Part 9 — What happens next, in order

This is the actual to-do list, and it is also where you can help.

**1. Make the model fast enough. ← this is the whole ballgame right now**

Everything else is small next to this. The plan is to run the same 12 calls through three
different setups and compare accuracy, speed and memory in one table:

- the **compressed (CT2) build** of the Thai model we already use — I can do this now, it
  needs a one-off conversion step and about 3 GB of download;
- **Typhoon**, the other Thai model — **this needs your say-so**, because it means
  installing another large dependency (`nemo_toolkit`), which is a multi-gigabyte download
  on your laptop and your metered connection;
- the **large** version of the current model, mostly to confirm it does not fit in 4 GB.

> **DECISION I NEED FROM YOU: do you want me to install the Typhoon dependency?** It is the
> one option that might fix the speed problem structurally rather than by trading accuracy
> away. But it is a big download and I do not install those without asking.

**2. Decide how we score brand names** (I have written this down as `Q28`)

The dataset's human transcripts write brand and place names in **English letters** —
`True move`, `Mezzox Drip Cafe`, `Frosen Khaoyai`, `Router`. Our model correctly writes
them in **Thai script** — `ทูมู`, `เมโซเอ็กซ์ดิสกาแฟ`, `โฟร์เซนต์ เขา ใหญ่`.

Every character differs, so **a correct answer is scored as a total miss.** On the two
worst-scoring calls, that is most of the remaining error.

Three options: normalise both sides before scoring; report the score with those bits
excluded and say so; or accept it and treat our number as a worst case. **What we must not
do is edit the human answer key to match our model** — that is how a measurement stops
meaning anything.

> This one is worth your opinion because it is a judgement call, not a technical one.

**3. Finish the vocabulary hint** — a known, small, unglamorous bug (`B19`).

**4. The rest** is unchanged from before today: a timeout that can actually kill a runaway
transcription, encrypted recording storage, showing the live transcript on the agent's
screen, then Phase 4.

---

## Part 10 — Check any of this yourself

All of these run from the `FullProject` folder. The first two need no GPU.

Everything still passes:

```bash
uv run pytest -q
```
_expect: 609 passed, 42 skipped. The 42 skips are database tests needing a container that is currently stopped — normal._

The three tests that catch today's bug:

```bash
uv run pytest tests/unit/test_transcription_stream.py -q -k "lagging or own_audio or ordinary_case"
```

Score the detector (no GPU, no AI model loaded):

```bash
uv run python scripts/score_endpointer.py --vad silero
```
_expect: coverage 0.797, recall 0.902. Remember Part 2 — that "20% missed" is mostly annotator rounding._

See the actual words behind any score (**needs the GPU**, takes a few minutes):

```bash
uv run python scripts/bake_off.py --engines thonburian --vad silero --audio "tests/audio/thai_calls/*.wav" --dump transcripts.txt
```
_It writes to a file, never the screen — Thai text printed to a Windows console kills the program._

---

## Glossary — the jargon I keep using

| Word | What it means |
|---|---|
| **CER** | Character Error Rate. 0 = perfect, 1 = everything wrong. **The score we judge Thai on.** |
| **WER** | Word Error Rate. Useless for Thai, because Thai does not put spaces between words. We print it but ignore it. |
| **rtf** | Real-Time Factor. How long the model takes vs how long the audio is. 0.25 = four times faster than real time. **Above 1.0 the system can never catch up.** |
| **p95** | "95% of cases are at least this good." Used instead of an average so one disaster does not hide behind lots of fast cases. |
| **VAD / the detector** | Voice Activity Detection. Decides "somebody is talking right now". |
| **endpointer** | Decides where a sentence *ends*, using pauses. Cuts the audio into pieces. |
| **segment** | One piece of audio the endpointer cut out. Roughly one sentence. |
| **turn** | One line of text on the agent's screen. A segment that made it all the way through. |
| **buffer** | The stretch of recent audio held in memory. The thing that had the bug. |
| **paced vs `--fast`** | Paced = feed audio at real talking speed (slow, honest). `--fast` = shove it in (quick, but the timing numbers become meaningless). |
| **`D12`, `D100`…** | Numbered **decisions** in `docs/DECISIONS.md`. Why something is built the way it is. |
| **`B14`, `B20`…** | Numbered **bugs** in `docs/BUG_HISTORY.md`. What broke, why, and how it was found. |
| **`Q28`, `Q29`…** | Numbered **open questions** at the bottom of `docs/NEXT_SESSION.md`. Things not yet decided. |

---

## Where all of this is written down

| If you want… | Read |
|---|---|
| the live state, always start here | `docs/NEXT_SESSION.md` |
| the full bug write-up | `docs/BUG_HISTORY.md`, entry **`B20`** |
| why the buffer rule is what it is | `docs/DECISIONS.md`, entry **`D100`** |
| the same story with pictures | `docs/reading/the_audio_path.html` |
| what the whole audio path does | `docs/explanations/P3_voice.md` |

Today's work is three commits: `1b5f421` (the two measuring tools), `0ff17f8` (the fix plus
docs), `557f845` (the corrected latency numbers).
