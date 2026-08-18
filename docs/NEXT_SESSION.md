# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-18 (second review)._

---

## Where things stand right now

**Design phase, second pass complete. No code exists yet.** `src/` is still the `uv init` placeholder.

The team is **waiting on the hackathon's "teams that passed" announcement**; the pitch has been
submitted (`../Krungsri.pdf`). Building the full-system foundation is useful either way; demo scoping
happens later, separately, in `../DemoProject/`.

## What changed in this session (design review with the user)

The first design assumed the in-app tap was the entry point and treated routing as a simple weighted
score, and it described the agent screen as if the call happened on a separate phone. All three
were reworked, plus a batch of product decisions. New entries `D19`–`D33`:

1. **A plain phone call is the base case** (`D19`). A motor claim dialled off the windscreen sticker
   has no app, no intent, maybe no known caller — and that is the *most* compelling scenario. Entry
   channels are now in-app, **product-line DID** (a printed number *is* an intent signal), hotline,
   callback, transfer. Everything works with the enrichments absent.
2. **Identity is an assurance ladder L0–L3** (`D20`). ANI is never proof. Context is fetched as soon
   as a customer is guessed; what is *displayed* is gated by level, and the level is a badge on the
   agent screen. No policy numbers below L2.
3. **Matching reworked** (`D22`, `D23`): one waiting pool, **global optimal assignment (Hungarian)**
   over `fit × (1 + urgency)`, urgency growing with wait/SLA/priority/situational danger, fit ignored
   entirely past a hard wait ceiling, **guarded deferral** using call-progress prediction, and three
   explicit anti-hot-spot mechanisms. Fit is intent-confidence-weighted so a half-heard sentence
   nudges rather than yanks.
4. **The offer window is the intake grace period** (`D21`) — nobody is cut off mid-sentence and
   nobody waits longer.
5. **IVR prompts are pre-rendered TTS** (`D24`) — edit Thai text in YAML, re-render, zero call-time
   latency, works offline. Live TTS only for the future conversational intake.
6. **Both call legs transcribed live** (`D26`) — exact speaker labels with no diarisation; feeds
   wrap-up quality, call-progress estimation and future live assist.
7. **After-hours voicemail runs the full intake pipeline** into a pre-briefed `callback_task` (`D25`).
8. **Two LLM adapters from day one** (`D29`): Anthropic + a single `OpenAiCompatibleAdapter` that
   covers Typhoon API, OpenAI, vLLM and Ollama by base URL. Plus a comparison harness.
9. **Typhoon ASR is a first-class STT alternative** to benchmark against Thonburian (`D30`).
10. **Ratings from both sides** (`D27`), **generic-core vs domain-pack separation** for reuse (`D28`),
    and a full written-out **case against LLM frameworks** with two named triggers to revisit (`D31`).

11. **The agent workstation IS the phone** (`D32`) — corrects a genuine misread. It is not an info
    screen beside a telephone: it is a full contact-centre workstation in one browser tab, with the
    **softphone inside it** (SIP.js over WSS to Asterisk, WebRTC/Opus through the agent's headset).
    Softphone + brief + status control are required from day one; history/outbound/wallboard later.
12. **Offer/accept handshake with after-call work as a real state** (`D33`):
    `AVAILABLE → OFFERING → ON_CALL → AFTER_CALL_WORK → AVAILABLE`, offer timeout + RONA, ACW timer
    with a Done button, and `manual_accept` / `auto_accept` both supported by config. On Accept,
    Asterisk **bridges** an already-connected customer channel — no dial-out delay.

Also settled: insurance means **all lines** (motor/health/life/travel/PA), 20 mock agents across 6
teams with 3 live seats, agent workstation = React, customer side = responsive web simulator hitting
the same public API the real app would. **Telephony = Asterisk, confirmed** — chosen partly because a
real SIP trunk / DID can be attached later without changing the adapter, so the same code that runs
the demo runs against a real number.

## Next steps (in order)

1. **Read `PLAN.md`.** Then execute **P0 — Foundations**: package rename `fullproject → readycall`,
   folder skeleton, config, domain models, all ports, fake/null adapters, DB schemas + Alembic, mock
   bank-core generator + personas + scenarios (incl. the roadside motor claim), contract-test harness,
   scenario runner, compose file, CI.
2. **P1 — Context-Aware Calling** (no audio): both the app path *and* the cold-call path.
3. Then P2 (matching + workstation), P3 (voice/IVR/intake), P4 (analysis/brief), P5 (Asterisk +
   the real in-browser softphone).

Do **not** start the demo project until P0–P1 exist — the demo *selects from* the full design.

## Still open (not blocking P0)

| # | Question | Current default |
|---|---|---|
| Q7 | **The intent taxonomy** — the closed list of "reasons people call", per product line. Everything keys off it: matching skills, required slots, playbooks, default urgency, and the golden-set labels. A straw-man is drafted in `ARCHITECTURE.md` §13.1 | **Needs the team's domain input** — it is a product decision more than a technical one, and the main input P4 needs. Next action: review the straw-man and correct it |
| Q8 | Which Typhoon model ids / licence / pricing are current | **Verify against live docs when writing the adapter** — do not trust memory or these docs |
| Q9 | ACW timer default (45 s) and offer timeout (20 s) | Guesses. Tune against how the team thinks a real agent works |

Resolved: telephony = **Asterisk** (Q1) · GPU = RTX 3050 laptop (Q2) · LLM = both Claude and Typhoon,
compared (Q3) · agent workstation = React, softphone included (Q4) · customer side = web simulator
(Q5) · nothing known about their data yet, so `FixtureFileProvider` is built first (Q6).

## Things to be careful about (live landmines)

- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`) — read only.
- **Never let AI delay the call.** Leaving the queue is agent-availability only (`D12`).
- **Never let the model produce coverage numbers, eligibility, or prices** — data only (`D16`).
- **Never trust a client-supplied `customer_id`** — identity comes from the session (`D4`), and an ANI
  match is *probable*, not verified (`D20`).
- **Never hardcode an insurance literal in `services/`** — it goes in `config/` (`D28`).
- **Never `torch.hub.load` at call time** — bundle Silero VAD (`D9`).
- **Never run a local LLM and Whisper on the same 4–6 GB GPU.**
- **Never depend on the venue's network or their API during a stage demo.**
- **The agent takes the call in the browser** — never design as if there were a separate phone (`D32`).
- **Browsers need a secure context for microphone access**, and SIP-over-WSS needs a cert Asterisk
  serves. `localhost` is fine for one machine; other machines on the LAN need `mkcert`. P5 landmine.
- **Commit in the right repo** — `FullProject/` and `DemoProject/` are separate; root `CLAUDE.md` is
  in neither.
- Windows: paths have spaces (quote them); the console is cp1252 (write Thai to UTF-8 files).

## Handy references

- Pitch deck: `../Krungsri.pdf` (agent-screen mock is p.7 — build to that).
- Competition brief: `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf`
  (personas, journey leaks, available data, PDPA, in/out of scope, judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`
  (`main.py`, `utils.py::perform_vad`) and `…/ProjectCode/thonburian-whisper/README.md` (model/WER table).
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
