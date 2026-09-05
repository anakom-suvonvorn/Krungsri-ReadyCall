"""One suite, every durable store, three backends each (`D3`, `D75`, `D78`).

Six of them finished P2c — assignments, attestations, keypad captures, matching decisions,
context snapshots and wrap-ups — and `audio_recordings` joined them with `D110`. Every one
has an in-memory implementation that the default configuration uses and a Postgres
implementation that survives a restart, and the whole value of that arrangement depends on
the two behaving identically. So they are tested together, against the same assertions.

**The round-trips compare whole objects**, not field by field. A field added to a domain
model and forgotten in a hand-written mapper is the failure mode this layer has (`D77`),
and a test whose expectations were written by reading the mapper cannot see it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from readycall.db.base import SCHEMA, Base
from readycall.db.session import create_engine, create_session_factory
from readycall.db.stores import (
    PostgresAssignmentStore,
    PostgresAttestationStore,
    PostgresCaptureStore,
    PostgresMatchingDecisionStore,
    PostgresRecordingStore,
    PostgresSnapshotStore,
    PostgresWrapupStore,
)
from readycall.domain.enums import (
    AssuranceLevel,
    CallState,
    DegradationReason,
    EntryChannel,
    IdentityMethod,
    MatchKind,
    OfferOutcome,
    RecordingPhase,
)
from readycall.domain.models import (
    Assignment,
    AudioRecording,
    CallSession,
    CallWrapup,
    ContextSnapshot,
    Customer,
    Customer360,
    FieldProvenance,
    FitBreakdown,
    MatchCandidate,
    MatchingDecision,
    UrgencyBreakdown,
)
from readycall.services.agents.assignment import InMemoryAssignmentStore
from readycall.services.agents.dispatch import InMemoryMatchingDecisionStore
from readycall.services.capture.keypad import (
    Capture,
    CaptureState,
    InMemoryCaptureStore,
    LookupResult,
)
from readycall.services.context.store import InMemorySnapshotStore
from readycall.services.identity.attestation import (
    Attestation,
    AttestationOutcome,
    InMemoryAttestationStore,
)
from readycall.services.recording.store import InMemoryRecordingStore
from readycall.services.wrapup.store import InMemoryWrapupStore

#: A **separate database** from the one the app uses, and that separation is load-bearing.
#: These suites create their tables with `create_all` and drop them on teardown; pointed at
#: the dev database that deletes its contents *and* leaves `alembic_version` stamped at head
#: with no tables behind it, so `alembic upgrade head` becomes a silent no-op (`B9`).
POSTGRES_URL = os.environ.get(
    "READYCALL_TEST_DATABASE_URL",
    "postgresql+asyncpg://readycall:readycall@127.0.0.1:5432/readycall_test",
)
T0 = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)
CALL = "call_store_1"

#: Every store keyed to its two implementations. Adding a store without adding a row here
#: is the mistake this table exists to make obvious.
BUILDERS: dict[str, tuple[Any, Any]] = {
    "assignments": (InMemoryAssignmentStore, PostgresAssignmentStore),
    "attestations": (InMemoryAttestationStore, PostgresAttestationStore),
    "captures": (InMemoryCaptureStore, PostgresCaptureStore),
    "decisions": (InMemoryMatchingDecisionStore, PostgresMatchingDecisionStore),
    "recordings": (InMemoryRecordingStore, PostgresRecordingStore),
    "snapshots": (InMemorySnapshotStore, PostgresSnapshotStore),
    "wrapups": (InMemoryWrapupStore, PostgresWrapupStore),
}


async def _postgres_reachable() -> bool:
    try:
        engine = create_engine(POSTGRES_URL)
        async with engine.connect():
            pass
        await engine.dispose()
    except Exception:
        return False
    return True


async def _prepare(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            await conn.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {SCHEMA}")
        await conn.run_sync(Base.metadata.create_all)


class _Bundle:
    """Every store on one backend, plus the parent call their FKs point at."""

    def __init__(self, backend: str, factory: Any = None) -> None:
        self.backend = backend
        self.factory = factory
        pick = 0 if factory is None else 1
        for attr, impls in BUILDERS.items():
            impl = impls[pick]
            setattr(self, attr, impl() if factory is None else impl(factory))


@pytest_asyncio.fixture(params=["memory", "sqlite", "postgres"])
async def stores(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield _Bundle("memory")
        return

    url = "sqlite+aiosqlite://" if request.param == "sqlite" else POSTGRES_URL
    if request.param == "postgres" and not await _postgres_reachable():
        pytest.skip("no Postgres reachable; `docker compose -f infra/docker-compose.yml up -d`")

    engine = create_engine(url)
    await _prepare(engine)
    factory = create_session_factory(engine)
    # Every table here has a foreign key to `call_sessions`, and on a SQL backend that key
    # is enforced (`D75`). The parent row is part of the fixture rather than something each
    # test remembers, because "the FK is real" is the point and not an obstacle.
    from readycall.db.repositories import PostgresCallSessionRepository

    await PostgresCallSessionRepository(factory).save(
        CallSession(
            call_session_id=CALL,
            entry_channel=EntryChannel.HOTLINE,
            state=CallState.IN_CALL,
            created_at=T0,
        )
    )
    try:
        yield _Bundle(request.param, factory)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# --- assignments (D33, D52) ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_assignment_round_trips_whole(stores: Any) -> None:
    """Every measured timestamp survives, because every one of them is a claim (`D45`)."""
    assignment = Assignment(
        assignment_id="asgn_1",
        call_session_id=CALL,
        agent_id="A001",
        offered_at=T0,
        accept_mode="manual",
        outcome=OfferOutcome.ACCEPTED,
        accepted_at=T0,
        bridged_at=T0,
        ended_at=T0,
        acw_started_at=T0,
        acw_ended_at=T0,
        acw_ended_by="ready",
    )
    await stores.assignments.save(assignment)
    assert await stores.assignments.for_calls([CALL]) == [assignment]


@pytest.mark.asyncio
async def test_saving_an_assignment_twice_updates_it_rather_than_duplicating(stores: Any) -> None:
    """An offer is one row moving through its outcomes, not a row per outcome.

    The handshake saves the same assignment on offer, on accept, on end and again when
    after-call work closes. An implementation that inserted each time would look right —
    the newest row is correct and nobody reads the middle — while quietly reporting four
    offers where one was made.
    """
    assignment = Assignment(
        assignment_id="asgn_1",
        call_session_id=CALL,
        agent_id="A001",
        offered_at=T0,
        outcome=OfferOutcome.PENDING,
    )
    await stores.assignments.save(assignment)
    await stores.assignments.save(assignment.model_copy(update={"outcome": OfferOutcome.ACCEPTED}))

    rows = await stores.assignments.for_calls([CALL])
    assert len(rows) == 1
    assert rows[0].outcome is OfferOutcome.ACCEPTED


@pytest.mark.asyncio
async def test_a_decline_and_a_timeout_stay_distinguishable(stores: Any) -> None:
    """`D52` treats them the same for exclusion and `D51` treats them differently for the
    agent, so collapsing them into "did not take it" would lose a real fact about a person.
    """
    for n, outcome in enumerate([OfferOutcome.DECLINED, OfferOutcome.TIMEOUT]):
        await stores.assignments.save(
            Assignment(
                assignment_id=f"asgn_{n}",
                call_session_id=CALL,
                agent_id=f"A00{n}",
                offered_at=T0,
                outcome=outcome,
                decline_reason="busy" if outcome is OfferOutcome.DECLINED else None,
            )
        )
    rows = sorted(await stores.assignments.for_calls([CALL]), key=lambda a: a.assignment_id)
    assert [r.outcome for r in rows] == [OfferOutcome.DECLINED, OfferOutcome.TIMEOUT]
    assert rows[0].decline_reason == "busy"
    assert rows[1].decline_reason is None


# --- attestations: the disclosure log (D42, D57, D60, D61) ---------------------------------


@pytest.mark.asyncio
async def test_the_disclosure_log_appends_and_keeps_its_order(stores: Any) -> None:
    """A correction **appends** (`D61`). Both statements survive, in sequence.

    This is the single most important assertion in this file. If an amendment overwrote,
    the record would show only the final answer, and the whole reason for a disclosure log
    is that it shows what was said and when it changed.
    """
    first = Attestation(
        call_session_id=CALL,
        agent_id="A001",
        outcome=AttestationOutcome.CONFIRMED,
        at=T0,
        challenge="date_of_birth",
    )
    second = Attestation(
        call_session_id=CALL,
        agent_id="A001",
        outcome=AttestationOutcome.THIRD_PARTY,
        at=T0.replace(minute=5),
        caller_name="สุดา ใจดี",
        relationship="ลูกสาว",
    )
    await stores.attestations.append(first)
    await stores.attestations.append(second)

    rows = await stores.attestations.for_calls([CALL])
    assert rows == [first, second]


@pytest.mark.asyncio
async def test_a_third_party_keeps_its_name_and_relationship(stores: Any) -> None:
    """`D57`: the log must say *who* called, not merely that it was not the policyholder.

    Thai text on purpose — this is a Thai product, and a column that silently mangles
    non-ASCII would be found by a customer rather than by a test.
    """
    await stores.attestations.append(
        Attestation(
            call_session_id=CALL,
            agent_id="A001",
            outcome=AttestationOutcome.THIRD_PARTY,
            at=T0,
            caller_name="สุดา ใจดี",
            relationship="ลูกสาว",
        )
    )
    row = (await stores.attestations.for_calls([CALL]))[0]
    assert row.caller_name == "สุดา ใจดี"
    assert row.relationship == "ลูกสาว"


@pytest.mark.asyncio
async def test_a_rejection_records_who_was_wrongly_proposed(stores: Any) -> None:
    """Rejection is information, not the absence of it (`D42`) — and `D71` needs this
    field to offer the match back if the agent withdraws the rejection."""
    await stores.attestations.append(
        Attestation(
            call_session_id=CALL,
            agent_id="A001",
            outcome=AttestationOutcome.NOT_THIS_PERSON,
            at=T0,
            rejected_customer_id="C000002",
        )
    )
    assert (await stores.attestations.for_calls([CALL]))[0].rejected_customer_id == "C000002"


# --- keypad captures (D44, D58) ---------------------------------------------------------


def a_capture(**over: Any) -> Capture:
    base: dict[str, Any] = {
        "capture_id": "cap_1",
        "call_session_id": CALL,
        "agent_id": "A001",
        "started_at": T0,
        "state": CaptureState.STOPPED,
        "digits": "2024000811",
        "stopped_at": T0,
    }
    return Capture(**{**base, **over})


@pytest.mark.asyncio
async def test_unnamed_digits_are_not_stored_but_the_capture_is(stores: Any) -> None:
    """`D44`'s inverted default, and the reason this store takes a `store_digits` flag.

    Capture is untyped, so an unnamed run of digits could be a citizen id or a card number.
    What survives is the audit fact — a capture of this length happened — not the value.
    """
    await stores.captures.save(a_capture(), store_digits=False)

    row = (await stores.captures.for_calls([CALL]))[0]
    assert row.capture_id == "cap_1"
    assert row.digits == ""
    assert row.masked == ""  # nothing to mask once the digits are gone


@pytest.mark.asyncio
async def test_named_digits_are_stored_in_the_clear(stores: Any) -> None:
    """Once a lookup names them they are a policy number, which is not a secret (`D44`)."""
    capture = a_capture(
        labelled_as="policy_number",
        lookups=[
            LookupResult(kind="policy_number", matched=True, at=T0, matched_value="HL-2024-000811")
        ],
    )
    assert capture.is_named
    await stores.captures.save(capture, store_digits=capture.is_named)

    row = (await stores.captures.for_calls([CALL]))[0]
    assert row.digits == "2024000811"
    assert row.lookups[0].matched_value == "HL-2024-000811"


@pytest.mark.asyncio
async def test_a_stored_capture_does_not_change_under_the_reader(stores: Any) -> None:
    """`Capture` is mutable and the service keeps editing the one it handed us.

    A store that kept the live object would make the "durable" row follow every later
    keystroke — which would make every other test in this file pass for the wrong reason.
    """
    capture = a_capture(labelled_as="policy_number")
    await stores.captures.save(capture, store_digits=True)
    capture.digits = "9999999999"

    assert (await stores.captures.for_calls([CALL]))[0].digits == "2024000811"


# --- matching decisions (D18, D22, D50) ---------------------------------------------------


def a_decision(decision_id: str = "dec_1", **over: Any) -> MatchingDecision:
    base: dict[str, Any] = {
        "decision_id": decision_id,
        "call_session_id": CALL,
        "at": T0,
        "kind": MatchKind.ASSIGN,
        "candidates": (
            MatchCandidate(
                agent_id="A006",
                fit=FitBreakdown(skill_match=0.9, continuity=0.0, total=0.72),
                score=2.276,
            ),
            MatchCandidate(
                agent_id="A002",
                fit=FitBreakdown(hard_filter_failed="already_offered"),
                score=0.0,
            ),
        ),
        "urgency": UrgencyBreakdown(wait_pressure=0.4, total=0.4),
        "chosen_agent_id": "A006",
        "total_score": 2.276,
        "rationale_th": "เจ้าหน้าที่มีทักษะตรงและว่างอยู่",
        "weights_version": "v1",
        "solver": "hungarian",
        "decide_ms": 0.4,
    }
    return MatchingDecision(**{**base, **over})


@pytest.mark.asyncio
async def test_a_decision_keeps_every_candidate_and_every_term(stores: Any) -> None:
    """The explainability claim rests on this row (`D18`).

    Including the candidate that *failed a hard filter*: `D22` says a filter is not a low
    score, and a store that kept only the winner could never answer "why not that agent".
    """
    decision = a_decision()
    await stores.decisions.append(decision)

    restored = (await stores.decisions.latest_for_calls([CALL]))[CALL]
    assert restored == decision
    assert restored.candidates[1].fit.hard_filter_failed == "already_offered"


@pytest.mark.asyncio
async def test_an_unplaced_call_is_stored_with_which_reason_applies(stores: Any) -> None:
    """`D50`/`B4`: a roster gap and a capacity shortfall demand opposite responses, so a
    store that could not tell them apart would be keeping noise."""
    await stores.decisions.append(
        a_decision("dec_busy", kind=MatchKind.ALL_QUALIFIED_BUSY, chosen_agent_id=None)
    )
    restored = (await stores.decisions.latest_for_calls([CALL]))[CALL]
    assert restored.kind is MatchKind.ALL_QUALIFIED_BUSY
    assert restored.chosen_agent_id is None


@pytest.mark.asyncio
async def test_the_newest_decision_per_call_wins(stores: Any) -> None:
    """A call is matched repeatedly — every tick it is still waiting. The offer card
    renders the latest rationale, so restore has to hand back the latest one."""
    await stores.decisions.append(a_decision("dec_old", rationale_th="เก่า"))
    await stores.decisions.append(
        a_decision("dec_new", at=T0.replace(minute=9), rationale_th="ใหม่")
    )
    assert (await stores.decisions.latest_for_calls([CALL]))[CALL].rationale_th == "ใหม่"


# --- context snapshots (D6, D42) -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_snapshot_round_trips_with_its_provenance(stores: Any) -> None:
    """Without this a restored call renders an empty screen, because the brief is a
    re-render of the frozen payload rather than a fetch (`D42`)."""
    snapshot = ContextSnapshot(
        snapshot_id="snap_1",
        customer_id="C000001",
        built_at=T0,
        payload=Customer360(
            customer=Customer(customer_id="C000001", first_name_th="ภัทธีรา", last_name_th="ส.")
        ),
        provenance=(
            FieldProvenance(
                field="customer", source="core:customers", fetched_at=T0, provider="fixtures"
            ),
        ),
        provider_name="fixtures",
        build_ms=1.8,
        degraded=DegradationReason.NONE,
    )
    await stores.snapshots.save(snapshot)

    restored = await stores.snapshots.get("snap_1")
    assert restored == snapshot
    assert restored is not None
    assert restored.payload.customer is not None
    assert restored.payload.customer.first_name_th == "ภัทธีรา"


@pytest.mark.asyncio
async def test_an_unknown_snapshot_is_none_rather_than_an_error(stores: Any) -> None:
    assert await stores.snapshots.get("snap_missing") is None


# --- wrap-ups (D45) --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wrapup_round_trips(stores: Any) -> None:
    wrapup = CallWrapup(
        call_session_id=CALL,
        agent_id="A001",
        saved_at=T0,
        disposition="resolved",
        notes="ลูกค้าต้องการเอกสารเพิ่ม",
        follow_up_required=True,
        was_edited=True,
    )
    await stores.wrapups.save(wrapup)
    assert await stores.wrapups.for_calls([CALL]) == [wrapup]


@pytest.mark.asyncio
async def test_no_wrapup_is_a_meaningful_answer(stores: Any) -> None:
    """`D45` refuses to auto-save, so *nothing here* records that the call was never
    wrapped up. An empty list is the data, not a missing case."""
    assert await stores.wrapups.for_calls([CALL]) == []


# --- shared behaviour ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_store_ignores_calls_it_was_not_asked_about(stores: Any) -> None:
    """Restore is bounded to the live calls (`D78`), so a store that ignored the filter
    would pull a shift of history into memory and look fine doing it."""
    await stores.assignments.save(
        Assignment(
            assignment_id="asgn_1",
            call_session_id=CALL,
            agent_id="A001",
            offered_at=T0,
            outcome=OfferOutcome.ACCEPTED,
        )
    )
    assert await stores.assignments.for_calls(["call_other"]) == []
    assert await stores.assignments.for_calls([]) == []


@pytest.mark.asyncio
async def test_a_row_cannot_point_at_a_call_that_does_not_exist(stores: Any) -> None:
    """The foreign key is real on every SQL backend, SQLite included (`D75`).

    Worth asserting per store rather than once: a table added later without the FK would
    let orphan rows accumulate, and orphaned audit rows are worse than missing ones —
    they describe a call nobody can look up.
    """
    if stores.backend == "memory":
        pytest.skip("a dict has no referential integrity to enforce")

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await stores.attestations.append(
            Attestation(
                call_session_id="call_does_not_exist",
                agent_id="A001",
                outcome=AttestationOutcome.CONFIRMED,
                at=T0,
                challenge="date_of_birth",
            )
        )


@pytest.mark.asyncio
async def test_the_identity_resolution_on_a_call_survives(stores: Any) -> None:
    """Not a store of its own (`D78`): the live resolution is a column on the session, so
    a restored call already knows how sure we were about who was calling."""
    if stores.backend == "memory":
        pytest.skip("covered by the call-session suite on the SQL backends")

    from readycall.db.repositories import PostgresCallSessionRepository
    from readycall.domain.models import IdentityResolution

    repo = PostgresCallSessionRepository(stores.factory)
    session = await repo.get(CALL)
    assert session is not None
    resolution = IdentityResolution(
        method=IdentityMethod.ANI,
        customer_id="C000001",
        assurance=AssuranceLevel.L1_PROBABLE,
        resolved_at=T0,
    )
    await repo.save(session.model_copy(update={"identity": resolution}))

    restored = await repo.get(CALL)
    assert restored is not None
    assert restored.identity == resolution


# --- recordings (D110, D14) ----------------------------------------------------------------


def _recording(
    recording_id: str = "rec_1", *, delete_after: datetime | None = None
) -> AudioRecording:
    return AudioRecording(
        recording_id=recording_id,
        call_session_id=CALL,
        phase=RecordingPhase.INTAKE,
        storage_ref="s3://readycall-recordings/calls/call_store_1/intake-customer.wav",
        created_at=T0,
        duration_s=19.44,
        size_bytes=622228,
        checksum="a" * 64,
        encryption_key_ref="local:v1",
        delete_after=delete_after,
        intake_id="ik_1",
    )


@pytest.mark.asyncio
async def test_a_recording_round_trips_whole(stores: Any) -> None:
    """Including `encryption_key_ref` — a row that loses it is a recording nobody can open."""
    recording = _recording(delete_after=T0.replace(month=11))

    await stores.recordings.save(recording)

    assert await stores.recordings.for_calls([CALL]) == [recording]


@pytest.mark.asyncio
async def test_retention_finds_only_what_is_actually_due(stores: Any) -> None:
    """The purge job's only query, and the one that must not over-collect (`D14`)."""
    await stores.recordings.save(_recording("rec_due", delete_after=T0.replace(day=1)))
    await stores.recordings.save(_recording("rec_later", delete_after=T0.replace(month=12)))
    #: No `delete_after` at all means an indefinite hold, not "delete immediately". The
    #: opposite reading would quietly erase anything written before retention existed.
    await stores.recordings.save(_recording("rec_forever", delete_after=None))

    due = await stores.recordings.due_for_deletion(now=T0)

    assert [r.recording_id for r in due] == ["rec_due"]


@pytest.mark.asyncio
async def test_the_purge_cap_is_honoured(stores: Any) -> None:
    """An erasure run has to be resumable rather than one enormous transaction."""
    for i in range(5):
        await stores.recordings.save(_recording(f"rec_{i}", delete_after=T0.replace(day=1)))

    assert len(await stores.recordings.due_for_deletion(now=T0, limit=2)) == 2


@pytest.mark.asyncio
async def test_deleting_a_recording_removes_the_row(stores: Any) -> None:
    await stores.recordings.save(_recording())

    await stores.recordings.delete("rec_1")

    assert await stores.recordings.for_calls([CALL]) == []
    # Twice is not an error: a purge and an erasure request can genuinely race.
    await stores.recordings.delete("rec_1")
