# Krungsri ReadyCall — full system

An AI context layer for insurance service calls: it turns hold time into preparation time.

1. **Context-Aware Calling** — tapping *Contact* on a plan inside the logged-in app carries verified
   identity, the selected product, active policies and recent activity into the call.
2. **AI Pre-Call Intake** — while queued, the customer can describe their issue; it's recorded,
   transcribed (Thai), summarised and structured into a case brief before they reach the front.
3. **A ready agent screen** — who, which policy, what they want, what to say, what to do — plus the
   routing rationale and a calibrated confidence signal.

Built for the Krungsri Universe × KMITL Hackathon ("Reimagine Insurance Brokerage") by team
**HACK VIDVA**. This repo is the **full system**; the stage demo is a separate project in
`../DemoProject/`.

> **Status: planning.** No code yet. Start with [`docs/NEXT_SESSION.md`](docs/NEXT_SESSION.md),
> then [`docs/PLAN.md`](docs/PLAN.md).

## Docs

| File | Contains |
|---|---|
| [`docs/NEXT_SESSION.md`](docs/NEXT_SESSION.md) | Live state, next steps, open questions, landmines |
| [`docs/PLAN.md`](docs/PLAN.md) | Build phases P0–P8, exit criteria, risks, team tracks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, call lifecycle, data flows, latency budget, degradation |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Both databases and the bank-data swap strategy |
| [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) | Ports & adapters, models, libraries, config |
| [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) | Stack, folder structure, feature status, constraints |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why it's built this way (D1…) |
| [`docs/BUG_HISTORY.md`](docs/BUG_HISTORY.md) | Solved bugs (B1…) |

## Credits

Thai speech recognition uses **Thonburian Whisper** by Looloo Technology and the Biomedical and Data
Lab, Mahidol University — <https://github.com/biodatlab/thonburian-whisper> (ICNLSP 2024), fine-tuned
from OpenAI Whisper. Voice activity detection uses **Silero VAD**.
