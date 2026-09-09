"""Everything the API needs, built once and handed to routes by dependency injection.

The container is assembled from `Settings`, so switching the bank-data adapter or the event
bus is an env var (`D3`). Routes never construct anything themselves — they ask for what
they need, which is what keeps them thin enough to read in one screen.

The one piece of behaviour that lives here rather than in a service is the **prefetch
handler**: on `intent.created`, build the context snapshot. It sits here because it is
wiring — deciding *that* the assembler runs on that event — rather than logic. Doing it
this way is what puts assembly off the request path (`D6`): the endpoint publishes and
returns a dial target immediately, and the six reads to the bank core happen afterwards,
while the customer is still lifting the phone to their ear.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Request

from readycall.adapters.agent_directory.fixtures import FixtureAgentDirectory
from readycall.adapters.blob_storage import build_blob_storage
from readycall.adapters.core_data.caching import CachingCoreDataProvider
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.adapters.llm import build_fast_llm, build_llm
from readycall.adapters.llm.openai_compatible import OPENAI_BASE_URL
from readycall.adapters.stt.scripted import ScriptedSttEngine, load_scripted_turns
from readycall.adapters.telephony.simulated import SimulatedTelephonyProvider
from readycall.adapters.vad.energy import EnergyVad
from readycall.api.realtime import AgentHub
from readycall.api.schemas import (
    BriefContextOut,
    BriefCoverageOut,
    BriefCustomerOut,
    BriefIntentOut,
    BriefOut,
    BriefPolicyOut,
    BriefProvenanceOut,
    KnownFactOut,
)
from readycall.api.security import (
    DemoAgentSessionStore,
    DemoSessionStore,
    Principal,
    SessionResolver,
)
from readycall.clock import Clock, SystemClock
from readycall.config import (
    CoreDataProviderName,
    LlmProviderName,
    Settings,
    SttEngineName,
    SttWorkerMode,
    VadEngineName,
)
from readycall.db.storage import Storage, build_storage
from readycall.domain import events as ev
from readycall.domain.enums import CallState, ProductLine, Urgency
from readycall.domain.models import CallSession, CallWrapup, CaseBrief, IdentityResolution
from readycall.domainpack import DomainPack
from readycall.logging import get_logger
from readycall.ports.core_data import CoreDataProvider
from readycall.ports.stt import SttEngine, SttHint
from readycall.ports.vad import VoiceActivityDetector
from readycall.prompts import PromptLibrary
from readycall.services.agents.assignment import AssignmentService, OfferPolicy
from readycall.services.agents.dispatch import DispatchService
from readycall.services.agents.presence import PresenceService
from readycall.services.analysis import (
    ComparisonReasonWriter,
    ContextSummariser,
    IntakeSummariser,
    SummaryResult,
)
from readycall.services.assist import AssistService
from readycall.services.brief.builder import BriefBuilder
from readycall.services.brief.context_facts import facts_for_prompt, known_facts
from readycall.services.call_orchestrator.orchestrator import CallOrchestrator
from readycall.services.capture.keypad import KeypadCaptureService
from readycall.services.capture.matching import (
    DateMatch,
    DigitMatch,
    best_digit_match,
    match_date,
)
from readycall.services.comparison import ComparisonService
from readycall.services.context.assembler import ContextAssembler
from readycall.services.context.store import AppContextEvent, InMemoryAppContextStore
from readycall.services.identity.attestation import AttestationService
from readycall.services.identity.intents import IntentService
from readycall.services.identity.resolver import IdentityResolver
from readycall.services.identity.store import InMemoryCallIntentStore
from readycall.services.intake.service import IntakeService
from readycall.services.ivr.service import IvrService
from readycall.services.matching.engine import MatchingEngine
from readycall.services.matching.scoring import WaitingCall
from readycall.services.matching.weights import MatchingWeights
from readycall.services.queues.hours import QueueHours
from readycall.services.recording.service import RecordingService
from readycall.services.transcription.delivery import TranscriptDeliveryService
from readycall.services.transcription.service import TranscriptionService
from readycall.services.transcription.store import TranscriptRecorder
from readycall.services.transfer import TransferService
from readycall.voiceprompts import load_prompt_pack

log = get_logger(__name__)

#: How many times one call's preview summary may be recomputed while its offer card is on
#: screen (`D131`). The refresh runs on every sweep, so without a ceiling a caller who
#: keeps talking buys a model call a second for the whole offer window. Three is already
#: more updates than a human reads off a card they are about to accept.
_MAX_PREVIEWS = 3


def _digits_of(value: str) -> str:
    """`MT-2025-004512` -> `2025004512`. A caller keys digits; we store formatted ids."""
    return "".join(ch for ch in value if ch.isdigit())


def _digit_tier_th(hit: DigitMatch, noun_th: str) -> str:
    """Say *which rung* matched, in the agent's language (`D66`).

    A bare "matched" hides the difference between the whole number and four trailing
    digits, and those are very different pieces of evidence for an agent about to attest
    an identity (`D42`). The screen has to carry the distinction, so the sentence does.
    """
    if hit.tier == "exact":
        return f"{noun_th}ตรงทั้งหมด ({hit.digits} หลัก)"
    if hit.tier == "suffix":
        return f"{noun_th}ตรง {hit.digits} ตัวท้าย"
    if hit.tier == "prefix":
        return f"{noun_th}ตรง {hit.digits} ตัวแรก"
    return f"{noun_th}มี {hit.digits} หลักนี้อยู่ภายใน"


def _date_tier_th(hit: DateMatch) -> str:
    era = " (พ.ศ.)" if hit.buddhist_era else ""
    if hit.tier == "full":
        return f"วันเดือนปีเกิดตรงทั้งหมด{era}"
    if hit.tier == "day_month":
        return "วันและเดือนเกิดตรง (ไม่ได้ระบุปี)"
    return f"ปีเกิดตรง{era} — เป็นหลักฐานที่อ่อนที่สุด"


def _no_digit_match_th(noun_th: str) -> str:
    return f"ไม่ตรงกับ{noun_th}ใดของลูกค้ารายนี้ (ตรวจทั้งเลขเต็ม ตัวท้าย และตัวแรกแล้ว)"


def build_vad(settings: Settings) -> Callable[[], VoiceActivityDetector]:
    """A FACTORY, not an instance (`D96`).

    Detectors are stateful across frames, so two concurrent calls sharing one would
    interleave their hidden states and each would be endpointed against the other's
    audio. The symptom is a clipped or missing first word, which looks like an STT fault
    and is not.
    """
    if settings.vad_engine is VadEngineName.SILERO:
        from readycall.adapters.vad.silero import SileroVad

        return SileroVad
    return EnergyVad


def build_stt(settings: Settings) -> SttEngine:
    """Pick the STT engine by config (`D3`) - one env var from scripted to a real GPU.

    `scripted` is the default and stays the default: every test, all three scenarios and
    the stage-safe demo path run on it, and nothing in the system above this line can tell
    which one it got (`D9`).

    **`STT_WORKER=subprocess` wraps whichever engine was chosen** in a child process, so a
    runaway decode can be killed on a deadline (`D112`, `D98`). The wrapping happens here
    rather than in the caller for the same reason the encryption wrapper happens in
    `build_blob_storage` (`D110`): a decorator that only applies when somebody remembers
    to apply it is not a guarantee.
    """
    if settings.stt_worker is SttWorkerMode.SUBPROCESS and not _in_stt_worker():
        from readycall.adapters.stt.worker import SubprocessSttEngine

        # The child is told to run INLINE, or it would spawn a worker of its own, forever.
        # Passed through the environment rather than an argument because the child builds
        # its own `Settings` from exactly that.
        env = {**os.environ, "STT_WORKER": SttWorkerMode.INLINE.value}
        return SubprocessSttEngine(timeout_s=settings.stt_decode_timeout_s, env=env)
    name = settings.stt_engine
    device = settings.stt_device
    if device == "auto":
        # Resolved here rather than in `Settings`, so importing config does not import
        # torch. A box with the `ml` extra and no usable GPU falls back to CPU instead of
        # dying at model load, which is the right direction to fail on a demo morning.
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    if name is SttEngineName.THONBURIAN_CT2:
        from readycall.adapters.stt.faster_whisper import DEFAULT_MODEL, FasterWhisperEngine

        return FasterWhisperEngine(
            model=settings.stt_model or DEFAULT_MODEL,
            device=device,
            compute_type=settings.stt_compute_type,
        )
    if name is SttEngineName.THONBURIAN_HF:
        from readycall.adapters.stt.thonburian_hf import DEFAULT_MODEL, ThonburianHfEngine

        return ThonburianHfEngine(model=settings.stt_model or DEFAULT_MODEL, device=device)
    if name is SttEngineName.TYPHOON:
        from readycall.adapters.stt.typhoon_asr import DEFAULT_MODEL, TyphoonAsrEngine

        # The engine `D104` selects. It needs the `asr` extra (NeMo), which is deliberately
        # separate from `ml` (`D99`) - so this import is the thing that fails, loudly and
        # with instructions, on a box that has not installed it.
        return TyphoonAsrEngine(model=settings.stt_model or DEFAULT_MODEL, device=device)
    if name is not SttEngineName.SCRIPTED:
        # `distill` and `cloud` are named in the enum and not built (`D30`).
        # Saying so beats a silent fallback that makes a demo look like it is running a
        # model it is not.
        log.warning(
            "stt engine not implemented yet, falling back to scripted",
            requested=str(name),
        )
    # Its lines come from config (`D107`). It used to be built empty, so the engine whose
    # own docstring calls it "the stage-safe demo path" produced a transcript with nothing
    # in it — a fallback that looks like the system working and finding nothing to say.
    return ScriptedSttEngine(load_scripted_turns(settings.demo_transcript_file))


def _in_stt_worker() -> bool:
    """True inside `entrypoints/stt.py`, which must never wrap itself.

    Belt and braces beside the env var the parent sets: a child that spawned a child would
    fork model loads until the machine gave up, and the symptom would be a demo laptop
    quietly filling its VRAM.
    """
    return os.environ.get("READYCALL_STT_WORKER_CHILD") == "1"


def build_core_data(settings: Settings, clock: Clock) -> CoreDataProvider:
    """Pick the bank-data adapter by config, then wrap it so a slow core degrades cheaply."""
    inner: CoreDataProvider
    if settings.core_data_provider is CoreDataProviderName.FIXTURES:
        inner = FixtureFileProvider(settings.core_fixtures_dir)
    else:
        # P1b: mock_postgres and http_api land with the DB layer at P2 (`D39`).
        log.warning(
            "core data provider not implemented yet, using null",
            requested=str(settings.core_data_provider),
        )
        inner = NullCoreDataProvider()
    return CachingCoreDataProvider(inner, clock=clock)


class Container:
    """Every long-lived object the API uses. One per process."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        storage: Storage | None = None,
    ) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self.pack = DomainPack.load(settings.config_dir)
        #: Every spoken line, cross-checked against the menus and numbers that name them
        #: (`D24`). A dangling prompt id is a silent gap in a call, so it fails here.
        self.prompts = load_prompt_pack(settings.config_dir, self.pack)
        self.bus = InMemoryEventBus()
        self.core = build_core_data(settings, self.clock)

        #: The LLM, and the versioned prompts it is given (`D119`). `build_llm` is the only
        #: place a client may be constructed; the default is the rule-based adapter, which
        #: is a real one needing no key, no network and no extra (`D3`).
        self.prompt_library = PromptLibrary.load(settings.prompt_dir)
        self.llm = build_llm(settings, prompts=self.prompt_library, clock=self.clock)
        #: Returns `None` on timeout, on failure, and whenever the provider is rule-based.
        #: Every caller has to be correct in that case, because it is the ordinary one.
        self.summariser = IntakeSummariser(
            llm=self.llm, clock=self.clock, timeout_s=settings.llm_timeout_s
        )
        #: The preview pass (`D131`), which runs while the offer card is on screen. Falls
        #: back to the same client and a tighter deadline when `LLM_FAST_MODEL` is unset,
        #: so the two-stage behaviour does not depend on configuring a second model — only
        #: its *speed* does.
        self.fast_llm = build_fast_llm(settings, prompts=self.prompt_library, clock=self.clock)
        self.preview_summariser = IntakeSummariser(
            llm=self.fast_llm or self.llm,
            clock=self.clock,
            timeout_s=settings.llm_preview_timeout_s,
        )
        #: *"What else do we know about this person"* (`D134`). On the FAST model, because
        #: it runs alongside the preview during the offer window and is competing for the
        #: same seconds — and because summarising records we already hold is the easier of
        #: the two jobs.
        self.context_summariser = ContextSummariser(
            llm=self.fast_llm or self.llm,
            clock=self.clock,
            timeout_s=settings.llm_preview_timeout_s,
        )
        #: The comparison's reason sentences (`D137`), on the FAST model for the same
        #: reason as the context panel: it is awaited by a screen somebody is looking at.
        self.comparison_reasons = ComparisonReasonWriter(
            llm=self.fast_llm or self.llm,
            timeout_s=settings.llm_comparison_timeout_s,
        )
        #: `{(call_session_id, line): {product_code: sentence}}`. The ranking is cheap and
        #: recomputed every time; the sentences are not, so the second open of the panel —
        #: and every line the broker flips back to — is instant and free.
        self.comparison_reason_cache: dict[tuple[str, str], dict[str, str]] = {}
        #: What `LLM_PROVIDER` says, kept so the runtime switch can put it back (`D138`).
        #: `Settings` is frozen and read once at startup, so "which model is on" cannot be
        #: answered from it after somebody has flipped the switch.
        self.llm_choice: str = str(settings.llm_provider)

        #: Every durable store, chosen by `STORAGE_BACKEND` (`D75`, `D78`). Each service
        #: below gets its own and writes through to it; nothing reads it on the hot path.
        self.storage = storage or build_storage(settings)

        self.intent_store = InMemoryCallIntentStore()
        self.snapshots = self.storage.snapshots
        self.app_context = InMemoryAppContextStore(clock=self.clock)

        self.assembler = ContextAssembler(core=self.core, clock=self.clock)
        self.intents = IntentService(
            store=self.intent_store,
            bus=self.bus,
            clock=self.clock,
            ttl_s=settings.intent_ttl_s,
            dial_target=settings.default_dial_target,
        )

        self.sessions: SessionResolver = DemoSessionStore(
            clock=self.clock, ttl_s=settings.session_ttl_s
        )

        # --- the agent workstation (P2b) ------------------------------------------------
        #
        # Staff auth is a separate store and a separate cookie from the customer one. One
        # shared lookup, and a customer session would resolve to an agent principal.
        self.agent_sessions = DemoAgentSessionStore(
            clock=self.clock, ttl_s=settings.agent_session_ttl_s
        )
        self.agents = FixtureAgentDirectory(settings.config_dir.parent / "mock/agents/agents.json")
        self.hours = QueueHours.load(settings.config_dir / "queue_hours.yaml")
        self.calls = self.storage.calls
        self.orchestrator = CallOrchestrator(repository=self.calls, bus=self.bus, clock=self.clock)

        #: Telephony until P5. Demo calls carry no channel, so nothing is played through
        #: it yet — but the IVR talks to the port rather than to a special case, which is
        #: what makes Asterisk a config change rather than a rewrite (`D3`).
        self.telephony = SimulatedTelephonyProvider(clock=self.clock)
        self.ivr = IvrService(
            pack=self.pack,
            prompts=self.prompts,
            telephony=self.telephony,
            orchestrator=self.orchestrator,
            clock=self.clock,
        )
        #: Everything below the "queue is now known" line (`ARCHITECTURE.md` section 6):
        #: the queue position, the intake offer, and the recording that outlives the
        #: request that started it, because an agent accepting is what ends it (`D21`).
        self.intake = IntakeService(
            pack=self.pack,
            prompts=self.prompts,
            telephony=self.telephony,
            orchestrator=self.orchestrator,
            clock=self.clock,
            bus=self.bus,
            settings=settings,
        )
        #: The audio path (`D96`). `IntakeService.on_turn` has existed since `D88` with
        #: nothing feeding it; this is what feeds it. On the default config the engine is
        #: scripted and the detector is the energy one, so this costs nothing and runs
        #: everywhere - which is the point (`B7`: a path only exercised on one laptop is a
        #: path nobody runs).
        self.stt = build_stt(settings)
        self.transcription = TranscriptionService(
            intake=self.intake,
            vad_factory=build_vad(settings),
            stt=self.stt,
            clock=self.clock,
            settings=settings,
            hint=SttHint(language="th", vocabulary=self.pack.stt_vocabulary),
        )
        #: The encrypted recording (`D110`) — the last piece of `ARCHITECTURE` §6. It
        #: shares the transcriber's gateway rather than opening a second one, so both
        #: subscribe to the same normalised frames and neither depends on the other
        #: running: a transcriber that is down still records, and a caller who refused
        #: analysis is still not recorded, because consent is checked at close.
        self.blob = build_blob_storage(settings)
        self.recording = RecordingService(
            blob=self.blob,
            clock=self.clock,
            settings=settings,
            gateway=self.transcription.gateway,
            store=self.storage.recordings,
            calls=self.calls,
        )
        #: Accept closes the recording explicitly, in an order that matters (`B24`).
        #: Every other ending — an abandoned call, a failure, the queue closing — only
        #: ever arrives as a state change, so the service listens for one.
        self.recording.subscribe(self.bus)
        self.hub = AgentHub(clock=self.clock)
        self.presence = PresenceService(
            clock=self.clock,
            bus=self.bus,
            heartbeat_ttl_s=settings.agent_presence_ttl_s,
            long_acw_after_s=settings.acw_long_after_s,
            state_log=self.storage.agent_state_log,
        )
        self.assignments = AssignmentService(
            orchestrator=self.orchestrator,
            presence=self.presence,
            bus=self.bus,
            clock=self.clock,
            policy=OfferPolicy(
                accept_mode=str(settings.agent_accept_mode),
                timeout_s=settings.offer_timeout_s,
                long_acw_after_s=settings.acw_long_after_s,
            ),
            store=self.storage.assignments,
        )
        weights = MatchingWeights.load(settings.config_dir / "matching_weights.yaml")
        self.matching = MatchingEngine(
            directory=self.agents,
            weights=weights,
            clock=self.clock,
        )
        self.dispatch = DispatchService(
            engine=self.matching,
            assignments=self.assignments,
            presence=self.presence,
            notifier=self.hub,
            clock=self.clock,
            offer_timeout_s=settings.offer_timeout_s,
            decisions=self.storage.decisions,
            # From the weights file, not from `Settings` (`D113`) — the matcher's own
            # config is where every other guard lives, and `Q26` is what happens to an
            # env var nothing reads.
            max_offer_rounds=weights.max_offer_rounds,
            # The pre-offer summary (`D131`). The hook fires wherever the offer is made,
            # which is what stops it depending on which caller of `tick()` ran.
            on_offer=self._start_preview_summary,
        )
        self.identity = IdentityResolver(
            core=self.core,
            intents=self.intent_store,
            clock=self.clock,
            pending_intent_window_s=settings.intent_ttl_s,
        )
        self.captures = KeypadCaptureService(clock=self.clock, store=self.storage.captures)
        self.attestations = AttestationService(
            clock=self.clock,
            # One source of truth, shared with the workstation (`D72`).
            challenges=frozenset(self.pack.challenges),
            store=self.storage.attestations,
        )
        self.brief_builder = BriefBuilder(pack=self.pack, clock=self.clock)

        #: Live identity per call. Mutable on purpose — assurance moves *during* a call
        #: (`D42`), and the workstation re-renders from whatever is here now.
        self.identity_for_call: dict[str, IdentityResolution] = {}
        #: call_session_id -> the context snapshot the brief is rendered from.
        self.snapshot_for_call: dict[str, str] = {}
        #: AI summaries, per call, once one has been produced (`D119`). Empty is the
        #: ordinary state: the rule-based summary is already on the screen and this only
        #: ever REPLACES it. Nothing waits for this dict to be filled.
        self.ai_summaries: dict[str, SummaryResult] = {}
        #: How many transcript turns each cached summary was built from (`D131`). This is
        #: the supersede key: a preview is re-run only when the caller has actually said
        #: something since the last one, so a quiet caller costs one model call rather
        #: than one per sweep.
        self.ai_summary_turns: dict[str, int] = {}
        #: How many preview attempts each call has had (`D131`). The refresh runs every
        #: sweep while a card is ringing, and a caller who keeps talking would otherwise
        #: buy a fresh model call every second for the whole offer window. Three updates
        #: is more than a 20-second card can usefully show.
        self.ai_preview_count: dict[str, int] = {}
        #: The context summary, per call (`D134`). Computed once, in the background, from
        #: the frozen snapshot — which cannot change during a call, so unlike the intake
        #: summary there is nothing to supersede.
        self.context_summaries: dict[str, SummaryResult] = {}
        #: In-flight preview tasks, held so the event loop cannot collect one mid-call.
        self._preview_tasks: set[asyncio.Task[None]] = set()
        #: Saved wrap-up forms, projected from the store. Never written by anything but
        #: an agent (`D45`) — the *absence* of an entry is meaningful data.
        self.wrapups: dict[str, CallWrapup] = {}

        #: intent_id -> snapshot_id, so an intent can report what the prefetch produced.
        self.snapshot_for_intent: dict[str, str] = {}
        self.bus.subscribe(ev.IntentCreated.name, self._prefetch_context)

        #: Agent Delivery for the transcript (`D106`). It subscribes itself so the topics
        #: live beside the handlers; what it needs from here is the hub and the bus.
        #: **Nothing here would run without `pump_once` in `api/app.py`** — `publish()`
        #: enqueues and handlers wait for `drain()` (`D15`), and until `D105` the only
        #: drain in the live process was a background task on `POST /v1/calls/intents`.
        self.transcript_delivery = TranscriptDeliveryService(notifier=self.hub)
        #: The customer's paired screen (`D120`). Holds nothing durably: a pairing dies
        #: with the call, which is the right retention for a token whose only purpose is
        #: one conversation (`D14`).
        self.assist = AssistService(clock=self.clock)
        #: Where a call goes when it is not staying with us (`D124`). Holds the handoff
        #: for the length of the call so the wrap-up can be prefilled; the durable record
        #: is the `call_wrapups` row the broker actually files (`D45`).
        self.transfer = TransferService(clock=self.clock)
        #: Compare & best-fit (`D126`). No I/O of its own: the catalogue is read through
        #: `CoreDataProvider` by the caller and handed in, which is what lets the thing
        #: that DECIDES be tested with a list of plans and no adapter at all.
        self.comparison = ComparisonService(settings=self.pack.comparison_settings)
        self.transcript_delivery.subscribe(self.bus)

        #: The FOURTH consumer of `transcript.turn` (`D114`), and its own subscriber
        #: rather than a line inside delivery: a storage failure must not be able to
        #: touch the agent's screen, which is the half that may not be blocked (`D12`).
        self.transcript_recorder = TranscriptRecorder(store=self.storage.transcripts)
        self.transcript_recorder.subscribe(self.bus)

    # --- restore (D78) ------------------------------------------------------------------

    #: Call states that are over. Everything else is a call the next tick may act on, and
    #: is what restore reloads. Deliberately a list of *finished* states rather than of
    #: live ones: a state added later is live until somebody says otherwise, and the safe
    #: default is to reload one call too many rather than to silently drop one.
    FINISHED_STATES = (
        CallState.CLOSED,
        CallState.FAILED,
        CallState.ABANDONED,
        CallState.VOICEMAIL,
        CallState.TRANSFERRED,
    )

    # --- the runtime model switch (`D138`) -------------------------------------------

    def llm_options(self) -> list[dict[str, object]]:
        """Which model choices this process can actually make, and why not (`D138`).

        ⚠️ **Availability is decided from the keys already in the environment, and a key
        never leaves this method.** The panel is told *"anthropic: usable"*, never the
        secret that makes it so — a settings screen that echoed a key back would put a live
        credential in a browser, in a log, and in a screenshot of a demo.

        `rulebased` is always offered because it is a real adapter needing nothing
        (`D119`), and it is what "turn the AI off" means here.
        """
        settings = self.settings
        fast = settings.llm_fast_model or None
        options: list[dict[str, object]] = [
            {
                "provider": "rulebased",
                "label_th": "ปิดโมเดล — ใช้สรุปแบบกฎ",
                "model": None,
                "fast_model": None,
                "available": True,
                "why_th": "ไม่เรียกโมเดลใดๆ ไม่ต้องใช้คีย์ ไม่ต้องต่อเน็ต",
            },
            {
                "provider": "anthropic",
                "label_th": "Anthropic",
                "model": settings.llm_model or "claude-sonnet-5",
                "fast_model": fast,
                "available": bool(settings.anthropic_api_key),
                "why_th": (
                    "สรุปหลังรับสายด้วยโมเดลนี้"
                    if settings.anthropic_api_key
                    else "ยังไม่มี ANTHROPIC_API_KEY ใน .env"
                ),
            },
            {
                "provider": "openai_compatible",
                "label_th": "OpenAI-compatible",
                "model": settings.llm_fast_model or "gpt-5.4-mini",
                "fast_model": fast,
                "available": bool(settings.llm_api_key or settings.openai_api_key),
                "why_th": (
                    "เร็วกว่า เหมาะกับสรุประหว่างรอรับสาย"
                    if (settings.llm_api_key or settings.openai_api_key)
                    else "ยังไม่มี OPENAI_API_KEY หรือ LLM_API_KEY ใน .env"
                ),
            },
        ]
        return options

    def apply_llm_choice(self, provider: str) -> None:
        """Rebuild every model-using service against a different provider (`D138`).

        ⚠️ **Through `build_llm`, always.** That factory is the only place a client may be
        constructed — the same rule `build_blob_storage` enforces (`D110`) — and a second
        construction path here is how the runtime switch would eventually disagree with
        startup about what `anthropic` means.

        ⚠️ **`Settings` is frozen and stays frozen.** This copies it with an override and
        keeps the copy for the clients only; `self.settings` is untouched, so nothing else
        in the process starts reading a different configuration than it booted with.

        Runtime only, deliberately: a restart returns to whatever `.env` says. A switch
        thrown for a demo that then silently outlives the demo is a configuration nobody
        can find later.
        """
        from readycall.config import LlmProviderName

        chosen = LlmProviderName(provider)
        overrides: dict[str, object] = {"llm_provider": chosen}
        if chosen is LlmProviderName.OPENAI_COMPATIBLE:
            # `_check_coherent` refuses this provider with no base URL, and the copy is
            # validated like any other `Settings`. Defaulting to OpenAI's own URL is what
            # makes the choice one click rather than one click and a text field.
            overrides["llm_base_url"] = self.settings.llm_base_url or OPENAI_BASE_URL
            overrides["llm_model"] = self.settings.llm_fast_model or "gpt-5.4-mini"
        effective = self.settings.model_copy(update=overrides)

        self.llm = build_llm(effective, prompts=self.prompt_library, clock=self.clock)
        self.fast_llm = build_fast_llm(effective, prompts=self.prompt_library, clock=self.clock)
        if chosen is LlmProviderName.RULEBASED:
            # Turning the model off must turn the FAST one off too, or the offer card keeps
            # calling a hosted model while the panel says the AI is disabled.
            self.fast_llm = None

        self.summariser = IntakeSummariser(
            llm=self.llm, clock=self.clock, timeout_s=effective.llm_timeout_s
        )
        self.preview_summariser = IntakeSummariser(
            llm=self.fast_llm or self.llm,
            clock=self.clock,
            timeout_s=effective.llm_preview_timeout_s,
        )
        self.context_summariser = ContextSummariser(
            llm=self.fast_llm or self.llm,
            clock=self.clock,
            timeout_s=effective.llm_preview_timeout_s,
        )
        self.comparison_reasons = ComparisonReasonWriter(
            llm=self.fast_llm or self.llm,
            timeout_s=effective.llm_comparison_timeout_s,
        )
        # ⚠️ Both caches hold text a DIFFERENT model wrote. Keeping them would show the
        # broker Sonnet's sentences on a screen reporting that the model is off, which is
        # the panel lying about the thing it exists to report.
        self.comparison_reason_cache.clear()
        self.context_summaries.clear()
        self.llm_choice = str(chosen)

    def llm_state(self) -> dict[str, object]:
        """What the panel renders. Never a key, and never `Settings` itself."""
        real = self.llm_choice != "rulebased"
        return {
            "provider": self.llm_choice,
            "enabled": real,
            # `.model`, not `.name` — `name` is the PROVIDER ("anthropic"), and a panel
            # that reported the provider under a heading saying "model" would be wrong in
            # the one place somebody checks before quoting a number in a pitch.
            "model": self.llm.model if real else None,
            "fast_model": (self.fast_llm.model if self.fast_llm is not None else None),
            "configured_provider": str(self.settings.llm_provider),
            "options": self.llm_options(),
        }

    async def restore(self) -> dict[str, int]:
        """Rebuild the working set from the durable stores. Returns what was reloaded.

        **The order is the design, not an implementation detail.** Live calls come first,
        because every other store is loaded *for those calls* — a bounded read rather than
        a full table scan, and the reason none of these stores needs a "load everything"
        method. Presence comes last, because it is the one thing that is a projection of a
        log rather than a reload of rows (`D76`).

        What deliberately does **not** come back:

        * **the waiting pool as stored rows.** It is rebuilt from `call_sessions` in
          `queued`/`matched` — a second table saying who is waiting is a second thing that
          can disagree with the call's own state.
        * **`system_state`.** Every agent returns `OFFLINE`, because after a restart the
          platform has genuinely given them nothing to do (see `PresenceService.restore`).
        * **unnamed keypad digits**, which were never stored (`D44`).

        On the in-memory backend this reads back what this process itself wrote, so it is
        a no-op in effect and a fully exercised code path in fact — which is the point of
        the memory stores existing at all (`B7`).
        """
        live = await self.calls.list_in_states(
            *[s for s in CallState if s not in self.FINISHED_STATES]
        )
        call_ids = [s.call_session_id for s in live]

        for session in live:
            if session.identity is not None:
                self.identity_for_call[session.call_session_id] = session.identity
            if session.snapshot_id is not None:
                self.snapshot_for_call[session.call_session_id] = session.snapshot_id

        restored = {
            "calls": len(live),
            "assignments": await self.assignments.restore(call_ids),
            "attestations": await self.attestations.restore(
                call_ids, resolutions=self.identity_for_call
            ),
            "captures": await self.captures.restore(call_ids),
            "wrapups": 0,
            "waiting": 0,
            "agents": 0,
        }

        for wrapup in await self.storage.wrapups.for_calls(call_ids):
            self.wrapups[wrapup.call_session_id] = wrapup
        restored["wrapups"] = len(self.wrapups)

        decisions = await self.storage.decisions.latest_for_calls(call_ids)
        pool: list[tuple[CallSession, WaitingCall]] = []
        for session in live:
            waiting = self._waiting_call_for(session)
            if waiting is not None:
                pool.append((session, waiting))
        restored["waiting"] = self.dispatch.restore(pool, decisions=decisions)
        restored["agents"] = await self.presence.restore()

        log.info("restored from storage", backend=self.storage.backend, **restored)
        return restored

    def _waiting_call_for(self, session: CallSession) -> WaitingCall | None:
        """Rebuild the matcher's view of a waiting caller from the call itself.

        This is what makes the pool a **projection** rather than a table. Everything the
        matcher needs is already recorded: the queue on the session, the skill and SLA on
        the queue spec, the urgency on the intent. Storing a second copy would let the two
        disagree, and the copy is always the one that ends up wrong.
        """
        if session.state not in {CallState.QUEUED, CallState.MATCHED, CallState.OFFERED}:
            return None
        if session.queue_id is None:
            return None
        spec = self.pack.queues.get(session.queue_id)
        if spec is None:
            return None
        intent_code = session.menu_intent_code
        # Waiting time is recomputed from `queued_at`, not restored from a counter. The
        # caller really has been waiting through the restart, and a reset would hand them
        # to the back of the urgency ordering for our outage.
        waited = (
            (self.clock.now() - session.queued_at).total_seconds() if session.queued_at else 0.0
        )
        return WaitingCall(
            call_session_id=session.call_session_id,
            queue_id=session.queue_id,
            required_skill=spec.required_skill,
            intent_code=intent_code or "unknown",
            intent_urgency=(
                self.pack.intent(intent_code).default_urgency if intent_code else Urgency.NORMAL
            ),
            waiting_s=waited,
            sla_seconds=spec.sla_seconds,
            acceptable_languages=session.acceptable_languages,
            customer_id=session.customer_id,
        )

    async def aclose(self) -> None:
        """Release whatever the storage backend holds open."""
        if self.storage.engine is not None:
            await self.storage.engine.dispose()

    async def set_identity(self, session: CallSession, resolution: IdentityResolution) -> None:
        """Record who we think is calling, in memory **and** on the call (`D78`).

        `D42` makes assurance mutable for the whole call, so this is written every time an
        agent attests. It has to land on `CallSession.identity` and not only in the live
        dict: the column has existed since P0, and while nothing wrote it a restored call
        came back with no identity at all — which meant `render_brief` returned `None` and
        the agent's screen was blank on a call they were in the middle of.
        """
        self.identity_for_call[session.call_session_id] = resolution
        session.identity = resolution
        if resolution.customer_id is not None:
            session.customer_id = resolution.customer_id
        await self.calls.save(session)

    async def _prefetch_context(self, event: ev.Event) -> None:
        """`D6`: assembly starts at *tap*, not at *answer*."""
        if not isinstance(event, ev.IntentCreated):
            return
        snapshot = await self.assembler.build(
            customer_id=event.customer_id, product_code=event.product_code
        )
        await self.snapshots.save(snapshot)
        self.snapshot_for_intent[event.intent_id] = snapshot.snapshot_id
        log.info(
            "context prefetched for intent",
            intent_id=event.intent_id,
            snapshot_id=snapshot.snapshot_id,
            build_ms=round(snapshot.build_ms or 0.0, 2),
        )

    async def record_app_event(self, event: AppContextEvent) -> int:
        return await self.app_context.record(event)

    async def recent_app_events(self, customer_id: str) -> tuple[AppContextEvent, ...]:
        return await self.app_context.recent_for(customer_id, within=timedelta(hours=24))

    # --- what the workstation asks for -------------------------------------------------

    async def render_brief(self, call_session_id: str) -> dict[str, Any] | None:
        """Render the brief **for the assurance level this call is at right now** (`D42`).

        Promotion is a re-render, not a re-fetch: the frozen snapshot already holds the
        full policy numbers and every coverage figure, and only the *rendering* is gated.
        So this costs no bank-core round trip and no spinner — which is exactly why the
        assembler was never gated by assurance and must not become gated.

        **Returns a wire DTO, never `CaseBrief.model_dump()`.** The domain object carries
        the whole frozen `ContextSnapshot`, so dumping it shipped the policy number, sum
        insured, every coverage figure and the date of birth to a call sitting at L1 —
        beside a field that said disclosure was locked. `BriefOut` has nowhere to put
        those until the level permits them, which is the difference between a gate and a
        promise (`D42`).
        """
        snapshot_id = self.snapshot_for_call.get(call_session_id)
        if snapshot_id is None:
            return None
        snapshot = await self.snapshots.get(snapshot_id)
        if snapshot is None:
            return None
        identity = self.identity_for_call.get(call_session_id)
        if identity is None:
            return None
        session = await self.calls.get(call_session_id)
        brief = self.brief_builder.build_context_only(
            call_session_id=call_session_id,
            snapshot=snapshot,
            identity=identity,
            intent_code=getattr(session, "menu_intent_code", None),
            product_line=getattr(session, "product_line", ProductLine.UNKNOWN),
            # `D7`: promotion produces a NEW version rather than mutating the old one, so
            # the record shows what the agent saw before and after, and when it changed.
            version=1 + len(self.attestations.history(call_session_id)),
        )
        # The AI summary REPLACES the rule-based one if it arrived, and this read path
        # never calls a model: `brief_snapshot` runs on every `/me` and every socket
        # push, so summarising here would hit the provider dozens of times per call
        # (`D12`, and the cost line in `INTEGRATIONS` 3). `summarise_call` computes it
        # once, in the background, after the agent is already talking.
        summary = self.ai_summaries.get(call_session_id)
        if summary is not None:
            brief = brief.model_copy(update={"summary_th": summary.text_th})
        out = _brief_out(brief, identity)
        # `D134`. Attached at the wire rather than built into `CaseBrief`, for `B5`'s
        # reason: the domain brief is the object that must not grow fields casually, and
        # this is a fact about the rendering.
        context_summary = self.context_summaries.get(call_session_id)
        if out.context is not None:
            out = out.model_copy(
                update={
                    "context": out.context.model_copy(
                        update={
                            "summary_th": context_summary.text_th if context_summary else None,
                            # Say WHY there is no sentence, so an empty line reads as "no
                            # model configured" rather than as a failure (`D71`).
                            "summary_unavailable": context_summary is None
                            and self.settings.llm_provider is LlmProviderName.RULEBASED,
                        }
                    )
                }
            )
        if summary is not None and not summary.is_final:
            # `D131`: say which pass wrote this. Set on the DTO rather than on the domain
            # brief because it is a fact about the rendering, not about the case — and
            # `CaseBrief` is the object `B5` proved must not grow fields casually.
            out = out.model_copy(update={"summary_is_preview": True})
        return out.model_dump(mode="json")

    async def refresh_previews(self) -> None:
        """Re-summarise every call whose offer card is still on a screen (`D131`, `D21`).

        The offer window is the intake grace period: the caller keeps talking after the
        desk starts ringing, so the preview taken at the instant of the offer is usually a
        summary of the first sentence or of silence. This gives it another look each sweep.

        Awaited rather than fire-and-forget **because the sweep is already off the call
        path** — nothing is waiting for this loop, and awaiting means two sweeps cannot
        stack up duplicate calls for the same caller. `summarise_call` is itself bounded:
        it returns immediately unless the turn count has grown, and stops after
        `_MAX_PREVIEWS` attempts.
        """
        pending = [
            (a.call_session_id, a.agent_id)
            for a in self.assignments.pending()
            # A call whose FINAL summary already landed needs no preview: the agent has
            # accepted and the whole-transcript pass has run. Cheap to check, and it stops
            # a re-offered caller paying for a preview that would be discarded anyway.
            if not (held := self.ai_summaries.get(a.call_session_id)) or not held.is_final
        ]
        for call_session_id, agent_id in pending:
            try:
                await self.summarise_call(call_session_id, agent_id, preview=True)
            except Exception:
                log.exception("preview refresh failed", call_session_id=call_session_id)

    async def summarise_context(self, call_session_id: str, agent_id: str | None = None) -> None:
        """Summarise what the bank already knows, in the background (`D134`).

        Computed **once** per call: it reads the frozen `ContextSnapshot` (`D6`), which by
        construction cannot change while the call is happening — so unlike the intake
        summary there is nothing to supersede and no race to lose.

        Every rule the intake summary obeys applies here, and one more: the prompt forbids
        recommending a product and `_RECOMMENDS` refuses the output if it does anyway
        (`D116`). Describing a record an affiliate lawfully shared is internal processing;
        recommending from it needs consent this panel does not have.
        """
        if call_session_id in self.context_summaries:
            return
        snapshot_id = self.snapshot_for_call.get(call_session_id)
        identity = self.identity_for_call.get(call_session_id)
        # At L0 there is nobody to describe, and the panel is absent from the wire anyway.
        if snapshot_id is None or identity is None or not identity.may_see_record:
            return
        snapshot = await self.snapshots.get(snapshot_id)
        if snapshot is None:
            return
        facts = known_facts(snapshot.payload)
        if not facts:
            return
        session = await self.calls.get(call_session_id)
        code = getattr(session, "menu_intent_code", None)
        spec = self.pack.intents.get(code) if code else None
        customer = snapshot.payload.customer
        result = await self.context_summariser.summarise(
            facts_for_prompt(facts),
            fact_count=len(facts),
            customer_name_th=customer.polite_name_th if customer else "ลูกค้า",
            intent_label_th=spec.label_th if spec else "ไม่ทราบเรื่องที่ติดต่อ",
        )
        if result is None:
            return
        self.context_summaries[call_session_id] = result
        log.info(
            "context summary ready",
            call_session_id=call_session_id,
            facts=len(facts),
            model=result.model,
            latency_ms=round(result.latency_ms or 0.0, 1),
        )
        if agent_id:
            await self.hub.send(agent_id, "brief_updated", {"call_session_id": call_session_id})

    def _start_preview_summary(self, call_session_id: str, agent_id: str) -> None:
        """`DispatchService`'s `on_offer` hook: summarise while the card is on screen.

        Synchronous and instant by contract — it starts a task and returns, because it is
        called from inside the dispatch tick and `D12` forbids anything on a call's path
        waiting for a model. A failure to even schedule is logged and swallowed: the worst
        case is the screen keeping the rule-based summary, which is what it had before.

        ⚠️ **The task is kept in a set.** `asyncio` holds only a weak reference to a
        running task, so a fire-and-forget `create_task` whose result nobody keeps can be
        collected mid-flight — intermittently, under load, which is the single worst shape
        of bug for this project to acquire.
        """
        try:
            started = [
                asyncio.create_task(self.summarise_call(call_session_id, agent_id, preview=True)),
                # `D134`, started at the same instant and for the same reason: the seconds
                # an agent spends reading the offer card are the seconds both of these have
                # to land in. Two independent tasks, so one failing cannot take the other
                # with it — they summarise different things and degrade separately (`D12`).
                asyncio.create_task(self.summarise_context(call_session_id, agent_id)),
            ]
        except RuntimeError:
            # No running loop: a scenario replay or a unit test driving the matcher
            # directly. Nothing to summarise onto, and not an error.
            return
        for task in started:
            self._preview_tasks.add(task)
            task.add_done_callback(self._preview_tasks.discard)

    async def summarise_call(
        self, call_session_id: str, agent_id: str | None = None, *, preview: bool = False
    ) -> None:
        """Ask the model for a summary, in the background, and never block on it.

        Runs **twice** per call (`D131`), and the two passes answer different questions:

        * `preview=True` fires when the offer card appears, on the transcript so far, on
          the fast model and a tight deadline. It is trying to land inside the seconds the
          agent spends reading the card — so the brief is already summarised *before* they
          press Accept, which is what the pitch has always claimed.
        * `preview=False` fires from the accept path, on the **whole** transcript. The
          caller keeps talking through the entire offer window (`D21` — the offer window
          IS the intake grace period), so the preview was necessarily built from a partial
          transcript, and the final pass is what makes the screen right.

        Neither is ever awaited by a request. `D12`: the agent is connected the instant
        `accept_offer` returns, and every one of these paths is allowed to do nothing.

        **The supersede rule is turn count, not presence.** Returning early on
        `call_session_id in self.ai_summaries` — which is what this did before there were
        two passes — would have made the preview permanently *win*, so the final pass would
        never run and the screen would keep a summary of the first half of the call. A
        preview is replaced when there is genuinely more to say; a final result is never
        replaced by anything.
        """
        turns = self.transcript_delivery.turns_for(call_session_id)
        if not turns:
            return
        held = self.ai_summaries.get(call_session_id)
        if held is not None:
            if held.is_final:
                return
            # A second preview on the same offer is only worth money if the caller
            # has actually said something since the last one.
            if preview and len(turns) <= self.ai_summary_turns.get(call_session_id, 0):
                return
        if preview and self.ai_preview_count.get(call_session_id, 0) >= _MAX_PREVIEWS:
            return
        session = await self.calls.get(call_session_id)
        code = getattr(session, "menu_intent_code", None)
        spec = self.pack.intents.get(code) if code else None
        summariser = self.preview_summariser if preview else self.summariser
        result = await summariser.summarise(
            [str(t.get("text", "")) for t in turns],
            intent_label_th=spec.label_th if spec else "ไม่ทราบเรื่องที่ติดต่อ",
            line_label_th=str(spec.line) if spec else "unknown",
        )
        if result is None:
            return
        # Recorded on the result rather than in a second dict, so anything reading the
        # summary can tell a partial-transcript preview from the finished article. The
        # screen uses it to label one (`D131`).
        result = replace(result, is_final=not preview)
        # ⚠️ A slow preview can land AFTER the final pass has already written: the agent
        # pressed Accept while the fast model was still thinking. Losing that race must
        # not downgrade the screen from a whole-transcript summary to half of one.
        current = self.ai_summaries.get(call_session_id)
        if current is not None and current.is_final and preview:
            log.info(
                "preview summary discarded: the final one already landed",
                call_session_id=call_session_id,
            )
            return
        self.ai_summaries[call_session_id] = result
        self.ai_summary_turns[call_session_id] = len(turns)
        if preview:
            self.ai_preview_count[call_session_id] = (
                self.ai_preview_count.get(call_session_id, 0) + 1
            )
        log.info(
            "ai summary ready",
            call_session_id=call_session_id,
            stage="preview" if preview else "final",
            turns=len(turns),
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            latency_ms=round(result.latency_ms or 0.0, 1),
        )
        if agent_id:
            # Nudge the screen. Without this the upgrade waits for the ten-second
            # heartbeat, which is exactly the "it refreshes every ten seconds" symptom
            # `B27` teaches to distrust.
            await self.hub.send(agent_id, "brief_updated", {"call_session_id": call_session_id})

    async def lookup_digits(
        self, call_session_id: str, *, kind: str, digits: str
    ) -> tuple[bool, str | None, str | None]:
        """Interpret a keypad capture. **Evidence only** (`D44`).

        Returns `(matched, matched_value, detail)` and changes nothing: not assurance,
        not the identity record, not what the brief discloses. A daughter holding her
        father's documents will type his policy number correctly, and a match that
        promoted her would be the exact failure `D42` exists to prevent.
        """
        identity = self.identity_for_call.get(call_session_id)
        if identity is None or identity.customer_id is None or not digits:
            return False, None, "ยังไม่มีลูกค้าที่ระบุไว้กับสายนี้"

        if kind == "policy_number":
            policies = await self.core.list_policies(identity.customer_id)
            found = best_digit_match(digits, {p.policy_no: p.policy_no for p in policies})
            if found is None:
                return False, None, _no_digit_match_th("กรมธรรม์")
            policy_no, hit = found
            line = next((str(p.line) for p in policies if p.policy_no == policy_no), "")
            return True, policy_no, f"{_digit_tier_th(hit, 'เลขกรมธรรม์')} · {line}"

        if kind == "claim_number":
            claims: dict[str, str] = {}
            details: dict[str, str] = {}
            for policy in await self.core.list_policies(identity.customer_id):
                for claim in await self.core.list_claims(policy.policy_no):
                    claims[claim.claim_id] = claim.claim_id
                    details[claim.claim_id] = f"{claim.kind} · {claim.status}"
            found = best_digit_match(digits, claims)
            if found is None:
                return False, None, _no_digit_match_th("เคลม")
            claim_id, hit = found
            return True, claim_id, f"{_digit_tier_th(hit, 'เลขเคลม')} · {details[claim_id]}"

        if kind == "date_of_birth":
            customer = await self.core.get_customer(identity.customer_id)
            dob = customer.dob if customer else None
            dated = match_date(digits, dob) if dob is not None else None
            if dated is None:
                return False, None, "ไม่ตรงกับวันเดือนปีเกิด ไม่ว่ารูปแบบใด"
            # The OUTCOME only, never the answer. A challenge answer is not stored (`D42`),
            # which is why this branch returns no `matched_value` even on a full match.
            return True, None, _date_tier_th(dated)

        return False, None, f"lookup {kind!r} is not implemented yet"


