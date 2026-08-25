"""`identity_attestations` and `keypad_captures` — the disclosure log, and what was typed.

These two tables are the PDPA half of the record. Between them they answer the question a
regulator or an auditor actually asks: **on what basis did an employee decide it was safe
to talk to this caller?**

`identity_attestations` is append-only and never updated. `D60` made an attestation a signed
statement rather than a toggle, and `D61` made correcting one an *append* — so a call whose
agent confirmed, then realised they were speaking to the policyholder's daughter, keeps both
rows. That is a better record than either version alone, and it only works if nothing here
ever overwrites.

`keypad_captures` carries the one storage rule that inverts from everywhere else in this
codebase: **capture is untyped, so raw digits are sensitive until something names them**
(`D44`). See `KeypadCaptureRow.digits`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from readycall.db.base import Base, Json, Utc


class AttestationRow(Base):
    """One row of the disclosure log: what the *agent* said, and on what basis (`D42`)."""

    __tablename__ = "identity_attestations"

    #: A surrogate key rather than a natural one, because the same agent may legitimately
    #: attest the same outcome twice on one call — a `confirmed → confirmed` correction
    #: with a different challenge is exactly the amendment `D61` exists to allow.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    call_session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    at: Mapped[datetime] = mapped_column(Utc, nullable=False)

    #: Which challenge was asked — **never the answer** (`D43`). Storing "we checked the
    #: date of birth" is the evidence; storing the date of birth would be a second copy of
    #: the secret, held for no reason.
    challenge: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Required when the challenge is `other` (`D57`). An `other` with nothing written in
    #: it is the unfalsifiable audit row the whole control exists to prevent.
    challenge_note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    rejected_customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: Both required for a third party (`D57`): the log has to say *who* was on the phone,
    #: not merely that somebody who was not the policyholder called.
    caller_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    relationship: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        # Ordered replay of one call's identity history, which is how it is always read:
        # the *sequence* is the record, not the latest row.
        Index("ix_identity_attestations_call_id", "call_session_id", "id"),
    )


class KeypadCaptureRow(Base):
    """Digits the caller keyed, and what was made of them (`D44`)."""

    __tablename__ = "keypad_captures"

    capture_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    call_session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.call_session_id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(Utc, nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(Utc, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)

    #: What the agent said this was, after the fact and only if they said anything.
    labelled_as: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: **Always stored.** `••••••4512` is what every non-agent surface gets (`D58`).
    masked: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    digit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: **Conditionally stored, and this is the inverted default in `D44`.** Because capture
    #: is untyped we do not know what the digits are, so they are treated as potentially
    #: sensitive until something identifies them: a lookup that matched, or the agent
    #: labelling them. Named → stored in the clear (a policy number is not a secret and is
    #: useful in the record). Unnamed → only the mask survives.
    #:
    #: The visible consequence, which is correct rather than a limitation: a process
    #: restart mid-capture gives the agent back the *fact* that a capture happened and its
    #: length, not the unidentified digits. `D44` asks for short retention and one-click
    #: discard; a durable copy of an unknown number would be the opposite of that.
    digits: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Every lookup run against these digits, with its outcome and which rung matched
    #: (`D66`). Evidence, never an action — nothing here ever moved the assurance level.
    lookups: Mapped[list[dict[str, Any]]] = mapped_column(Json, nullable=False, default=list)

    __table_args__ = (Index("ix_keypad_captures_call", "call_session_id", "started_at"),)


__all__ = ["AttestationRow", "KeypadCaptureRow"]
