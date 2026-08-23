# The agent workstation

React 18 + TypeScript + Vite. `D32`: one browser tab, and the call happens inside it.

```bash
npm install      # once
npm run build    # produces dist/, which the API mounts at /workstation
npm run dev      # optional: HMR on :5173, proxying /v1 to the API on :8000
```

Then `uv run python -m readycall.entrypoints.api` and open
<http://127.0.0.1:8000/workstation>.

**The API does not need node.** `dist/` is mounted only if it exists, so the API and the
customer simulator run with Python alone; the workstation appears once somebody has built
it. Node is needed **once**, to build — never at run time.

> ⚠️ `dist/` is gitignored, so a fresh clone has no workstation until `npm run build` has
> been run there. On a machine with no internet, `npm install` is the step that fails.
> Build before you travel. (Open question `Q17`: whether to commit `dist/` for exactly
> this reason.)

## What is real and what is stubbed

| Area | State |
|---|---|
| Offer / accept / decline / RONA | **Real** — the server's handshake (`D33`) |
| Presence, the two axes, after-call work | **Real** (`D45`, `D51`) |
| Identity control, three outcomes | **Real** (`D42`) |
| Keypad capture + lookups | **Real** (`D44`); the on-screen pad stands in for DTMF |
| The brief and its disclosure gate | **Real**, gated server-side (`D42`) |
| Mute / hold / call timer | **Stubbed** — UI state only until WebRTC lands at P5 |
| Audio | **Not here yet** — SIP over WSS to Asterisk is P5 |

## Layout

```
src/
  api.ts        typed fetch wrapper; never sends an agent_id (the cookie carries it)
  useSocket.ts  the WebSocket: sequence tracking, replay-on-reconnect, backoff
  panels.tsx    presentational panels — they render what the server sent, nothing more
  App.tsx       the shell: state, wiring, the stubbed call bar
  styles.css    dark, dense, readable across a room
```

The rule that matters: **no component decides what may be displayed.** The brief arrives
already gated for the current assurance level, so a locked field is *absent from the
payload*. If you find yourself writing `assurance >= L2 ? show : hide` here, the gate has
moved to the wrong side of the wire — and that exact bug shipped once (`B5`).
