# Explanation — P0: Foundations

_A plain-language walkthrough of everything built in phase P0, written for someone who
wants to understand the system a layer at a time rather than all at once._
_Written 2026-08-19, describing the code as of commit `p0: foundations`._

> These `explanations/` files are **teaching notes, not specifications**. They explain
> *why* each piece exists in ordinary language. The authoritative documents are
> `ARCHITECTURE.md`, `DATA_MODEL.md`, `DECISIONS.md` and the code itself — if this file
> and one of those ever disagree, the other one is right and this one is stale.
> A note at the bottom lists what changed after this was written.

---

## 0. The one-paragraph version

A call arrives. Something has to remember what state that call is in, look up who's
calling, record what they said, decide which agent gets them, and tell the agent's
screen. Right now this is **the skeleton that does all of that**, where every part that
touches the outside world (the phone network, the speech model, the AI, the bank's
database) is replaced by a **stand-in that behaves the same way but is fake**. So the
whole call flows end to end — you can run it and watch it — but nothing real is plugged
in yet. The next phases swap the fakes for real things, one at a time, without changing
anything else.

---

## 1. The folder map

```
FullProject/
├─ src/readycall/          ← all the code
│  ├─ (small utilities)    ← errors, clock, ids, logging, console, config
│  ├─ domain/              ← the vocabulary: what a Call, a Policy, an Event IS
│  ├─ ports/               ← the SHAPE of every external thing (no code, just shapes)
│  ├─ adapters/            ← actual implementations that fill those shapes
│  └─ services/            ← the business logic (currently: the call orchestrator)
├─ config/                 ← insurance-specific settings (intents, skills, phone numbers)
├─ mock/bank_core/fixtures/← fake bank data as JSON
├─ scripts/run_scenario.py ← runs a whole fake call and prints it
└─ tests/                  ← 84 tests + 3 scenario files
```

**The one rule that shapes everything:** each layer only imports from the layers above it
in that list. `domain` imports nothing. `ports` imports only `domain`. `services` imports
`domain` + `ports` but **never** `adapters`. If a file breaks that, it's a bug.

Why? Because it means business logic literally *cannot* know whether it's talking to a
real phone network or a fake one. That's what makes swapping things safe.

---

## 2. The small utilities

Five little files. Boring individually, but each one exists because of a specific problem.

### `errors.py` — the exception types

Instead of generic `Exception` everywhere, there are five kinds, split along **the line
that matters at runtime**:

```python
TransientError    # network blip — retrying might work
PermanentError    # bad request, unknown ID — retrying won't help
DegradedError     # this stage failed, but THE CALL MUST CONTINUE
IllegalTransition # our own bug: tried to move a call somewhere impossible
ConfigError       # bad settings — raised at startup, never mid-call
```

`DegradedError` is the interesting one. It's how a piece of code says *"I couldn't do my
job — use the lesser version."* If the AI times out, it raises this, and the caller falls
back to a simpler brief. The call never stops. That's the "never block the call on AI"
rule made into a type.

### `clock.py` — time is handed to you, never taken

Normally you'd write `datetime.now()` wherever you need the time. That is banned here.
Instead there's a `Clock` object that gets passed in:

```python
class SystemClock:      # real time
    def now(self): return datetime.now(UTC)

class ManualClock:      # only moves when you tell it to
    def advance(self, seconds): ...
```

**Why this matters so much:** a real call takes 5 minutes. With `ManualClock`, the test
says `clock.advance(240)` and 240 seconds pass instantly. So a full call lifecycle runs
in *milliseconds*, and — crucially — produces **exactly the same timestamps every single
run**. That's what makes the timeline reproducible.

There's also `Stopwatch`, which measures how long a stage took. That's what produces
"context ready in 0.0ms" in the output.

### `ids.py` — readable, sortable IDs

Every call gets an ID like `call_01JQK7M2R4X8ZB3N`. Two deliberate properties:

- **Prefixed** — you see `call_`, `intent_`, `brief_`, `match_` and instantly know what
  you're looking at. A bare UUID tells you nothing.
- **Time-sortable** — the front part is a timestamp, so IDs sort chronologically.

And like the clock, the generator is swappable. Tests install `DeterministicIds()`, which
produces `call_000001`, `call_000002`… So two runs of the same scenario produce identical
IDs, which is why the entire printed output can be asserted byte-identical twice.

