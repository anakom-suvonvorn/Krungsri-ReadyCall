# P2a — the matching engine

_Written 2026-08-24. A snapshot, not a specification — see "changes since" at the bottom._

Until now the scenario runner picked the agent: the YAML said `agent: A006` and the system
believed it. P2a replaces that with an engine that decides, and — more importantly — can
explain why.

---

## 1. Three stages, and the order is the design

```
hard filters  →  fit  →  urgency  →  solve the whole matrix
   exclude       "how good      "how badly does      one global
                 is this        THIS CALL need       assignment
                 agent for      someone?"
                 this call?"
```

**Hard filters exclude, they do not down-rank.** Skill and language are pass/fail. This is
the one I'd defend hardest: scoring them softly is exactly how a caller ends up with an
agent who cannot help them, purely because everything else about the pairing scored well.
In the code a failed filter returns a *string* — `"skill"`, `"language"`, `"at_capacity"` —
which is kept on the candidate record, so the screen can say *why* someone was excluded
rather than just omitting them.

Language is **graded**, not a flag (`D38`):

```python
floor = CefrLevel(weights.min_language_level)      # b1
if not any(agent.speaks(lang, at_least=floor) for lang in call.acceptable_languages):
    return "language"
```

An agent with A2 English cannot carry a complex claim conversation in English, and
pretending otherwise produces a worse call than a longer wait.

---

## 2. Urgency multiplies. That is the whole anti-starvation argument.

```python
total = fit.total * urgency.total
```

Not `fit + urgency`. Under addition — or under pure best-fit — a caller nobody is a great
match for waits while better-matched callers overtake them, forever. Multiplying means
waiting eventually wins **on its own merit**, without anyone needing a special case.

Two details that took thought:

**Wait pressure is relative to the queue's own SLA.** Forty seconds is nothing on a 120 s
policy question and nearly a breach on a 45 s pre-authorisation. Absolute seconds would
treat those identically.

**The multiplier is clamped** to `[1.0, 3.0]`. Urgency should dominate; it must never make
skill irrelevant. A caller at a crash scene still must not be routed to someone who cannot
handle a motor claim. And the loader *refuses* a `min_multiplier` below 1.0, because that
would make waiting **lower** a score — the exact opposite of the intent.

---

## 3. The solver, and a bug that taught me something

`D22` said Hungarian rather than greedy. I wrote it rather than adding `scipy` — ~90 lines
against a ~30MB dependency that would land on the STT box too (`D49`, same reasoning as
`D31`).

The first version **returned an empty matching on every input**. Not an error — every caller
came back `no_candidates`, which looks exactly like "nobody was available". The cause:

```python
j0, p[j0] = way[j0], p[way[j0]]     # WRONG
```

Python evaluates the right-hand side first, but then assigns **left to right** — so `j0` is
rebound before `p[j0]` is resolved, and the write lands on the wrong column. Three
statements fix it.

What makes this worth writing down is the *shape* of the failure. It was silent, it produced
plausible-looking output, and no amount of running the simulator would have flagged it —
"nobody could be matched" is a legitimate outcome. Only a **known-optimal small case**
catches it:

```python
matrix = [[1.0, 5.0], [4.0, 2.0]]     # greedy: 5 then stuck with 2 = 7. Optimal = 9.
assert total_score(matrix, hungarian(matrix)) == 9.0
```

Same family as `B3`, the `0.0 ms` timing bug: **a confident, plausible, wrong number is much
harder to notice than a crash.**

---

## 4. What the measurement actually said

I built `--compare` to prove greedy was worse. It mostly is not:

| seed | greedy leaves on the table |
|---|---|
| 7, 42 | **0.0%** — identical |
| 13 | 0.1% |
| 2024 | 0.5% |
| 1 | 0.7% |
| 99 | 5.1% |
| 123 | **8.2%** |

So the honest claim is *not* "greedy is bad". It is: **greedy is usually fine and
occasionally meaningfully worse — and it is worst exactly when agents are scarce relative to
skill diversity**, which is precisely when routing matters most.

I'd rather ship that sentence than the punchier one. It is also the more useful finding: it
says the global solver earns its keep during a staffing crunch and costs nothing when the
centre is quiet.

---

## 5. Guard rails

Three, and each exists because the naive version has a specific failure:

- **Wait ceiling** (180 s) → `FALLBACK` to any qualified agent. A perfect match nobody is
  available for is worth less than a good one who is.
- **Anti-hot-spot** — if one agent has taken more than 35% of the last 20 assignments, they
  stop being preferred. Otherwise the strongest agent absorbs every hard call in a shift.
- **Guarded deferral** — hold a caller briefly for a clearly better agent, but *never* past
  60 s of waiting, *never* for more than 25 s, *never* for a gap smaller than 0.25, and
  **never for a high or critical caller**. Unguarded, "wait for someone better" is how a
  caller gets forgotten.

---

