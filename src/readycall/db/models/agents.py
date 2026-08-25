"""`agent_state_log` — the append-only record of who was doing what, and who said so.

**Presence is a projection of this table, not a table of its own** (`D76`). Storing
"current state" alongside the log would create two facts that can disagree, and the one
that matters for workforce planning is the history: *"what was true at 14:03"* is the
question a supervisor actually asks, and a current-state row cannot answer it.

`D51` is the reason `set_by` and `reason` exist as separate columns. "They chose break" and
"we stopped offering because nobody picked up" are very different facts about the same
person, and a shift report that cannot tell them apart is worse than no shift report.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from readycall.db.base import Base, Utc


class AgentStateLogRow(Base):
    __tablename__ = "agent_state_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(Utc, nullable=False)

    #: Both axes on every row, even though only one moves at a time. Reconstructing the
    #: other from earlier rows is possible and is exactly the kind of thing that goes wrong
    #: once, silently, in a report nobody re-reads.
    system_state: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_intent: Mapped[str] = mapped_column(String(32), nullable=False)

    #: `agent` | `platform`. The platform writing the person's axis is the narrow exception
    #: in `D51`, and this column is what makes it auditable rather than sneaky.
    set_by: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)

    call_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="SET NULL"), nullable=True
    )
    #: Only on the row that ends after-call work: disconnect → declaration (`D45`).
    acw_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # The two queries this table exists to answer: "rebuild one agent's current state"
        # (newest row per agent, on startup) and "show me the shift" (everything in a
        # window). Both are covered by leading with agent_id and ordering on `at`.
        Index("ix_agent_state_log_agent_at", "agent_id", "at"),
        Index("ix_agent_state_log_at", "at"),
    )




class AssignmentRow(Base):
    """The offer handshake, measured end to end (`D33`).

    Every timestamp here is a metric the product claims. `offered_at → accepted_at` is how
    long an agent took to pick up; `acw_started_at → acw_ended_at` is after-call work,
    which is **the** number the AI-drafted wrap-up is supposed to shrink (`D45`). A claim
    you cannot measure across a restart is not a claim, which is most of why this table
    exists rather than the dict it replaces.

    Updated in place rather than appended, unlike the two logs beside it: an assignment is
    one offer moving through its outcomes, not a sequence of statements. The transitions it
    passes through are already recoverable from the timestamps.
    """

    __tablename__ = "assignments"

    assignment_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    call_session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)

    offered_at: Mapped[datetime] = mapped_column(Utc, nullable=False)
    accept_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    #: `pending` | `accepted` | `declined` | `timeout` | `cancelled`. A decline and a
    #: timeout both exclude this agent from re-matching the call (`D52`); a cancel does
    #: not, because nobody did anything wrong. That distinction is only recoverable
    #: because the outcome is stored rather than collapsed into "did not take it".
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    accepted_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    bridged_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)

    acw_started_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    acw_ended_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    #: The intent the agent declared — `ready`, `lunch`, … — never `timer`. A timer cannot
    #: end after-call work (`D45`), so it can never be the reason one ended.
    acw_ended_by: Mapped[str | None] = mapped_column(String(24), nullable=True)

    __table_args__ = (
        # "Which agents has this call already been offered to" runs on every dispatch tick
        # to build `D52`'s exclusion set, so it leads with the call.
        Index("ix_assignments_call", "call_session_id", "offered_at"),
        Index("ix_assignments_agent", "agent_id", "offered_at"),
    )


__all__ = ["AgentStateLogRow", "AssignmentRow"]
