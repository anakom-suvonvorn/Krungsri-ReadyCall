# P2b — the workstation client, wired

_Written 2026-08-25. A snapshot, not a specification — see "changes since" at the bottom._

`P2b_workstation.md` explains the *system* behind the agent desk: the two axes, the offer
handshake, after-call work, queue hours. This one explains the **browser tab** — what it
holds, what it asks for, what it is told, and which of those it decides for itself.

It exists because that question kept being answered by reading four TypeScript files, and
because the answer is where a whole class of bug lives: anything the client tracks that the
server also knows is a place the two can disagree, and the client's copy is always the
wrong one.

---

## 1. Two channels, one host, and nothing else

The workstation is a React bundle served as static files **by the same FastAPI process that
serves the API**. There is no separate dev server in the running system, no second origin,
and no connection to anything else — no database, no Redis, no telephony.

```
  BROWSER TAB                          ONE FASTAPI PROCESS
 ┌──────────────────────┐             ┌────────────────────────────────┐
 │ App.tsx              │──── HTTP ──▶│ routers/agent.py               │
 │  one cached Snapshot │  15 calls   │   validate → delegate → return │
 │  + 13 local values   │◀────────────│   a FRESH SNAPSHOT             │
 │                      │             │                                │
 │ useSocket.ts         │──── WSS ───▶│ realtime.py · AgentHub         │
 │  lastSeq, backoff    │  4 pushes   │   per-agent seq + 200 outbox   │
 │                      │◀────────────│                                │
 │ HttpOnly cookie      │             │ services/  (presence, offers,  │
 │  JS cannot read it   │             │   dispatch, matching, brief…)  │
 │                      │             │ schemas.py — THE GATE          │
 │ softphone: STUBBED   │             │ all state in memory (P2c debt) │
 └──────────────────────┘             └────────────────────────────────┘
                                        also serves /workstation, /sim
```

Because the bundle is served by the API itself, the socket URL is derived from
`window.location.host` and no CORS is involved. Everything behind the services — fixtures
standing in for the bank core, `POST /v1/demo/calls` standing in for telephony — is
invisible to the client, which is the port boundary doing its job (`D3`).

---

## 2. Who owns what

The client's rule is **the server owns state, the client owns presentation**. Every
exception is deliberate, and every exception is a place drift can start.

### Server-owned — all of it arrives as one `Snapshot`

`GET /v1/agent/me` returns it, and so does **every mutating request**. There is no partial
update path, which is why the UI stays coherent after any click without a follow-up fetch.

| Field | Decides |
|---|---|
| `presence.system_state` | Which panels exist: `on_call` shows the call bar, `after_call_work` the ACW bar |
| `presence.agent_intent` | Which status button reads as current |
| `presence.offerable` | The รับสายได้ badge, and whether a test call may be placed |
| `presence.declarable[]` | Exactly which status buttons are pressable (mid-call, three of seven) |
| `presence.awaiting_declaration` | Stops the pre-call instruction rendering as if it were current |
| `presence.intent_reason` | Which of three sentences explains a `not_ready`; whether *Save & Ready* is offered |
| `presence.acw_since` | The anchor the ACW timer counts from |
| `presence.long_acw` | The "running long" warning, from `acw_long_after_s` |
| `offer` | The offer card, **including a gated preview of the brief** (`D69`) |
| `active_call_session_id` | Which call can still be acted on (`IN_CALL` or `WRAP_UP` only) |
| `wrapup_call_session_id` | Which call is being wrapped — **outlives the record closing** (`D68`) |
| `wrapup_saved` | Whether the record was saved (`D68`) |
| `identity` | Assurance badge, lock state, `attestation_count`, disclosure flag |
| `brief` | The whole brief panel. A locked field is **absent from the payload** |
| `captures[]` | Digits, mask, and the tiered lookup results |
| `queues[]` | Queue depth, each flagged `mine` for this agent's skills (`D70`) |
| `call_answered_at` | The anchor the call timer counts from |
| `server_time` | The skew correction every timer applies (`D68`) |

