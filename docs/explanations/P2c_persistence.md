# P2c — the database layer, and how it was kept honest

_Written 2026-08-25. A snapshot, not a specification — see "changes since" at the bottom._

> **Part 1 covers the first half of P2c; Part 2 (from §9) finishes it the same day.**
> §8's list of *"what is still in memory"* is superseded by §17 — everything on it is now
> durable, except two things left deliberately. Read §10 before adding a table.

P2b left one thing undone, and `NEXT_SESSION` had carried it as the biggest outstanding
debt ever since: **presence, assignments and `agent_state_log` were in memory, so a restart
lost a shift.** Every agent came back silently `NOT_READY`, whatever they had actually
declared, and every call in flight ceased to exist.

This is the phase that fixes it — and most of the interesting decisions are not about
SQLAlchemy. They are about making sure the database path is *actually exercised*, because
the failure mode this project keeps hitting is code that is correct and never runs (`B3`,
`B4`, `B7`).

---

## 1. Why it waited, and what made it due

`D39` deferred the whole layer with an explicit trigger: *"the moment two processes need to
see the same call, or a demo needs to survive a restart."* That was the right call — the
schema moved under active design for three phases, and writing the models earlier would
have meant writing the migrations twice.

What made it due was not a new feature. It was that the agent state log became the substrate
for the product's headline metric: ACW shrinking because the AI drafts the wrap-up (`D45`).
A metric you cannot measure across a restart is not a metric.

---

## 2. The seam was already there

The only reason this phase is small is that P0 built the seam on the way past:

```python
class CallSessionRepository(Protocol):
    async def save(self, session: CallSession) -> None: ...
    async def get(self, call_session_id: str) -> CallSession | None: ...
    async def find_by_telephony_id(self, telephony_call_id: str) -> CallSession | None: ...
    async def list_in_states(self, *states: object) -> list[CallSession]: ...
```

`InMemoryCallSessionRepository` is a dict. `PostgresCallSessionRepository` implements the
same four methods. **Nothing above `db/` changed.** That is `D3`'s ports-and-adapters
discipline applied to an internal seam, and it is the whole return on having written a
Protocol around a dict eighteen decisions ago.

---

## 3. Three backends, one contract suite

The tests that matter are in `tests/contracts/`, and they run **the same suite against
in-memory, SQLite and Postgres**:

| Backend | Why it exists | When it runs |
|---|---|---|
| memory | what every other test in the project uses | always |
| SQLite | the fast path — no container | always |
| Postgres | the deployment target | when reachable, else skipped |

The in-memory store is the one nearly the whole suite runs against, so **if the two ever
disagree the suite is green while production is broken**. A shared contract is the only
thing that stops that.

### The divergence it caught within an hour

**SQLite ignores foreign keys unless you ask it to enforce them.** An `agent_state_log` row
pointing at a call that did not exist was refused by Postgres and cheerfully accepted by
SQLite — the same test passing on one backend and failing on the other.

That is worse than having no SQLite path at all: it turns a real constraint into one that
only appears in production. `PRAGMA foreign_keys=ON` is now set on every SQLite connection,
and a test asserts the foreign key bites on both SQL backends.

> The general rule, and it is the same one as `B7`: **a fast path that enforces less than
> production is a fast path that lies.**

---

## 4. Presence is a projection, not a table

The obvious schema has two tables: `agent_presence` for current state, `agent_state_log`
for history. We store only the log (`D76`).

```
  agent_state_log  (append-only)
  ─────────────────────────────────────────────
   A001  10:00  available  ready       agent     agent_declared
   A002  10:01  available  lunch       agent     agent_declared
   A001  10:02  available  break       agent     agent_declared
   A003  10:03  available  draining    agent     agent_declared
                    │
                    ▼  latest_per_agent()  — one ordered SELECT, once at startup
   A001 → break     A002 → lunch     A003 → draining
```

Three reasons:

- **Two facts that can disagree will.** A crash between the writes, or one path that updates
  presence without logging, and the screen and the shift report tell different stories.
  There is no reconciliation code because there is nothing to reconcile.
- **The log answers the question that gets asked.** *"What was true at 14:03"* — a
  current-state row cannot answer it. If only one survives, it should be this one.
- **It costs nothing.** One ordered scan over a shift's worth of rows, once per process.

**What is deliberately not stored: the heartbeat.** Liveness is a property of a live socket,
not a durable fact. An agent whose laptop is shut is not present regardless of the last row,
which is what the sweep already handles (`B7`).

---

## 5. Domain models cross the boundary, ORM rows do not

Every repository method maps to and from the domain model by hand (`D77`). An ORM row
carries a session lifetime with it, and the first place that breaks is a background sweep
whose session has closed — a lazy-load error a long way from its cause.

The rule for what becomes a column, since it will be asked again:

> **Anything the matcher, a report, or a query filters on is a column. Anything only ever
> read back whole is JSON.**

So `state`, `queue_id`, `telephony_call_id` and the timestamps are indexed columns;
consents, stage timings and the identity resolution are JSON.

The mapping is hand-written on purpose — it is the one place the database shape and the
domain shape are allowed to differ, and generated mapping hides exactly that decision. The
risk it creates (a new field on `CallSession` that the mapper forgets) is covered the only
way that works: the contract test **round-trips a whole object and compares equality**,
rather than asserting field by field from a list somebody wrote by reading the mapper.

---

## 6. Migrations

Alembic, async template, with three deviations from the generated default — each one a bug
avoided rather than a preference:

- **The URL comes from `Settings`**, not `alembic.ini`. A migration run against a different
  database from the one the app opens fails as *"the table does not exist"*, and costs an
  hour every time. The ini value is left blank with a comment saying why.
- **`include_object` refuses to emit DDL against the `core` schema.** The bank's data is
  read-only to us (`D5`), enforced by grants — but a migration is exactly the sort of thing
  that runs as a superuser and defeats a grant. Second lock.
- **The version table lives in the `readycall` schema.** In `public` it would be the one
  piece of our state outside the boundary `D5` draws.

Two papercuts worth knowing, both now fixed permanently in the template:

- autogenerate proposes **dropping `alembic_version`** if it exists when the comparison
  runs. Excluded in `include_object`.
- generated migrations reference custom column types by their full path
  (`readycall.db.base.Utc`) **without importing them**, so every one dies with
  `NameError` on the first timestamp column. `script.py.mako` now imports the module.

---

## 7. What was verified, and how

Not asserted — run, against a real container:

```
docker compose -f infra/docker-compose.yml up -d postgres
  → schemas core + readycall, roles readycall_rw + readycall_ro   (D5, by grant)

alembic upgrade head        → 4 tables in the readycall schema
alembic downgrade base      → clean, only alembic_version left
alembic upgrade head        → back

write a call through the repository, then:
  psql -c "SELECT ... FROM readycall.call_sessions"     → the row
  psql -c "SELECT ... FROM readycall.call_state_transitions" → one transition, not two
  read it back through a BRAND NEW engine               → survives a restart, not a cache
```

The transition count is the one worth dwelling on. The orchestrator saves the same
`CallSession` object many times per call, so a naive implementation re-inserts the whole
timeline on every save. It would still *look* right — the last transition is correct, and
nobody reads the middle of a timeline — while quietly multiplying `D18`'s demonstrable
timeline by the number of saves.

---

## 8. What is still in memory

Being explicit, because the debt is not fully paid:

| Still in memory | Consequence of a restart |
|---|---|
| `assignments` (the offer handshake) | an in-flight offer is lost; the caller is re-matched |
| attestations / identity per call | a verified caller drops to their resolved level |
| captures | typed digits are lost |
| the waiting pool | queued callers are lost |
| `matching_decisions` | the persisted rationale (`D18`, `D22`) is not yet durable |

