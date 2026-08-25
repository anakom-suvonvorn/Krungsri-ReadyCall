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

_Nothing yet. Append dated entries here rather than editing the body — this file is a record
of what was true on 2026-08-25, and the "why" above stays useful even when a number moves._
