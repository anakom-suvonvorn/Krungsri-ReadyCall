# The caller's words finally reach the agent's screen

_Written 2026-09-05, for you, assuming nothing. There is a **glossary at the bottom**._

**The one-sentence version:** the thing the whole project is *for* — an agent picking up a
call and already seeing what the caller said while they were waiting — now works, and
building it uncovered that **two entire pieces of the system had never actually run**, not
because they were broken but because nothing in the code ever called them.

---

## Part 0 — What was supposed to already work

Here is the pitch, in five steps. Steps 1–4 have worked for a while.

```
  1. somebody phones in
  2. a keypad menu works out why, and which queue they belong in
  3. while they wait, we offer to record what they want to say - they press 1
  4. their speech becomes lines of Thai text                     <- this was built
  5. an agent presses Accept and those lines are on their screen <- this was NOT
```

Step 4 landed last week and it was the hard one: choosing a speech model, measuring it on
real Thai phone calls, fixing four of our own bugs along the way. By the end of it the
system could turn a WAV file into ordered lines of Thai in under two-tenths of a second per
sentence.

**Step 5 was supposed to be small.** The lines existed. The screen existed. All that was
missing was carrying one to the other.

---

## Part 1 — It was not small, and the reason is worth understanding

Before writing any code I traced the path from a sound file to a browser tab, stopping at
every hop to ask one question: **what calls this?**

Two of the answers were *nothing*.

### Finding one: nobody ever started a recording

There is a service called `TranscriptionService`. It has a method called `open()` — "a
recording is now running for this call". It has its own test file. It appears in every
architecture document we have.

**`open()` was called by its own tests and by nothing else in the entire codebase.**

So in the actual running system, when a caller pressed 1 to consent to being recorded, the
system wrote down *"recording: yes"* and then did absolutely nothing. No audio was
captured. No sentence was ever transcribed. The whole audio path — the model, the bake-off,
the four bug fixes, its whole test suite — **had never transcribed a single thing in the running
product.**

It was not carelessness. There is a deliberate design rule (`D88`) that the intake service
must not know anything about audio, so that the audio half could be built and tested three
phases before the GPU arrived. That rule is right. But it means *somebody else* has to be
the one to start the recording — and the design never said who. So nobody did.

### Finding two: the messages were being posted to a queue nobody emptied

The system passes news around on an "event bus" — one part announces *"a new sentence was
transcribed"* and any other part that cares can listen.

The bus works like a letterbox. `publish()` puts a letter in the box. Nothing is *delivered*
until something calls `drain()`. That is deliberate: it makes replays of a call come out in
exactly the same order every time, which is what lets us compare a test run against a
recorded expected result.

**In the live server the only thing that ever emptied the letterbox was an unrelated
request** — the one the mobile app makes when it starts a call. So anything listening for
transcript sentences would have sat there, correct and tested and completely silent, until
somebody happened to open the app.

I measured this rather than assuming it:

```
published on the bus : ['intake.started', 'transcript.turn']
subscriber saw       : []    <- after publishing, before any delivery
subscriber saw       : []    <- after 5 rounds of the system's regular housekeeping
subscriber saw       : ['transcript.turn']    <- after explicitly emptying the box
```

That middle line is the important one. The system has a housekeeping loop that runs every
second and does things like expiring unanswered offers. **It does not empty the letterbox.**
I had assumed it did.

### Why this keeps happening, and it is the same shape every time

This project now has four of these:

| | what was written and correct | what was missing |
|---|---|---|
| `B7` | three services that expire offers and drop absent agents | nothing ran them on a timer |
| `B9` | an entire database package | it was never committed, because of one line in `.gitignore` |
| `B12` | the rule that a caller's urgency grows as they wait | it was fed a number that never changed |
| **`B24`** | **the whole audio path, and any bus listener** | **nothing called either one** |

Every one of them had passing tests. **A test proves the thing works; it says nothing about
whether anything uses it.** The only check that finds these is to follow the path from
something a *person* does — pressing a button, dialling a number — and see whether it
actually reaches the code you think it reaches.

---

## Part 2 — The interesting design question, which the plumbing was hiding

Once the two gaps were fixed there was a real decision left, and it is not a technical one.

**While the caller is talking, nobody has been assigned the call.** That is the entire point
of the product: they are recording their problem *while they wait*, before an agent is free.
So at the moment a sentence is transcribed, there is no agent to send it to.

So the sentences have to be **held**, and delivered when somebody takes the call. The
question is: **when exactly?**

There are two candidate moments:

- **When the call is offered to an agent** (their desk rings, a card appears)
- **When the agent presses Accept**

I chose Accept, and the reason is not technical. An offer can be **declined**, or ignored
until it times out — and then the call goes to somebody else. If we delivered on the offer,
an agent who declined would have read the caller's words **verbatim** for a call they never
took. Over a shift that is a lot of people's private problems read by people who did not
handle them.

The offer card still shows a short *summary* of what the call is about — that already
existed, and it is deliberately gated by how sure we are of the caller's identity. A summary
is a different kind of disclosure from a transcript.

### One thing I deliberately did *not* gate

There is a rule in this system that the agent only sees a customer's **record** — policy
numbers, coverage — once the caller's identity is established.