### Client-owned — exists only in this tab

| State | For | A refresh costs |
|---|---|---|
| `snapshot` | The cached server truth | nothing, it re-fetches |
| `busy` | A **user-initiated** request is in flight → disable controls | nothing |
| `error` | The toast | nothing |
| `muted`, `held` | Softphone stub; cleared whenever not `on_call` | nothing today; P5 makes them real |
| `challenge`, `challengeNote`, `callerName`, `relationship` | The identity form before submission | the typing |
| `reopened` | "I pressed amend" — re-locks on `attestation_count` (`D61`) | nothing |
| `disposition`, `notes`, `followUp` | The wrap-up draft | **the typed notes** — no draft persistence exists |
| `lastSeq`, `attempt`, `stopped` | Replay position and backoff | replays the outbox, then a snapshot lands on top |
| `tick` | Forces a re-render 4×/s so timers move | nothing |

**Nothing in the second table decides what the agent may do or see.** Permission —
`offerable`, `declarable`, what the brief contains — is entirely server-side. That is the
invariant to check any change against.

---

## 3. The socket carries less than you would think

Four push types, and the client acts on the *content* of exactly one:

```
  offer          ─┐
  offer_revoked  ─┼──▶ quietRefresh()  ──▶ GET /v1/agent/me ──▶ setSnapshot
  presence       ─┘    (never sets `busy` — that was the flicker)

  capture        ────▶ patch that ONE capture in place, no round trip

  snapshot, heartbeat_ack   seq 0 · out of band · never acked, never replayed
```

Three of the four are **doorbells**: *something changed, go and re-read*. Only `capture`
carries a payload applied directly, because keystrokes arrive fast and a full snapshot per
digit is wasteful and jumpy — and even there the server remains the only writer, which is
the fix for the capture that once invented an eleventh digit.

Client → server on the same socket: `hello {last_seq}`, `ack {seq}`, and a heartbeat every
10 s. Reconnect backs off 0.5 → 8 s.

**Nothing on the socket is state-bearing.** If it is down when an offer is made, the offer
still exists, still times out, still re-matches. The tab is a *view*, which is what makes
`D32` safe: a browser is allowed to be flaky.

### The ordering contract

Each agent has their own sequence counter and a 200-message outbox, and **a message is
written to the outbox before delivery is attempted** — a message that failed to send is
exactly the one a reconnecting client needs replayed. On reconnect the client sends the
highest `seq` it applied, the server replays the gap, then pushes a fresh snapshot on top.
Anything at or below what the client already applied is dropped: a duplicate offer is a
call ringing at a desk that already declined it.

---

## 4. Every request the client can make

Fifteen, all `credentials: "include"`, **none carrying an `agent_id`** — identity comes from
the HttpOnly cookie the JavaScript cannot read (`D4`, staff side).

| The agent does | Request | Returns |
|---|---|---|
| opens sign-in | `GET /v1/demo/agents` | roster (DEMO) |
| signs in | `POST /v1/agent/demo-login` | Presence + cookie |
| loads / refreshes | `GET /v1/agent/me` | **Snapshot** |
| presses a status button | `POST /v1/agent/state` | Presence, then re-reads `/me` |
| accepts / declines | `POST /v1/agent/offers/{id}/accept|decline` | **Snapshot** |
| hangs up | `POST /v1/agent/calls/{id}/end` | **Snapshot** |
| saves the wrap-up | `POST /v1/agent/calls/{id}/wrapup` | **Snapshot** |
| attests identity | `POST /v1/agent/calls/{id}/identity` | **Snapshot**, or 409 when locked |
| capture: start / keys / backspace / stop / discard / lookup | `POST /v1/agent/…` | Capture |
| signs out | `POST /v1/agent/logout` | 204 |
| places a test call | `POST /v1/demo/calls` | call id (DEMO) |
| — always open — | `WS /v1/agent/ws` | §3 |

**Two server endpoints no client calls:** `POST /v1/agent/captures/{id}/label` and
`GET /v1/agent/queues`. Queues ride along in the Snapshot, and labelling a capture was
never wired to a control. Routed, tested, unused — worth knowing before assuming they are
load-bearing.

