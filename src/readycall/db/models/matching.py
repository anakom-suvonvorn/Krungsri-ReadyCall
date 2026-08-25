"""`matching_decisions` — why this caller reached this desk, and why that one is still waiting.

This is the table the product's explainability claim rests on. `D18` and `D22` both say the
same thing from different angles: every candidate, every score term, the chosen pair and any
deferral is written down, **including for the calls we chose not to assign** (`D50`). A judge
asking *"why did it pick that agent?"* and a supervisor asking *"why has this person waited
twelve seconds longer?"* are asking for the same row.

**`candidates` is deliberately fat JSON.** It is the one thing here nothing filters on — it
is read back whole, to render or to audit — and its shape follows `MatchCandidate`, which
changes as scoring changes. A column per score term would need a migration every time a
weight is added, and would still not answer a question anyone asks in SQL.

`kind` is a real column for the opposite reason: `D50` exists so a supervisor can see
*"eleven callers waiting on a skill gap"* at a glance, and that is a `GROUP BY`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from readycall.db.base import Base, Json, Utc


class MatchingDecisionRow(Base):
    __tablename__ = "matching_decisions"

    decision_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    call_session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(Utc, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)

    #: Null for every unplaced kind, and **also** for a deliberate `DEFER` — three
    #: different situations behind one null, which is why `kind` is the field to read
    #: and this one never is on its own (`B4`).
    chosen_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    deferred_for_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expected_free_in_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_gap: Mapped[float | None] = mapped_column(Float, nullable=True)

    rationale_th: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rationale_en: Mapped[str | None] = mapped_column(String(512), nullable=True)

    #: Which weights produced this. Without it a decision from last week cannot be
    #: reproduced after somebody tunes `matching_weights.yaml`, and the whole point of
    #: storing the breakdown is that it can be re-derived.
    weights_version: Mapped[str] = mapped_column(String(32), nullable=False, default="unversioned")
    solver: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    decide_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Read back whole (see the module docstring).
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)
    urgency: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)

    __table_args__ = (
        Index("ix_matching_decisions_call_at", "call_session_id", "at"),
        # "How many callers are unplaced right now, and for which of the two reasons"
        # (`D50`). An aggregate over a window, so both columns are needed together.
        Index("ix_matching_decisions_kind_at", "kind", "at"),
    )


__all__ = ["MatchingDecisionRow"]
