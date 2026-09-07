# Diagrams — the whole system, visually

_Last updated: 2026-09-07._

**69 diagrams** covering every part of Krungsri ReadyCall. Written to be read in order the
first time, and dipped into afterwards.

---

## Read them in this order

| # | Page | What you get out of it |
|---|---|---|
| 1 | **[Start here](01_start_here.md)** | What the system *is*, who talks to it, and the one structural idea (ports & adapters) that everything else rests on |
| 2 | **[The call, end to end](02_the_call.md)** | The lifecycle every call follows — as a state machine, as two human journeys, and as five detailed sequence diagrams |
| 3 | **[Identity & disclosure](03_identity.md)** | How we work out who is calling, why that is a ladder rather than a yes/no, and what unlocks policy details |
| 4 | **[Routing](04_routing.md)** | How a caller reaches the right specialist — the keypad menu, the intent taxonomy, the published numbers |
| 5 | **[Context & the brief](05_context_and_brief.md)** | How the agent's screen gets filled in before the phone is answered, and what happens when things break |
| 6 | **[Agents & matching](06_agents_and_matching.md)** | Who gets which call and why, the agent's two-axis state, and the workstation itself |
| 7 | **[Voice & AI](07_voice_and_ai.md)** | Speech to text, the LLM layer, and the intake seam that becomes a full AI caller later |
| 8 | **[Data & events](08_data_and_events.md)** | Every domain object, the two stores, and the event backbone |
| 9 | **[The project](09_the_project.md)** | Phases, what is built vs faked, how it is tested, and a map of every decision |
| 10 | **[Sessions & tokens](10_sessions_and_tokens.md)** | How the system knows who is calling when the client never sends a customer id — and why that is hard to attack |
| 11 | **[Persistence](11_persistence.md)** | The half with no screen: what survives a restart, what deliberately does not, and why half the state is derived rather than stored |
| 12 | **[The line](12_the_menu.md)** | What the caller actually hears — where every sentence comes from, why a menu is not one clip, and what happens when they press the wrong key |
| 13 | **[The broker, and the customer's screen](13_broker_and_assist.md)** | Why a broker is not a small insurer and what that changed, where the AI summary does nothing, and how a phone call gets bound to the customer's phone so the broker can push a form onto it |

Short on time? **[Start here](01_start_here.md)** then **[The call](02_the_call.md)** is about
fifteen minutes and covers most of it.

**[Persistence](11_persistence.md)** and **[The line](12_the_menu.md)** are the two parts
with no screen at all — one runs when nobody is watching, the other is heard only by the
caller. Both are drawn rather than described for that reason.

---

## Why some of these are generated, and why that matters

Roughly a quarter of the diagrams are **generated directly from the running system** rather
than drawn by hand:

| Diagram | Built by reading |
|---|---|
| `state_machine`, `state_machine_readable` | the actual `TRANSITIONS` table |
| `menu_tree`, `routing_chain`, `dids`, `product_lines` | the loaded, validated `DomainPack` |
| `domain_models` | the pydantic models and their real field names |
| `events` | the event registry |
| `ports_adapters` | the packages present on disk |
| `assurance_ladder`, `agent_state`, `degradation_ladder` | the enums |
| `db_schema` | `Base.metadata` — every table, key, index and column count |
| `voice_prompts` | the loaded `PromptPack` joined to the menus and numbers that name each id |

A hand-drawn architecture diagram is a comment, and comments rot — six weeks from now it
quietly describes a system that no longer exists. These cannot: if someone adds a menu
option, a state, or an adapter, the diagram changes when it is regenerated. Every generated
file carries a `%% GENERATED` banner naming its source of truth.

The rest are hand-drawn because they describe *intent* rather than structure — sequence
flows, the reasoning behind a decision, the roadmap. Those carry a `%% HANDWRITTEN` banner
naming the doc or module they were checked against.

**Hand-drawn is where rot collects, and it did.** A sweep on 2026-08-26 found several
teaching decisions that had since been *reversed*: `brief_gating` still gated what the agent
could SEE, which `D74` overturned, and `identity_promotion` still said a verified third party
stayed locked, which `D65` overturned. Others had simply been overtaken — `real_vs_fake`
listed five things as unbuilt that had shipped, `decision_map` counted 43 decisions when there
were 81, and `test_layers` was three phases behind on the suite size. All are corrected.

`decision_map` drifted the same way again and was extended on 2026-09-06: it stopped at `D81`
while `DECISIONS.md` had reached `D109`. Extending a mindmap is a redraw rather than an edit,
which is exactly why it lags — so the rule is now written into its own banner: a reversed
decision gets a leaf naming what replaced it, in the commit that reverses it.

The lesson for anyone editing these: **the banner is a claim.** If you change a decision,
`grep` the banner lines for its number — `grep -n "D74" src/*.mmd` — and fix every diagram
that cites it in the same commit. A generated diagram cannot lie about the system, but a
hand-drawn one can, and it is more convincing than prose while doing it.

---

## Regenerating

```bash
uv run python scripts/gen_diagrams.py      # rebuild the derived .mmd sources
uv run python scripts/render_diagrams.py   # render every .mmd to .svg
```

`render_diagrams.py` needs the mermaid CLI:

```bash
npm install -g @mermaid-js/mermaid-cli
```

It drives a headless browser. If puppeteer has no bundled Chromium it will find an
installed Chrome or Edge automatically; override with `PUPPETEER_EXECUTABLE_PATH`, or point
at a specific CLI with `MMDC=/path/to/mmdc`.

To check whether any SVG has fallen behind its source:

```bash
uv run python scripts/render_diagrams.py --check
```

This compares **content hashes**, recorded in `.render-manifest.json` when each SVG is
rendered — not modification times. Re-running `gen_diagrams.py` rewrites every source file,
so an mtime check reported all 45 as stale when nothing had changed, and a check that cries
wolf gets ignored.

Render a single diagram while iterating:

```bash
uv run python scripts/render_diagrams.py brief_gating
```

---

## Layout

```
docs/diagrams/
├─ README.md              ← you are here
├─ 01_start_here.md …     ← the twelve explanation pages
├─ *.svg                  ← 69 rendered diagrams (committed, so no tooling is needed to read them)
├─ .render-manifest.json  ← source hash per diagram, so --check compares content not mtimes
└─ src/*.mmd              ← mermaid sources; GENERATED ones say so in the header
```

`.svg` files are committed deliberately. Anyone should be able to read this documentation
by opening a file, with no npm, no browser automation, and no build step.

---

## Two gotchas if you edit the sources

- **`call` is a reserved word in mermaid.** A flowchart `classDef call` or a gantt task
  beginning with "call" is parsed as the click/callback syntax and fails. Both bit us; both
  are worked around by renaming.
- **Thai renders fine, but needs the font stack.** The shared config in
  `render_diagrams.py` sets `Segoe UI, Noto Sans Thai, Tahoma` — dropping that gives tofu
  boxes for every Thai label.
