"""`audio_recordings` — where a call's audio is, and when it must stop existing (`D110`).

The row is the **index**, never the audio and never the key. It says which object holds
this call's recording, which master key was used to wrap that object's own data key, and
the date after which holding it is no longer something the caller agreed to (`D14`).

That split is the point: someone who reads this table learns that a recording exists and
where, and can decrypt none of it — the wrapped data key lives in the object's header and
the master lives in the key ring.

**The row is written after the upload confirms, never before.** A reference to an object
that was never written is a recording that looks retrievable, satisfies an audit, and
plays nothing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from readycall.db.base import Base, Utc


class AudioRecordingRow(Base):
    __tablename__ = "audio_recordings"

    recording_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    call_session_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("call_sessions.call_session_id"), nullable=False
    )
    #: `intake` or `live_call`. A column rather than JSON because a retention policy per
    #: phase is the obvious next ask, and it is what a report groups by.
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `mem://…`, `file://…` or `s3://bucket/key`. Scheme-qualified so the row still
    #: means something after the backend changes (`D110`).
    storage_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(Utc, nullable=False)

    duration_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: sha256 of the **audio**, not of the stored ciphertext — GCM's nonce is fresh on
    #: every write, so a checksum over what is stored could never answer "is this the
    #: same recording".
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=16000)
    audio_format: Mapped[str] = mapped_column(String(24), nullable=False, default="wav_pcm16")

    #: Names the MASTER key (`local:v1`, `kms:arn:…`). Null only for a store that wrote
    #: plaintext, which nothing in `services/` does. Not a secret, and useless alone.
    encryption_key_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The retention promise, made concrete at write time (`D14`). Indexed because the
    #: purge job's only query is "everything due before now".
    delete_after: Mapped[datetime | None] = mapped_column(Utc, nullable=True)

    intake_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (
        Index("ix_audio_recordings_call", "call_session_id"),
        Index("ix_audio_recordings_delete_after", "delete_after"),
    )


class TranscriptTurnRow(Base):
    """`transcript_turns` — one utterance, written as it happens (`D114`).

    **Incremental by design** (`ARCHITECTURE` §6): a dropped call still leaves the
    sentences it produced, which is the case a durable transcript is actually for.

    `is_final` is not decoration. `False` means the endpointer cut the utterance at
    `max_segment_ms` rather than at a pause, so the caller was still talking — a brief
    built from it must not read as a finished thought.

    Indexed on `(call_session_id, seq)` because that is the only query: give me this
    call's transcript, in order. `seq` is what makes a transcript a transcript.
    """

    __tablename__ = "transcript_turns"

    turn_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    call_session_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("call_sessions.call_session_id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    t_start_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    t_end_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    engine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    intake_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (Index("ix_transcript_turns_call_seq", "call_session_id", "seq"),)


__all__ = ["AudioRecordingRow", "TranscriptTurnRow"]
