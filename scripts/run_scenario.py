"""Replay a scripted call end to end, with no telephony and no GPU.

    uv run python scripts/run_scenario.py tests/scenarios/pattheera_ipd.yaml

This is the harness that keeps the project unblocked: telephony is the hardest thing to
stand up, so the whole system is designed to be driven without it (`ARCHITECTURE.md` §17).

As of **P3** the identity ladder, **the IVR itself**, the context assembler and the brief
builder are all real services doing real work here — only the *edges* (the phone, the
speech model, the AI, the bank's database) are fakes. The scenario now supplies only what
a caller supplies: which number they dialled and which keys they pressed. Remaining
stand-ins are marked `# P1:`; `grep -rn "# P1:" scripts/` is the handover list.

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
from readycall.adapters.core_data.caching import CachingCoreDataProvider
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.adapters.llm.rulebased import RuleBasedLlm
from readycall.adapters.stt.scripted import ScriptedSttEngine, ScriptedTurn
from readycall.adapters.telephony.simulated import SimulatedTelephonyProvider
from readycall.clock import ManualClock
from readycall.config import load_settings
from readycall.console import enable_utf8
from readycall.domain import events as ev
from readycall.domain.enums import (
    AssuranceLevel,
    CallState,
    ConsentScope,
    EntryChannel,
    ProductLine,
    RatingSource,
    SpeakerRole,
)
from readycall.domain.models import CallIntent, CaseBrief, ContextSnapshot, TranscriptTurn
from readycall.domainpack import DomainPack
from readycall.services.brief import BriefBuilder
from readycall.services.call_orchestrator import CallOrchestrator, InMemoryCallSessionRepository
from readycall.services.context import ContextAssembler
from readycall.services.identity import IdentityResolver, InMemoryCallIntentStore, hash_token
from readycall.services.intake.service import IntakeService
from readycall.services.ivr.personalise import PersonalisationInputs
from readycall.services.ivr.service import IvrService, ScriptedChoices
from readycall.voiceprompts import PromptPack

CONSENT_BY_NAME = {s.value: s for s in ConsentScope}

#: What pressing 1 on the intake offer grants by itself (`D88`). Listing these in a
#: scenario's `consent:` block as well would record the same fact twice.
_OFFER_GRANTS = frozenset({ConsentScope.RECORDING, ConsentScope.AI_PROCESSING})


@dataclass(slots=True)
class Scenario:
    name: str
    description: str = ""
    entry_channel: EntryChannel = EntryChannel.HOTLINE
    customer_id: str | None = None
    product_code: str | None = None
    product_line: ProductLine = ProductLine.UNKNOWN
    app_intent: str | None = None
    caller_number: str | None = None
    dialled_did: str | None = None
    menu_path: list[str] = field(default_factory=list)
    ivr_verifies: bool = False
    agent_id: str = "A001"
    consents: list[str] = field(default_factory=list)
    declines_intake: bool = False
    turns: list[dict[str, Any]] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)
    core_provider: str = "fixtures"
    expect_state: str = "closed"
    expect_assurance: str | None = None
    expect_queue: str | None = None
    expect_intent: str | None = None

    @classmethod
    def load(cls, path: Path) -> Scenario:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entry = raw.get("entry", {})
        intake = raw.get("intake", {})
        menu = raw.get("menu", {})
        expect = raw.get("expect", {})
        return cls(
            name=raw.get("name", path.stem),
            description=raw.get("description", ""),
            entry_channel=EntryChannel(entry.get("channel", "hotline")),
            customer_id=entry.get("customer_id"),
            product_code=entry.get("product_code"),
            product_line=ProductLine(entry.get("product_line", "unknown")),
            app_intent=entry.get("app_intent"),
            caller_number=entry.get("caller_number"),
            dialled_did=entry.get("dialled_did"),
            menu_path=[str(k) for k in (menu.get("path") or [])],
            ivr_verifies=bool(menu.get("ivr_verifies", False)),
            agent_id=raw.get("agent", "A001"),
            consents=list(raw.get("consent", []) or []),
            declines_intake=bool(intake.get("declined", False)),
            turns=list(intake.get("turns", []) or []),
            timing=dict(raw.get("timing", {}) or {}),
            core_provider=raw.get("core_provider", "fixtures"),
            expect_state=raw.get("expect_state", expect.get("state", "closed")),
            expect_assurance=expect.get("assurance"),
            expect_queue=expect.get("queue"),
            expect_intent=expect.get("intent"),
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
        self.intents = InMemoryCallIntentStore()
        self.pack = DomainPack.load(root / "config")
        self.prompts = PromptPack.load(root / "config" / "voice_prompts.yaml")
        self.prompts.validate_against(self.pack)
        self.telephony = SimulatedTelephonyProvider(clock=self.clock)
        self.llm = RuleBasedLlm(clock=self.clock)

        inner = (
            FixtureFileProvider(root / "mock" / "bank_core" / "fixtures")
            if scenario.core_provider == "fixtures"
            else NullCoreDataProvider()
        )
        self.core = CachingCoreDataProvider(inner, clock=self.clock)

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
        self.identity_resolver = IdentityResolver(
            core=self.core, intents=self.intents, clock=self.clock
        )
        self.assembler = ContextAssembler(core=self.core, clock=self.clock)
        self.brief_builder = BriefBuilder(pack=self.pack, clock=self.clock)
        self.ivr = IvrService(
            pack=self.pack,
            prompts=self.prompts,
            telephony=self.telephony,
            orchestrator=self.orchestrator,
            clock=self.clock,
        )
        self.intake = IntakeService(
            pack=self.pack,
            prompts=self.prompts,
            telephony=self.telephony,
            orchestrator=self.orchestrator,
            clock=self.clock,
            bus=self.bus,
            settings=load_settings(config_dir=root / "config"),
        )

        self.snapshot: ContextSnapshot | None = None
        self.brief: CaseBrief | None = None
        self.rating: ev.RatingReceived | None = None
        self.transcript: list[str] = []
        self.notes: list[str] = []

    async def execute(self) -> Any:
        sc = self.scenario
        orch = self.orchestrator

        # --- arrival ------------------------------------------------------------------
        token: str | None = None
        if sc.entry_channel is EntryChannel.IN_APP:
            if not sc.customer_id:
                raise SystemExit("an in_app scenario needs entry.customer_id")
            token = ids.correlation_token()
            now = self.clock.now()
            await self.intents.save(
                CallIntent(
                    intent_id=ids.intent_id(),
                    customer_id=sc.customer_id,
                    product_code=sc.product_code,
                    correlation_token_hash=hash_token(token),
                    created_at=now,
                    expires_at=now.replace(microsecond=0) + _minutes(15),
                )
            )

        telephony_call_id = self.telephony.inject_incoming(
            caller_number=sc.caller_number,
            dialled_number=sc.dialled_did,
            correlation_token=token,
        )

        # --- identity: the assurance ladder, for real now (D20) ------------------------
        identity = await self.identity_resolver.resolve(
            correlation_token=token,
            caller_number=sc.caller_number,
            ivr_verified_customer_id=(
                sc.customer_id if sc.ivr_verifies and sc.customer_id else None
            ),
        )
        self.notes.append(
            f"identity: {identity.assurance} via {identity.method}"
            f" -> {identity.customer_id or 'nobody'}"
            f" (disclose policy details: {identity.may_disclose_policy_details})"
        )

        # --- the DID, and what it implies (D19) ---------------------------------------
        did = self.pack.did(sc.dialled_did) if sc.dialled_did else None
        line = sc.product_line
        if line is ProductLine.UNKNOWN and did is not None:
            line = did.product_line
        if did is not None:
            self.notes.append(
                f"DID {did.number}: {did.label} -> line={did.product_line}"
                f", menu step 1 {'skipped' if did.skip_product_menu else 'needed'}"
            )

        if sc.entry_channel is EntryChannel.IN_APP:
            session = await orch.start_from_intent(
                intent_id=self.intents.all()[0].intent_id,
                customer_id=sc.customer_id or "",
                product_code=sc.product_code,
                product_line=line,
            )
            session.telephony_call_id = telephony_call_id
            session.provider = self.telephony.name
        else:
            session = await orch.start_cold_call(
                entry_channel=sc.entry_channel,
                telephony_call_id=telephony_call_id,
                caller_number=sc.caller_number,
                dialled_did=sc.dialled_did,
                product_line=line,
                provider=self.telephony.name,
            )
        session.identity = identity
        session.customer_id = identity.customer_id

        # --- context prefetch: starts NOW, before the phone is answered (D6) ----------
        self.snapshot = await self.assembler.build(
            customer_id=identity.customer_id, product_code=sc.product_code, product_line=line
        )
        session.snapshot_id = self.snapshot.snapshot_id
        session.record_timing("context_build_ms", self.snapshot.build_ms or 0.0)
        self.notes.append(
            f"context prefetched in {self.snapshot.build_ms:.1f}ms: "
            f"{len(self.snapshot.payload.active_policies)} active policies, "
            f"{len(self.snapshot.payload.recent_interactions)} interactions, "
            f"{len(self.snapshot.provenance)} fields with provenance"
        )

        if sc.entry_channel is EntryChannel.IN_APP:
            session = await orch.transition(session, CallState.CONNECTING, reason="app_placed_call")
        await self.telephony.answer(telephony_call_id)
        self.clock.advance(sc.seconds("connect", 1.0))

        # --- IVR: the menu routes the call, before any AI (D37) -----------------------
        # The real service since P3. The scenario supplies the keypresses; everything
        # else - greeting, notice, menu order, reserved keys, retries, the queue - is
        # the production walk. `ScriptedChoices` presses CANONICAL keys, so a scenario
        # keeps meaning what it says even when the menu is reordered (`D81`).
        ivr_result = await self.ivr.run(
            session,
            caller=ScriptedChoices(sc.menu_path),
            did=did,
            known_intent=sc.app_intent if sc.entry_channel is EntryChannel.IN_APP else None,
            inputs=PersonalisationInputs.from_snapshot(
                self.snapshot.payload if self.snapshot else None, today=self.clock.now().date()
            ),
        )
        outcome = ivr_result.outcome
        intent_code = ivr_result.intent_code
        if outcome.product_line is not ProductLine.UNKNOWN:
            line = outcome.product_line
        self.notes.append(
            f"ivr {outcome.kind.value}: {len(ivr_result.played)} lines played, "
            f"keys {'/'.join(outcome.pressed) or '-'} -> path {'/'.join(outcome.path) or '-'}"
            f", line={line}, intent={intent_code or 'none'} ({ivr_result.intent_source})"
        )
        if ivr_result.personalised:
            self.notes.append(
                "menu reordered for this caller: " + "; ".join(ivr_result.promoted_because)
            )

        # Consent the OFFER does not cover. Pressing 1 grants `recording` and
        # `ai_processing` together (`D88`), so listing them here as well would write the
        # same fact twice; `health_data` is a genuinely separate scope (`D14`) and is the
        # only one that still needs its own row.
        for name in sc.consents:
            scope = CONSENT_BY_NAME.get(name)
            if scope is None:
                raise SystemExit(f"unknown consent scope in scenario: {name!r}")
            if scope in _OFFER_GRANTS:
                continue
            session = await orch.record_consent(
                session, scope=scope, granted=True, basis="ivr_keypress", channel="ivr"
            )
        self.clock.advance(sc.seconds("ivr", 8.0))

        # --- queue: known from the menu, not from AI ----------------------------------
        queue_id = ivr_result.queue_id
        session = await orch.enqueue(session, queue_id=queue_id, position=3, estimated_wait_s=90.0)

        # --- brief v1: context only, no speech, no AI ---------------------------------
        self.brief = self.brief_builder.build_context_only(
            call_session_id=session.call_session_id,
            snapshot=self.snapshot,
            identity=identity,
            intent_code=intent_code,
            product_line=line,
            urgency_floor=did.urgency_floor if did else None,
        )
        session.brief_version = self.brief.version

        # --- the hold: position, offer, and the recording if they want one -------------
        #
        # The real service since P3 step 4. Only the keypress is scripted, exactly as the
        # menu's is: `declined: true` presses 2, a scenario with turns presses 1, and a
        # scenario with neither says nothing and falls through to hold.
        intake_keys = ["2"] if sc.declines_intake else (["1"] if sc.turns else [])
        hold = await self.intake.run_offer(session, caller=ScriptedChoices(intake_keys))
        self.notes.append(
            f"hold: {len(hold.played)} lines played, keys {'/'.join(hold.pressed) or '-'}"
            f" -> consent={hold.consented}, {hold.degraded}"
        )

        # P3-media: the media gateway and VAD land in the next slice, so the utterances
        # come from the scripted engine on the scenario's own timings rather than from
        # audio. Everything downstream of `on_turn` is the production path.
        if hold.recording:
            await self.stt.warmup()
            for seq, _ in enumerate(sc.turns, start=1):
                result = await self.stt.transcribe_utterance([])
                if not result.text:
                    break
                self.transcript.append(result.text)
                await self.intake.on_turn(
                    session.call_session_id,
                    TranscriptTurn(
                        turn_id=ids.turn_id(),
                        call_session_id=session.call_session_id,
                        seq=seq,
                        speaker_role=SpeakerRole.CUSTOMER,
                        text=result.text,
                        t_start_ms=result.t_start_ms,
                        t_end_ms=result.t_end_ms,
                        asr_confidence=result.confidence,
                        engine=result.engine,
                        engine_version=result.engine_version,
                    ),
                )
                self.clock.advance(sc.seconds("per_turn", 4.0))
            # They stopped talking. One re-prompt, then the recording closes (`D88`).
            await self.intake.on_silence(session.call_session_id)
            await self.intake.on_silence(session.call_session_id)
        elif sc.declines_intake:
            self.notes.append(
                "caller pressed 2 - routing already settled by the menu, so the brief is "
                "menu-derived rather than empty (D37)"
            )
        elif not session.may_run_intake:
            self.notes.append("no consent - intake skipped, call proceeds anyway (D14)")

        self.clock.advance(sc.seconds("wait", 20.0))

        # --- matching and the offer handshake -----------------------------------------
        # P1: the matching engine lands in P2; the agent is named by the scenario.
        session = await orch.transition(session, CallState.MATCHED, reason="agent_available")
        session.assigned_agent_id = sc.agent_id
        session = await orch.transition(
            session, CallState.OFFERED, reason=f"offered_to:{sc.agent_id}"
        )
        self.clock.advance(sc.seconds("offer", 6.0))
        # The offer window IS the intake's grace period (`D21`), so this is what closes
        # the hold - including for a caller who never answered the offer at all.
        final = await self.intake.on_agent_accepted(session.call_session_id)
        if final is not None and final.kind is not None:
            self.notes.append(f"hold ended on accept: {final.kind}")
        await self.telephony.bridge(telephony_call_id, f"sip:{sc.agent_id}@readycall.local")
        session = await orch.transition(session, CallState.IN_CALL, reason="agent_accepted")

        self.clock.advance(sc.seconds("call", 180.0))
        session = await orch.transition(session, CallState.WRAP_UP, reason="caller_hung_up")

        # The customer rates in the IVR within seconds of hanging up, while the agent is
        # still writing the wrap-up. The two are concurrent, so the rating is an event
        # attached to the call rather than a state the call passes through (`D46`).
        acw_s = sc.seconds("acw", 30.0)
        rating_delay_s = min(sc.seconds("rating_delay", 5.0), acw_s)
        self.clock.advance(rating_delay_s)
        self.rating = ev.RatingReceived(
            call_session_id=session.call_session_id,
            occurred_at=self.clock.now(),
            trace_id=session.trace_id,
            source=str(RatingSource.CUSTOMER_IVR),
            csat=int(sc.timing.get("csat", 4)),
        )
        await self.bus.publish(self.rating)

        # P1: the agent's own rating (D27) lands with the workstation at P2.
        self.clock.advance(acw_s - rating_delay_s)
        session = await orch.transition(session, CallState.CLOSED, reason="wrapup_saved")

        await self.telephony.hangup(telephony_call_id, "completed")
        await self.bus.drain()
        return session


def _minutes(n: int) -> Any:
    from datetime import timedelta

    return timedelta(minutes=n)


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

    if run.brief is not None:
        brief = run.brief
        add("")
        add(f"CASE BRIEF  (v{brief.version}, {brief.kind})")
        add(f"  intent      {brief.intent.intent_code}  [{brief.intent.source}]")
        add(f"              {brief.intent.label_th}")
        add(f"  urgency     {brief.urgency}")
        add(f"  queue       {session.queue_id}")
        add(
            f"  assurance   {brief.identity.assurance}  ->  policy details "
            + ("shown" if brief.identity.may_disclose_policy_details else "WITHHELD")
        )
        if brief.degraded.value != "none":
            add(f"  degraded    {brief.degraded}")
        add(f"  summary     {brief.summary_th}")
        add("  actions")
        for action in brief.recommended_actions:
            add(f"    {action.order}. {action.text_th}")
        add(f"  opening     {brief.suggested_opening_th}")

    if run.snapshot is not None and run.snapshot.provenance:
        add("")
        add("PROVENANCE  (every field can say where it came from and when)")
        for prov in run.snapshot.provenance:
            flag = " [STALE]" if prov.stale else ""
            add(f"  {prov.field:<22} {prov.source:<26} via {prov.provider}{flag}")

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
    add("RATING")
    if run.rating is not None:
        # Printed with the offset it actually arrived at, not the moment the call closed.
        # The customer rates while the agent is still typing - that is the whole point
        # of the rating being an event rather than a state (`D46`).
        offset = (run.rating.occurred_at - session.created_at).total_seconds()
        add(f"  csat={run.rating.csat}/5  via {run.rating.source}  at +{offset:.1f}s")
        closed = (session.ended_at - session.created_at).total_seconds()
        add(f"  (call closed at +{closed:.1f}s - the rating landed {closed - offset:.1f}s earlier)")
    else:
        add("  (none - the customer hung up without rating, which is normal)")

    add("")
    add(f"FINAL STATE  {session.state}   reason={session.end_reason}")
    add(f"DURATION     {(session.ended_at - session.created_at).total_seconds():.1f}s (simulated)")
    return "\n".join(lines)


def check(run: ScenarioRun, session: Any) -> list[str]:
    """Assert whatever the scenario declared it expects."""
    sc = run.scenario
    problems: list[str] = []
    if str(session.state) != sc.expect_state:
        problems.append(f"expected final state {sc.expect_state}, got {session.state}")
    if sc.expect_assurance and session.identity is not None:
        actual = str(session.identity.assurance)
        if actual != sc.expect_assurance:
            problems.append(f"expected assurance {sc.expect_assurance}, got {actual}")
    if sc.expect_queue and session.queue_id != sc.expect_queue:
        problems.append(f"expected queue {sc.expect_queue}, got {session.queue_id}")
    if sc.expect_intent and run.brief is not None:
        actual_intent = run.brief.intent.intent_code if run.brief.intent else None
        if actual_intent != sc.expect_intent:
            problems.append(f"expected intent {sc.expect_intent}, got {actual_intent}")
    if run.bus.errors:
        problems.append(f"{len(run.bus.errors)} event handler error(s)")
    return problems


async def main_async(path: Path, *, quiet: bool) -> int:
    root = Path(__file__).resolve().parents[1]
    ids.install(ids.DeterministicIds())
    rc_logging.configure(log_format="console", level="ERROR" if quiet else "INFO")

    scenario = Scenario.load(path)
    run = ScenarioRun(scenario, root=root)
    session = await run.execute()

    print(render(run, session))

    problems = check(run, session)
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


__all__ = ["AssuranceLevel", "Scenario", "ScenarioRun", "check", "render"]

if __name__ == "__main__":
    raise SystemExit(main())
