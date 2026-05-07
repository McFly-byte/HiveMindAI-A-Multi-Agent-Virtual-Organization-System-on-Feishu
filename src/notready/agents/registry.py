from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

from agents.base import BusinessAgent
from agents.config_loader import AgentDefinition


class AgentRegistry:
    """Small in-memory registry used by the runtime.

    Agent.yaml declares the agent identity/routing metadata. Python handlers still
    implement the runtime behavior. This keeps the runtime explicit while making the
    registration boundary clear for future dynamic loading.
    """

    def __init__(self, definitions: dict[str, AgentDefinition] | None = None) -> None:
        self.definitions = definitions or {}
        self._agents: "OrderedDict[str, BusinessAgent]" = OrderedDict()

    def register(self, agent: BusinessAgent) -> BusinessAgent:
        definition = self.definitions.get(agent.id)
        if definition:
            agent.apply_definition(definition)
        if agent.id in self._agents:
            raise ValueError(f"agent already registered: {agent.id}")
        self._agents[agent.id] = agent
        return agent

    def get(self, agent_id: str) -> BusinessAgent | None:
        return self._agents.get(agent_id)

    def values(self) -> Iterable[BusinessAgent]:
        return self._agents.values()

    def as_dict(self) -> dict[str, BusinessAgent]:
        return dict(self._agents)

    def ui_meta(self) -> list[dict]:
        return [agent.ui_meta() for agent in self._agents.values()]