The transcript is **not** gated that way, and that is a decision rather than an oversight.
It is the caller's own speech, on the call the agent is about to take. It is not anything we
looked up about them. And the case where it matters most is an **anonymous** caller — the
one where we know nothing else at all. Withholding it there would delete the feature to
protect nothing.

---

## Part 3 — Two bugs that only showed up when a real server was running

Everything passed. Then I opened a browser.

### The second demo call showed an empty panel

For demos we have a fake speech engine that just reads canned lines from a file, so the demo
does not depend on a model loading in a noisy room. It reads them **in order**, keeping
track of where it got to.

The system builds **one** engine when it starts up. That is correct for a real AI model —
it has no memory between sentences. It is wrong for a script, which does: the first demo
call used up all six lines, and the second call found the script finished and showed
nothing.

**Every test passed**, because every test placed one call. There is now one that places
three.

That is the same lesson as three earlier bugs in this project: **one of something proves
nothing about the second.**

### The caller's last sentence was being thrown away

This one I caught by re-reading my own change before committing it.

When the agent presses Accept, two things happen: the recording is stopped, and the intake
is closed off. I had them in that order — close the intake, then stop the recording.

Stopping the recording *finishes transcribing whatever the model is still working on*. Those
final sentences then arrive at an intake that has already closed, and get politely logged
and discarded.

Which sentence is that, in practice? **The one the caller was saying at the exact moment the
agent picked up.** Gone, silently. The order is swapped now, and there is a note explaining
why so nobody tidies it back.

### And the tests were passing for a reason I could not name

The transcription runs in the background so it never holds up the call. In the test
harness, "the background" only gets a turn to run while a request is in flight — so
sometimes the sentences were there when the test looked and sometimes they were not. They
happened to be there.

**A test that passes for a reason you cannot explain is not yet a test.** They now keep
checking until the work is genuinely done, rather than hoping.

---

## Part 4 — What you can actually do now

Three commands and a browser, no GPU, no phone, no API key:

```bash
uv run python scripts/make_demo_audio.py
uv run python -m readycall.entrypoints.api
```

Open `http://127.0.0.1:8000/workstation`, sign in as **A001**, **A002** or **A003** (they
have the motor-claim skill), and press **พร้อมรับสาย**. Then:

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" \
  -d '{"intent_code":"motor.claim.accident","intake_keys":["1"],"audio":"demo_intake.wav","ignore_hours":true}'
```

The desk rings. **Press Accept**, and under the brief you get:

```
บทสนทนาก่อนรับสาย · 6 ประโยค

00:00   สวัสดีครับ ผมโทรมาเรื่องเคลมรถ
00:03   รถชนเมื่อเช้านี้ครับ
00:07   อยู่แถวรัชดา ตอนนี้จอดข้างทางแล้ว
00:10   ไม่มีใครบาดเจ็บครับ
00:14   อยากทราบว่าต้องทำยังไงต่อ
00:17   แล้วต้องใช้เอกสารอะไรบ้างครับ
```

**A caveat about what you are looking at.** On the default settings the *words* come from a
file (`config/demo_transcript.yaml`) — that is the stage-safe fallback. But the **timings
and the number of sentences are real**: they come from the actual audio being cut up by the
actual detector. Switch to `STT_ENGINE=typhoon` and the identical path transcribes real Thai
speech with the model we chose last week.

Why the fallback exists: `PLAN.md`'s risk register has one rule in capitals — *never depend
on the venue*. A demo that needs a 3 GB model to load on a laptop in a room with bad wifi is
a demo that can fail at 9am. This one cannot.

---

## What is left

**One thing, and it is not an audio problem.** The call should also be saved as an encrypted
audio file, and that needs key management — where the encryption keys live, who can read
them, how long the recording is kept. That is a security piece scheduled for later (P7).

After that, P4: using an AI model to turn the transcript into a summary, suggested actions
and an opening line for the agent.

Two smaller loose ends, both written down so they are not forgotten:

- If the speech engine ever fails mid-call, the screen currently cannot say so. The panel
  already has the sentence written for it; nothing sets the flag yet.
- A "the model is taking too long, kill it" timeout needs a separate worker process, which
  is planned but not built. It must not be faked — the obvious Python way of doing it does
  not actually stop the work, which would give us a safety guard that looks like one and is
  not.

---

## Glossary

| Word | What it means here |
|---|---|
| **event bus** | how one part of the system tells the others that something happened, without them being wired directly together |
| **publish / drain** | putting a message in the box / delivering everything in the box |
| **subscriber** | a piece of code that has asked to hear about a kind of message |
| **endpointer** | the thing that decides where one spoken sentence ends and the next begins, from the pauses |
| **turn** | one transcribed sentence, with the time it started and ended |
| **intake** | the recording made while the caller waits |
| **the offer** | a call ringing at one agent's desk, which they can accept, decline, or ignore |
| **snapshot** | the single blob of data the agent's browser gets that describes everything on their screen |
| **scripted engine** | the fake speech model that reads canned lines, so a demo never depends on a real model |
| **`D105`, `B24`** | numbered entries in `docs/DECISIONS.md` and `docs/BUG_HISTORY.md` — the permanent record of why something is the way it is, and of a bug that was fixed |
