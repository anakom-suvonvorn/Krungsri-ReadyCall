"""The durable half of a recording: where it is and when it dies (`D110`, `D78`).

Deliberately **not** a write-through projection like the others. Every other store in
`Storage` backs a working set some service keeps in memory, because a matcher tick cannot
afford a query (`D78`). Nothing reads recordings on a hot path — they are read by a
playback the agent asks for and by the retention job — so this store has no in-memory
half to keep in step, and adding one would create the second copy `D76` warns about.

`due_for_deletion` is the whole reason the table has `delete_after` on it rather than a
retention rule computed at purge time: the promise made when the audio was stored is the
promise that is kept, even if the setting changes (`D14`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from readycall.domain.models import AudioRecording


class RecordingStore(Protocol):
    async def save(self, recording: AudioRecording) -> None: ...

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[AudioRecording]: ...

    async def due_for_deletion(self, *, now: datetime, limit: int = 500) -> list[AudioRecording]:
        """Everything whose retention has expired. Oldest first, capped.

        Capped because an erasure run on a full store must be resumable rather than one
        enormous transaction, and oldest-first because the oldest is the one that has
        been held past its promise for longest.
        """
        ...

    async def delete(self, recording_id: str) -> None: ...


class InMemoryRecordingStore:
    """The fake. Same contract suite as the real one (`D3`, `D75`)."""

    def __init__(self) -> None:
        self._rows: dict[str, AudioRecording] = {}

    async def save(self, recording: AudioRecording) -> None:
        self._rows[recording.recording_id] = recording

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[AudioRecording]:
        wanted = set(call_session_ids)
        return [r for r in self._rows.values() if r.call_session_id in wanted]

    async def due_for_deletion(self, *, now: datetime, limit: int = 500) -> list[AudioRecording]:
        due = [
            r for r in self._rows.values() if r.delete_after is not None and r.delete_after <= now
        ]
        due.sort(key=lambda r: r.created_at)
        return due[:limit]

    async def delete(self, recording_id: str) -> None:
        self._rows.pop(recording_id, None)


__all__ = ["InMemoryRecordingStore", "RecordingStore"]