There's also `correlation_token()` — a random secret that binds an app-initiated call to
its intent. Treated as a password: never logged, stored only as a hash.

### `console.py` — the Thai problem

This one file exists because of a bug hit within an hour of starting:

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-23
```

The Windows console defaults to cp1252, which has no Thai characters. Printing a Thai
transcript **crashes the process**. Not the string — the string was perfectly fine in
memory — just the printing.

```python
def enable_utf8():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
```

`errors="replace"` rather than crashing, on purpose: a mangled character in a log line is
cosmetic; a dead process mid-call is not. Every entrypoint that can print Thai calls this
first. Written up as `B1` in `BUG_HISTORY.md`.

### `logging.py` — every log line knows which call it's about

Uses `structlog`. Two things worth knowing:

**Context binding.** The call ID is set once and then automatically attached to every log
line inside that block:

```python
with call_context(session.call_session_id, trace_id=...):
    log.info("call state changed", from_state=..., to_state=...)
    # ↑ automatically gets call_session_id and trace_id attached
```

Without this, someone eventually forgets to include the ID in the one log line that
mattered.

**Redaction.** A processor scans every log line and blanks out anything named
`correlation_token`, `api_key`, `citizen_id`, `password`, etc. Cheap insurance — better
than trusting every future call site to remember.

*(This file is also where bug `B2` lived: `structlog` reserves the keyword `event` for the
log message, so `log.error("failed", event=...)` crashes with "multiple values for
argument 'event'". Renamed to `event_name`.)*

### `config.py` — every setting, from the environment

One `Settings` class with ~60 fields matching the documented `.env` exactly. Adapter
choices are **enums**, so a typo fails at startup instead of at 2am:

```python
telephony_provider: TelephonyProviderName = SIMULATED   # simulated|asterisk|twilio|livekit
stt_engine:         SttEngineName          = SCRIPTED
llm_provider:       LlmProviderName        = RULEBASED
core_data_provider: CoreDataProviderName   = FIXTURES
```

The bit worth pointing at is the **coherence checks** — validation that rejects settings
that are *individually valid but collectively nonsense*:

```python
if self.max_wait_before_any_agent_s <= self.target_wait_s:
    raise ConfigError(
        "MAX_WAIT_BEFORE_ANY_AGENT_S must exceed TARGET_WAIT_S — the hard "
        "anti-starvation ceiling has to sit above the soft target"
    )
```

If someone sets the "give up on finding a good agent" ceiling *below* the "we'd like to
answer within" target, the deferral logic silently never fires and the system just quietly
behaves differently. The check turns that into a loud startup error with an explanation.

---

## 3. `domain/` — the vocabulary

This is where the system defines *what things are*. No database, no network, no files.
Pure definitions.

### `enums.py` — the fixed lists

The important one is **`CallState`** — the states a call can be in:

```
INTENT_CREATED → CONNECTING → IVR → QUEUED → INTAKE_ACTIVE → INTAKE_COMPLETE
                                   → MATCHED → OFFERED → IN_CALL → WRAP_UP
                                   → RATING → CLOSED
plus: ABANDONED, VOICEMAIL, TRANSFERRED, FAILED
```

Others worth knowing:

- **`EntryChannel`** — `IN_APP`, `PRODUCT_DID`, `HOTLINE`, `CALLBACK`, `TRANSFER`. How the
  call arrived.
- **`AssuranceLevel`** — L0 anonymous → L1 probable (caller ID matched) → L2 strong → L3
  verified. With a helper:
  ```python
  def at_least(self, other) -> bool:   # so you can ask "is this at least L2?"
  ```
- **`ConsentScope`** — `RECORDING`, `AI_PROCESSING`, `HEALTH_DATA`, `CROSS_ORG`. Separate,
  because health data legally needs its own consent.
- **`DegradationReason`** — `NO_CONSENT`, `STT_UNAVAILABLE`, `LLM_UNAVAILABLE`,
  `LOW_ASSURANCE`… so when the agent gets a thin brief, the screen can say *why*.

They're all `StrEnum`, so they serialise as readable strings. A transition log full of
`"queued"` beats one full of `3`.

### `models.py` — the objects

About 30 of them, in two groups.

**The bank's side** (what we read): `Customer`, `Policy`, `Coverage`, `Claim`,
`Interaction`, `Holding`, `LifeEvent`, `Product`.

One design choice worth calling out — `Coverage` is a **typed object, not a free-form
dictionary**:

```python
class Coverage(DomainModel):
    kind: str  # "ipd_room_board"
    label_th: str  # "ค่าห้องและค่าอาหาร"
    amount: float | None
    currency: str = "THB"
    unit: str | None  # "per_day"
