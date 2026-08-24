# P2b — the agent workstation

_Written 2026-08-24. A snapshot, not a specification — see "changes since" at the bottom._

P2a decided *which agent should take a call*. P2b is where that decision reaches a person:
a desk that rings, a human who accepts, a screen that already knows everything, and the
minutes afterwards that nobody usually measures honestly.

It is also the phase where two real security-shaped bugs turned up, both found by running
the thing rather than reading it. They are the most useful part of this document.

---

## 1. Two axes, and who is allowed to write each

Every agent has two states at once, and conflating them is the classic contact-centre
modelling error.

```
system_state   set by the PLATFORM   offline · available · offering · on_call · after_call_work
agent_intent   set by the PERSON     ready · break · lunch · training · admin · last_call · draining
```

`system_state` says *what the call platform has given you to do*. `agent_intent` says
*what you say you are doing*. Availability is neither — it is derived:

```python
offerable = system_state is AVAILABLE and agent_intent is READY
```

Keeping them apart makes a whole class of awkward cases evaporate. An agent who finishes a
call and goes to lunch: `system_state` returns to `AVAILABLE` (the platform has no work for
them) while `agent_intent` becomes `LUNCH` (so nothing is offered). No third state, no
special case, and the metrics stay truthful — they are available-to-the-platform and
unavailable-to-callers, which is exactly what happened.

### The narrow exception, and why it is logged

The platform writes the *person's* axis in exactly two places (`D51`):

| when | why | recorded as |
|---|---|---|
| sign-in | they have not declared anything yet | `set_by=platform`, `reason=signed_in` |
| a missed offer (RONA) | an empty desk must not black-hole the queue | `set_by=platform`, `reason=rona_missed_offer` |

Both set `NOT_READY`, a value an agent may **not** choose for themselves — because a person
saying "I'm not ready" is really saying break, lunch, training or admin, and collapsing
those throws away the only thing the state log is good for.

`set_by` earns its column because *"they chose break"* and *"we stopped offering because
nobody picked up"* are very different facts about the same person, and a supervisor looking
at a shift has to be able to tell them apart.

**Signing in does not make you ready.** You open the tab, and the workstation shows a
prominent Ready button. Starting an agent as READY hands a call to someone who just sat
down and is making coffee, and the caller pays for that with a full offer timeout.

## 2. The offer handshake, and four outcomes rather than two

```
AVAILABLE ──match──► OFFERING ──Accept──► ON_CALL ──disconnect──► AFTER_CALL_WORK
                        │                                              │
                        ├─ Decline  → AVAILABLE, still READY           └─ ends only when the
                        ├─ Timeout  → AVAILABLE, NOT_READY (RONA)          agent declares
                        └─ Cancel   → AVAILABLE, still READY
```

Declining and timing out look similar and are not. **A decline is a person telling us
something; a timeout is the absence of a person.** Only the second may take an agent out of
rotation — conflating them either punishes an honest agent who said "not me, I don't speak
enough English", or lets an empty desk quietly absorb the queue.

A cancel (the caller hung up while it rang) blames nobody at all.

### The bit that is easy to leave out

**Both rejections exclude that agent from re-matching that call** (`D52`). Without it the
matcher re-solves a millisecond later, reaches the same optimum, and offers the same caller
to the same desk — forever, with every individual decision defensible.

The exclusion is a **hard filter**, not a penalty, for the same reason skill is (`D22`): a
penalty big enough to work is a hard filter with extra steps, and — more importantly — the
filter names itself. `already_offered` is the answer to "why is this caller still waiting",
and a score cannot say it.

## 3. After-call work, which is where most systems lie

`D45` is the decision, and P2b is where it becomes code. Three rules, each of which looks
like it could be tidied away, and each tidying is a bug with a customer attached:

1. **The clock starts at the media disconnect**, not at any form action. It measures the
   agent's real remaining work, including the other systems we do not own — which is what
   workforce planning actually wants, and what makes "the AI drafts the wrap-up, so ACW
   shrinks" a claim we can defend.

2. **Any declared intent ends it.** Ready, Break, Lunch, Training, Admin. An agent who
   saves the record and goes to lunch has finished their after-call work; they are simply
   not available. Ending it only on *Ready* would show them in wrap-up for an hour.

3. **Nothing ends it by itself.** No timer, no auto-save, no auto-ready. The thresholds in
   config are *visibility* only: past `acw_long_after_s` the agent sees their own wrap-up
   running long, past `acw_supervisor_alert_after_s` a supervisor does. They write nothing.

Saving the wrap-up form closes the **call record**. It does not end after-call work. Those
are two different statements — "I finished your form" and "I am done with this call" — and
only the second is about availability. The workstation offers **Save & Ready** so the common
case is still one click, but that button fires two requests, deliberately, because either
may happen alone.

The two settings were renamed while implementing this. `acw_timer_s` and `acw_max_s` read
like expiry deadlines and were a standing invitation to implement one.

## 4. The socket, and why it is never authoritative

One WebSocket per signed-in workstation. Three properties matter more than the transport:

**Every message is sequenced, per agent.** An offer that arrives twice is a call ringing at
a desk that already declined it.