## 6. Every decision is explainable

A `MatchingDecision` is emitted **per call, including the ones that were not assigned**, and
carries every candidate considered with its full sub-scores, the weights version, the solver
name, and a Thai rationale:

```
call_sim_003  motor.claim.accident
  queue=q_motor_claim  waited=130s/30s  urgency=critical
  -> assign  A001
     ทักษะ motor.claim 95% · รอเกิน SLA (130s / 30s) · เรื่องเร่งด่วน
     urgency x3.00  score=2.850
   * A001  score=2.850  skill=0.95
     A002  score=2.550  skill=0.85
      excluded: skill=5
```

That is `D18` paying off. "Why did I get this call?" and "why is this caller *still*
waiting?" are both answerable from stored data — and a matching decision nobody can explain
is one nobody will trust enough to leave switched on.

The `weights_version` field matters more than it looks: when someone asks about a route from
last Tuesday, the answer has to include which weights were in force at the time.

---

## 7. What P2a did not build

- **The agent workstation.** P2b — it needs the websocket, the offer/accept handshake, the
  identity control (`D42`) and the keypad panel (`D44`).
- **Presence heartbeat.** The engine takes presence as an argument; nothing yet maintains it.
- **Postgres.** Still dicts. This is where `D39` finally comes due — agent presence is the
  first thing that genuinely must outlive a process.
- **Queue opening hours.** `respect_schedule` is in the weights and not yet consulted.

---

## Try it

```bash
uv run python scripts/run_matching.py --calls 25 --compare
uv run python scripts/run_matching.py --seed 123 --calls 25   # full per-call reasoning
```

---

## Changes since this was written

_Append here rather than editing above._

- **2026-08-24 — `no_candidates` no longer exists; it was two outcomes wearing one name
  (`D50`, `B4`).** Anywhere above that says a call came back `no_candidates`, read
  **`NO_QUALIFIED_AGENT`** (nobody online holds the skill or language — a roster gap,
  waiting cannot help) or **`ALL_QUALIFIED_BUSY`** (qualified agents exist but were all won
  by higher-scoring calls this tick — a capacity shortfall, this caller is next). The old
  single kind claimed the first while the decision record often listed a qualified
  candidate: on the default 25-call run, **12 of 17 unplaced callers were mislabelled**.
- The starvation summary in `run_matching.py` used to assert that every caller past the wait
  ceiling had failed a hard filter. It now reads the reason off the decisions. On
  `--seed 123` the old text was flatly wrong.
- `chosen_agent_id is None` means **three** things, not two — the third is a deliberate
  `DEFER`. Filter it out before reasoning about unplaced callers.

- **2026-09-01 — none of the anti-starvation described above was actually running (`B12`).**
  `WaitingCall` is frozen, and `DispatchService.tick()` rebuilt it with `replace()` naming
  **only** `excluded_agent_ids` — so `waiting_s` kept whatever `admit()` was given (`0.0` on
  the demo path) for the entire life of the call. `wait_pressure` was pinned at 0, `sla_risk`
  never fired, and neither the 180 s ceiling nor the 60 s defer cap was ever reachable. §2's
  argument was right and the code executed it against a constant. `tick()` now refreshes
  `waiting_s` from `session.wait_seconds(now)`, and two tests move nothing but the clock.
- **§4's "`FALLBACK` to any qualified agent" was not what the code did — and now it is
  (`D93`, `B13`).** The ceiling lived inside `_guard`, which only runs for a call the solver
  **already chose an agent for**; a starved caller who lost the matrix hit `continue` several
  lines earlier and never reached it. So the one guarantee the config had always advertised
  could not fire for the one caller it was written for. It is a **pre-pass before the solver**
  now: past-ceiling callers are handed a qualified free agent, longest wait first, taking the
  *lowest*-fit one so the specialists stay free. The dead branch in `_guard` is deleted rather
  than left reading like a rule.
  *(This bullet replaces one written a day earlier that said "accept it, fit is never ignored"
  — that was an accurate description of broken behaviour.)*
- **Numbers after `D93`:** 564 tests, 34 of them on matching — five new ones all putting a
  second caller in the way, because the ceiling test that existed gave one caller an empty
  floor and therefore proved nothing about a contention rule.
- **Two scoring inputs are still fed by nothing on the live path.** `customer_priority`
  reads `WaitingCall.is_vulnerable`, which is set on the `Customer` and on the brief DTO but
  never on the `WaitingCall`; `fit_continuity` reads `last_agent_id` / `last_contact_at`,
  which the context assembler sets on the **snapshot**, not on the `WaitingCall`. Both score
  **0 on every real call**. `run_matching.py` generates all three synthetically, which is
  precisely why the simulator looks like it exercises them — the same illusion as `B4`.
- **Speech may move urgency and fit, never `queue_id` or `required_skill`** (`D92`). §2 lists
  "situational urgency" as an input; today it is the *keypad's* intent, from config.