```

Why bother? Because "the AI must never invent a coverage number" is the biggest safety
rule in this product. If a coverage figure can *only* arrive by being read into a declared
field from real data, then there's no code path where an LLM's output becomes a number on
the agent's screen. The rule is enforced by the type system, not by a politely-worded
prompt.

**Our side** (what we produce): `CallSession`, `ContextSnapshot`, `IdentityResolution`,
`TranscriptTurn`, `IntakeResult`, `CaseBrief`, `MatchingDecision`, `Assignment`,
`Consent`, `StateTransition`…

`CallSession` is the spine — everything about one call hangs off it. Note this bit:

```python
intent_id: str | None = None  # ← optional!
customer_id: str | None = None  # ← also optional!
```

A cold call from a printed number has neither. That's the base case, not an edge case, and
the model says so.

And a few small helpers that encode rules:

```python
@property
def may_run_intake(self) -> bool:
    return self.has_consent(RECORDING) and self.has_consent(AI_PROCESSING)
```

One place decides whether intake is allowed. Not scattered `if` statements.

All models are **frozen** (immutable) except `CallSession`, which the orchestrator
mutates. Frozen-by-default means you can pass a `Policy` around with zero worry that
something modified it.

### `events.py` — the announcements

19 event types. When something happens, an event is published and anyone interested
reacts. Every event carries the same envelope:

```python
event_id, call_session_id, occurred_at, trace_id, schema_version
```

Examples: `intent.created`, `call.state.changed`, `consent.recorded`, `transcript.turn`,
`matching.decided`, `call.offered`, `offer.resolved`, `call.ended`.

Plus a `decode()` function that turns a name + payload back into a typed event — and
**raises on an unknown name** rather than shrugging. Silently ignoring an event you don't
understand is how a replay stops reproducing the truth.

---

## 4. `ports/` — the seams

This is the concept most worth getting, so here's the analogy: **a port is a wall socket.**

The socket defines the *shape* — two holes, this voltage. It doesn't care whether the
electricity comes from a hydro dam or a solar panel. Anything that fits the shape works.

A port in code is the same: a `Protocol` that says "whatever fills this must have these
methods with these signatures." Nothing more.

```python
class CoreDataProvider(Protocol):
    async def get_customer(self, customer_id: str) -> Customer | None: ...
    async def find_customer_by_phone(self, phone: str) -> Customer | None: ...
    async def list_policies(self, customer_id: str, *, active_only=True) -> list[Policy]: ...

    ...
