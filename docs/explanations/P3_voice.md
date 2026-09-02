# P3 (part 1) — the line: prompts, and the menu that routes the call

_Written 2026-08-25. A snapshot, not a specification — see "changes since" at the bottom._

> **This covers the first three of P3's four steps.** The prompt pipeline, the IVR service,
> and the handover from the two places that used to fake the walk. **Media, VAD and the STT
> worker are not built** — that is step four, and it is where the RTX 3050 risk lives. §9
> says exactly what does not exist yet.

The briefing for this phase said to go lowest-risk-first: config and its guard, then the
build script, then the IVR against the simulated telephony adapter, and only then touch the
GPU. That order held, and it was worth holding: the first three steps needed no model, no
audio hardware and no network, and they surfaced two design problems that would have been
much more expensive to find with a microphone in the loop.

---

## 1. What was already decided, and what that left

More existed than it looked. `config/menus.yaml` already held the whole tree — reserved
keys, catch-alls, the seven-option limit — validated by tests since P1. `ports/stt.py` and
`ports/tts.py` were written in P0, with `D24`'s deliberate asymmetry already in the docstring:
`synthesize()` is a **build-time** call and `stream()` exists only for a future conversational
intake. `IVR`, `INTAKE_ACTIVE` and `INTAKE_COMPLETE` were already real `CallState`s that
every scenario drove through.

So P3 did not add states or invent a flow. It replaced stand-ins.

The one number worth having up front: `menus.yaml` and `dids.yaml` between them **already
named 13 prompt ids** — 8 menu prompts and 5 greetings — before `voice_prompts.yaml`
existed. That is a known minimum, and it is why the very first thing built was not the
prompts but the **test that every referenced id exists**.

---

## 2. The guard came before the thing it guards

The failure this prevents is silent, and it is silent in the worst possible place: config
loads, every test passes, the call is routed correctly, and **the caller hears a gap where a
menu should be**. Nothing in the system is in a position to notice.

This is the same shape as `D72`, which moved the challenge list into config because it
existed twice and the copies could drift, with the server refusing an option the screen had
offered. A list enforced on one side and read from the other needs something checking, or it
drifts.

So the check runs in **both directions**:

- every id named by `menus.yaml`, `dids.yaml`, the invalid-prompt setting, the language menu
  (`D38` — off, but its id must still resolve) or a flow role **is defined**;
- every defined prompt **is reachable** from one of those.

An orphan is not a tidiness problem. It means either a line somebody wrote and never wired
up, or a reference somebody deleted — and both are worth knowing.

Two details that made the guard stronger than a list of assertions:

**The reference set is computed by the code, not by the test.** `PromptPack.referenced_ids(pack)`
is what the startup gate calls *and* what the test calls. A guard whose test re-implements
the rule is two rules, and they diverge on the day one of them is updated.

**It is a startup gate, not only a test.** `Container` cross-validates on boot, so the test
cannot pass while the server refuses to start.

---

## 3. Slots are declared, and both kinds of mismatch fail

A prompt whose Thai text contains `{position}` must list `position` in its `slots`. A prompt
that declares a slot it never uses fails too — that one means the wording changed and the
declaration did not.

The alternative is a `KeyError` inside a live call, or worse, a caller hearing the literal
string `{position}` read out in the middle of a Thai sentence.

```python
prompts.say(PromptRole.QUEUE_POSITION, position=3)
# ConfigError: prompt 'queue.position' slot mismatch: missing ['wait_minutes']
```

---

## 4. Roles, not prompt ids

`services/ivr` asks for `PromptRole.INTAKE_OFFER`. It never writes the string
`"intake.offer"`. The mapping lives in a `flow:` block in `voice_prompts.yaml`.

This is `D28` applied to speech — nothing domain-specific in `services/` — and it buys three
things: renaming a prompt is a config edit; a typo in a role name **fails loudly** at load
rather than being ignored as an unread key; and the guard test can check the mapping from
both ends.

There are 19 roles, and adding a `PromptRole` member without mapping it is a startup error.
That is deliberate: a role with no prompt is a hole in the flow that otherwise only shows up
when a caller reaches it.

---

## 5. The first real surprise: a menu cannot be one clip