`call_sessions` and `agent_state_log` were done first because they are the two that make a
restart *survivable* rather than merely *recoverable*: the calls exist and the shift is
intact. The rest is the same pattern repeated — a table, a mapper, a row in the contract
suite — and none of it needs a new decision.

---

# Part 2 — finishing it (2026-08-25, later the same day)

Part 1 above ends with §8, *"what is still in memory"*, listing five things and calling each
"the same pattern repeated". That turned out to be half true. The tables were mechanical.
The question they forced was not.

---

## 9. The question the pattern did not answer

Five stores were left: assignments, attestations, captures, the waiting pool,
`matching_decisions`. Writing the first one exposed something §8 had not noticed.

**Every one of those services has synchronous readers on the hot path.**

```
AssignmentService.excluded_agents(call)   ← runs INSIDE the matcher tick, every second
AttestationService.history(call)          ← runs on every workstation snapshot
DispatchService.waiting()                 ← runs on every tick
KeypadCaptureService.for_call(call)       ← runs on every snapshot
```

So "persist it" is ambiguous. It could mean:

| | Reads go to | Cost |
|---|---|---|
| **Read-through** | the database, every time | a round trip inside a 50 ms budget, and `async` rippling through every router |
| **Write-through** | memory, rebuilt at startup | the working set can drift from the database within one process lifetime |

We chose **write-through** (`D78`), and the reasoning is worth having in one place because
it is the kind of thing that gets "improved" later by someone who reads only the code:

- **There is one writer and one process** (`D2`). The usual reason to read through is that
  somebody else may have written. Nobody else can.
- **`D76` already made this call for presence** and explained why. Doing it differently for
  the other five would leave two durability models in one codebase, which is worse than
  either.
- **`D39`'s trigger has not fired.** It says the moment to revisit is *"when two processes
  need to see the same call"*. At that point the working set stops being a projection and
  becomes a cache with an invalidation problem — and that wants Redis or read-through, not
  a patch on this.

---

## 10. What gets a table — the rule, because it will be asked again

> **A table records something a person or a service DID. Anything computable from those
> records is derived.**

```
   STORED — somebody did this                DERIVED — computable from the above
   ──────────────────────────────            ────────────────────────────────────
   assignments          an offer was made    current presence  ← newest agent_state_log row
   identity_attestations an agent stated     the waiting pool  ← call_sessions in queued/matched
   keypad_captures      a caller keyed       identity_for_call ← CallSession.identity
   matching_decisions   the matcher decided  snapshot_for_call ← CallSession.snapshot_id
   context_snapshots    the assembler froze  accrued wait      ← recomputed from queued_at
   call_wrapups         an agent wrote up
```

The waiting pool is the clearest case. A `queue_entries` table would be a second answer to
*"is this caller waiting?"*, and `call_sessions.state` is already the first. That is `D76`'s
argument again, and it does not get weaker for being about calls rather than agents.

**Accrued wait is worth dwelling on.** It is recomputed from `queued_at` rather than restored
from a counter, so a caller who sat through our outage comes back *more* urgent, not reset.
Being pushed down the ordering because we restarted is precisely what `D22`'s urgency term
exists to prevent.

---

## 11. What a restart deliberately does **not** restore

Three things, and none of them is a gap to fix later.

**`system_state`.** Every agent comes back `OFFLINE`. That axis describes what the *platform*
has given the person to do, and after a restart it has given them nothing — no socket, no
offer, no call. Restoring `ON_CALL` from a log row would assert a conversation that is not
happening and let the matcher count a desk that is not there, which is the failure the
heartbeat sweep exists to prevent (`B7`) coming back through a different door.

**`READY` and `LAST_CALL`.** Signing in after a restart carries the standing instruction
forward — *lunch* is still lunch — with those two excluded. This does **not** weaken `D51`:

