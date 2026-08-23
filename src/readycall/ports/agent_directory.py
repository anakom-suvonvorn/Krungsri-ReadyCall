"""AgentDirectory port — who works here, and what they can do.

Separate from `CoreDataProvider` on purpose. The bank's customer data is **read-only**
(`D5`); the agent roster is *also* somebody else's system (an HR or workforce-management
tool), but it is a different somebody else, with a different shape and a different swap on
hackathon day. Merging them would mean one adapter that has to be rewritten if either
source changes.

Presence — who is available *right now* — is deliberately **not** here. That is our own
live state, it changes every few seconds, and it belongs in our store. This port answers
the slow-moving question: who exists, what skills do they hold, which languages do they
speak.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from readycall.domain.models import Agent


@runtime_checkable
class AgentDirectory(Protocol):
    @property
    def name(self) -> str:
        """Adapter name, recorded on matching decisions for provenance."""
        ...

    async def get_agent(self, agent_id: str) -> Agent | None: ...

    async def list_agents(self, *, active_only: bool = True) -> list[Agent]:
        """The whole roster. Small enough to hold in memory; a contact centre is not a
        social network."""
        ...

    async def agents_with_skill(self, skill_code: str) -> list[Agent]:
        """Everyone who holds a skill, at any proficiency.

        Used by the startup check that no skill is held by fewer than two people (`D22`) —
        a single-point-of-failure skill means one person's lunch break closes a queue.
        """
        ...
