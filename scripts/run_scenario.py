"""Replay a scripted call end to end, with no telephony and no GPU.

    uv run python scripts/run_scenario.py tests/scenarios/pattheera_ipd.yaml

This is the harness that keeps the project unblocked: telephony is the hardest thing
to stand up, so the whole system is designed to be driven without it
(`ARCHITECTURE.md` §17). It uses the real orchestrator, the real state machine, the
real event bus and the real fixtures provider — only the *edges* are fakes.

In P0 this script performs the lifecycle steps itself. As each service lands (identity
in P1, matching in P2, IVR/intake in P3, analysis in P4) it hands that step over and
stops doing it by hand. The `# P0:` comments mark what is still standing in.

Deterministic by construction: `ManualClock` + `DeterministicIds` mean two runs produce
byte-identical output, which is what makes golden-output comparison possible.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from readycall import ids
from readycall import logging as rc_logging
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.adapters.llm.rulebased import RuleBasedLlm
from readycall.adapters.stt.scripted import ScriptedSttEngine, ScriptedTurn
from readycall.adapters.telephony.simulated import SimulatedTelephonyProvider
from readycall.clock import ManualClock
from readycall.console import enable_utf8
from readycall.domain.enums import (
    CallState,
    ConsentScope,
    EntryChannel,
    ProductLine,
)
from readycall.services.call_orchestrator import (
    CallOrchestrator,
    InMemoryCallSessionRepository,
)

CONSENT_BY_NAME = {s.value: s for s in ConsentScope}


@dataclass(slots=True)
class Scenario:
    name: str
    description: str = ""
    entry_channel: EntryChannel = EntryChannel.HOTLINE
    customer_id: str | None = None
    product_code: str | None = None
    product_line: ProductLine = ProductLine.UNKNOWN
    caller_number: str | None = None
    dialled_did: str | None = None
    queue_id: str = "q_general"
    agent_id: str = "A001"
    consents: list[str] = field(default_factory=list)
    declines_intake: bool = False
    turns: list[dict[str, Any]] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)
    core_provider: str = "fixtures"
    expect_state: str = "closed"

    @classmethod
    def load(cls, path: Path) -> Scenario:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entry = raw.get("entry", {})
        intake = raw.get("intake", {})
        return cls(
            name=raw.get("name", path.stem),
            description=raw.get("description", ""),
            entry_channel=EntryChannel(entry.get("channel", "hotline")),
            customer_id=entry.get("customer_id"),
            product_code=entry.get("product_code"),
            product_line=ProductLine(entry.get("product_line", "unknown")),
            caller_number=entry.get("caller_number"),
            dialled_did=entry.get("dialled_did"),
            queue_id=raw.get("queue", "q_general"),
            agent_id=raw.get("agent", "A001"),
            consents=list(raw.get("consent", []) or []),
            declines_intake=bool(intake.get("declined", False)),
            turns=list(intake.get("turns", []) or []),
            timing=dict(raw.get("timing", {}) or {}),
            core_provider=raw.get("core_provider", "fixtures"),
            expect_state=raw.get("expect_state", "closed"),
        )

    def seconds(self, key: str, default: float) -> float:
        return float(self.timing.get(key, default))


class ScenarioRun:
    """One replay, with everything it produced kept for inspection."""

    def __init__(self, scenario: Scenario, *, root: Path) -> None:
        self.scenario = scenario
        self.root = root
        self.clock = ManualClock()
        self.bus = InMemoryEventBus()
        self.repo = InMemoryCallSessionRepository()
        self.telephony = SimulatedTelephonyProvider(clock=self.clock)
        self.llm = RuleBasedLlm(clock=self.clock)
        self.core = (
            FixtureFileProvider(root / "mock" / "bank_core" / "fixtures")
            if scenario.core_provider == "fixtures"
            else NullCoreDataProvider()
        )
        self.stt = ScriptedSttEngine(
            [
                ScriptedTurn(
                    text=t["text"],
                    t_start_ms=int(t.get("t_start_ms", 0)),
                    t_end_ms=int(t.get("t_end_ms", 0)),
                    confidence=float(t.get("confidence", 0.93)),
                )
                for t in scenario.turns
            ]
        )
        self.orchestrator = CallOrchestrator(repository=self.repo, bus=self.bus, clock=self.clock)
        self.transcript: list[str] = []
        self.notes: list[str] = []

    async def execute(self) -> Any:
        sc = self.scenario
        orch = self.orchestrator

        # --- arrival ------------------------------------------------------------------
        token = ids.correlation_token() if sc.entry_channel is EntryChannel.IN_APP else None
        telephony_call_id = self.telephony.inject_incoming(
            caller_number=sc.caller_number,
            dialled_number=sc.dialled_did,
            correlation_token=token,
        )

        if sc.entry_channel is EntryChannel.IN_APP:
            if not sc.customer_id:
                raise SystemExit("an in_app scenario needs entry.customer_id")
            session = await orch.start_from_intent(
                intent_id=ids.intent_id(),
                customer_id=sc.customer_id,
                product_code=sc.product_code,
                product_line=sc.product_line,
            )
            session.telephony_call_id = telephony_call_id
            session.provider = self.telephony.name
            # P0: the context assembler lands in P1. Prove the read path works now.
            await self._prefetch_context(sc.customer_id)
            session = await orch.transition(session, CallState.CONNECTING, reason="app_placed_call")
        else:
            session = await orch.start_cold_call(
                entry_channel=sc.entry_channel,
                telephony_call_id=telephony_call_id,
                caller_number=sc.caller_number,
                dialled_did=sc.dialled_did,
                product_line=sc.product_line,
                provider=self.telephony.name,
            )
            # P0: the identity resolver lands in P1. ANI lookup only, no assurance model.
            if sc.caller_number:
                found = await self.core.find_customer_by_phone(sc.caller_number)
                if found:
                    session.customer_id = found.customer_id
                    self.notes.append(
                        f"ANI {sc.caller_number} matched {found.customer_id} "
                        f"({found.polite_name_th}) - probable identity only (D20)"
                    )
                    await self._prefetch_context(found.customer_id)
                else:
                    self.notes.append(
                        f"ANI {sc.caller_number} matched nobody - anonymous path (L0)"
                    )

        await self.telephony.answer(telephony_call_id)
        self.clock.advance(sc.seconds("connect", 1.0))

        # --- IVR ---------------------------------------------------------------------
        # P0: the IVR service lands in P3; consent capture is scripted here.
        session = await orch.enter_ivr(session)
        for name in sc.consents:
            scope = CONSENT_BY_NAME.get(name)
            if scope is None:
                raise SystemExit(f"unknown consent scope in scenario: {name!r}")
            session = await orch.record_consent(
                session, scope=scope, granted=True, basis="ivr_keypress"
            )
        self.clock.advance(sc.seconds("ivr", 8.0))

        # --- queue -------------------------------------------------------------------
        session = await orch.enqueue(
            session, queue_id=sc.queue_id, position=3, estimated_wait_s=90.0
        )

        # --- intake ------------------------------------------------------------------
        may_intake = session.may_run_intake and not sc.declines_intake and bool(sc.turns)
        if may_intake:
            session = await orch.transition(
                session, CallState.INTAKE_ACTIVE, reason="consent_given_press_1"
            )
            session.intake_id = ids.intake_id()
            # P0: the media gateway and VAD land in P3. Here the scripted engine
            # stands in for "an utterance was endpointed and transcribed".
            await self.stt.warmup()
            for _ in sc.turns:
                result = await self.stt.transcribe_utterance([])
                if not result.text:
                    break
                self.transcript.append(result.text)
                self.clock.advance(sc.seconds("per_turn", 4.0))
            session = await orch.transition(
                session, CallState.INTAKE_COMPLETE, reason="silence_timeout"
            )
        elif sc.declines_intake:
            self.notes.append("caller pressed 2 - context-only brief, and that is fine (D19)")
        elif not session.may_run_intake:
            self.notes.append("no consent - intake skipped, call proceeds anyway (D14)")

        self.clock.advance(sc.seconds("wait", 20.0))

        # --- matching and the offer handshake ----------------------------------------
        # P0: the matching engine lands in P2; the agent is named by the scenario.
        session = await orch.transition(session, CallState.MATCHED, reason="agent_available")
        session.assigned_agent_id = sc.agent_id
        session = await orch.transition(
            session, CallState.OFFERED, reason=f"offered_to:{sc.agent_id}"
        )
        # The offer window is the intake grace period (D21).
        self.clock.advance(sc.seconds("offer", 6.0))
        await self.telephony.bridge(telephony_call_id, f"sip:{sc.agent_id}@readycall.local")
        session = await orch.transition(session, CallState.IN_CALL, reason="agent_accepted")

        # --- the call, then wrap-up --------------------------------------------------
        self.clock.advance(sc.seconds("call", 180.0))
        session = await orch.transition(session, CallState.WRAP_UP, reason="caller_hung_up")
        self.clock.advance(sc.seconds("acw", 30.0))
        session = await orch.transition(session, CallState.RATING, reason="wrapup_saved")
        session = await orch.transition(session, CallState.CLOSED, reason="rating_received")

        await self.telephony.hangup(telephony_call_id, "completed")
        await self.bus.drain()
        return session

    async def _prefetch_context(self, customer_id: str) -> None:
        """Context assembly starts before the phone rings (`D6`).

        P0: this is a raw read to prove the port works and to measure it. The real
        assembler, with provenance and freezing, lands in P1.
        """
        watch = self.orchestrator.stopwatch()
        customer = await self.core.get_customer(customer_id)
        policies = await self.core.list_policies(customer_id)
        interactions = await self.core.list_interactions(customer_id, limit=5)
        elapsed = watch.elapsed_ms()
        if customer is None:
            self.notes.append(f"context prefetch: customer {customer_id} not found")
            return
        self.notes.append(
            f"context prefetch in {elapsed:.1f}ms: {len(policies)} active policies, "
            f"{len(interactions)} recent interactions"
        )


def render(run: ScenarioRun, session: Any) -> str:
    sc = run.scenario
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"SCENARIO  {sc.name}")
    if sc.description:
        add(f"          {sc.description}")
    add(f"entry     {sc.entry_channel}" + (f" via {sc.dialled_did}" if sc.dialled_did else ""))
    add(f"call      {session.call_session_id}   customer={session.customer_id}")
    add("=" * 78)

    add("")
    add("TIMELINE")
    for row in CallOrchestrator.timeline(session):
        add("  " + row)

    if run.notes:
        add("")
        add("NOTES")
        for note in run.notes:
            add(f"  - {note}")

    if run.transcript:
        add("")
        add("INTAKE TRANSCRIPT (scripted)")
        for i, text in enumerate(run.transcript, 1):
            add(f"  {i}. {text}")

    add("")
    add("EVENTS")
    for name in run.bus.names(session.call_session_id):
        add(f"  {name}")

    add("")
    add("CONSENT")
    if session.consents:
        for consent in session.consents:
            add(f"  {consent.scope}: {'granted' if consent.granted else 'refused'}")
    else:
        add("  (none - intake skipped, call unaffected)")

    add("")
    add(f"FINAL STATE  {session.state}   reason={session.end_reason}")
    add(f"DURATION     {(session.ended_at - session.created_at).total_seconds():.1f}s (simulated)")
    return "\n".join(lines)


async def main_async(path: Path, *, quiet: bool) -> int:
    root = Path(__file__).resolve().parents[1]
    ids.install(ids.DeterministicIds())
    rc_logging.configure(log_format="console", level="ERROR" if quiet else "INFO")

    scenario = Scenario.load(path)
    run = ScenarioRun(scenario, root=root)
    session = await run.execute()

    print(render(run, session))

    problems: list[str] = []
    if str(session.state) != scenario.expect_state:
        problems.append(f"expected final state {scenario.expect_state}, got {session.state}")
    if run.bus.errors:
        problems.append(f"{len(run.bus.errors)} event handler error(s)")
    if problems:
        print("")
        for problem in problems:
            print(f"FAIL  {problem}")
        return 1
    print("")
    print("OK    scenario completed as expected")
    return 0


def main() -> int:
    enable_utf8()
    parser = argparse.ArgumentParser(description="Replay a scripted ReadyCall scenario")
    parser.add_argument("scenario", type=Path, help="path to a scenario YAML file")
    parser.add_argument("--quiet", action="store_true", help="suppress info logs")
    args = parser.parse_args()
    if not args.scenario.exists():
        print(f"no such scenario: {args.scenario}", file=sys.stderr)
        return 2
    return asyncio.run(main_async(args.scenario, quiet=args.quiet))


if __name__ == "__main__":
    raise SystemExit(main())