# --- FastAPI dependencies ----------------------------------------------------------------


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_principal(request: Request, container: ContainerDep) -> Principal:
    """Resolve the caller from their session cookie — never from the request body (`D4`)."""
    token = request.cookies.get(container.settings.session_cookie_name)
    return await container.sessions.resolve(token)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


__all__ = [
    "Container",
    "ContainerDep",
    "PrincipalDep",
    "build_core_data",
    "get_container",
    "get_principal",
]


def _brief_out(brief: CaseBrief, identity: IdentityResolution) -> BriefOut:
    """Domain brief -> wire brief, gated by assurance at the serialisation boundary.

    Written as one function rather than a method on `CaseBrief` on purpose: the domain
    model has no business knowing what a wire looks like, and the gate belongs where the
    bytes leave (`D42`).
    """
    snapshot = brief.snapshot
    payload = snapshot.payload if snapshot else None
    # `D74`: the agent sees the record from L1. What assurance gates is what they may SAY
    # and DO, which is `actions_th` below and `may_act_on_policy` on the wire. L0 still
    # shows nothing, because at L0 there is nobody to show.
    known = identity.may_see_record
    disclose = identity.may_act_on_policy

    customer_out: BriefCustomerOut | None = None
    if known and payload and payload.customer:
        who = payload.customer
        customer_out = BriefCustomerOut(
            display_name_th=" ".join(x for x in (who.first_name_th, who.last_name_th) if x),
            segment=str(who.segment),
            is_vulnerable=who.is_vulnerable,
        )

    # `D134`. Gated on `known` like the rest of the record: `D74` says assurance gates
    # what the agent may SAY and DO, not what they may SEE, and at L0 there is nobody to
    # render. The facts are worded on the server (`D68`) and state nothing they do not
    # source.
    context_out: BriefContextOut | None = None
    if known and payload:
        facts = known_facts(payload)
        if facts:
            context_out = BriefContextOut(
                facts=tuple(
                    KnownFactOut(
                        kind=f.kind,
                        label_th=f.label_th,
                        detail_th=f.detail_th,
                        at_th=f.at_th,
                        source=f.source,
                        confidence=f.confidence,
                    )
                    for f in facts
                )
            )

    policy_out: BriefPolicyOut | None = None
    if known and payload and payload.relevant_policy:
        policy = payload.relevant_policy
        policy_out = BriefPolicyOut(
            policy_no=policy.policy_no,
            product_th=payload.selected_product.name_th if payload.selected_product else None,
            insurer=policy.insurer,
            status=str(policy.status),
            line=str(policy.line),
            sum_insured=policy.sum_insured,
            next_due_date=policy.next_due_date,
            coverages=tuple(
                BriefCoverageOut(
                    label_th=coverage.label_th,
                    limit_text=(
                        f"{coverage.amount:,.0f} {coverage.currency}"
                        if coverage.amount is not None
                        else None
                    ),
                )
                for coverage in policy.coverages
            ),
        )

    last_contact: str | None = None
    if known and payload and payload.recent_interactions:
        latest = payload.recent_interactions[0]
        last_contact = latest.topic or latest.summary

    return BriefOut(
        version=brief.version,
        kind=str(brief.kind),
        urgency=str(brief.urgency),
        intent=(
            BriefIntentOut(
                code=brief.intent.intent_code,
                label_th=brief.intent.label_th,
                confidence=brief.intent.confidence,
                source=brief.intent.source,
            )
            if brief.intent
            else None
        ),
        summary_th=brief.summary_th,
        suggested_opening_th=brief.suggested_opening_th,
        # A playbook step the agent may not take yet is omitted, not disabled. "Read the
        # policy number back to them" is not a greyed-out button at L1, it is advice that
        # does not apply.
        actions_th=tuple(
            action.text_th
            for action in brief.recommended_actions
            if identity.assurance.at_least(action.requires_assurance)
        ),
        customer=customer_out,
        relevant_policy=policy_out,
        other_policy_count=(
            max(len(payload.active_policies) - (1 if payload.relevant_policy else 0), 0)
            if disclose and payload
            else 0
        ),
        recent_claim_count=len(payload.recent_claims) if disclose and payload else 0,
        last_contact_th=last_contact,
        context=context_out,
        disclosure_locked=not disclose,
        handoff_to_insurer=bool(brief.intent and brief.intent.handoff_to_insurer),
        degraded=str(brief.degraded),
        build_ms=brief.build_ms,
        provenance=tuple(
            BriefProvenanceOut(field=p.field, source=p.source, stale=p.stale)
            for p in (snapshot.provenance if snapshot else ())
        ),
    )
