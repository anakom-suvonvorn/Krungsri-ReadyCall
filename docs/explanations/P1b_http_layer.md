# P1b — the HTTP layer, and the customer side of the demo

_Written 2026-08-23, after P1b landed. A snapshot, not a specification — see
"changes since" at the bottom._

Up to now the whole system ran **inside a Python process**. The scenario runner drove it,
tests drove it, and nothing outside could reach it. P1b puts a door on the building: a
public API that an app talks to, and a page that pretends to be that app.

Four things landed. I'll go through them in the order they run.

---

## 1. The session seam — where `D4` actually lives

`src/readycall/api/security.py`

Start here, because everything else depends on it being right.

When the customer taps *Contact*, the app sends us a request. That request must say **which
plan** they tapped and **which screen** they were on. It must **not** say who they are.

Why that matters is worth being concrete about. If the request body could carry
`customer_id`, then anyone with `curl` could send:

```
POST /v1/calls/intents  {"customer_id": "C000002", "product_code": "..."}
```

…and get back a correlation token bound to a stranger. Carry that token on a phone call and
the identity resolver hands you **`L3_VERIFIED`** — the top of the assurance ladder, full
policy disclosure. The entire ladder from `D20` would rest on a value the attacker typed.

So identity comes from the **session**, resolved server-side, through a `Protocol`:

```python
class SessionResolver(Protocol):
    async def resolve(self, session_token: str | None) -> Principal: ...
```

Two implementations are possible; only one exists. `DemoSessionStore` maps a cookie to a
persona the user picked. A real one would validate Krungsri's own session token. **The
endpoint cannot tell them apart**, which is the whole reason for the seam.

The demo store is deliberately not sloppy:

```python
for known in self._sessions:
    if hmac.compare_digest(known, session_token):
```

A dict lookup would work fine for a hackathon. But this class is the thing a real adapter
gets modelled on, and a credential check that leaks timing is a bad model to copy.

---

## 2. The schema is the enforcement

`src/readycall/api/schemas.py`

Here is the entire mechanism that stops a client asserting identity:

```python
class CreateIntentRequest(ApiModel):
    product_code: str | None = None
    plan_id: str | None = None
    entry_screen: str | None = None
    app_intent: str | None = None
    preferred_channel: str = "pstn"
```

There is no `customer_id` field. Not "we ignore it" — **there is nowhere to put it.** And
because `ApiModel` sets `extra="forbid"`, sending one anyway is a `422`, not a silently
dropped key.

That is `D4` expressed as a data structure rather than as a check somebody has to remember
to write. And there is a test whose only job is to notice if that ever changes:

```python
def test_create_intent_schema_has_no_customer_id_field() -> None:
    assert "customer_id" not in CreateIntentRequest.model_fields
```

It looks trivial. It is guarding the most dangerous single line anyone could add to this
codebase.

---

## 3. Creating an intent, and *not* waiting for the context

`src/readycall/services/identity/intents.py` and `src/readycall/api/routers/mobile.py`

The route is nine lines of real work: take the principal, call the service, return. The
service mints a token, stores **only its SHA-256 hash**, and publishes `intent.created`.

The bit worth slowing down on is what the route does *next*:

```python
background.add_task(container.bus.drain)
```

`D6` says context assembly starts at *tap*, not at *answer*. But it must not start
*in the request* — six reads to the bank core would sit between the customer tapping a
button and the app receiving a number to dial.

So the endpoint publishes and returns. FastAPI drains the bus **after the response is
sent**, and a handler wired up in `deps.py` runs the assembler. Measured on the dev laptop:

| | |
|---|---|
| dial target back to the app | **~50 ms** |
| context assembled, after that | **1.8 ms** |

The customer starts dialling before the reads have finished. That is the pitch's central
claim, working, with numbers you can read off the screen.

This is also the first time the **event bus does real work**. Until now it recorded what
happened; here it decouples two things that genuinely should not wait for each other.

---

## 4. The simulator

`apps/customer_sim/index.html` — one file, no build step (`D47`).

The agent workstation at P2 will be React: it needs component state, a websocket and a
softphone. The simulator needs three screens and a `fetch` call. Adding npm, a bundler and
`node_modules` to that would buy nothing except a new way for a demo to fail.

It does three things: pick a persona, browse a page or two (which posts context events),
and tap Contact. Then it shows what the server did — the build time, the policies found,
the provenance field count.

It also prints its own **request log**, live:

```
GET  /v1/demo/personas           200
POST /v1/demo/session            200
POST /v1/app/context-events      200
POST /v1/calls/intents           201
GET  /v1/calls/intents/{id}      200
```

That list is the argument. Every line is a call to the public `/v1` API. There is no
privileged back door and no simulator-only endpoint, so *"replace this page with the real
Krungsri app and nothing on the server changes"* is checkable rather than merely claimed.

---

## 5. The bug that only showed up because I looked at it

The first time the simulator ran, the headline metric read:

> **0.0 ms** to assemble context

Not an error. Not missing. A confident zero — which is how it had gone unnoticed in the
scenario runner's timing records too.

`SystemClock.monotonic_ms()` used `time.monotonic()`. On Windows that is backed by the
~15.6 ms system tick:

```
monotonic    resolution: 0.015625 s   ->      2 distinct values in 200,000 samples
perf_counter resolution: 1e-07 s      -> 200,000 distinct values
```

Every stage in this system so far finishes in well under 15 ms, so **all of them recorded
as exactly zero**. That quietly emptied `D18` — *every stage writes a timing record* — and
made the demo's own "context ready before the phone rang" story unprovable on the machine
we will demo from.

One-line fix (`perf_counter`), written up as **`B3`**. The lesson worth keeping: *a timing
story told with an instrument that cannot resolve what it is timing is worse than no story,
because it looks like it works.*

And note how it was found — not by a test. `0.0` is a perfectly valid float and the code did
exactly what it said. It took running the thing and reading the screen.

---

## What P1b did not build

- **The agent screen.** It was on the P1 list, but it belongs with the workstation at P2 —
  building a static version now means building it twice.
- **Persistence.** Intents, snapshots and sessions are in dicts. Postgres lands at P2
  (`D39`), where agent presence is the first thing that genuinely must outlive a process.
- **Real telephony.** The `dial_target` is a string from config. Nothing dials it yet (P5).

---

## Try it

```bash
uv sync --extra web
uv run python -m readycall.entrypoints.api
```

Then <http://127.0.0.1:8000/sim> for the simulator, or `/docs` for the API.

Sign in as **คุณปัณณธัช** — the persona holding two policies in different lines — and watch
`policies: 2` come back. That is also the customer the context assembler deliberately
*refuses* to pick a relevant policy for, because with two unrelated policies and no signal,
a confidently wrong one on the agent's screen is worse than an empty panel.

---

## Changes since this was written

_Append here rather than editing above._

- (nothing yet)
