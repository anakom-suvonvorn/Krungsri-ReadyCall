# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-18._

---

## Where things stand right now

**Design phase, second pass complete. No code exists yet.** `src/` is still the `uv init` placeholder.

The team is **waiting on the hackathon's "teams that passed" announcement**; the pitch has been
submitted (`../Krungsri.pdf`). Building the full-system foundation is useful either way; demo scoping
happens later, separately, in `../DemoProject/`.

## What changed in this session (design review with the user)

The first design assumed the in-app tap was the entry point and treated routing as a simple weighted
score. Both were reworked, plus a batch of product decisions. New entries `D19`–`D31`:

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
4. **The agent's ring time is the intake grace period** (`D21`) — nobody is cut off mid-sentence and
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

Also settled: insurance means **all lines** (motor/health/life/travel/PA), 20 mock agents across 6
teams with 3 live seats, agent desktop = React in the browser, customer side = responsive web
simulator hitting the same public API the real app would.

## Next steps (in order)

1. **Read `PLAN.md`.** Then execute **P0 — Foundations**: package rename `fullproject → readycall`,
   folder skeleton, config, domain models, all ports, fake/null adapters, DB schemas + Alembic, mock
   bank-core generator + personas + scenarios (incl. the roadside motor claim), contract-test harness,
   scenario runner, compose file, CI.
2. **P1 — Context-Aware Calling** (no audio): both the app path *and* the cold-call path.
3. Then P2 (matching + agent desktop), P3 (voice/IVR/intake), P4 (analysis/brief).

Do **not** start the demo project until P0–P1 exist — the demo *selects from* the full design.

## Still open (not blocking P0)

| # | Question | Current default |
|---|---|---|
| Q1 | Telephony: Asterisk / Twilio / LiveKit — **answered with a recommendation**, awaiting confirmation | Simulated → **Asterisk** at P5, with a softphone-on-a-real-phone-over-local-WiFi demo path; Twilio optional second adapter |
| Q6 | Anything known about the data they will provide? | No → assume CSV/JSON extracts, build `FixtureFileProvider` first |
| Q7 | Exact intent taxonomy per product line | Drafted in P4 — **needs the team's domain input**, it is a product decision more than a technical one |
| Q8 | Which Typhoon model ids / licence / pricing are current | **Verify against live docs when writing the adapter** — do not trust memory or these docs |

Resolved this session: GPU = RTX 3050 laptop (Q2) · LLM = both Claude and Typhoon, compared (Q3) ·
agent desktop = React web app (Q4) · customer side = web simulator (Q5).

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