`D24` says every spoken line is pre-rendered and played as a file. The natural reading is
one clip per prompt id, so `menu.product_line` would be a single recording of
*"กด 1 ประกันรถยนต์ กด 2 ประกันสุขภาพ …"*.

`D37` says a recognised caller hears their likely options **first**.

Those cannot both be true of one baked recording. The order differs per caller.

`D80` resolves it: a menu is **composed** from clips that were all pre-rendered — a static
lead-in, one `menu.option` line per option, then the reserved-key hint. Composition is
playback, not synthesis, so `D24` holds exactly as written; nothing is generated during the
call.

The rejected alternative is worth naming because it sounds cheaper than it is: *one clip per
menu per ordering*. The product-line menu has five options, so 120 orderings, and every
wording edit multiplies through all of them.

What composition turned out to buy, beyond making the feature possible:

- **the label exists in exactly one file.** `voice_prompts.yaml` contains no menu options at
  all; `scripts/build_prompts.py` reads them from the domain pack. There is no second copy of
  "ประกันสุขภาพ" that can drift from the one doing the routing.
- **the same words are one clip.** "ติดตามสถานะเคลม" is key `3` in both the motor and the
  health menu. 32 prompts and 32 menu options come to **63 distinct clips**.

That dedupe is not an optimisation footnote. It is the same mechanism that makes a dynamic
line cacheable at all: everyone who is third in the queue hears the same file, so the pack
warms up in minutes rather than never (`D24` §5).

---

## 6. The build, and the property that makes it worth having

`scripts/build_prompts.py` renders through the `TtsEngine` port, cached by
`hash(text, voice, engine)` — **the rendered text, not the prompt id**.

The claim that matters was measured rather than asserted: editing one Thai line and
re-running reported *one clip rendered, 62 reused*, and `--check` named exactly which id was
stale and which was orphaned. Reverting the edit restored the original.

Three smaller decisions:

- **The manifest is committed and a test asserts it is fresh**, the same argument as the
  generated diagrams: a pack that ships stale is worse than no pack, because the caller
  hears last week's wording and nothing says so.
- **`--check` and its test share one function** (`drift()`), so they cannot disagree about
  what stale means.
- **`hash()` would not do.** Python randomises string hashing per process, so every build
  would re-render everything. A pinned SHA-256 value is asserted in the tests, which is the
  cheapest possible way to catch a change to the key derivation.

**The honest limit:** `TTS_ENGINE` defaults to `null`, which records what it was asked to say
and synthesises nothing. So today this is a manifest, not a playable pack. A real voice is a
config change and a re-run — and it should be chosen on a listening test of these actual 63
lines, not on a vendor's spec sheet.

---

## 7. The second surprise: what the caller pressed is not what to store

`D37` says promoted options *take the low numbers*. Follow that through.

Travel is promoted for this caller. They press `1`. `CallSession.menu_path` records
keypresses. So the stored path says `1`, which in `menus.yaml` means **motor**.

Nothing fails. The call is routed correctly, because the routing happened from the live
presentation. But the *record* is wrong, and everything reading it later — the brief, the
intent source shown to the agent, a replayed scenario, an auditor asking what the caller
chose — is quietly wrong with it.

`D81` fixes it at the only moment the answer is unambiguous: **the press is resolved to its
canonical option the instant it arrives.** `menu_path` always holds keys as `menus.yaml`
spells them. The raw presses are kept on the outcome, where they are a UX signal (a confused
menu path is worth seeing) rather than routing evidence.

The rule reaches into the test suite too. `ScriptedChoices` — used by every scenario file —
takes **canonical** keys and looks up whichever key is actually under that option. A scenario
file keeps meaning what it says when a menu is reordered, instead of silently pressing the
wrong thing. Without that, every scenario involving a recognised caller becomes a trap that
springs the day personalisation is tuned.

Two alternatives were considered and rejected: storing the presented order alongside the path
(a `CallSession` column and a migration, to preserve a fact nothing downstream wants), and
storing raw presses to re-derive later (which assumes the snapshot, the weights and the
config are all identical months later — exactly the assumption that rots).

And renumbering rather than merely shuffling, because *"กด 3 … กด 1 … กด 2"* makes the caller
do the sorting. If the order changes, the numbers change with it.

---

## 8. The machine has no I/O in it

`begin` / `on_digit` / `on_timeout` / `on_hangup`, each returning *what to play next*. A thin
async driver plays it and knows no rules.