---

## 5. One call, end to end

```
 01  sign in            → POST /agent/demo-login      AVAILABLE + NOT_READY, cookie set
 02  socket opens       → hello {last_seq: 0}         replay gap, then a snapshot
 03  press พร้อมรับสาย   → POST /agent/state           intent READY → offerable
 04  (nothing)          ← push: offer (seq n)         the matcher chose this desk
 05  doorbell           → GET /agent/me               offer card + gated brief preview
 06  press Accept       → POST /offers/{id}/accept    ON_CALL, answered_at stamped
 07  (renders)          ← Snapshot with gated BriefOut  at L1 the policy block is ABSENT
 08  capture digits     ← push: capture               the only patched message type
 09  attest identity    → POST /calls/{id}/identity   L3 → brief v2, re-render not re-fetch
 10  hang up            → POST /calls/{id}/end        WRAP_UP · ACW clock starts HERE
 11  save the wrap-up   → POST /calls/{id}/wrapup     CLOSED — the record, not the ACW
 12  declare next state → POST /agent/state           ACW ends — ANY state ends it
```

Steps 11 and 12 are separate on purpose (`D45`). *Save & Ready* is one button firing both
requests, because either can legitimately happen without the other.

Note what step 05 gained in `D69`: the card now says what the call is **about**, not only
why it was routed here. The preview is built from the same gated `BriefOut` the panel
renders, so it cannot disclose more than the assurance level permits.

---

## 6. The lapses this audit found

Written up because the *pattern* repeats: **every one was the client holding, deriving, or
guessing something the server already knew.**

| Found | Was | Now |
|---|---|---|
| Saving the wrap-up showed no confirmation at all | the badge keyed on `active_call_session_id`, which goes `null` the instant saving closes the record — so the lookup was always `has("")` | `wrapup_saved` + `wrapup_call_session_id` are server fields (`D68`) |
| Every timer trusted the laptop's clock | `Date.now() − server_iso`, mixing two clocks; `server_time` was in the payload and read by nothing | a `server − browser` skew is applied to every timer (`D68`) |
| The client second-guessed a config threshold | `long_acw \|\| acwSeconds >= 45`, duplicating `acw_long_after_s = 45.0` | the client half is deleted (`D68`) |
| The queue strip listed all nine queues identically | a health agent watched motor and life fill up with no way to tell which were theirs | each queue carries `mine`; the strip defaults to the agent's own (`D70`) |
| An unanswered offer stranded the agent forever | **nothing called `expire_offers`, `tick`, or `presence.sweep`** | a sweeper runs them every second (`B7`) |
| `previousState` | written on every state change, read by nothing | deleted |

The two design notes that are *not* bugs yet, recorded so they are measured rather than
discovered:

- **Every push costs a full snapshot.** Three of four socket messages call
  `quietRefresh()`, so N pushes are N complete `GET /me` round trips. Correct and simple at
  one agent; a thundering-herd shape at fifteen. The payload already carries everything
  needed to patch in place.
- **The whole tree re-renders 4×/second.** `useSecondTicker` lives in `App`, so every panel
  re-renders while an agent is signed in even with no call and no timer on screen. It is
  also why the old `busy` flicker was so visible. The fix, if it ever matters, is moving
  the ticker into the two components that display a timer.

---

## 7. What holds

Worth stating as plainly as the faults:

- **The disclosure gate is structural.** `BriefOut` has no field for a policy number until
  assurance permits one, so there is nowhere for a leak to hide — the client could not
  render what it never receives (`D53`, `B5`).
- **No identity is asserted by the client.** No request carries an `agent_id`.
- **Permission is never computed on this side.** `offerable` and `declarable` arrive as
  answers, so the screen and the matcher cannot disagree about who is available.
- **The socket is not authoritative.** Drop it and offers still exist, still time out.
- **One writer for the digits.** The optimistic append that invented a digit is gone.

---

## Changes since this was written

_Append here rather than editing above._
