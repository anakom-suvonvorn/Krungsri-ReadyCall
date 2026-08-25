"""Where a saved wrap-up lives.

Small, and one property is worth stating: **the absence of a row is meaningful.** `D45`
struck out an auto-save precisely because an unsaved wrap-up is honest data — it records
that this call was never wrapped up, which is true and useful. Nothing here may create a
row on the agent's behalf, and no timer may call `save`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from readycall.domain.models import CallWrapup


class WrapupStore(Protocol):
    async def save(self, wrapup: CallWrapup) -> None: ...

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[CallWrapup]: ...


class InMemoryWrapupStore:
    """The fake, held to the same contract suite as the real one (`D3`)."""

    def __init__(self) -> None:
        self._rows: dict[str, CallWrapup] = {}

    async def save(self, wrapup: CallWrapup) -> None:
        self._rows[wrapup.call_session_id] = wrapup

    async def for_calls(self, call_session_ids: Sequence[str]) -> list[CallWrapup]:
        wanted = set(call_session_ids)
        return [w for w in self._rows.values() if w.call_session_id in wanted]


__all__ = ["InMemoryWrapupStore", "WrapupStore"]
