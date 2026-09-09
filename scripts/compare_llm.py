"""Run the same Thai intakes through several models and print a table (`D130`, `D29`).

    uv run python scripts/compare_llm.py --list
    uv run python scripts/compare_llm.py --preset fast --repeat 3
    uv run python scripts/compare_llm.py --model anthropic:claude-haiku-4-5
    uv run python scripts/compare_llm.py --model openai:gpt-5.4-nano --repeat 5
    uv run python scripts/compare_llm.py --preset fast --out var/llm_compare.md

**This is `PLAN.md`'s P4 exit criterion**: *"a Claude-vs-Typhoon comparison table produced
by the harness, not by opinion"*. It had not existed, which is why `D119`'s single measured
number — one call, one model — was doing the work of a comparison.

**What it measures, and what it deliberately does not.** It drives the real
`IntakeSummariser` through the real `build_llm` factory over the real prompt file, so what
comes back is what the product would have produced. It reports latency, tokens, cost, and
every objective guard the service already applies: schema validity, the model's own
`is_clear`, `D16`'s figure refusal, and a hallucination probe. It does **not** score Thai
prose quality, because there is no honest way to do that here and a fabricated quality
column would be the metric problem `Q28` warns about — the outputs are dumped in full so a
human reads them and decides. **Read the dump; do not rank on latency alone.**

**Latency is the point.** `Q35` is open because the summary takes 4.5 s against a 1 s brief
budget, which is survivable only because it runs after Accept. The number that closes it is
a p95 that fits inside `OFFER_TIMEOUT_S` — 20 s today, but the useful target is the few
seconds an agent actually looks at the offer card before pressing Accept.

⚠️ **Every run spends real money.** `--repeat 3` over 4 cases and 6 models is 72 calls;
at these models' rates that is cents, not dollars, and it is still a real invoice. The
`--dry-run` flag prints the matrix and calls nothing.

⚠️ **Output goes to a FILE by default, not the console.** Thai on a cp1252 Windows console
kills the process (`B1`), and this prints Thai by design.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.adapters.llm import build_llm  # noqa: E402
from readycall.adapters.llm.openai_compatible import OPENAI_BASE_URL  # noqa: E402
from readycall.config import LlmProviderName, Settings  # noqa: E402
from readycall.console import enable_utf8  # noqa: E402
from readycall.prompts import PromptLibrary  # noqa: E402
from readycall.services.analysis.summary import IntakeSummariser  # noqa: E402


@dataclass(frozen=True, slots=True)
class Case:
    """One intake, plus what an honest answer to it looks like."""

    name: str
    turns: tuple[str, ...]
    intent_label_th: str
    line_label_th: str
    #: Substrings that must NOT appear: facts the caller never stated. A model that
    #: produces one has invented it, which is the whole risk `D16` names.
    must_not_contain: tuple[str, ...] = ()
    #: True when the honest answer is "no summary" — too little was said (`D119`'s
    #: `min_characters`) or the model should set `is_clear: false`. A model that confidently
    #: summarises this case is worse than one that returns nothing.
    expect_none: bool = False


#: Real Thai, taken from the fixtures the rest of the system already runs on: the scripted
#: engine's motor claim (`config/demo_transcript.yaml`) and the two scenarios. Using the
#: project's own material rather than freshly invented text keeps this comparable with
#: everything else measured on it.
CASES: tuple[Case, ...] = (
    Case(
        name="motor_claim",
        turns=(
            "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ",
            "รถชนเมื่อเช้านี้ครับ",
            "อยู่แถวรัชดา ตอนนี้จอดข้างทางแล้ว",
            "ไม่มีใครบาดเจ็บครับ",
            "อยากทราบว่าต้องทำยังไงต่อ",
            "แล้วต้องใช้เอกสารอะไรบ้างครับ",
        ),
        intent_label_th="แจ้งเคลมรถยนต์",
        line_label_th="motor",
        # The caller never named a hospital, an amount or a fault finding.
        must_not_contain=("โรงพยาบาล", "บาท", "คุ้มครอง"),
    ),
    Case(
        name="health_ipd",
        turns=(
            "สวัสดีครับ พรุ่งนี้ผมต้องไปนอนโรงพยาบาลกรุงเทพครับ",
            "อยากทราบว่าค่าห้องที่ประกันจ่ายให้คือวันละเท่าไหร่",
            "แล้วต้องเตรียมเอกสารอะไรบ้างสำหรับเคลมครับ",
        ),
        intent_label_th="แจ้งเคลมค่ารักษาพยาบาล / เข้ารักษาผู้ป่วยใน",
        line_label_th="health",
        # The room rate is the thing the caller ASKED for. A model that answers it has
        # invented a coverage figure — `D16`'s exact failure, in the case most likely to
        # provoke it. `_FIGURE` should refuse the summary outright if it does.
        must_not_contain=("วันละ 4,000", "4000", "ครอบคลุม"),
    ),
    Case(
        name="rambling_travel",
        turns=(
            "คือว่าผมจะไปญี่ปุ่นเดือนหน้าครับ",
            "ไปกับครอบครัว สี่คน",
            "ยังไม่เคยทำประกันเดินทางเลยครับ",
            "ไม่รู้ว่าต้องทำแบบไหน",
            "แล้วถ้าไฟลท์ดีเลย์นี่ได้ด้วยไหมครับ",
        ),
        intent_label_th="ซื้อประกันเดินทาง",
        line_label_th="travel",
        must_not_contain=("บาท",),
    ),
    Case(
        name="barely_spoke",
        turns=("อืม", "ครับ"),
        intent_label_th="เรื่องอื่นๆ",
        line_label_th="unknown",
        # Below `min_characters`, so the service must not call the model at all. Present
        # so the comparison shows every model "passing" it for free — the honest answer.
        expect_none=True,
    ),
)


#: `provider:model` pairs. Anthropic ids are `shared/models.md`'s exact strings — never
#: date-suffixed (that suffix is what made the old cost table's Haiku key match nothing).
PRESETS: dict[str, tuple[str, ...]] = {
    # What a pre-accept summary would realistically run on: the small, fast tier.
    "fast": (
        "anthropic:claude-haiku-4-5",
        "openai:gpt-5.4-nano",
        "openai:gpt-4.1-nano",
        "openai:gpt-4.1-mini",
        "openai:gpt-5.4-mini",
    ),
    # Adds the mid tier, to price what the extra seconds buy.
    "full": (
        "anthropic:claude-haiku-4-5",
        "anthropic:claude-sonnet-5",
        "openai:gpt-5.4-nano",
        "openai:gpt-4.1-nano",
        "openai:gpt-4.1-mini",
        "openai:gpt-5.4-mini",
        "openai:gpt-5.4",
    ),
    # The single call `D119` published, so the old number is reproducible.
    "d119": ("anthropic:claude-sonnet-5",),
}


@dataclass
class Observation:
    case: str
    ok: bool
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    text: str = ""
    verdict: str = ""


@dataclass
class ModelRun:
    label: str
    observations: list[Observation] = field(default_factory=list)
    error: str | None = None

    @property
    def latencies(self) -> list[float]:
        return [o.latency_ms for o in self.observations if o.latency_ms is not None]

    def p50(self) -> float | None:
        values = self.latencies
        return statistics.median(values) if values else None

    def worst(self) -> float | None:
        values = self.latencies
        return max(values) if values else None

    def total_cost(self) -> float | None:
        costs = [o.cost_usd for o in self.observations if o.cost_usd is not None]
        return sum(costs) if costs else None

    def per_call_cost(self) -> float | None:
        costs = [o.cost_usd for o in self.observations if o.cost_usd is not None]
        return sum(costs) / len(costs) if costs else None

    def clean(self) -> int:
        return sum(1 for o in self.observations if o.verdict == "ok")

    def problems(self) -> list[str]:
        return [f"{o.case}: {o.verdict}" for o in self.observations if o.verdict not in {"ok", ""}]


def _settings_for(spec: str, timeout_s: float) -> Settings:
    """One `Settings` per model, so `build_llm` is the only construction path (`D119`)."""
    provider, _, model = spec.partition(":")
    if not model:
        raise SystemExit(f"--model wants provider:model, got {spec!r}")
    if provider == "anthropic":
        return Settings(
            llm_provider=LlmProviderName.ANTHROPIC, llm_model=model, llm_timeout_s=timeout_s
        )
    if provider in {"openai", "openai_compatible"}:
        return Settings(
            llm_provider=LlmProviderName.OPENAI_COMPATIBLE,
            llm_model=model,
            llm_base_url=os.environ.get("LLM_BASE_URL") or OPENAI_BASE_URL,
            llm_timeout_s=timeout_s,
        )
    raise SystemExit(f"unknown provider {provider!r} in {spec!r}; use anthropic: or openai:")


def _judge(case: Case, result: object | None) -> tuple[str, str]:
    """Objective checks only. Returns `(verdict, text)`.

    Nothing here scores prose. Every rule is one the running service already enforces or
    one the case's own transcript makes checkable.
    """
    if case.expect_none:
        return ("ok" if result is None else "SUMMARISED A CALLER WHO SAID NOTHING", "")
    if result is None:
        # Either the model refused (`is_clear: false`), `D16`'s figure guard fired, or the
        # call failed. The summariser's counters tell them apart in the log; here it is one
        # outcome, and it is the outcome the agent's screen sees: no AI summary.
        return ("refused or failed (no summary)", "")
    text = getattr(result, "text_th", "")
    hits = [needle for needle in case.must_not_contain if needle in text]
    if hits:
        return (f"INVENTED: mentions {', '.join(hits)}", text)
    return ("ok", text)


async def _run_model(
    spec: str, cases: tuple[Case, ...], *, repeat: int, timeout_s: float
) -> ModelRun:
    run = ModelRun(label=spec)
    try:
        settings = _settings_for(spec, timeout_s)
        library = PromptLibrary.load(ROOT / "prompts" / "th")
        llm = build_llm(settings, prompts=library)
    except Exception as exc:  # a missing key, a bad id, a missing extra
        run.error = f"{type(exc).__name__}: {exc}"[:300]
        return run

    summariser = IntakeSummariser(llm=llm, timeout_s=timeout_s)
    for case in cases:
        for _ in range(repeat):
            before = summariser.attempted
            try:
                result = await summariser.summarise(
                    case.turns,
                    intent_label_th=case.intent_label_th,
                    line_label_th=case.line_label_th,
                )
            except Exception as exc:
                run.observations.append(
                    Observation(case=case.name, ok=False, verdict=f"RAISED: {type(exc).__name__}")
                )
                continue
            verdict, text = _judge(case, result)
            called = summariser.attempted > before
            run.observations.append(
                Observation(
                    case=case.name,
                    ok=result is not None,
                    latency_ms=getattr(result, "latency_ms", None) if result else None,
                    tokens_in=getattr(result, "tokens_in", None) if result else None,
                    tokens_out=getattr(result, "tokens_out", None) if result else None,
                    cost_usd=getattr(result, "cost_usd", None) if result else None,
                    text=text,
                    verdict=verdict if called or case.expect_none else "ok (model not called)",
                )
            )
    return run


def _render(runs: list[ModelRun], *, repeat: int, budget_ms: float) -> str:
    lines: list[str] = []
    lines.append("# LLM comparison — the intake summary")
    lines.append("")
    lines.append(
        f"Generated by `scripts/compare_llm.py`. {len(CASES)} cases x {repeat} repeats "
        f"per model, through the real `IntakeSummariser` and the real "
        f"`prompts/th/summarize_intake.v1.md`."
    )
    lines.append("")
    lines.append(
        "`clean` counts observations where the summary came back and stated nothing the "
        "caller did not say. Latency is the adapter's own measurement of the round trip."
    )
    lines.append("")
    lines.append("| model | p50 | worst | inside budget | clean | $/call | tokens in/out |")
    lines.append("|---|---|---|---|---|---|---|")
    for run in sorted(runs, key=lambda r: (r.p50() is None, r.p50() or 0.0)):
        if run.error:
            lines.append(f"| `{run.label}` | — | — | — | **{run.error}** | — | — |")
            continue
        p50, worst = run.p50(), run.worst()
        inside = sum(1 for value in run.latencies if value <= budget_ms)
        cost = run.per_call_cost()
        tin = [o.tokens_in for o in run.observations if o.tokens_in]
        tout = [o.tokens_out for o in run.observations if o.tokens_out]
        cells = [
            f"`{run.label}`",
            f"{p50 / 1000:.2f} s" if p50 is not None else "—",
            f"{worst / 1000:.2f} s" if worst is not None else "—",
            f"{inside}/{len(run.latencies)}" if run.latencies else "—",
            f"{run.clean()}/{len(run.observations)}",
            f"${cost:.5f}" if cost is not None else "n/a",
            f"{int(statistics.mean(tin))}/{int(statistics.mean(tout))}" if tin and tout else "—",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"Budget line: **{budget_ms / 1000:.1f} s** (`--budget-ms`).")
    lines.append("")

    problems = [(run.label, run.problems()) for run in runs if run.problems()]
    lines.append("## Objective failures")
    lines.append("")
    if not problems:
        lines.append("None. Every model returned a summary that stated nothing the caller did not.")
    for label, items in problems:
        lines.append(f"- **`{label}`**")
        for item in sorted(set(items)):
            lines.append(f"  - {item}")
    lines.append("")

    lines.append("## What each model actually wrote")
    lines.append("")
    lines.append("**Read this part.** The table above cannot tell you which Thai is better.")
    lines.append("")
    for case in CASES:
        if case.expect_none:
            continue
        lines.append(f"### {case.name}")
        lines.append("")
        lines.append("> " + " ".join(case.turns))
        lines.append("")
        for run in runs:
            texts = [o.text for o in run.observations if o.case == case.name and o.text]
            if not texts:
                lines.append(f"- **`{run.label}`** — no summary")
                continue
            lines.append(f"- **`{run.label}`** — {texts[0]}")
        lines.append("")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    specs: list[str] = list(args.model) or list(PRESETS[args.preset])
    cases = tuple(c for c in CASES if not args.case or c.name in args.case)

    if args.dry_run:
        print(
            f"{len(specs)} models x {len(cases)} cases x {args.repeat} repeats "
            f"= {len(specs) * len(cases) * args.repeat} calls"
        )
        for spec in specs:
            print("  ", spec)
        return 0

    runs: list[ModelRun] = []
    for spec in specs:
        print(f"  {spec} ...", flush=True)
        runs.append(await _run_model(spec, cases, repeat=args.repeat, timeout_s=args.timeout_s))

    report = _render(runs, repeat=args.repeat, budget_ms=args.budget_ms)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    # ASCII only: this console is cp1252 and the report is full of Thai (`B1`).
    print()
    print(f"{'model':<34} {'p50':>8} {'worst':>8} {'clean':>8} {'$/call':>10}")
    for run in sorted(runs, key=lambda r: (r.p50() is None, r.p50() or 0.0)):
        if run.error:
            print(f"{run.label:<34} {'ERROR':>8} {run.error[:40]}")
            continue
        p50, worst, cost = run.p50(), run.worst(), run.per_call_cost()
        print(
            f"{run.label:<34} "
            f"{(p50 / 1000 if p50 else 0):>7.2f}s "
            f"{(worst / 1000 if worst else 0):>7.2f}s "
            f"{run.clean():>4}/{len(run.observations):<3} "
            f"{(f'${cost:.5f}') if cost is not None else 'n/a':>10}"
        )
    print()
    print(f"Full report (Thai included): {out}")
    return 0


def main() -> int:
    enable_utf8()  # `B1`: Thai on a cp1252 console kills the process.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[], help="provider:model, repeatable")
    parser.add_argument("--preset", default="fast", choices=sorted(PRESETS))
    parser.add_argument("--case", action="append", default=[], help="run only these cases")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument(
        "--budget-ms",
        type=float,
        default=5000.0,
        help="the line the 'inside budget' column counts against",
    )
    parser.add_argument("--out", default="var/llm_compare.md")
    parser.add_argument("--dry-run", action="store_true", help="print the matrix, call nothing")
    parser.add_argument("--list", action="store_true", help="list presets and cases")
    args = parser.parse_args()

    if args.list:
        for name, specs in PRESETS.items():
            print(f"{name}: {', '.join(specs)}")
        print()
        for case in CASES:
            print(f"case {case.name}: {len(case.turns)} turns, expect_none={case.expect_none}")
        return 0

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
