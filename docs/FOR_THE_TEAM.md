# FOR THE TEAM

_Two guides in one file. **Part 1** is git and GitHub from zero, for somebody who has never
used them. **Part 2** is how to change how ReadyCall looks without breaking how it works._
_Last updated: 2026-09-10._

> If you read nothing else, read these three lines.
>
> 1. **Never commit `.env`.** It has real API keys in it. It is already ignored — leave it that way.
> 2. **Work on a branch, not on `main`.**
> 3. **Before you push a UI change, run `npm run build` and open the page.** If the build
>    fails, the workstation silently keeps serving the *old* version and everything looks fine.

---

# PART 1 — Git and GitHub, from zero

## 1.1 What these things actually are

Two different things with confusingly similar names.

**Git** is a program on your laptop. It takes snapshots of your project folder so you can go
back to any of them. That is genuinely all it is. The snapshots are called **commits**.

**GitHub** is a website that stores a copy of those snapshots so other people can get them.
Git works fine with no internet; GitHub is how the team shares.

The mental model that makes the rest make sense:

```
your folder  →  staging area  →  your commits  →  GitHub
  (edit)         (git add)       (git commit)     (git push)
```

Three separate steps, on purpose. You edit a lot of files, choose which of them belong
together, and save that group with a message explaining it.

## 1.2 One-time setup