```

That's it — no implementation. Seven of these exist:

| Port | Hides |
|---|---|
| `TelephonyProvider` | The phone system |
| `SttEngine` | Speech-to-text |
| `LlmClient` | The AI model |
| `TtsEngine` | Text-to-speech |
| `CoreDataProvider` | **The bank's data** |
| `EventBus` | The message system |
| `BlobStorage` | Where recordings live |

**Why this is the single most important structural choice for this competition:** on
hackathon morning they hand you data in some shape you didn't predict. If the rest of the
system talked directly to a specific database, that's a rewrite. Because it talks to
`CoreDataProvider`, you write one new adapter that fits the socket, flip an env var, and
nothing else changes.

Two details built into the port shapes on purpose:

- `CoreDataProvider` **has no write methods at all.** Not "we promise not to write" —
  there is no method to call. We cannot corrupt the bank's data because we cannot reach
  it. A test even asserts this by scanning for method names like `save`/`delete`/`update`.
- `TelephonyProvider` has `bridge()` but no `dial_agent()`. Because the customer's line is
  *already connected* and parked on hold — accepting a call **joins two live lines**, which
  is instant. There's no dialling delay by construction.

---

## 5. `adapters/` — filling the sockets

Seven implementations, all fake for now. But "fake" doesn't mean "useless" — each one
models the real behaviour that matters.

### `event_bus/memory.py` — the messaging system

Publish an event, and it goes in a queue. Nothing runs until you call `drain()`:

```python
await bus.publish(event)  # nothing happens yet
await bus.drain()  # NOW handlers run, in order
```

Splitting those apart is what makes everything deterministic — no background timing, no
race conditions, same order every time.

Three real behaviours it implements:
- **Idempotent** — publish the same event twice, each handler sees it once. (Real message
  systems can deliver twice; code must survive that.)
- **Replayable** — keeps the whole log, so `history(call_id)` replays one call.
- **Loop detection** — if a handler publishes an event that triggers itself forever, it
  errors at 10,000 iterations instead of hanging.

### `core_data/fixtures.py` — the bank's data from JSON files

Reads `mock/bank_core/fixtures/*.json` and maps each row into a domain object. Two pieces
of real Thai-specific work in here:

**Phone normalisation, both directions:**
```python
"0812345678"   → "+66812345678"
"081-234-5678" → "+66812345678"
"66812345678"  → "+66812345678"
```
The data might store one format and the caller ID arrive as another. Get this wrong and
*every cold call silently fails to identify anyone* — the base case, broken invisibly.

**Buddhist-era dates:**
```python
if parsed.year >= 2400:
    parsed = parsed.replace(year=parsed.year - 543)
```
Thai data often has years like `2530`. Without this, you get a 543-year-old customer. One
of the fixture customers has a BE birthdate specifically so a test catches it if this ever
breaks.

### `core_data/null.py` — everything unavailable

Returns `None` and `[]` for everything. This is *not* laziness — it's how you prove the
system survives the bank's data being down. Set `CORE_DATA_PROVIDER=null` and the whole
thing must still take a call and give the agent a minimal brief. If it can't, that's a bug
you want to find now, not on stage.

### `telephony/simulated.py` — a phone network in a dictionary

Models a phone channel with a state (`ringing` → `answered` → `held` → `bridged` →
`hungup`) and lets tests inject events:

```python
telephony.inject_incoming(caller_number="0898887777", dialled_number="+6621234111")
telephony.inject_dtmf(call_id, "1")  # customer pressed 1
telephony.inject_hangup(call_id)
```

It models the things that actually matter downstream: **hold** (the line stays connected
while queued), **per-leg media forks** (each side of the call is a separate audio stream,
so you know who's talking without a speaker-detection model), **DTMF with barge-in**
(pressing a key cuts off the prompt), and an optional **injectable fork failure** so the
"audio capture broke but the call continued" path can be tested.

### `stt/scripted.py` — speech-to-text that replays a script

You hand it a list of lines with timings; it returns them one at a time as if it had just
transcribed them. When it runs out, it returns **empty text, not invented text** — because
that's what a real engine does on silence, and Whisper hallucinating on silence is a known
failure mode we shouldn't imitate.

### `llm/rulebased.py` — AI without any AI

This is the one people underestimate. It's not a placeholder — it's the **fallback that
runs in production when the model is down**. Thai keyword matching:

```python
KEYWORDS = {
    "motor.claim.accident": ("ชน", "อุบัติเหตุ", "เฉี่ยว", "รถชน"),
    "health.ipd.preauth":   ("นอนโรงพยาบาล", "แอดมิท", "ค่าห้อง", "ผู้ป่วยใน"),
    ...
}
```

Tested live:
```
"พรุ่งนี้จะไปนอนโรงพยาบาล อยากทราบค่าห้องครับ"  →  health.ipd.preauth  (0.5)
"รถผมชนมาครับ ตอนนี้อยู่ที่ถนนพระราม 9"          →  motor.claim.accident (0.4)
```

Note the confidence numbers: **deliberately capped below the 0.55 floor.** A keyword hit
is weak evidence, so the agent's screen correctly says "intent unclear — please confirm"
rather than showing a confident percentage. And its summary *quotes* the customer rather
than paraphrasing — a template physically cannot hallucinate.

The real payoff: **P0 needs no API key.** The entire pipeline runs. Swapping to Claude at
P4 is one env var.

### `tts/null.py` and `storage/memory.py`

Small. TTS records what *would* have been said (so tests can assert which prompt played)
and returns a plausible duration. Storage keeps bytes in a dict — deliberately not writing
to disk, because a real recording must never sit unencrypted on a dev machine.

---

## 6. `services/call_orchestrator/` — the spine

This is where the actual logic lives. Three files.

### `machine.py` — the rules, as a table

One dictionary saying which states can follow which:

```python
S.QUEUED:        frozenset({S.INTAKE_ACTIVE, S.MATCHED, S.VOICEMAIL, S.ABANDONED, S.FAILED}),
S.INTAKE_ACTIVE: frozenset({S.INTAKE_COMPLETE, S.MATCHED, S.ABANDONED, S.FAILED}),
S.OFFERED:       frozenset({S.IN_CALL, S.MATCHED, S.ABANDONED, S.FAILED}),
```

Three entries encode real decisions rather than mechanics:

- `QUEUED → MATCHED` **and** `INTAKE_ACTIVE → MATCHED` both exist. Meaning: a call can
  leave the queue whether or not intake finished. That's "never make the customer wait for
  AI," written as a rule.
- `OFFERED → MATCHED` exists. Meaning: an agent declines or doesn't respond → back to
  matching, find someone else. The caller isn't stranded.
- `IN_CALL → ABANDONED` **doesn't** exist. Abandoned means "gave up waiting." Once
  answered it's impossible, and allowing it would quietly corrupt the abandonment metric.

There's also `validate_table()`, which self-checks: every state present, terminal states
have no exits, non-terminal states have at least one, no unreachable states. It runs as a
test.

### `repository.py` — where sessions are stored

A dictionary for now, behind a small interface so the Postgres version drops in later
without touching anything else.

### `orchestrator.py` — the only thing that moves a call

**One method** ever assigns `session.state`:

```python
async def transition(self, session, to_state, *, reason):
    machine.assert_can(session.state, to_state)  # 1. legal?
    session.state = to_state  # 2. move
    session.transitions += (StateTransition(...),)  # 3. log it with time + reason
    await self._repo.save(session)  # 4. persist
    await self._bus.publish(CallStateChanged(...))  # 5. announce
```

Everything else (`enqueue`, `record_consent`, `abandon`, `fail`) goes through it. One
writer means no races.

Step 3 is the one that pays off visibly. Because every transition is logged with a
timestamp and a *reason*, you get this for free:

```python
@staticmethod
def timeline(session) -> list[str]:
    # "+  10.00s  queued -> intake_active    consent_given_press_1"
```

That's not a debug feature bolted on — it's a side effect of recording things properly,
and it's what makes the demo *show* rather than claim.

---

## 7. `config/` and `mock/` — the domain pack

Everything insurance-specific lives in data files, never in code. That's what makes this
reusable for another project later.

**`config/intents.yaml`** — the reasons people call:
```yaml
motor.claim.accident:
  label_th: "แจ้งอุบัติเหตุรถยนต์"
  skill: motor.claim
  default_urgency: critical
  required_slots: [location, plate_number, injuries, other_party, drivable]
  playbook: motor_accident
```

**`config/skills.yaml`** — 13 skills, 9 queues, each with an SLA and opening hours.

**`config/dids.yaml`** — the printed phone numbers, mapping a dialled number to a product
line and queue.

**`mock/bank_core/fixtures/`** — 7 JSON files: 3 customers (one per persona from the
brief), 4 policies across 4 product lines, products, interactions, claims, holdings, life
events.

---

## 8. `scripts/run_scenario.py` — the thing you actually run

This is the payoff. It reads a YAML file describing a call and plays it out.

```yaml
name: pattheera_ipd
entry:
  channel: in_app
  customer_id: C000001
consent: [recording, ai_processing, health_data]
intake:
  turns:
    - text: "พรุ่งนี้ผมต้องไปนอนโรงพยาบาลกรุงเทพครับ"
      t_start_ms: 0
      t_end_ms: 3600
timing:
  wait: 22.0
  offer: 7.0
```

It builds all the fakes, then walks the call through its lifecycle and prints the
timeline, notes, transcript, events and consent.

**The honest bit:** in P0 this script performs several steps *itself* that will later
belong to real services. Every one is marked:

```python
# P0: the identity resolver lands in P1. ANI lookup only, no assurance model.
# P0: the matching engine lands in P2; the agent is named by the scenario.
```

So `grep -rn "# P0:" scripts/` is a literal to-do list of what's still standing in. Not
hidden scaffolding — a checklist.

---

## 9. `tests/` — what's actually protected

84 tests, three kinds.

**Unit tests** — the state machine and orchestrator. Some assert *decisions*, not
mechanics:

```python
def test_leaving_the_queue_does_not_require_intake_to_finish():
    """D12: agent availability drives the queue, never AI completeness."""
    assert machine.can(S.QUEUED, S.MATCHED)
    assert machine.can(S.INTAKE_ACTIVE, S.MATCHED)
```

If someone six weeks from now "tidies up" that table and removes a transition, the test
fails **with a message naming the decision they just broke**. That's the real value.

**Contract tests** — the same suite run against *every* adapter of a port. This is the
hackathon-day integration checklist: write a new adapter for whatever data they hand you,
run the suite, and when it's green you're integrated. Minutes instead of an afternoon.

**Scenario tests** — run all three YAML files and check the final state, plus assert that
**running the same scenario twice produces byte-identical output**. That determinism is
the precondition for comparing AI outputs later.

---

## 10. One call, all the way through

Tying it together. The roadside motor claim:

1. **`SimulatedTelephonyProvider.inject_incoming()`** — a call arrives on `+6621234111`
   from `0898887777`.
2. **`CallOrchestrator.start_cold_call()`** — creates a `CallSession` in state
   `CONNECTING`. No intent, no customer.
3. Runner calls **`FixtureFileProvider.find_customer_by_phone("0898887777")`** —
   normalises to `+66898887777`, finds `C000002`. Note recorded: *"probable identity only
   (D20)."*
4. Prefetch reads policies + interactions. Timed with `Stopwatch`.
5. **`orchestrator.enter_ivr()`** → checks the table, logs the transition, publishes
   `call.state.changed`.
6. **`record_consent()`** twice → `may_run_intake` becomes true.
7. **`enqueue()`** → state `QUEUED`, publishes `call.queued`.
8. **`ScriptedSttEngine`** returns three Thai lines; `ManualClock` advances 4.5s per turn.
9. Transitions through `MATCHED` → `OFFERED` → (`clock.advance(5)` = the offer window) →
   **`telephony.bridge()`** → `IN_CALL`.
10. `WRAP_UP` → `RATING` → `CLOSED`.
11. **`bus.drain()`** runs every handler; the timeline is printed from
    `session.transitions`.

Every step used a real component. Only the *edges* — the phone, the speech model, the AI,
the database — were fakes.

---

## 11. Real vs fake, honestly

| Piece | Status |
|---|---|
| Call state machine, orchestrator, transition log | **Real.** Won't be rewritten. |
| Domain models, events, ports | **Real.** The vocabulary is settled. |
| Config, logging, clock, IDs | **Real.** |
| Phone network | Fake (Asterisk at P5) |
| Speech-to-text | Fake (Thonburian at P3) |
| AI | Rule-based (Claude/Typhoon at P4) |
| Bank data | JSON files (whatever they give us) |
| Identity resolution | Caller-ID lookup only (full ladder at P1) |
| Matching | Scenario names the agent (real engine at P2) |
| Agent workstation | Doesn't exist yet (P2) |
| Database | In-memory dicts (Postgres next) |

---

## 12. What came next

Finishing P0: Postgres schema + migrations, the mock-data generator, docker-compose. Then
**P1 — Context-Aware Calling**: the identity resolver with the real L0–L3 ladder, the
context assembler that records where every field came from and how old it is, and the
first version of the agent screen.

---

## Changes since this was written

_Append here rather than editing above, so the walkthrough stays a snapshot of P0._

- **2026-08-19 — menu-first IVR (`D37`).** The design now puts a **DTMF menu before the
  queue**: the caller picks their product line and reason from spoken options, so the
  system knows where to route them before any AI runs. AI intake became the layer *on top*
  of that base rather than the primary intent mechanism. This changes §7 (`config/menus.yaml`
  is new) and the flow described in §10.
- **2026-08-19 — `<line>.other` catch-all intents.** Every product line now has an
  explicit "something else" intent, so an unexpected reason lands somewhere sensible
  instead of falling through to a generic unknown.
- **2026-08-19 — language modelled but not implemented (`D38`).** `Language`,
  `CefrLevel`, `AgentLanguage`, and preferred/acceptable language on the call session were
  added to `domain/`, and language became a hard filter in the matching design. Everything
  still runs Thai-only; the model just doesn't have to be retrofitted later.
