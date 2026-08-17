# NEXT_SESSION

_The live working state. READ THIS FIRST every session. Keep it short and current._
_Last updated: 2026-08-17._

---

## Where things stand right now

**Planning session complete. No code exists yet.** The full system (`FullProject/`) has been designed
end to end and written up in this `docs/` folder. `src/` is still the `uv init` placeholder.

The team is currently **waiting on the hackathon's "teams that passed" announcement** — the pitch and
application have been submitted (`../Krungsri.pdf`). Building the full-system foundation now is
useful either way; the demo scoping happens later, in `../DemoProject/`.

## What was decided this session

The shape of the whole thing — see `DECISIONS.md` `D1`–`D18`. The five that matter most:

1. **Ports & adapters for everything external** (`D3`) — so hackathon-day data/telephony/model swaps
   are an env-var change, backed by contract tests every adapter must pass.
2. **The bank's data is read-only; our output lives in our own DB** (`D5`) — matches what a hackathon
   will actually hand us, and matches the pitch's "we don't touch the core system" claim.
3. **Pre-call intake is a swappable strategy** (`D10`) — `PassiveRecordIntake` now,
   `ConversationalAgentIntake` (the AI that talks to the customer while they wait) later, with the
   *same* `IntakeResult` and the same events, so nothing downstream changes.
4. **The call is never blocked on AI** (`D12`) — queue pop is driven by agent availability alone;
   every stage has a timeout and a documented degradation rung.
5. **Routing is deterministic and explainable; the LLM only labels intent** (`D8`) — auditable, and
   the brief explicitly forbids decisioning that could become discriminatory.

Also: STT is **re-implemented streaming-first** rather than adapted from the scam project's batch CLI
(`D9`) — same model family, opposite shape. The old code stays read-only reference.

## Next steps (in order)

1. **Read `PLAN.md`.** Then execute **P0 — Foundations**:
   package rename `fullproject → readycall`, folder skeleton, config, domain models, all ports,
   fake/null adapters, DB schemas + Alembic, mock bank-core generator + personas + scenarios,
   contract-test harness, scenario runner, compose file, CI.
2. **P1 — Context-Aware Calling** (no audio) — the first genuinely valuable slice, zero AI risk.
3. Then P2 (routing + agent screen), P3 (intake/STT), P4 (analysis/brief).

Do **not** start the demo project until the full system's P0–P1 exist — the demo is meant to *select
from* the full design, not become it.

## Open questions for the user (answer before/while doing P0)

| # | Question | Why it matters | Current assumption |
|---|---|---|---|
| Q1 | **Telephony:** self-hosted Asterisk, cloud CPaaS (Twilio), or WebRTC-only (LiveKit)? | Biggest schedule risk; changes the P5 shape | Asterisk default, simulated adapter until P5, Twilio as escape hatch |
| Q2 | **Is there a GPU** on the dev/demo machine? Which? | Decides Thonburian medium vs distilled vs cloud STT, and the whole latency budget | Assume a modest GPU; benchmark in P3 and fall back to distilled |
| Q3 | **LLM:** Claude API (cloud) or a local Thai model (Typhoon/Ollama)? | Cost, latency, and the "runs inside the bank" story | Claude Sonnet 5 by default; local adapter kept viable |
| Q4 | **Agent desktop:** React SPA, or plain server-rendered + WebSocket? | Team skill fit vs realtime ergonomics | React + Vite |
| Q5 | **Customer side:** real React Native app, or a web "customer simulator"? | The simulator is enough for a demo and far cheaper | Web simulator first; RN only if the demo needs a phone in hand |
| Q6 | Do we know **anything** about the data they'll provide (format, fields, live API vs extract)? | Decides which `CoreDataProvider` adapter to build first | Assume CSV/JSON extracts → `FixtureFileProvider` |

None of these block P0 — every one of them sits behind a port on purpose.

## Things to be careful about (live landmines)

- **Never edit the reference folders** (`scamprojectthing/ProjectCode`, `music-backlog-adder`) — read only.
- **Never let AI delay the call.** Queue pop is agent-availability only (`D12`).
- **Never let the model produce coverage numbers, eligibility, or prices** — data only (`D16`).
- **Never trust a client-supplied `customer_id`** — identity comes from the session (`D4`).
- **Never `torch.hub.load` at call time** (network fetch mid-call) — bundle Silero VAD (`D9`).
- **Never depend on the venue's network or their API during a stage demo** — offline mock always runnable.
- **Commit in the right repo** — `FullProject/` and `DemoProject/` are separate git repos, and
  `CLAUDE.md` at the `krungsri/` root is in neither.
- Windows: paths have spaces (quote them); the console is cp1252 (write Thai to UTF-8 files).

## Handy references

- Pitch deck: `../Krungsri.pdf` (agent-screen mock is p.7 — build to that).
- Competition brief: `../KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf`
  (personas, journey leaks, available data, PDPA, in/out of scope, judging = **I-F-C-U**).
- Thai STT reference (read-only): `…/scamprojectthing/ProjectCode/STT_Thonburian_Whisper/`
  (`main.py`, `utils.py::perform_vad`) and `…/ProjectCode/thonburian-whisper/README.md` (model/WER table).
- Docs style reference (read-only): `…/music-backlog-adder/CLAUDE.md` + `docs/`.