This shape is a direct consequence of `B7`. An IVR written as a coroutine awaiting DTMF can
only be tested by feeding it a fake phone line — and the rungs that fire **because time
passed** are then exactly the ones that end up driven by nothing, which is what `B7` was.
Here, silence is a method call:

```python
first  = machine.on_timeout(run)   # re-prompt once
second = machine.on_timeout(run)   # -> EXHAUSTED, reason="silence_x2"
```

The rules it holds are all `D37`'s floor, and every one of them ends in a queue rather than a
hang-up: `0` reaches a human from any depth; `9` repeats without spending an attempt (re-listening
is being careful, not failing); three wrong keys or two silences route to the best queue
available; a product-line number with no keypress still reaches that **line's** queue rather
than the general one (`D40`).

### One thing it refuses to claim

A DID may assume an intent — dialling the number printed in the glovebox suggests a damaged
car (`D19`). That is real evidence and it picks the queue. But it is weaker than a keypress,
so the two are kept apart: `outcome.intent_code` holds only what was pressed, and
`IvrResult.intent_code` carries the best available answer with `intent_source` saying whether
it came from `dtmf`, the `app`, the `did`, or nothing. The agent's brief has to be able to
tell *"they told us"* from *"we assumed"*.

---

## 9. The handover, and what is still faked

Both places that hand-walked the menu now call the real service, and their markers are
retired: `run_scenario.py`'s `# P1:` for the IVR, and `demo.py`'s `# P2b:`. `_queue_for` was
deleted from the scenario runner — `IvrService.queue_for` owns that ladder now, in one place.

All three scenarios still replay, with the right queues:

```
anonymous_declined     17 lines played   keys 2/4   -> q_health_policy   (dtmf)
roadside_motor_claim   10 lines played   keys 1     -> q_motor_claim     (dtmf)
pattheera_ipd           2 lines played   keys -     -> q_health_ipd      (app)
```

Two lines for the app path is `D48` expressed as a number: a greeting, a recording notice,
and not one question asked.

**What is genuinely not built** (step four, unchanged from the phase briefing): the media
gateway, per-leg forking, resampling, encrypted recording, Silero VAD endpointing, the STT
worker, the bake-off on real hardware, `TranscriptTurn` persistence, live transcript in the
workstation, and the `IntakeStrategy` implementation. The `ml` extra is still commented out
in `pyproject.toml`, so `uv sync --extra ml` still fails — that remains step zero for the
next stretch, and it is the slow install.

The intake prompts (`intake.offer`, `intake.start`, `intake.done`, the voicemail pair, the
rating request) **are written and rendered**, and roles exist for all of them. They are text
waiting for the machinery that plays them; nothing calls those roles yet.

---

## 10. Two bugs, both in tests, both of which would have passed review

**A test that asserted a reordering that never happened.** The personalisation fixture gave
the caller motor and health policies — canonical keys `1` and `2`. Promoting them put them
in positions one and two, which is where they already were. The assertion "the caller pressed
the right key" passed because the right key had not changed. Rewriting the fixture around
travel and life, which sit at `3` and `4`, made it a real test.

**Entering the second menu wiped the evidence of the first.** `run.promotions` described the
menu currently being spoken and was reset on every `_enter`. So a call whose product-line menu
was personalised reported itself as ordinary the moment the caller reached the reason menu.
The fix separates the two: `promotions` is this menu's, `personalisations` is the call's.

Both belong to the family `NEXT_SESSION` already names — *a confident, plausible, wrong result
nobody looked at*. Neither is a database bug or a UI bug; they are both a test agreeing with
the code about something that was never checked against reality.

---

## 11. Numbers, as of writing

| | |
|---|---|
| Prompts defined | 32 |
| Distinct clips after dedupe | 63 |
| Flow roles | 19 |
| Tests in the suite | 496 — 454 pass + 42 skip without the Postgres container; 493 pass + 3 skip with it |
| New tests this phase | 55 — 27 on the prompt pack, 24 on the IVR, 3 on the demo path, and one more generated diagram to keep fresh |
| Diagrams | 60, one of them generated from the prompt pack |

---

## Changes since

**2026-08-26 — the review pass (`D82`–`D84`).** Three rules above were changed after the
user worked the built menu. §8's list of what the machine holds is superseded:

- **there is no attempt limit any more** (`D82`). "Three wrong keys or two silences route to
  the best queue available" was half wrong: a wrong key *proves* somebody is there and
  silence does not, so only silence stays bounded. `menu.invalid` now names both escape keys,
  which it has to once there is no ceiling to rescue a lost caller.
- **`0` no longer short-circuits the queue ladder** (`D83`). `queue_for` special-cased it and
  jumped to the DID's default, discarding a product line the caller had already chosen —
  which was the real substance behind "the operator key introduces a path where we know
  nothing about the person". **`D86` then removed the key entirely the next day**: with the
  ladder fixed, `0` was provably identical to the "เรื่องอื่นๆ" option every menu already
  speaks, so it cost a reserved key and a prompt to save two keypresses. `9` is now the only
  reserved key.

- **an unfiled wrap-up is no longer lost** (`D87`). `B10` closed the stranded call, which
  stopped it haunting the screen and left the customer's record with a permanent hole. It
  now goes to a **backlog** the agent clears whenever they like — which is what makes
  `D45`'s "the person decides when after-call work ends" survivable rather than lossy.
- **the identify step described in §9 as "prompts written, nothing calls them" is deleted**
  (`D84`). An automated check promoting to L3 with no human in the loop is what `D44`
  refuses; the agent's keypad capture does the job with judgement attached. The prompt count
  in §11 is therefore 29, not 32, and there are 17 roles, not 19.

**2026-08-31 — step 4a: the offer, and everything below the queue line.** §9 said the intake
prompts were "text waiting for the machinery that plays them". The machinery now exists, and
`services/intake/` is the second half of this phase.

- **it is a separate machine, not two more states on the IVR** (`D88`). `IvrMachine` decides
  where the call goes; `HoldMachine` decides how much the agent will know when it gets there.
  Those are the two halves of `D37`, and putting them in one file with one outcome type would
  have left nothing standing between "the caller declined" and "route them differently". The
  test that guards it is one assertion: record, decline and ignore all land in the same queue.
- **the driver stops at the answer, and the recording outlives it.** The first version looped
  until the intake finished, and it was wrong in a way only a scripted caller exposed — press
  `1`, run out of script, and the loop "hears" a silence that never happened. The three things
  that actually end a recording (a VAD silence, the maximum duration, an agent pressing
  Accept) all arrive from outside, and the last one arrives seconds later on another
  connection (`D21`). Keeping the loop would have meant owning a media loop that does not
  exist and **inventing what it reports** — `B3`, `B4`, `B7` and `B8`'s whole family.
- **the seam takes turns, not frames** (`D88`, amending `ARCHITECTURE.md` §7 as drawn). That
  is why `PassiveRecordIntake` is written, implemented and tested three phases before the GPU
  it will run beside, rather than being a protocol nobody has ever run.
- **pressing 2 records the refusal.** "They said no" and "we never asked" are different facts,
  and an empty consent list cannot tell you which happened. Two scenario tests asserted
  `session.consents == ()` for the declining caller and correctly failed; they now assert the
  better fact — nothing granted, and the refusal on the record with its basis.
- **the caller hears their position and no invented wait** (`D89`). Counting a queue and
  predicting how long it drains are different problems, and only the first is solved.
- **numbers:** 29 prompts, 17 roles, **64 clips** (up one, for `queue.position_only`), 559
  tests (517 pass + 42 skipped without the container), 61 diagrams.
- **the sweep gained `reoffer_due()`.** `B7` in advance rather than in hindsight: the only
  thing that happens at `INTAKE_REOFFER_AFTER_S` is that the wait got long, so nothing else
  is around to carry it, and its test moves nothing but the clock.
- **the scenario runner's intake stand-in is retired.** Its `# P1:` marker for the media
  gateway is now `# P3-media:` and covers only the utterances themselves; the offer, the
  consent, the state transitions and the ending on accept are all the production path.
- **what is still missing is exactly the audio**: no media gateway, no VAD, no STT worker, no
  encrypted recording, no live transcript on the workstation. `IntakeService.on_turn` is the
  socket it plugs into, and today only tests and scenarios call it. **`Q24` is open**: a
  health-line caller speaks health data into a recording whose `health_data` scope nothing
  asks for.

