"""Where call sessions live.

An internal seam, not a port: this is *our* store, so the Protocol exists to let P0
run without Postgres, not to abstract a vendor. The SQLAlchemy implementation lands in
the next phase behind the same interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from readycall.domain.models import CallSession


@runtime_checkable
class CallSessionRepository(Protocol):
    async def save(self, session: CallSession) -> None: ...

    async def get(self, call_session_id: str) -> CallSession | None: ...

    async def find_by_telephony_id(self, telephony_call_id: str) -> CallSession | None: ...

    async def list_in_states(self, *states: object) -> list[CallSession]: ...


class InMemoryCallSessionRepository:
    """Dict-backed. Tests, the scenario runner, and P0."""

    def __init__(self) -> None:
        self._by_id: dict[str, CallSession] = {}

    async def save(self, session: CallSession) -> None:
        self._by_id[session.call_session_id] = session

    async def get(self, call_session_id: str) -> CallSession | None:
        return self._by_id.get(call_session_id)

    async def find_by_telephony_id(self, telephony_call_id: str) -> CallSession | None:
        for session in self._by_id.values():
            if session.telephony_call_id == telephony_call_id:
                return session
        return None

    async def list_in_states(self, *states: object) -> list[CallSession]:
        wanted = set(states)
        return [s for s in self._by_id.values() if s.state in wanted]

    def all(self) -> list[CallSession]:
        return list(self._by_id.values())
