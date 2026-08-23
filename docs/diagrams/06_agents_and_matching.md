# 6. Agents & matching

_Who gets which call, why, and the one browser tab they work in._

← [Context & the brief](05_context_and_brief.md) · [index](README.md) · next → [Voice & AI](07_voice_and_ai.md)

---

## 6.1 The matching loop

![matching flow](flow_matching.svg)

**One waiting pool, not one queue per agent** (`D22`). Per-agent queues look tidy and behave
badly — a caller stuck behind one busy agent waits while three qualified people sit idle.

The loop runs on a tick *and* on relevant events, so an agent becoming free triggers a match
immediately rather than up to a tick later.

---

## 6.2 How a pairing is scored

![matching scoring](matching_scoring.svg)

Three stages, and the order matters.

**Hard filters first.** Does the agent have the required skill? Can they speak a language
this caller accepts? A failed hard filter is *not a low score* — it is exclusion. Scoring
these as soft preferences is how you end up routing a caller to someone who cannot help
them, just because everything else scored well.

**Fit** answers "how good is this agent *for this call*": skill match and proficiency,
continuity (did they handle this customer before?), historical performance on this intent,
minus a penalty for current load.

**Urgency** answers a different question — "how badly does *this call* need someone": how
long they have waited, SLA risk, customer priority, and situational urgency like being at a
crash scene.

**Urgency multiplies rather than adds**, and that is the design's answer to starvation. Under
pure best-fit, a caller nobody is a great match for waits forever while better-matched
callers overtake them. Multiplying means waiting eventually wins on its own, and a hard wait
ceiling then drops to any-qualified-agent.

**Global, not greedy.** Greedy best-first is locally optimal and globally poor — it hands the
one bilingual agent to the first caller who asks, then strands the caller who genuinely
needed them. The Hungarian algorithm optimises the whole matrix at once.

**Every candidate is persisted** with its sub-scores, the weights version, and the solver
used (`D18`). "Why did I get this call?" has to be answerable — a matching decision nobody
can explain is one nobody will trust enough to leave switched on.

---

## 6.3 An agent has two states at once

![agent state](agent_state.svg)

**Generated from the enums.** The insight is that "agent status" is really two independent
things, and conflating them causes bugs:

- **`system_state`** is what the *platform* knows: offline, available, being offered a call,
  on a call, in after-call work. The agent does not set this; it follows from what is
  happening.
- **`agent_intent`** is what the *person* chose: ready, break, lunch, training, admin, last
  call, draining.

Offerable means **both** — `AVAILABLE` *and* an intent of ready or last-call. Keeping them
separate is what lets "I want to stop after this call" (`LAST_CALL`) work cleanly: the person's
intent changes immediately while the platform state follows the actual call.

`AFTER_CALL_WORK` being a real state is `D33`. And RONA flips the agent out of ready —
otherwise the same unattended desk keeps being offered calls forever.

---

## 6.4 The workstation

![workstation](workstation.svg)

The correction that produced this design: the agent screen is **not an info display next to a
phone**. It is the contact centre. Calls are taken, made and spoken through the browser tab
itself, with the headset plugged into the PC (`D32`).

That has real consequences — the softphone is SIP.js over WSS with WebRTC audio, so the
workstation needs device selection, a self-test, and reconnect handling. It also removes an
entire class of demo risk: no desk phones to configure, no second device, nothing to install.

The three panels answer the three questions an agent has: **who is this, why are they
calling, and what should I say?** The evidence column is the trust layer — provenance, the
live transcript, and the gated policy panel.

The visible stage timeline ("context ready at 0.4s, before the phone rang") is `D18` paying
off on stage. If the system cannot explain why it did something, it is not finished.

---

← [Context & the brief](05_context_and_brief.md) · [index](README.md) · next → [Voice & AI](07_voice_and_ai.md)