> the platform is not *writing* the person's axis, it is declining to overwrite it. The write
> it makes is `OFFLINE → AVAILABLE`, on its own axis, and the intent rides along unchanged
> exactly as it does on every other platform move.

`D51` forbids inventing a state the person did not choose. Lunch is what they chose. `READY`
is excluded because it is the one value that makes the platform send someone a call, and we
cannot know whether they came back.

**Unnamed keypad digits**, which were never stored at all — §12.

---

## 12. The one place where "persist it" was the wrong instinct

`D44` inverted the storage default for keypad capture: because capture is **untyped**, an
unnamed run of digits could be a citizen id or a card number, so it is treated as sensitive
until something says what it is.

That rule had only ever applied to logs and transcripts. Writing the table made it a column
decision:

```
   keypad_captures
   ────────────────────────────────────────────────────────
   masked        "••••••4512"     ALWAYS written
   digit_count   10               ALWAYS written
   digits        "2024000811"     ONLY once is_named
                 NULL             otherwise
```

`is_named` is true when a lookup matched or the agent labelled the capture — at which point
the digits are a policy or claim number, which is not a secret and is worth having.

The consequence is visible and correct: **a restart mid-capture gives the agent back the fact
that a capture happened and how long it was, not the digits.** `D44` asks for short retention
and one-click discard; a durable copy of an unidentified number is the opposite of both.

---

## 13. How the restart claim is actually proved

The per-store contract suite (§3) proves each store round-trips. It would have passed just as
happily on `B7`'s three services that were correct and driven by nothing — so it is not
sufficient for a claim about *restarting*.

`tests/integration/test_restart.py` does the only thing that is:

```
   ┌── process 1 ────────────────┐              ┌── process 2 ────────────────┐
   │ boot the real app           │              │ boot the real app           │
   │ sign in, declare, take a    │   the ONLY   │ restore()                   │
   │ call, attest an identity    │   thing that │ sign in                     │
   │ …through the real HTTP API  │   crosses →  │ ask it what it knows        │
   └─────────────────────────────┘   is the DB  └─────────────────────────────┘
```

No shared container, no shared service, nothing that could be answering from a cache it
happens to still hold. Seven assertions, each tied to a decision that would silently break:

| what is checked | what it protects |
|---|---|
| a declared *lunch* comes back | the debt §1 opened — an agent misreported all day (`D51`, `D76`) |
| `system_state` does **not** | the platform never asserts what it cannot see (`D78`) |
| a waiting caller is still waiting, with their wait | the pool as a projection; urgency not reset (`D22`) |
| an attested `L3` identity **and its count** | `attestation_count` re-locks the control (`D61`) |
| an agent who declined is still excluded | `D52` — otherwise the same silent desk is re-offered forever |
| the brief still renders | §14 below |
| a saved wrap-up survives, an unsaved one stays absent | `D45` — the absence is honest data |

Then re-verified outside pytest, because `TestClient` is still one Python process: two real
uvicorn processes against a live Postgres, the first one killed. All five checks passed,
including the Thai summary being byte-identical across the restart.

---

## 14. Two things this found that were not persistence bugs

**`CallSession.identity` had existed since P0 and nothing ever wrote it.** The live resolution
lived only in a dict on the API container. So a restored call had no identity → `render_brief`
returned `None` → **the agent's screen was blank on a call they were in the middle of.** The
column was right, the model was right, and the write was missing — findable only by restarting
and looking. Every write now goes through `Container.set_identity`.

**Declaring READY does not tick the matcher.** Only placing a call, declining an offer, and the
background sweep do. Harmless in production, where the sweep runs every second — but any test
that disables the sweeper must drive `sweep_once` itself, or a restored caller sits in a
restored pool that nothing ever looks at. Which would be `B7` again, in a test this time.

---

## 15. Two migration papercuts, fixed permanently (`D79`)

