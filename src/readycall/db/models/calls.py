"""`call_sessions` and `call_state_transitions` — the spine, and its timeline.

Everything about one call hangs off `call_session_id`, which is why almost every other
table here carries it as a foreign key. The transition rows are what make the timeline
demonstrable rather than asserted (`D18`): a scenario replay prints the real elapsed time
between states because each one was written with its own timestamp and reason.

**A note on what is a column and what is JSON.** Anything the matcher, a report, or a query
filters on is a real column; anything only ever read back whole is JSON. Consents and
stage timings are read whole. `state`, `queue_id` and the timestamps are filtered on, so
they are columns with indexes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from readycall.db.base import Base, Json, Utc


class CallSessionRow(Base):
    __tablename__ = "call_sessions"

    call_session_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    entry_channel: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(Utc, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    intent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dialled_did: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_line: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    telephony_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    preferred_language: Mapped[str] = mapped_column(String(8), nullable=False, default="th")
    #: A list, not a column per language: the set is open and nothing filters on a single
    #: member — matching loads the call and asks it.
    acceptable_languages: Mapped[list[str]] = mapped_column(Json, nullable=False, default=list)

    menu_path: Mapped[list[str]] = mapped_column(Json, nullable=False, default=list)
    menu_intent_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    queue_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    snapshot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    intake_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: Read back whole, never filtered on.
    identity: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    consents: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    stage_timings_ms: Mapped[dict[str, float]] = mapped_column(Json, nullable=False, default=dict)

    transitions: Mapped[list[CallStateTransitionRow]] = relationship(
        back_populates="call",
        cascade="all, delete-orphan",
        order_by="CallStateTransitionRow.id",
        lazy="selectin",
    )

    __table_args__ = (
        # "Which calls are waiting" runs on every matcher tick, so state is indexed. The
        # telephony id is how an inbound webhook finds its call and must be fast and unique
        # enough to be trustworthy - two calls sharing one channel id is a bug we want the
        # database to refuse rather than a mystery to debug later.
        Index("ix_call_sessions_state", "state"),
        Index("ix_call_sessions_telephony_call_id", "telephony_call_id"),
        Index("ix_call_sessions_customer_id", "customer_id"),
        Index("ix_call_sessions_queued_at", "queued_at"),
    )


class CallStateTransitionRow(Base):
    """One row per state change. Append-only: this is the timeline (`D18`)."""

    __tablename__ = "call_state_transitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    call_session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="CASCADE"), nullable=False
    )
    #: Null on the first row: a call does not transition *into* existence from anywhere.
    from_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    at: Mapped[datetime] = mapped_column(Utc, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)

    call: Mapped[CallSessionRow] = relationship(back_populates="transitions")

    __table_args__ = (Index("ix_call_state_transitions_call", "call_session_id", "id"),)




class ContextSnapshotRow(Base):
    """The frozen `Customer360` a brief is rendered from (`D6`, `D7`).

    **Not an FK to `call_sessions`, deliberately.** A snapshot is built when the customer
    taps *Contact*, which is up to fifteen minutes before any call exists (`D6`) — that
    head start is the whole product. `CallSession.snapshot_id` points here when a call
    turns up; a snapshot for an intent that never became a call is still a valid row.

    Without this table a restored call is a shell: the state, the timeline and the identity
    all survive, and the agent's screen renders nothing, because the brief is a **re-render
    of the frozen payload** (`D42`) and the payload was in a dict.
    """

    __tablename__ = "context_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    built_at: Mapped[datetime] = mapped_column(Utc, nullable=False)

    #: The whole `Customer360`, read back whole and never filtered on. Freezing it is the
    #: point: the screen must show *what the system knew when it decided*, and a demo has
    #: to be reproducible even if the upstream source moves under it.
    payload: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    #: Field → where it came from and how old it was (`D18`). This is what lets every
    #: value on the agent's screen name its source.
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)

    provider_name: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    build_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    degraded: Mapped[str] = mapped_column(String(24), nullable=False, default="none")

    __table_args__ = (Index("ix_context_snapshots_customer", "customer_id", "built_at"),)


class CallWrapupRow(Base):
    """What the agent wrote when the call was over. **Only ever written by a person.**

    `D45` struck out an auto-save for the reason this table makes concrete: a row here is
    an agent's statement about a customer's file. An unsaved wrap-up is *honest data* — it
    records that this call was never wrapped up, which is true and useful. Auto-saving
    would replace it with a fabricated one, so the absence of a row is meaningful and
    nothing may create one on the agent's behalf.
    """

    __tablename__ = "call_wrapups"

    call_session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(Utc, nullable=False)
    disposition: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    follow_up_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Did the agent change the AI's draft? The honest input to "did the draft help",
    #: which is the evaluation signal behind the ACW claim (`D27`).
    was_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


__all__ = [
    "CallSessionRow",
    "CallStateTransitionRow",
    "CallWrapupRow",
    "ContextSnapshotRow",
]