Install [Git](https://git-scm.com/downloads). On Windows this also installs **Git Bash**,
which is the terminal to use for everything below.

Tell git who you are — this is stamped on every commit you make:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

Then log in to GitHub. The easiest way by far is the [GitHub CLI](https://cli.github.com/):

```bash
gh auth login
```

Choose *GitHub.com* → *HTTPS* → *Login with a web browser*, and paste the code it gives you.
That is it — you will never be asked for a password again.

> **If you cannot install `gh`:** GitHub does not accept your account password from the
> command line. You need a **Personal Access Token**: github.com → your avatar → Settings →
> Developer settings → Personal access tokens → Tokens (classic) → Generate new token, tick
> `repo`, copy it. When git asks for a password, paste the token. Save it somewhere; it is
> shown once.

## 1.3 Getting the project

Once, ever:

```bash
cd "D:/where/you/keep/projects"
git clone https://github.com/<owner>/<repo>.git
cd <repo>
```

`clone` downloads the whole history, not just the current files. That is why you can look at
any past version offline.

## 1.4 The loop you will actually repeat

This is the whole job. Five commands, in this order, every time.

### Step 1 — get everyone else's work first

```bash
git switch main
git pull
```

**Do this before you start, every session.** `pull` downloads what other people pushed. If
you skip it you will write your change on top of an old version and have to untangle it
later.

### Step 2 — make a branch

```bash
git switch -c ui/bigger-offer-card
```

A **branch** is a separate line of snapshots. You can experiment, break things and throw it
away, and `main` is untouched the whole time.

Name it `area/what-you-are-doing`, all lowercase, dashes not spaces. `ui/dark-mode-contrast`,
`ui/fix-thai-line-height`. The name is for humans; nothing depends on it.

### Step 3 — do the work, then look at what you did

```bash
git status
```

Shows which files you changed. Run it constantly — it is free and it is the answer to "what
is going on".

```bash
git diff
```

Shows the actual lines you changed, red for removed and green for added. Press `q` to quit
that view. **Read this before every commit.** It is how you notice you left a `console.log`
or a colour you were experimenting with.

### Step 4 — save a snapshot

```bash
git add apps/workstation/src/styles.css
git commit -m "workstation: raise the contrast on the assurance badge"
```

`add` chooses which files go in this snapshot. `commit` saves them with a message.

`git add -A` adds *everything you changed*. It is convenient and it is how a secret gets
committed by accident, so **run `git status` first and look at the list**.

**Message style for this repo:** `<area>: <what changed>`, lowercase, no full stop.

```
workstation: bigger offer card, and the timer stops overlapping the queue name
sim: the call screen buttons were unreadable on a phone
docs: how the UI team changes colours
```

Say what changed, not what you did. "fixed stuff", "update", "changes" are not messages.

> **This repo has one extra rule:** do **not** add `Co-Authored-By: Claude` or
> "Generated with Claude Code" lines to commit messages. The commits are the team's work.

Commit **often** — several small commits beat one enormous one. A small commit is easy to
read, easy to undo, and easy to explain.

### Step 5 — send it to GitHub

```bash
git push
```

The very first push on a new branch will complain and tell you the exact command to run
instead. It looks like this, and you just paste what it printed:

```bash
git push --set-upstream origin ui/bigger-offer-card
```

### Step 6 — open a Pull Request

A **Pull Request** (PR) is asking the team to merge your branch into `main`. Either:

```bash
gh pr create --fill --web
```

…or go to the repo on github.com, where a yellow *"Compare & pull request"* button will be
waiting. Write a sentence or two saying what changed and what you checked. Someone reads it,
comments, and merges it.

After it is merged:

```bash
git switch main
git pull
```

…and you are ready for the next one. **Do not keep working on a merged branch** — start a
new one from a freshly pulled `main`.

## 1.5 The five things that will go wrong, and what to type

### "I made changes and I want to throw them away"

One file:

```bash
git restore apps/workstation/src/styles.css
```

Everything, back to your last commit:

```bash
git restore .
```

⚠️ **This is not undoable.** Your edits are gone.

### "I committed on `main` by accident"

Nothing is broken as long as you have not pushed. Move the commit onto a branch:

```bash
git switch -c ui/my-actual-branch
git switch main
git reset --hard origin/main
git switch ui/my-actual-branch
```

Your commit is now on the branch and `main` matches GitHub again.

### "`git push` was rejected"

The message says something about *"fetch first"* or *"non-fast-forward"*. It means somebody
pushed to that branch since you last pulled. Fix:

```bash
git pull
git push
```

If the `pull` reports a **conflict**, see the next one.

### "I have a merge conflict"

Two people changed the same lines. Git cannot guess who is right, so it puts both versions
in the file and asks you:

```
<<<<<<< HEAD
  --accent: #ffc72c;
=======
  --accent: #ffd447;
>>>>>>> main
```

Open the file, **delete the `<<<<<<<`, `=======` and `>>>>>>>` lines**, and leave the code
you want (which might be a mix of both). Then:

```bash
git add <the file>
git commit
```

`git status` lists every conflicted file, so you cannot lose track. Conflicts feel alarming
and are just this — it is text you edit by hand.

### ⚠️ "I committed something secret"

Tell somebody immediately, and **assume the key is burned**. If it was pushed to GitHub, it
is public even after you delete it — deleting the file does not remove it from history.

The only real fix is to **revoke and regenerate the key at the provider**. Do that first.
Cleaning the history is secondary and someone experienced should do it.

This is why `.env` is in `.gitignore` and why `git status` before `git add -A` matters.

## 1.6 What must never be committed here

Already covered by `.gitignore`, but know *why*:

| Never commit | Why |
|---|---|
| `.env` | real API keys and the recording master key |
| `*.wav` | real customer audio (`D97`), and it is huge |
| `models/`, `data/` | gigabytes of model weights and the 22 GB Thai dataset |
| `apps/workstation/dist/` | a build output; it is rebuilt from source (`Q17`) |
| `node_modules/`, `.venv/` | installed dependencies; reinstalled from the lockfiles |

If `git status` ever shows one of these, **stop and ask** rather than committing it.

## 1.7 The commands, in one table

| Command | What it does |
|---|---|
| `git status` | what has changed. Run it constantly |
| `git diff` | the actual changed lines. Read before committing |
| `git switch main` | move to the main line |
| `git switch -c name` | make a new branch and move to it |
| `git switch name` | move to an existing branch |
| `git pull` | download and apply what others pushed |
| `git add <file>` | choose a file for the next snapshot |
| `git commit -m "msg"` | save the snapshot |
| `git push` | send your commits to GitHub |
| `git log --oneline -10` | the last ten commits |
| `git restore <file>` | throw away your changes to a file |

Two you should **not** use without asking: `git push --force` and `git rebase`. Both rewrite
history, and this repo's rule is *prefer new commits over amends*.

---

# PART 2 — Changing how it looks, without breaking how it works

## 2.1 There are three front-ends, and they are deliberately different

| What | Where | Build step? | Who sees it |
|---|---|---|---|
| **Workstation** | `apps/workstation/src/` | **yes**, React + Vite | the broker, all shift |
| **Customer app / simulator** | `apps/customer_sim/index.html` | **no** | the customer, on a phone |
| **Paired screen** | `apps/customer_assist/index.html` | **no** | a customer who tapped a link |

The two customer pages are **one HTML file each, on purpose** (`D47`, `D120`) — open the
file, edit, refresh the browser. No build, nothing to install. Keep them that way: they are
the parts that must not be able to fail five minutes before a demo.

The workstation earns its build because it is a dense operator tool with ten live panels and
a websocket.

## 2.2 Colours: change the tokens, not the components

Every colour is a **CSS variable** defined once at the top of the file. Change it there and
it changes everywhere, consistently.

**Workstation** — `apps/workstation/src/styles.css`, the `:root` block at the top:

```css
:root {
  --bg: #0e1420;        /* page background */
  --panel: #161e2e;     /* card background */
  --panel-2: #1d2739;   /* buttons, insets */
  --line: #2a3548;      /* borders */
  --ink: #e8edf6;       /* normal text */
  --ink-dim: #97a3b8;   /* secondary text */
  --ink-faint: #6b7789; /* labels, hints */
  --accent: #ffc72c;    /* Krungsri yellow */
  --accent-ink: #221a00;/* text ON the accent */
  --ok / --warn / --bad / --info
  --radius: 10px;       /* corner rounding, everywhere */
}
```

**Customer pages** — the same idea, near the top of each `index.html`. Note those two are
**light** themes with a dark-mode block below, so if you change one you must check the other.

**The rule:** if you find yourself typing a `#hex` anywhere except in a `:root` block, stop.
Add a token or use an existing one. One hardcoded colour is how a screen ends up with four
slightly different greys.

⚠️ **`--accent` is Krungsri yellow.** It is the brand colour, it is on the deck, and a judge
from the bank will notice. Change the *shade* if the contrast is wrong; do not change the
*hue* to something that is not theirs.

## 2.3 The one rule that matters more than any other

> **The server decides what is allowed. The screen only draws it.**

You will see fields like `declarable`, `offerable`, `disclosure_locked`, `disabled`,
`summary_is_preview`, `personal`. **Render them. Never compute them.**

It is tempting to write `if (assurance === "l1_probable") hide the policy number`. Do not.
The server already decided and the payload already reflects it; a second copy of the rule in
the browser will eventually disagree with the first, and the browser's copy is the wrong one.
This has bitten this project before (`B5` shipped a real data leak).

**The safe version:** if the screen needs to know something, the server should send it. Ask
for a new field rather than deriving one.

## 2.4 The traps that have actually caught people here

These are real bugs from `docs/BUG_HISTORY.md`. Every one looked like a server problem and
was not.

### ⚠️ Every `useState` / `useEffect` goes ABOVE the component's first `return`

This one blanked the **entire** workstation — a white page, needing a reload — every time
someone pressed Accept or saved a wrap-up (`B43`):

```jsx
function AssistPanel({ callId }) {
  const [open, setOpen] = useState(false);
  if (!callId) return <p>ใช้ได้เมื่อรับสายแล้ว</p>;   // early return
  const [copied, setCopied] = useState(false);       // BROKEN: only runs when there IS a call
  ...
}
```

React requires the same hooks in the same order on every render. Here there is one hook with
no call and two with one, so the moment a call starts (Accept) or ends (Save) React throws
and unmounts everything. **TypeScript compiles this happily**, which is why it shipped.

It cannot ship again: `npm run build` now runs `eslint src` first and **fails** with
`react-hooks/rules-of-hooks` if you do this. The fix is always the same — move the hook up
above the `if (...) return`. Run `npm run lint` to check without building.

### ⚠️ The workstation re-renders every single second

It has ticking call timers, so the whole thing redraws once a second by design.

That breaks any `setInterval` longer than a second whose effect depends on a function:

```jsx
// BROKEN. Rebuilt every second, so a 2-second interval NEVER fires.
useEffect(() => {
  const id = setInterval(onRefresh, 2000);
  return () => clearInterval(id);
}, [open, callId, onRefresh]);   //  <-- onRefresh is new on every render
```

Hold the callback in a ref and depend only on the real inputs (`B33`). **The symptom is
that the data is correct on the server and simply never appears**, which sends you looking
in the wrong place for an hour.

### ⚠️ "Fetch once on mount" happens before sign-in

```jsx
useEffect(() => { fetchTools().catch(() => setTools(null)); }, []);
```

An empty `[]` means *as early as possible* — which for anything needing a login is **before
the login**. It 401s, the `.catch` swallows it, and the panel is empty for the entire shift
(`B32`). Key the effect to the thing that had to happen first: `[agentId]`.

### ⚠️ A duration on screen needs an anchor, not a number

If the server sends `waited_s: 42`, that 42 never changes until the next message arrives —
and for someone sitting in a queue, nothing happens to cause one. The screen shows a frozen
timer (`B27`, `B8`).

The server sends `waited_since` (an instant) alongside it. Use that plus the ticker. Never
compute a call's elapsed time from when the component mounted — a refresh mid-call would
restart the clock at zero.

### ⚠️ Never put user text into `innerHTML`

The customer pages are plain HTML with hand-written rendering. Since brokers can type free
text that appears on a customer's screen (`D128`), raw interpolation is a script-injection
hole. Both pages escape now. **Do not "simplify" that escaping away.**

### ⚠️ Do not re-render the customer page on every poll

`/assist` polls once a second. If it rebuilt the DOM each time it would wipe a form the
customer is halfway through typing (`D120`). It compares a content signature first. Keep
that.

### ⚠️ Radio buttons and Thai do not line up

Native `<input type="radio">` puts its marker on the first line's baseline while a Thai
label wraps underneath it, so nothing aligns, and each option takes a full-width row. This
project uses a `Pill` control — `<button role="radio" aria-checked>` inside a `radiogroup`
(`D129`).

**If you build a new one-of-many control, keep `role="radio"` and `aria-checked`.** Dropping
the `<input>` must not drop the meaning for a screen reader. And show selection with **three**
things at once — border, fill and weight — not colour alone.

### ⚠️ A disabled control must *look* disabled

A button that silently does nothing reads as a broken button. If something cannot be pressed,
grey it out and say why (there is usually a `reason` field next to it in the payload).

## 2.5 How to run it while you work

**Both customer pages** — no build at all. Start the API and open the page:

```bash
uv run python -m readycall.entrypoints.api
#   http://127.0.0.1:8000/sim        the customer's app
#   http://127.0.0.1:8000/workstation the broker
```

Edit `apps/customer_sim/index.html`, refresh the browser. Done.

**The workstation** — two options.

*Quick change:* edit, rebuild, refresh.

```bash
cd apps/workstation
npm install     # once, ever
npm run build
```

*Lots of changes:* run the dev server, which reloads as you type. Keep the API running in a
second terminal.

```bash
cd apps/workstation
npm run dev
```

⚠️ **`npm run build` must succeed before you commit.** The API serves
`apps/workstation/dist/`, which is *the last successful build*. If your build fails, the
page keeps working perfectly — on the old code — and you will swear your change did nothing.

## 2.6 Before you push: the four-minute check

```bash
cd apps/workstation && npm run build && cd ../..   # must pass
uv run pytest -q                                    # must be all green
```

Then **actually look at it**. This project's own hardest-won lesson is that its test suite
cannot see UI faults — nine hundred tests have never once caught one; every single UI bug
here was found by a person clicking. So click:

1. Open `/workstation`, sign in as **A006**, press **พร้อมรับสาย**.
2. Place a call:
   ```bash
   curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" \
     -d '{"intent_code":"motor.claim.notify","caller_number":"0812345678","intake_keys":["1"],"ignore_hours":true}'
   ```
3. Check the offer card, press Accept, check the brief panels.
4. Open `/sim` on a **375px-wide** window (phone size) and check nothing overflows.
5. If you touched the plan or transfer dialogs, open them — they are the widest things on
   the screen and the first to break.

**Thai is longer than English and wraps differently.** Always check with the real Thai text,
never with placeholder Latin.

## 2.7 Safe, careful, and ask-first

| Safe on your own | Be careful | Ask first |
|---|---|---|
| colours in `:root` | changing layout structure / grid columns | anything in `src/readycall/` |
| spacing, radius, font size | adding a `useEffect` with a timer | removing a field from a payload |
| wording of a **label** | anything in a dialog that pushes to a customer | changing what `disabled` is computed from |
| adding a CSS class | dark-mode blocks on the customer pages | escaping, sanitising, or `innerHTML` |
| icons, shadows, borders | anything that renders a coverage figure | Thai wording that is *spoken* (`config/voice_prompts.yaml`) |

⚠️ **Thai the caller HEARS is not in the UI.** It lives in `config/voice_prompts.yaml` and
`config/menus.yaml`, and changing it needs `uv run python scripts/build_prompts.py`. That is
a different job from changing a label on a screen.

## 2.8 Where to look things up

- **`docs/reading/workstation_wiring.html`** — the workstation's two channels, every endpoint
  and what the server owns versus what the client owns. Open it in a browser. **Read this
  before changing `apps/workstation/`.**
- **`docs/explanations/P2b_workstation_client.md`** — the same thing in prose.
- **`docs/BUG_HISTORY.md`** — search it *first* when something is weird. There is a good
  chance it has happened before and the entry says exactly why.
- **`docs/DECISIONS.md`** — why something is the way it is, before you change it.