**Autogenerate proposed churning every foreign key, every run.** The model spells a target
unqualified and the reflected database spells it qualified, so the comparison always differed:
two `drop_constraint` + `create_foreign_key` pairs that changed nothing — and the generated
`drop_constraint` omitted `schema=`, so it would have failed if anyone ran it. Broken DDL that
changes nothing is worse than churn, because it only fails much later.

`include_object` now excludes `foreign_key_constraint`, which suppresses only *alterations*;
new tables still get their keys inline. The check that this is right is not "it looks quieter"
— it is that **a second autogenerate against the migrated database produces an empty
migration**, which says the models and the schema genuinely agree.

**Running the suite destroyed the dev database.** The Postgres contract tests build their
tables with `create_all` and drop them on teardown. Pointed at the app's database that deletes
everything *and* leaves `alembic_version` stamped at head with nothing under it — so
`alembic upgrade head` becomes a silent no-op and the app dies at startup with *"relation
readycall.call_sessions does not exist"*. It cost two debugging detours before the pattern was
visible. The suite now has its own database, `readycall_test`.

Recovery, if it ever happens again:

```bash
uv run alembic stamp base && uv run alembic upgrade head
```

---

## 16. And the phase shipped a bug of its own (`B9`)

Worth putting here rather than only in `BUG_HISTORY`, because it is a **new failure mode for
this project** and the docs are where the lessons live.

`.gitignore` contained `models/`, under a heading reading *"Models / caches"*, meaning ML
weights. A gitignore pattern with no internal slash matches a directory of that name **at any
depth** — so it also matched `src/readycall/db/models/`, and **the entire ORM package was
never committed.** Part 1 of this document describes tables whose model files were not in the
repository. The in-memory path worked perfectly, which is why nothing complained.

Every check this project runs — tests, `mypy`, `ruff`, the migration, the live Postgres
verification — reads the **working tree**. None of them is a statement about what the
repository contains. That is the gap.

The family this belongs to is the one `NEXT_SESSION` already names: `B3`, `B4`, `B6`, `B7`,
`B8` — *a confident, plausible, wrong result nobody looked at*. `B9` adds a hiding place:
**the gap between the working tree and the repository.**

---

## 17. What is left in memory now, and why it is fine

| Still in memory | Why it is acceptable |
|---|---|
| `call_intents` | an intent expires in 15 minutes (`D6`); a restart inside that window loses an app-path binding, and the caller falls back to the cold-call path, which is the base case (`D19`) |
| `app_context_events` | TTL-pruned browsing telemetry; stale screen events are noise, not context |

Both are one table each if the demo ever needs them. Neither loses anything a restart cares
about, which is exactly why they were left rather than done for completeness.

---

## Changes since this was written

_Append here rather than editing above._

### 2026-09-06 — two more tables, and the pattern held

`audio_recordings` (`D110`) and `transcript_turns` (`D114`) joined the nine. Both were
built by copying the shape this phase established, and that is the interesting part: a
`Protocol` beside the service that consumes it, an in-memory implementation the default
configuration actually runs, a Postgres one returning **domain models** (`D77`), and a
block added to the same contract suite so all three backends prove the same behaviour.
Neither needed a new idea, which is what a good seam is supposed to feel like a month
later.

Two ways they are **not** like the nine, both deliberate:

- **Neither is a write-through projection** (`D78`). Every store this phase built backs a
  working set some service keeps in memory, because a matcher tick cannot afford a query.
  Nothing reads a recording or a stored turn on a hot path — they are read by a playback
  somebody asks for, by P4's analysis, and by the retention job. So there is no in-memory
  half to keep in step, and adding one would create the second answer `D76` warns about.
- **`transcript_turns` is written by a SUBSCRIBER, not by the service that owns the data.**
  `TranscriptRecorder` takes `transcript.turn` off the bus, so a storage failure cannot
  reach the agent's screen (`D12`). A test publishes through a store that raises and
  asserts the screen still got the sentence.

The count in §1 is now **eleven**, and `alembic upgrade head` runs three migrations.
