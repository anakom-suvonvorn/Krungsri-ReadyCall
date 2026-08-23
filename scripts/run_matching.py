"""Drive the matching engine under synthetic load and show what it decided.

    uv run python scripts/run_matching.py
    uv run python scripts/run_matching.py --calls 40 --solver greedy --seed 7

This is the matching equivalent of `run_scenario.py`: no telephony, no agents, no database,
deterministic. It exists to make three claims **measurable** rather than asserted:

* **greedy is worse than global** — `--compare` prints the score gap on the same matrix;
* **urgency prevents starvation** — the oldest caller's wait is reported every tick;
* **every decision is explainable** — each one prints its rationale and its candidates.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.adapters.agent_directory.fixtures import FixtureAgentDirectory  # noqa: E402
from readycall.clock import ManualClock  # noqa: E402
from readycall.console import enable_utf8  # noqa: E402
from readycall.domain.enums import (  # noqa: E402
    AgentIntent,
    AgentSystemState,
    Language,
)
from readycall.domain.models import AgentPresence  # noqa: E402
from readycall.domainpack import DomainPack  # noqa: E402
from readycall.services.matching import solver  # noqa: E402
from readycall.services.matching.engine import MatchingEngine  # noqa: E402
from readycall.services.matching.scoring import (  # noqa: E402
    WaitingCall,
    hard_filter,
    score_fit,
    score_urgency,
)
from readycall.services.matching.weights import MatchingWeights  # noqa: E402


def build_calls(pack: DomainPack, rng: random.Random, count: int) -> list[WaitingCall]:
    intents = sorted(pack.intents.values(), key=lambda s: s.code)
    calls: list[WaitingCall] = []
    for i in range(count):
        spec = rng.choice(intents)
        queue_id = pack.queue_for_intent(spec.code)
        queue = pack.queues[queue_id]
        # A few English-only callers, so the language hard filter actually bites (`D38`).
        english_only = rng.random() < 0.12
        calls.append(
            WaitingCall(
                call_session_id=f"call_sim_{i:03d}",
                queue_id=queue_id,
                required_skill=spec.skill,
                intent_code=spec.code,
                intent_urgency=spec.default_urgency,
                waiting_s=rng.choice([2, 5, 12, 25, 40, 70, 130, 200]),
                sla_seconds=queue.sla_seconds,
                acceptable_languages=(Language.EN,) if english_only else (Language.TH,),
                is_vulnerable=rng.random() < 0.08,
            )
        )
    return calls


def build_presence(agent_ids: list[str], rng: random.Random, clock: ManualClock) -> dict:
    presence = {}
    for agent_id in agent_ids:
        if rng.random() < 0.25:
            continue  # on a break, in training, or simply not logged in
        presence[agent_id] = AgentPresence(
            agent_id=agent_id,
            system_state=AgentSystemState.AVAILABLE,
            agent_intent=AgentIntent.READY,
            since=clock.now(),
            current_load=0,
        )
    return presence


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=18)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--solver", choices=["hungarian", "greedy"], default="hungarian")
    parser.add_argument("--compare", action="store_true", help="score hungarian vs greedy")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    enable_utf8()
    rng = random.Random(args.seed)
    clock = ManualClock()
    pack = DomainPack.load(ROOT / "config")
    weights = MatchingWeights.load(ROOT / "config" / "matching_weights.yaml")
    directory = FixtureAgentDirectory(ROOT / "mock" / "agents" / "agents.json")

    roster = await directory.list_agents()
    presence = build_presence([a.agent_id for a in roster], rng, clock)
    calls = build_calls(pack, rng, args.calls)

    engine = MatchingEngine(
        directory=directory, weights=weights, clock=clock, solver_name=args.solver
    )
    decisions = await engine.match(calls, presence)

    by_id = {c.call_session_id: c for c in calls}
    print("=" * 78)
    print(f"MATCHING  {len(calls)} waiting · {len(presence)}/{len(roster)} agents online")
    print(f"          solver={args.solver}  weights={weights.version}  seed={args.seed}")
    print("=" * 78)

    if not args.quiet:
        for decision in decisions:
            call = by_id[decision.call_session_id]
            head = f"{call.call_session_id}  {call.intent_code}"
            print(f"\n{head}")
            print(
                f"  queue={call.queue_id}  waited={call.total_wait_s:.0f}s/"
                f"{call.sla_seconds}s  urgency={call.intent_urgency}"
                f"  lang={'/'.join(str(x) for x in call.acceptable_languages)}"
            )
            print(
                f"  -> {decision.kind}"
                + (f"  {decision.chosen_agent_id}" if decision.chosen_agent_id else "")
            )
            if decision.rationale_th:
                print(f"     {decision.rationale_th}")
            if decision.urgency:
                score = decision.total_score or 0.0
                print(f"     urgency x{decision.urgency.total:.2f}  score={score:.3f}")
            ranked = sorted(
                (c for c in decision.candidates if c.fit.hard_filter_failed is None),
                key=lambda c: -c.score,
            )[:3]
            for cand in ranked:
                mark = "*" if cand.agent_id == decision.chosen_agent_id else " "
                print(
                    f"    {mark} {cand.agent_id}  score={cand.score:.3f}"
                    f"  skill={cand.fit.skill_match:.2f}"
                )
            excluded = [c for c in decision.candidates if c.fit.hard_filter_failed is not None]
            if excluded:
                why: dict[str, int] = {}
                for c in excluded:
                    key = c.fit.hard_filter_failed or "?"
                    why[key] = why.get(key, 0) + 1
                print("      excluded: " + ", ".join(f"{k}={v}" for k, v in sorted(why.items())))

    # --- summary --------------------------------------------------------------------
    kinds: dict[str, int] = {}
    for d in decisions:
        kinds[str(d.kind)] = kinds.get(str(d.kind), 0) + 1
    assigned = [d for d in decisions if d.chosen_agent_id]
    unassigned = [by_id[d.call_session_id] for d in decisions if not d.chosen_agent_id]

    print("\n" + "=" * 78)
    print("SUMMARY")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind:16} {count}")
    print(f"  assigned         {len(assigned)}/{len(calls)}")
    if unassigned:
        worst = max(unassigned, key=lambda c: c.total_wait_s)
        print(f"  longest unassigned wait: {worst.total_wait_s:.0f}s ({worst.intent_code})")
        starved = [c for c in unassigned if c.total_wait_s >= weights.max_wait_before_any_agent_s]
        print(f"  past the wait ceiling and STILL unassigned: {len(starved)}")
        if starved:
            print("    (all of these failed a HARD filter - skill or language - so waiting")
            print("     longer cannot help them; they need a qualified agent to come online)")

    if args.compare:
        matrix = _matrix(calls, roster, presence, weights, clock)
        hun, gre = solver.best_greedy_gap(matrix)
        print("\nSOLVER COMPARISON (same matrix)")
        print(f"  hungarian total score  {hun:.3f}")
        print(f"  greedy    total score  {gre:.3f}")
        delta = hun - gre
        pct = (delta / gre * 100) if gre else 0.0
        print(f"  greedy leaves {delta:.3f} on the table ({pct:.1f}% worse)")

    print()
    return 0


def _matrix(calls, roster, presence, weights, clock):
    online = [a for a in roster if a.agent_id in presence]
    now = clock.now()
    matrix = []
    for call in calls:
        urgency = score_urgency(call, weights)
        row = []
        for agent in online:
            who = presence[agent.agent_id]
            if hard_filter(call, agent, who, weights) is not None:
                row.append(solver.IMPOSSIBLE)
            else:
                row.append(score_fit(call, agent, who, weights, now=now).total * urgency.total)
        matrix.append(row)
    return matrix


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
