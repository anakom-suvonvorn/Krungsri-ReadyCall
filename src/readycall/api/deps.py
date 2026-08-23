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

from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Request

from readycall.adapters.agent_directory.fixtures import FixtureAgentDirectory
from readycall.adapters.core_data.caching import CachingCoreDataProvider
from readycall.adapters.core_data.fixtures import FixtureFileProvider
from readycall.adapters.core_data.null import NullCoreDataProvider
from readycall.adapters.event_bus.memory import InMemoryEventBus
from readycall.api.realtime import AgentHub
from readycall.api.schemas import (
    BriefCoverageOut,
    BriefCustomerOut,
    BriefIntentOut,
    BriefOut,
    BriefPolicyOut,
    BriefProvenanceOut,
)
from readycall.api.security import (
    DemoAgentSessionStore,
    DemoSessionStore,
    Principal,
    SessionResolver,
)
from readycall.clock import Clock, SystemClock
from readycall.config import CoreDataProviderName, Settings
from readycall.domain import events as ev
from readycall.domain.enums import AssuranceLevel, ProductLine
from readycall.domain.models import CaseBrief, IdentityResolution
from readycall.domainpack import DomainPack
from readycall.logging import get_logger
from readycall.ports.core_data import CoreDataProvider
from readycall.services.agents.assignment import AssignmentService, OfferPolicy
from readycall.services.agents.dispatch import DispatchService
from readycall.services.agents.presence import PresenceService
from readycall.services.brief.builder import BriefBuilder
from readycall.services.call_orchestrator.orchestrator import CallOrchestrator
from readycall.services.call_orchestrator.repository import InMemoryCallSessionRepository
from readycall.services.capture.keypad import KeypadCaptureService
from readycall.services.context.assembler import ContextAssembler
from readycall.services.context.store import (
    AppContextEvent,
    InMemoryAppContextStore,
    InMemorySnapshotStore,
)
from readycall.services.identity.attestation import AttestationService
from readycall.services.identity.intents import IntentService
from readycall.services.identity.resolver import IdentityResolver
from readycall.services.identity.store import InMemoryCallIntentStore
from readycall.services.matching.engine import MatchingEngine
from readycall.services.matching.weights import MatchingWeights
from readycall.services.queues.hours import QueueHours

log = get_logger(__name__)


def _digits_of(value: str) -> str:
    """`MT-2025-004512` -> `2025004512`. A caller keys digits; we store formatted ids."""
    return "".join(ch for ch in value if ch.isdigit())


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

    def __init__(self, settings: Settings, *, clock: Clock | None = None) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self.pack = DomainPack.load(settings.config_dir)
        self.bus = InMemoryEventBus()
        self.core = build_core_data(settings, self.clock)

        self.intent_store = InMemoryCallIntentStore()
        self.snapshots = InMemorySnapshotStore()
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
        self.calls = InMemoryCallSessionRepository()
        self.orchestrator = CallOrchestrator(repository=self.calls, bus=self.bus, clock=self.clock)
        self.hub = AgentHub(clock=self.clock)
        self.presence = PresenceService(
            clock=self.clock,
            bus=self.bus,
            heartbeat_ttl_s=settings.agent_presence_ttl_s,
            long_acw_after_s=settings.acw_long_after_s,
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
        )
        self.matching = MatchingEngine(
            directory=self.agents,
            weights=MatchingWeights.load(settings.config_dir / "matching_weights.yaml"),
            clock=self.clock,
        )
        self.dispatch = DispatchService(
            engine=self.matching,
            assignments=self.assignments,
            presence=self.presence,
            notifier=self.hub,
            clock=self.clock,
            offer_timeout_s=settings.offer_timeout_s,
        )
        self.identity = IdentityResolver(
            core=self.core,
            intents=self.intent_store,
            clock=self.clock,
            pending_intent_window_s=settings.intent_ttl_s,
        )
        self.captures = KeypadCaptureService(clock=self.clock)
        self.attestations = AttestationService(clock=self.clock)
        self.brief_builder = BriefBuilder(pack=self.pack, clock=self.clock)

        #: Live identity per call. Mutable on purpose — assurance moves *during* a call
        #: (`D42`), and the workstation re-renders from whatever is here now.
        self.identity_for_call: dict[str, IdentityResolution] = {}
        #: call_session_id -> the context snapshot the brief is rendered from.
        self.snapshot_for_call: dict[str, str] = {}
        #: Saved wrap-up forms. Never written by anything but an agent (`D45`).
        self.wrapups: dict[str, dict[str, object]] = {}

        #: intent_id -> snapshot_id, so an intent can report what the prefetch produced.
        self.snapshot_for_intent: dict[str, str] = {}
        self.bus.subscribe(ev.IntentCreated.name, self._prefetch_context)

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
        return _brief_out(brief, identity).model_dump(mode="json")

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
            return False, None, "no customer proposed on this call"

        if kind == "policy_number":
            policies = await self.core.list_policies(identity.customer_id)
            for policy in policies:
                # Compare on digits only: a customer keys 2025004512, the stored value is
                # MT-2025-004512, and a raw equality check would never match anything.
                if _digits_of(policy.policy_no).endswith(digits):
                    return True, policy.policy_no, str(policy.line)
            return False, None, "no policy of this customer ends with those digits"

        if kind == "claim_number":
            for policy in await self.core.list_policies(identity.customer_id):
                for claim in await self.core.list_claims(policy.policy_no):
                    if _digits_of(claim.claim_id).endswith(digits):
                        return True, claim.claim_id, f"{claim.kind} · {claim.status}"
            return False, None, "no claim of this customer ends with those digits"

        if kind == "date_of_birth":
            customer = await self.core.get_customer(identity.customer_id)
            dob = customer.dob if customer else None
            if dob is not None and digits in {
                dob.strftime("%d%m%Y"),
                dob.strftime("%d%m") + str(dob.year + 543),  # Buddhist era, as printed
            }:
                # The OUTCOME only. A challenge answer is never stored (`D42`), which is
                # why this branch returns no `matched_value`.
                return True, None, "date of birth matches"
            return False, None, "no match"

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
    known = identity.assurance.at_least(AssuranceLevel.L1_PROBABLE)
    disclose = identity.may_disclose_policy_details

    customer_out: BriefCustomerOut | None = None
    if known and payload and payload.customer:
        who = payload.customer
        customer_out = BriefCustomerOut(
            display_name_th=" ".join(x for x in (who.first_name_th, who.last_name_th) if x),
            segment=str(who.segment),
            is_vulnerable=who.is_vulnerable,
        )

    policy_out: BriefPolicyOut | None = None
    if disclose and payload and payload.relevant_policy:
        policy = payload.relevant_policy
        policy_out = BriefPolicyOut(
            policy_no=policy.policy_no,
            product_th=payload.selected_product.name_th if payload.selected_product else None,
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
        disclosure_locked=not disclose,
        degraded=str(brief.degraded),
        build_ms=brief.build_ms,
        provenance=tuple(
            BriefProvenanceOut(field=p.field, source=p.source, stale=p.stale)
            for p in (snapshot.provenance if snapshot else ())
        ),
    )