**Reconnect replays rather than resyncs.** The client sends the last sequence it applied;
the server replays exactly the gap from a bounded outbox. The alternative — "reconnect, then
re-fetch everything" — loses precisely the events that happened during the gap, which is
when the interesting ones happen.

**The message is written to the outbox *before* delivery is attempted.** That is the whole
design in one line: a message that failed to send is exactly the one a reconnecting client
needs replayed. A dead socket is not an error — it is a closed laptop — and the heartbeat
sweep, not a failed write, is what decides an agent has gone.

Nothing on the socket is call-state-bearing. If it is down when an offer is made, the offer
still exists, still times out, still re-matches. The workstation is a *view* of the system,
never the system, which is what makes `D32` safe: a browser is allowed to be flaky.

## 5. Queue hours, and a closed queue as an answer

`config/queue_hours.yaml` holds named schedules and a Thai holiday calendar. Three exist:

| schedule | hours | holidays |
|---|---|---|
| `always` | 24/7 | **ignored** — a crash does not check the calendar |
| `extended` | 07:00–22:00 daily | ignored — hospitals admit on Songkran |
| `business` | Mon–Fri 08:30–18:00, Sat 09:00–16:00 | observed |

A closed queue is a **routing outcome**, not an error, so `OpenState` carries a *next open
time* and a reason. The caller gets a briefed callback (`D25`) rather than a dead end.

The cross-check test — every queue names a schedule that exists — found `q_health_ipd`
pointing at an `extended` schedule nobody had written, within a minute of being added.

Two Thai/Windows landmines came with this:

- `zoneinfo` has **no tz database on Windows**. `ZoneInfo("Asia/Bangkok")` raises there and
  only there — green on Linux CI, broken on the demo laptop. `tzdata` is now a declared
  dependency, and the error message says so.
- `ManualClock()` defaults to 09:00 UTC on **1 January** — New Year's Day in Bangkok. Every
  `business` queue is shut under a default test clock, and a placed call comes back
  `queue_closed:holiday` instead of an offer.

## 6. The two bugs

Both were found by driving the UI. Neither would have been caught by any test that existed,
and one of them is the most serious thing this project has shipped so far.

### 6.1 The brief leaked everything it was hiding (`B5`)

The screen was right. At `l1_probable` the policy row said *ปกปิดจนกว่าจะยืนยันตัวตน*, the
identity panel said disclosure was locked, and the Thai summary carefully said "มีกรมธรรม์ที่
เกี่ยวข้อง (ยังไม่ยืนยัน)" instead of a number.

The bytes said otherwise:

```
assurance: l1_probable      may_disclose_policy_details: False

  'HL-2024-000811'   present in payload: True
  'sum_insured'      present in payload: True
  'coverages'        present in payload: True
  'dob'              present in payload: True
```

`render_brief` returned `CaseBrief.model_dump()`. `CaseBrief` embeds the frozen
`ContextSnapshot`, which embeds the whole `Customer360`. `BriefBuilder` gated the rendered
Thai lines — exactly what it was asked to do — and nothing gated the object graph hanging
off the side of them.

**Every safeguard around it was working.** The ladder, the builder, the flag in the payload,
the UI. The leak lived in the gap between "the brief is gated" and "the brief *object* is
gated", and every test asserted the first.

`D42` predicted it in as many words — *"Sending the full brief and hiding fields in React
would put someone's coverage one devtools panel away"* — and the implementation did the
forbidden thing anyway, because the forbidden thing is what `model_dump()` does by default.

The fix is `D53`: a wire DTO that has **no field** for a policy number until the level
permits one. The rule stopped being a promise and became a property of the type. And the
test had to search the raw bytes, because an assertion about rendered text cannot see a
field the renderer never mentions.

### 6.2 The keypad display invented a digit

Typing `2024000811` showed `20240008111`, while the server held ten digits. The client
appended each digit optimistically *and* took the server's authoritative push, and the two
compounded whenever the push won the race.

Optimism bought nothing here — the round trip is a few milliseconds — and **an agent reads
those digits off the screen and acts on them**. A digit that is not there is far worse than
a hundred milliseconds of latency. One writer now: the server.

### 6.3 And a smaller one

The offer card claimed `normal` urgency and a zero wait for every call, because it read them
off `CallSession`, which has neither field, behind `getattr(..., default)`. So the card said
**ปกติ · รอมาแล้ว 00:00** directly above a rationale reading *"รอเกิน SLA (95s / 45s) ·
เรื่องเร่งด่วน"*. A card that contradicts its own reason is worse than one with no reason.

## 7. What the screen shows now

Verified in a browser, driving it by hand:

```
สรุปก่อนคุย · v2 · สร้างใน 0.2 ms
  เรื่อง        แจ้งเข้ารักษาผู้ป่วยใน / ตรวจสอบค่าห้อง   dtmf
  ลูกค้า        ภัทธีรา เสรีวัฒนชัย
  กรมธรรม์      HL-2024-000811 · active · ทุนประกัน 1,000,000 บาท
  ความคุ้มครอง  ค่าห้องและค่าอาหาร · 3,000 THB
                วงเงินผู้ป่วยในต่อปี · 1,000,000 THB
  สิ่งที่ควรทำ   1. สอบถามโรงพยาบาลและวันที่เข้ารับการรักษา
                2. ตรวจสอบสิทธิ์ผู้ป่วยในและวงเงินค่าห้อง
  ประโยคเปิด     สวัสดีค่ะ คุณภัทธีรา ทราบว่าติดต่อเรื่อง…
```

That is brief **v2** — v1 was the same screen at L1, with the policy row reading
*ปกปิดจนกว่าจะยืนยันตัวตน* and no coverage table at all. Between them the agent pressed one
button and named the challenge they used. **Promotion is a re-render, not a re-fetch**: the
snapshot already held every figure, and it cost 0.2 ms and no bank-core round trip.

## 8. How to run it

```bash
cd apps/workstation && npm install && npm run build     # once; node is not needed at run time
uv run python -m readycall.entrypoints.api              # then http://127.0.0.1:8000/workstation
```

Sign in as any roster agent, press **พร้อมรับสาย**, then **+ สายทดสอบ** to put a caller on
the line. The test call picks an intent the signed-in agent can actually handle, and sets
`ignore_hours` (`D54`) so a 2 a.m. rehearsal still works.

## Changes since this was written

_Append here rather than editing above._

### 2026-08-24 — a review pass that mostly re-read the docs (`B6`, `D55`–`D60`)

Six reported faults. Three of them were decisions that **already existed** and had been
implemented from a summary rather than from the source. Recording that plainly, because the
pattern is the lesson: `diagrams/src/identity_promotion.mmd` is handwritten, marked *checked
against D42*, and contains both the open-question rule and the three-parallel-paths shape.
Reading it would have prevented half of this.

- **The opening line must not name an unverified caller (`D55`).** Section 7 above shows
  `สวัสดีค่ะ คุณภัทธีรา…` at L3 — correct. At **L1** the same builder produced the same
  sentence, which is not. It now reads *"สวัสดีค่ะ ยินดีให้บริการเรื่อง… ขอทราบชื่อผู้ติดต่อ
  ด้วยค่ะ"*. Naming first tells whoever holds the phone that the number is theirs, **and** a
  leading question is weaker verification: "is this Khun X?" can be answered yes by anybody;
  "may I have your name?" has to be produced.
- **Third party is its own path, one click (`D42`, re-read).** It required pressing
  *Confirmed* afterwards, which is the exact binary the third button exists to avoid — the
  audit log would have said the policyholder was verified. It now takes a **name and a
  relationship**, both required (`D57`), and commits on its own.
- **`other` is a real challenge (`D57`).** `D44` said the free-text mode should be built
  *first*; it was built last. Verification does not fit a four-item dropdown.
- **The agent sees the digits (`D58`).** `D44` says masked *"in transcripts and logs"* —
  which the first implementation read as "everywhere", hiding the caller's own keystrokes
  from the person who asked for them. `mask()` is for the log line; `digits` is for the panel.
- **`agent_intent` is a standing instruction (`D59`).** This is the section 1 model, stated
  properly. It is never deselected; mid-call only `READY`/`LAST_CALL`/`DRAINING` can change;
  `LAST_CALL` is **spent** when that call ends. `awaiting_declaration` stops the screen
  showing the pre-call instruction as if it were current — which is why "I'm marked
  พร้อมรับสาย but I get no calls" happened. `intent_reason` separates the three routes into
  `not_ready`, so the wrap-up panel stops offering **Save & Ready** to someone who just said
  they are finishing.
- **An attestation locks the control (`D60`)**, with an explicit amend that appends a
  correction rather than overwriting.

### And three plain UI bugs from the same pass

- **The ACW timer was a prop of the wrap-up form**, so saving the form unmounted it —
  removing the only visible clock while the agent was still, correctly, in after-call work.
  It now has its own bar in the shell, which is also the honest place for it: after-call work
  is not a property of the form.
- **Both timers counted from component mount**, so a refresh mid-call restarted the call at
  `00:00`. They are now `now − server_timestamp` (`call_answered_at`, `acw_since`). Verified:
  `01:02` after a refresh, not zero.
- **The flicker was not React.** Every socket push ran through the same helper as user
  actions, which sets a `busy` flag that disables every control — so during an active call
  the whole UI greyed out and came back about once a second. Background refreshes now use a
  path that touches no flag. Measured after: **0 disabled-state changes across 3 s idle.**

### Where the recommended actions come from (`D56`)

Asked directly, and worth having in one place: `intents.yaml` gives each intent a **playbook
name** → the playbook is an ordered list of `(Thai text, required assurance)` → filtered
against the caller's level → **below L2 a verify-identity step is inserted at position 0**.
It lives in `_PLAYBOOKS` in `services/brief/builder.py` today; `config/playbooks/` is the P4
destination and **does not exist yet**, despite appearing in the folder map. Hand-written,
static, no AI — because this is the version that must never fail, and even at P4 the model
may only rank and select from the playbook, never write a step (`D16`).
