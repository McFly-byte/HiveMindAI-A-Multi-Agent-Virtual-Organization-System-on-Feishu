from __future__ import annotations

from agent_hive.schemas.orchestration import OrchestrationAction


class OrchestrationScheduler:
    def serial(self, actions: list[OrchestrationAction]) -> list[OrchestrationAction]:
        return list(actions)
