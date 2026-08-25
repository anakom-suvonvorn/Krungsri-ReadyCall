# P2c — the database layer, and how it was kept honest

_Written 2026-08-25. A snapshot, not a specification — see "changes since" at the bottom._

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

## Changes since this was written

_Append here rather than editing above._
