# P4 — the broker turn, the LLM, and the customer's screen

_A snapshot of 7 September 2026, written the day it landed. Like every file in this folder
it is a **snapshot, not a specification**: when it goes out of date, add a "changes since"
entry at the bottom rather than editing the body._

_Covers `D117`, `D118`, `D119`, `D120`, `B30`, `B31`. Diagrams:
[13. The broker, and the customer's screen](../diagrams/13_broker_and_assist.md)._

---

## What happened in one paragraph

The hackathon orientation on 6 September produced Krungsri's own slide showing what a
**broker** does versus what an **insurer** does, and it did not match what we had built. So
the domain pack was rewritten around the broker's five duties (`D117`), the
recommended-action playbooks moved out of Python into config (`D118`), the LLM seam was
finally built and measured on a real provider (`D119`), and a new mechanism binds a phone
call to the customer's screen so the broker can push a comparison or a form onto it instead
of describing a website down the phone (`D120`). Two latent bugs fell out on the way
(`B30`, `B31`), both of the "this was passing for the wrong reason" family.

---

## 1. Why the domain was wrong, and what "wrong" meant concretely

The system had `motor.claim` and `health.claim` skills, a `q_health_ipd` queue for
**pre-authorisation**, and intents like `health.claim.submit` and `motor.claim.status`.

Every one of those describes work an **insurance company** does. Pre-authorisation is a
coverage decision. Claim status is the adjudicator's record. A broker holding a queue called
"IPD pre-authorisation" is implicitly promising to approve admissions, which is
underwriting — and the brief puts underwriting explicitly out of scope.

The replacement follows the duty list exactly:

| broker duty (their slide) | what it became |
|---|---|
| วิเคราะห์ความต้องการของลูกค้า | `*.advice.quote` |
| **คัดสรรแบบประกันและบริษัทฯ ที่ตรงตามความต้องการ** | **`*.advice.compare`** — the mandate, and the brief's biggest leak |
| บริการด้านกรมธรรม์ | `*.service.policy` |
| **ติดตามการต่ออายุกรมธรรม์** | **`renewal.retention` + `q_renewal`** — its own desk |
| สร้างความสัมพันธ์กับลูกค้า | `general.advice.review` |
| *(not a broker duty)* ชดใช้ค่าสินไหม | **`claims.assist`** — take the notification, hand over |

**33 intents, 11 skills, 11 queues.** Compare that with what it replaced: 27 intents, 13
skills, 9 queues — similar size, completely different shape.

### The three things that were not config

The user's instinct was that this would be "mostly the YAML", and that was right. Three
things were not:

1. **`Policy.insurer`.** Meaningless for a single insurer, first-class for a broker. It
   decides who a claim is handed to and whose terms a comparison is against.
2. **`handoff_to_insurer`** on the intent, travelling all the way to a banner on the
   agent's screen.
3. **Multi-carrier fixtures.** With one policy per customer there is nothing to compare,
   and — see `B30` below — nothing to *choose between* either.

### Why renewal got its own desk

Because of one number: **72.55% of life premium is renewal, at 84% persistency**. One
policy in six lapses every year, and chasing them is a named broker duty.

Before this, a renewal call routed to `general.billing` — a payments queue. That buried
the largest single block of revenue in the business inside "how do I pay". A renewal
conversation is *retention* work: different script, different success measure, different
person on the phone.

---

## 2. Playbooks: the `Q19` that had been parked since P1

The recommended actions the agent sees lived in `_PLAYBOOKS`, a dict inside
`services/brief/builder.py`. `D28` says no insurance literal may live in `services/`, and
that dict had a comment admitting the violation and promising to fix it "at P4".

The broker rewrite would have doubled it from six lists to twenty-four, so it moved:
`config/playbooks.yaml`, loaded into the domain pack as `PlaybookSpec`, exactly the shape
`D72` used for the challenge list.

**Guarded both directions at startup.** An intent naming a playbook that does not exist
refuses to boot — and *so does a playbook no intent reaches*. That second half is the one
a naive check misses, and it is the one that catches domain content somebody keeps editing
that nothing can ever display.

`generic` is required to exist, because a missing playbook would otherwise render an empty
action list — which on screen reads as *"there is nothing to do"* rather than as a config
error.

---

## 3. The LLM, six phases after it was named

`D29` specified `AnthropicAdapter` and `OpenAiCompatibleAdapter` back at P0. As of
6 September, `Settings.llm_provider` accepted both names, `_check_coherent` validated their
API keys, and **neither existed**. `RuleBasedLlm` did exist, was correct, and was
constructed by nothing.

That is two known failure shapes at once: `B23` (a config surface that cannot select
anything) and `B7` (code that is correct and called by nothing). Both were found by reading
the tree rather than the docs — and the docs at the time said the adapters were "both
implemented", in the present tense.

### What was built

- **`build_llm`** — the factory, and the only place a client may be constructed.
- **`AnthropicLlm`** — structured output through **forced tool use**. The Pydantic schema is
  handed over as a tool definition with `tool_choice` pinned to it, so the model cannot
  answer in prose that happens to begin with a brace. Parsing free text and hoping is
  exactly the `D16` hazard.
- **`OpenAiCompatibleLlm`** — one adapter covering Typhoon-hosted, OpenAI, vLLM, Ollama and
  LM Studio, because all five speak the same wire format. It uses JSON-schema response
  format where the server supports it and falls back to schema-in-the-prompt where it does
  not, **and records which mechanism ran** — because *"the model was worse"* and *"the
  server could not constrain it"* are different findings, and a comparison table that
  conflates them is misleading.
- **Prompts as versioned files** in `prompts/th/<id>.<version>.md`. Rendering refuses a
  missing variable **and** an undeclared one.

That second refusal is worth dwelling on. A missing variable raises loudly. A *typo'd* one
— `transcrpit` instead of `transcript` — would render a prompt with an empty transcript and
get back a confident-looking summary of nothing. Declaring the slot list makes that a
startup-shaped error instead of a plausible sentence on an agent's screen.

### The summary, and the six ways it does nothing

`IntakeSummariser.summarise()` returns `SummaryResult | None`, and **`None` is the ordinary
case**: no key configured, caller barely spoke, model timed out, provider failed, model
said it could not tell, or the text stated a figure. Every caller has to be correct in that
case, and the answer is always the same — the rule-based summary, which quotes the caller
verbatim and cannot hallucinate, simply stays.

**It is started from `accept_offer` and never awaited.** The agent is connected the instant
that endpoint returns. If the model answers, the screen upgrades on a socket push; if it
never answers, nothing at all happens. That ordering is the one moment where breaking `D12`
would be tempting, so it is written as code rather than as a comment.

### The number that matters

One live call through the whole path, `claude-sonnet-5`:

```
latency 4,481 ms · tokens 1,561 in / 256 out · cost $0.0085
```

and the summary was faithful — it named the procedure, the hospital, the documents question
and the "must I pay upfront" question, and invented no figures.

⚠️ **4.5 s is well outside `ARCHITECTURE` §15's 1-second brief budget.** It is survivable
only because nobody waits for it. Anyone who later wants a summary *before* accept now
starts from a measurement instead of a hope.

---

## 4. The customer's screen

This was the user's idea, from the orientation, and it is the best piece of user insight in
their notes: a broker on the phone saying *"go to the website, tap the menu at the top
right, then Documents, then Upload"* is a design failure, and the brief prices it —
journey step 4 leaks on **เอกสารเยอะ ลูกค้า drop-off กลางทาง**.

### The pairing decision

**A link, not a code.** The call is already on a phone number and ANI says which, so a link
sent to *that number* is self-authenticating to exactly what the ANI is worth (L1,
probable) and costs one tap. A read-back code proves more — that they are looking at the
screen *and* on the call — and costs a chore to somebody who rang because they were stuck.

Three entry paths converge on one state:

| the customer was… | how pairing happens |
|---|---|
| in the app, tapped Contact | implicit — the app already carries the correlation token (`D6`) |
| on a phone call, not signed in | broker presses ส่งลิงก์, they tap it |
| signed in, called separately | the **server** knows a call is live for them, so the app is *told* and offers |

That last row is why there is no permanently visible "let an agent help me" button. It
would be odd on a screen nobody is calling from, and the customer would have to hunt for it
at the moment they are least able to.

### The tier gate

| tier | reached by | may be pushed |
|---|---|---|
| `GUEST` | tapping the link | anything true for **anybody** |
| `VERIFIED` | signing in | anything about **them** |

**No account is needed to be helped.** Someone comparing plans gets everything without
registering, because none of it is about them. A prefilled form is refused at guest tier —
and the refusal names the reason, so the broker asks the customer to sign in rather than a
stranger's policy landing on whoever holds that handset.

### Two front ends, deliberately different

The customer's screen is **one static HTML file with no build step**, polling once a second.
The workstation stays React with a socket. The reasoning is in
[13.6](../diagrams/13_broker_and_assist.md#136-one-round-trip-end-to-end); the short version
is that one is a dense operator tool carrying a 1.5 s transcript budget, and the other is a
page a stranger opens from a link and looks at for four minutes.

⚠️ **The page re-renders only when its content signature changes**, so a poll cannot wipe a
form the customer is halfway through typing.

---

## 5. Try it yourself

Everything below runs with **no API key, no GPU and no containers**.

### 5.1 See the broker domain

```bash
uv run python -c "from readycall.domainpack import DomainPack; p=DomainPack.load('config'); print(len(p.intents),'intents |',len(p.skills),'skills |',len(p.queues),'queues |',len(p.playbooks),'playbooks')"
```

Then read `config/intents.yaml` and look for `handoff_to_insurer: true` — those are the
calls that end with the insurer rather than with us.

### 5.2 Watch a claim call route and get its handoff banner

```bash
uv run python -m readycall.entrypoints.api
```

In another shell (or the browser at `/workstation`, signing in as **A006** and pressing
พร้อมรับสาย):

```bash
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" -d "{\"intent_code\": \"health.claim.notify\", \"caller_number\": \"0812345678\", \"intake_keys\": [\"2\"], \"ignore_hours\": true}"
```

Press Accept. The brief shows **บริษัทผู้รับประกัน: เมืองไทยประกันภัย** under the policy
number, and a banner above it saying the call must be handed to the insurer. At L1 you get
three recommended actions; attest the identity and the L2 steps appear.

### 5.3 The customer's screen, end to end

With the same call accepted, on the workstation, get the pairing link:

```bash
curl -X POST http://127.0.0.1:8000/v1/agent/calls/<CALL_ID>/assist/link -b cookies.txt
```

Open the returned link in a browser (or on your phone, if the API is reachable on the LAN)
and try the sequence that matters:

1. **Push a comparison** — it appears on the phone within a second, no sign-in needed.
2. **Push a form** — refused, with a message telling the broker to ask for a sign-in.
3. **Sign in on the phone**, push the form again — it appears, prefilled.
4. **Fill it in and submit** — then `GET /v1/agent/calls/<CALL_ID>/assist` shows the broker
   exactly what the customer typed.

Step 2 is the one to actually try. It is the rule that keeps this safe, and it is much
more convincing to watch it refuse than to read that it does.

### 5.4 Turn the real LLM on

```bash
uv sync --extra web --extra llm      # ⚠️ name EVERY extra you want: uv sync PRUNES (B31)
```

Set `LLM_PROVIDER=anthropic` in `.env` (the key is already there), restart, and place a call
where the caller actually says something:

```bash
uv run python scripts/make_demo_audio.py
curl -X POST http://127.0.0.1:8000/v1/demo/calls -H "Content-Type: application/json" -d "{\"intent_code\": \"health.claim.notify\", \"caller_number\": \"0812345678\", \"intake_keys\": [\"1\"], \"audio\": \"demo_intake.wav\", \"ignore_hours\": true}"
```

Accept it, and watch the server log for `ai summary ready`. The summary on screen replaces
the rule-based one a few seconds after you are already talking — which is the whole point.

**To prove `D12` rather than trust it:** set `LLM_BASE_URL` to a port with nothing on it and
`LLM_PROVIDER=openai_compatible`. Accept a call. It connects exactly as fast, the
rule-based summary stays, and the log carries one warning.

---

## 6. Two bugs, both of the same family

### `B30` — the cold-call path knew the product line and threw it away

Adding a second policy to the demo customer turned four workstation tests red with
`relevant_policy: None`.

The cause was not the fixture. `demo.py` passes `product_line` to the context assembler on
the **app** path and not on the **cold-call** path twelve lines below, although
`body.intent_code` is in scope and the expression is identical. `_pick_relevant_policy`
falls back to *"the only policy they have"* when it has no line signal — so with a
one-policy fixture the omission was invisible and correct output was being produced for the
wrong reason, since P1b.

**Lesson: a fixture with one of something tests nothing about choosing.** The branch that
declines to guess between unrelated policies was unreachable because no customer had two.
Cardinality is part of a fixture's design.

### `B31` — two checks that were passing because of what happened to be installed

`uv sync --extra web --extra llm` **prunes** anything not named, so it removed the `ml`
stack — and instantly produced five suite errors and five mypy errors that had been latent
for weeks and **would have been red on CI**.

The VAD contract suite caught `ImportError` around an import that always succeeds, because
`silero.py` imports `torch` lazily *inside the constructor* and re-raises it as a
`ConfigError`. And `torch` had never been in the mypy override list; the check passed
because every machine that ran it had torch installed as a side effect.

**Lesson: a deferred import relocates the failure, and every `except ImportError` written
around the import is then guarding an empty room.**

---

## 7. What this phase did NOT do

- **The comparison data.** `D120` built the transport. What fills the table — a
  `products.yaml`, gap analysis against what the customer holds, ranking on real attributes
  with the model writing only the reason sentence — is Track B and is not started.
- **Sending the link.** `NotifierPort` is P5.
- **Signature, OCR, upload.** `document_request` records the intent and stubs the rest.
- **Merging the two customer front ends.** `customer_sim` is still the old page.
- **A golden set for the LLM.** Without it, "85% intent accuracy" remains a claim rather
  than a measurement, and that is a named P4 exit criterion.

---

## Changes since

_Nothing yet — this file was written on 2026-09-07, the day the work landed. Add dated
entries here rather than editing the body above._
