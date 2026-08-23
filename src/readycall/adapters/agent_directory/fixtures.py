"""Agent roster from a JSON file. Runs with no HR system and no database.

Same shape as `FixtureFileProvider` for the bank's data: read once, map to domain objects
at the boundary, hand nothing raw upstream.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from readycall.domain.enums import CefrLevel, Language
from readycall.domain.models import Agent, AgentLanguage, AgentSkill
from readycall.errors import ConfigError
from readycall.logging import get_logger

log = get_logger(__name__)


class FixtureAgentDirectory:
    name = "fixtures"

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._agents: dict[str, Agent] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            raise ConfigError(f"agent roster not found: {self._path}")
        raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("agents", [])
        for row in rows:
            agent = Agent(
                agent_id=row["agent_id"],
                display_name=row["display_name"],
                team=row.get("team", "general"),
                level=int(row.get("level", 1)),
                languages=tuple(
                    AgentLanguage(language=Language(x["language"]), level=CefrLevel(x["level"]))
                    for x in row.get("languages", [])
                ),
                skills=tuple(
                    AgentSkill(skill_code=x["skill_code"], proficiency=float(x["proficiency"]))
                    for x in row.get("skills", [])
                ),
                max_concurrent=int(row.get("max_concurrent", 1)),
                is_active=bool(row.get("is_active", True)),
            )
            self._agents[agent.agent_id] = agent
        log.info("agent roster loaded", agents=len(self._agents), path=str(self._path))

    async def get_agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    async def list_agents(self, *, active_only: bool = True) -> list[Agent]:
        return [a for a in self._agents.values() if a.is_active or not active_only]

    async def agents_with_skill(self, skill_code: str) -> list[Agent]:
        return [
            a
            for a in self._agents.values()
            if a.is_active and any(s.skill_code == skill_code for s in a.skills)
        ]