**2026-09-01 — `D90`, `D91`, `D92` and `B12`.** Three of these came from the user reading the
system rather than the code, which is the pattern worth noticing.

- **repeat moved from `9` to `0`** (`D90`). `D86` freed the key and admitted one cost — a
  caller pressing `0` out of habit got an apology. Moving repeat there deletes that cost.
  Moving it also exposed a prompt spelling a config value into its own Thai (`"กด 9"`), which
  nothing checked; it is a `{repeat_key}` slot now.
- **the queue position is gone entirely** (`D91`, reversing `D89` two days after writing it).
  There is no line to have a position in: the matcher re-solves the whole caller × agent
  matrix every tick, so arrival order is not an input and both overtaking directions happen by
  design. `D89`'s reasoning — *"we can count a queue exactly"* — was true and irrelevant.
  **Counting a pool is not ranking it.** `queue.position` and `queue.position_only` are
  deleted from the pack and from `PromptRole`.
- **speech may change who answers and how soon, never which queue** (`D92`). `D23` implied it
  and never said it; `required_skill` comes from the queue spec, so a speech-revised intent
  could have re-routed the product line and quietly ended `D37`.
- **`B12`**: `WaitingCall` is frozen and `tick()` rebuilt it naming only `excluded_agent_ids`,
  so `waiting_s` never moved. `wait_pressure` stayed at 0, `sla_risk` never fired, and neither
  did the any-qualified ceiling — all of `D22`'s anti-starvation, driven by a constant. Found
  while answering the position question, not by looking for it.
- **numbers now:** 27 prompts, 15 roles, **54 clips**, 61 diagrams. (Test count moved again
  the same day with `D93`/`B13`, which are matching, not voice — see `P2a_matching.md`.)


**2026-09-02 — step 4b: the audio, and the phase is done bar two named things.** §9 said
what was missing: *"the media gateway, per-leg forking, resampling, encrypted recording,
Silero VAD endpointing, the STT worker, the bake-off, `TranscriptTurn` persistence, live
transcript"*. Most of that now exists (`D96`).

- **normalisation is one module at the edge**, and it is pure Python. `ports/stt.py` has
  always promised 16 kHz mono float32; `media/audio.py` is the only place allowed to know a
  phone call is not that. It handles **both G.711 laws** — Thailand is A-law, and decoding
  one as the other does not raise, it produces loud plausible garbage that gets blamed on
  the microphone. No numpy, because CI installs no extras and an audio path exercised only
  on the GPU laptop is `B7`'s shape.
- **VAD became the ninth port**, returning a probability rather than a decision, so `D9`'s
  inherited constants sit in one no-I/O machine and are assertable against a list of
  floats. `EnergyVad` is not a toy: it is the CI path *and* the degradation rung.
- **ingestion never waits for the model.** One queue, one consumer, one sequence counter.
  A task per segment would deliver a short phrase before the sentence that preceded it.
- **`B14` changed the design, and it was found by disbelieving the harness's own numbers.**
  Two broken instruments (a latency lookup that never matched, printing a clean 0 ms; a
  VRAM counter blind to CTranslate2's allocator) and an unpaced feed reporting a backlog as
  a latency. With honest ones: **155 ms** for real speech energy, **8578 ms** for one second
  of digital silence — which also comes back with invented Thai, including our own
  `stt_vocabulary.yaml` terms handed back as if the caller had said them. Three guards, and
  a warmup that had itself been feeding the model the worst input it has.
- **the two recording timeouts are driven from the sweep** (`B7`), with a test in which only
  the clock moves — written before the driver existed rather than after somebody noticed.
- **numbers now:** 27 prompts, 15 roles, 54 clips, **62 diagrams**, 632 tests
  (590 pass + 42 skipped without the container). 9 ports. The first three real vendor
  adapters in the project.
- **what is honestly not done:** the encrypted recording (P7's key management), the live
  transcript on the workstation, and **`D30`'s bake-off table** — the harness is built and
  correct and needs real Thai telephone speech plus the Thai weights. Synthetic tones
  measure the model's failure modes, not its performance, and that table must not be filled
  from a signal generator.

_Earlier body text stays as written — it is a record of what was true on 2026-08-25._ Append dated entries here rather than editing the body — this file is a record
of what was true on 2026-08-25, and the "why" above stays useful even when a number moves._
