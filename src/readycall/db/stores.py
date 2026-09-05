"""Postgres implementations of the six store seams the workstation depends on.

One module, because reading them together is the point: this is the **whole durable
surface** of a call. Six tables, six mappers, and the same three-part shape each time —
a `Protocol` beside the service that consumes it, an in-memory implementation for the
default configuration, and this.

Every method here returns **domain models, never ORM rows** (`D77`). A row carries a
session lifetime with it, and the first place that breaks is a background sweep whose
session has closed — a lazy-load error a long way from its cause.

**The mapping is hand-written on purpose.** It is the one place the database shape and the
domain shape are allowed to differ, and generated mapping hides exactly the decision that
matters: what is a column and what is JSON. The rule, once, since it keeps being asked:

> Anything the matcher, a report, or a query **filters on** is a column. Anything only ever
> read back whole is JSON.

The risk that creates — a field added to a domain model and forgotten in a mapper — is
covered the only way that works: the contract suite round-trips a **whole object and
compares equality**, rather than asserting field by field from a list somebody wrote by
reading the mapper.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from readycall.db.models import (
    AssignmentRow,
    AttestationRow,
    AudioRecordingRow,
    CallWrapupRow,
    ContextSnapshotRow,
    KeypadCaptureRow,
    MatchingDecisionRow,
)
from readycall.db.session import session_scope
from readycall.domain.enums import DegradationReason, MatchKind, OfferOutcome, RecordingPhase
from readycall.domain.models import (
    Assignment,
    AudioRecording,
    CallWrapup,
    ContextSnapshot,
    Customer360,
    FieldProvenance,
    MatchCandidate,
    MatchingDecision,
    UrgencyBreakdown,
)
from readycall.logging import get_logger
from readycall.services.capture.keypad import Capture, CaptureState, LookupResult
from readycall.services.identity.attestation import Attestation, AttestationOutcome

log = get_logger(__name__)


# --- assignments: the offer handshake (D33) -----------------------------------------------


class PostgresAssignmentStore:
    """Upsert, because an assignment is one offer moving through its outcomes."""

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def save(self, assignment: Assignment) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(AssignmentRow, assignment.assignment_id)
            if row is None:
                row = AssignmentRow(assignment_id=assignment.assignment_id)
                db.add(row)
            row.call_session_id = assignment.call_session_id
            row.agent_id = assignment.agent_id
            row.offered_at = assignment.offered_at
            row.accept_mode = assignment.accept_mode
            row.outcome = str(assignment.outcome)
            row.accepted_at = assignment.accepted_at
            row.decline_reason = assignment.decline_reason
            row.bridged_at = assignment.bridged_at
            row.ended_at = assignment.ended_at
            row.acw_started_at = assignment.acw_started_at
            row.acw_ended_at = assignment.acw_ended_at
            row.acw_ended_by = assignment.acw_ended_by

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Assignment]:
        if not call_session_ids:
            return []
        async with self._factory() as db:
            found = await db.scalars(
                select(AssignmentRow)
                .where(AssignmentRow.call_session_id.in_(list(call_session_ids)))
                .order_by(AssignmentRow.offered_at)
            )
            return [
                Assignment(
                    assignment_id=row.assignment_id,
                    call_session_id=row.call_session_id,
                    agent_id=row.agent_id,
                    offered_at=row.offered_at,
                    accept_mode=row.accept_mode,
                    outcome=OfferOutcome(row.outcome),
                    accepted_at=row.accepted_at,
                    decline_reason=row.decline_reason,
                    bridged_at=row.bridged_at,
                    ended_at=row.ended_at,
                    acw_started_at=row.acw_started_at,
                    acw_ended_at=row.acw_ended_at,
                    acw_ended_by=row.acw_ended_by,
                )
                for row in found.all()
            ]


# --- attestations: the disclosure log (D42, D57, D60, D61, D65) ----------------------------


class PostgresAttestationStore:
    """Append-only. There is no update path here and there must not be one."""

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def append(self, attestation: Attestation) -> None:
        async with session_scope(self._factory) as db:
            db.add(
                AttestationRow(
                    call_session_id=attestation.call_session_id,
                    agent_id=attestation.agent_id,
                    outcome=str(attestation.outcome),
                    at=attestation.at,
                    challenge=attestation.challenge,
                    challenge_note=attestation.challenge_note,
                    rejected_customer_id=attestation.rejected_customer_id,
                    caller_name=attestation.caller_name,
                    relationship=attestation.relationship,
                    note=attestation.note,
                )
            )

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Attestation]:
        if not call_session_ids:
            return []
        async with self._factory() as db:
            found = await db.scalars(
                select(AttestationRow)
                .where(AttestationRow.call_session_id.in_(list(call_session_ids)))
                # By id, not only by time: two attestations can share a timestamp under a
                # `ManualClock`, and the *sequence* is the record.
                .order_by(AttestationRow.at, AttestationRow.id)
            )
            return [
                Attestation(
                    call_session_id=row.call_session_id,
                    agent_id=row.agent_id,
                    outcome=AttestationOutcome(row.outcome),
                    at=row.at,
                    challenge=row.challenge,
                    challenge_note=row.challenge_note,
                    rejected_customer_id=row.rejected_customer_id,
                    caller_name=row.caller_name,
                    relationship=row.relationship,
                    note=row.note,
                )
                for row in found.all()
            ]


# --- keypad captures (D44, D58) ------------------------------------------------------------


class PostgresCaptureStore:
    """`store_digits` is decided by the service (`D44`); this only carries it out."""

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def save(self, capture: Capture, *, store_digits: bool) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(KeypadCaptureRow, capture.capture_id)
            if row is None:
                row = KeypadCaptureRow(capture_id=capture.capture_id)
                db.add(row)
            row.call_session_id = capture.call_session_id
            row.agent_id = capture.agent_id
            row.started_at = capture.started_at
            row.stopped_at = capture.stopped_at
            row.state = str(capture.state)
            row.labelled_as = capture.labelled_as
            # Always. The audit fact — a capture of this length happened — survives even
            # when the value does not.
            row.masked = capture.masked
            row.digit_count = capture.length
            row.digits = capture.digits if store_digits else None
            row.lookups = [
                {
                    "kind": x.kind,
                    "matched": x.matched,
                    "at": x.at.isoformat(),
                    "matched_value": x.matched_value,
                    "detail": x.detail,
                }
                for x in capture.lookups
            ]

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[Capture]:
        if not call_session_ids:
            return []
        async with self._factory() as db:
            found = await db.scalars(
                select(KeypadCaptureRow)
                .where(KeypadCaptureRow.call_session_id.in_(list(call_session_ids)))
                .order_by(KeypadCaptureRow.started_at)
            )
            return [
                Capture(
                    capture_id=row.capture_id,
                    call_session_id=row.call_session_id,
                    agent_id=row.agent_id,
                    started_at=row.started_at,
                    state=CaptureState(row.state),
                    # `""` when the digits were never named, which is the whole point of
                    # the column being nullable (`D44`).
                    digits=row.digits or "",
                    stopped_at=row.stopped_at,
                    labelled_as=row.labelled_as,
                    lookups=[
                        LookupResult(
                            kind=str(x["kind"]),
                            matched=bool(x["matched"]),
                            at=datetime.fromisoformat(str(x["at"])),
                            matched_value=x.get("matched_value"),
                            detail=x.get("detail"),
                        )
                        for x in row.lookups
                    ],
                )
                for row in found.all()
            ]


# --- matching decisions: the explainability record (D18, D22, D50) -------------------------


class PostgresMatchingDecisionStore:
    """Append-only, and it keeps the calls that were *not* assigned (`D50`)."""

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def append(self, decision: MatchingDecision) -> None:
        async with session_scope(self._factory) as db:
            db.add(
                MatchingDecisionRow(
                    decision_id=decision.decision_id,
                    call_session_id=decision.call_session_id,
                    at=decision.at,
                    kind=str(decision.kind),
                    chosen_agent_id=decision.chosen_agent_id,
                    total_score=decision.total_score,
                    deferred_for_agent_id=decision.deferred_for_agent_id,
                    expected_free_in_s=decision.expected_free_in_s,
                    fit_gap=decision.fit_gap,
                    rationale_th=decision.rationale_th,
                    rationale_en=decision.rationale_en,
                    weights_version=decision.weights_version,
                    solver=decision.solver,
                    decide_ms=decision.decide_ms,
                    candidates=[c.model_dump(mode="json") for c in decision.candidates],
                    urgency=decision.urgency.model_dump(mode="json") if decision.urgency else None,
                )
            )

    async def latest_for_calls(
        self, call_session_ids: Sequence[str]
    ) -> dict[str, MatchingDecision]:
        if not call_session_ids:
            return {}
        async with self._factory() as db:
            found = await db.scalars(
                select(MatchingDecisionRow)
                .where(MatchingDecisionRow.call_session_id.in_(list(call_session_ids)))
                .order_by(MatchingDecisionRow.at)
            )
            latest: dict[str, MatchingDecision] = {}
            for row in found.all():
                latest[row.call_session_id] = _row_to_decision(row)
            return latest


def _row_to_decision(row: MatchingDecisionRow) -> MatchingDecision:
    return MatchingDecision(
        decision_id=row.decision_id,
        call_session_id=row.call_session_id,
        at=row.at,
        kind=MatchKind(row.kind),
        candidates=tuple(MatchCandidate.model_validate(c) for c in row.candidates),
        urgency=UrgencyBreakdown.model_validate(row.urgency) if row.urgency else None,
        chosen_agent_id=row.chosen_agent_id,
        total_score=row.total_score,
        deferred_for_agent_id=row.deferred_for_agent_id,
        expected_free_in_s=row.expected_free_in_s,
        fit_gap=row.fit_gap,
        rationale_th=row.rationale_th,
        rationale_en=row.rationale_en,
        weights_version=row.weights_version,
        solver=row.solver,
        decide_ms=row.decide_ms,
    )


# --- context snapshots: what the brief is re-rendered from (D6, D42) -----------------------


class PostgresSnapshotStore:
    """Without this a restored call is a shell — see `ContextSnapshotRow`."""

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def save(self, snapshot: ContextSnapshot) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(ContextSnapshotRow, snapshot.snapshot_id)
            if row is None:
                row = ContextSnapshotRow(snapshot_id=snapshot.snapshot_id)
                db.add(row)
            row.customer_id = snapshot.customer_id
            row.built_at = snapshot.built_at
            row.payload = snapshot.payload.model_dump(mode="json")
            row.provenance = [p.model_dump(mode="json") for p in snapshot.provenance]
            row.provider_name = snapshot.provider_name
            row.build_ms = snapshot.build_ms
            row.degraded = str(snapshot.degraded)

    async def get(self, snapshot_id: str) -> ContextSnapshot | None:
        async with self._factory() as db:
            row = await db.get(ContextSnapshotRow, snapshot_id)
            if row is None:
                return None
            return ContextSnapshot(
                snapshot_id=row.snapshot_id,
                customer_id=row.customer_id,
                built_at=row.built_at,
                payload=Customer360.model_validate(row.payload),
                provenance=tuple(FieldProvenance.model_validate(p) for p in row.provenance),
                provider_name=row.provider_name,
                build_ms=row.build_ms,
                degraded=DegradationReason(row.degraded),
            )


# --- wrap-ups: what the agent wrote (D45) ---------------------------------------------------


class PostgresWrapupStore:
    """One row per call, written only by a person. Absence is meaningful (`D45`)."""

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def save(self, wrapup: CallWrapup) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(CallWrapupRow, wrapup.call_session_id)
            if row is None:
                row = CallWrapupRow(call_session_id=wrapup.call_session_id)
                db.add(row)
            row.agent_id = wrapup.agent_id
            row.saved_at = wrapup.saved_at
            row.disposition = wrapup.disposition
            row.notes = wrapup.notes
            row.follow_up_required = wrapup.follow_up_required
            row.was_edited = wrapup.was_edited

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[CallWrapup]:
        if not call_session_ids:
            return []
        async with self._factory() as db:
            found = await db.scalars(
                select(CallWrapupRow).where(
                    CallWrapupRow.call_session_id.in_(list(call_session_ids))
                )
            )
            return [
                CallWrapup(
                    call_session_id=row.call_session_id,
                    agent_id=row.agent_id,
                    saved_at=row.saved_at,
                    disposition=row.disposition,
                    notes=row.notes,
                    follow_up_required=row.follow_up_required,
                    was_edited=row.was_edited,
                )
                for row in found.all()
            ]


# --- recordings: where the audio is, and when it dies (D110) --------------------------------


class PostgresRecordingStore:
    """`audio_recordings`. The index to the objects, never the audio and never the key.

    The only store here with **no in-memory projection to keep in step** (`D78`): nothing
    reads a recording on a hot path, so there is no working set to rebuild at startup and
    no second copy of the fact to go stale.
    """

    name = "postgres"

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def save(self, recording: AudioRecording) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(AudioRecordingRow, recording.recording_id)
            if row is None:
                row = AudioRecordingRow(recording_id=recording.recording_id)
                db.add(row)
            row.call_session_id = recording.call_session_id
            row.phase = str(recording.phase)
            row.storage_ref = recording.storage_ref
            row.created_at = recording.created_at
            row.duration_s = recording.duration_s
            row.size_bytes = recording.size_bytes
            row.checksum = recording.checksum
            row.sample_rate = recording.sample_rate
            row.audio_format = recording.audio_format
            row.encryption_key_ref = recording.encryption_key_ref
            row.delete_after = recording.delete_after
            row.intake_id = recording.intake_id

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[AudioRecording]:
        if not call_session_ids:
            return []
        async with session_scope(self._factory) as db:
            found = await db.execute(
                select(AudioRecordingRow).where(
                    AudioRecordingRow.call_session_id.in_(list(call_session_ids))
                )
            )
            return [_recording(row) for row in found.scalars().all()]

    async def due_for_deletion(self, *, now: datetime, limit: int = 500) -> list[AudioRecording]:
        async with session_scope(self._factory) as db:
            found = await db.execute(
                select(AudioRecordingRow)
                .where(AudioRecordingRow.delete_after.is_not(None))
                .where(AudioRecordingRow.delete_after <= now)
                .order_by(AudioRecordingRow.created_at)
                .limit(limit)
            )
            return [_recording(row) for row in found.scalars().all()]

    async def delete(self, recording_id: str) -> None:
        async with session_scope(self._factory) as db:
            row = await db.get(AudioRecordingRow, recording_id)
            if row is not None:
                await db.delete(row)


def _recording(row: AudioRecordingRow) -> AudioRecording:
    return AudioRecording(
        recording_id=row.recording_id,
        call_session_id=row.call_session_id,
        phase=RecordingPhase(row.phase),
        storage_ref=row.storage_ref,
        created_at=row.created_at,
        duration_s=row.duration_s,
        size_bytes=row.size_bytes,
        checksum=row.checksum,
        sample_rate=row.sample_rate,
        audio_format=row.audio_format,
        encryption_key_ref=row.encryption_key_ref,
        delete_after=row.delete_after,
        intake_id=row.intake_id,
    )


__all__ = [
    "PostgresAssignmentStore",
    "PostgresAttestationStore",
    "PostgresCaptureStore",
    "PostgresMatchingDecisionStore",
    "PostgresRecordingStore",
    "PostgresSnapshotStore",
    "PostgresWrapupStore",
]
