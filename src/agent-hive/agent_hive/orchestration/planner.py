from __future__ import annotations

from agent_hive.events.models import HiveEvent
from agent_hive.schemas.orchestration import OrchestrationAction, OrchestrationPlan


class OrchestrationPlanner:
    def plan_for_target(self, event: HiveEvent, target_agent_id: str) -> OrchestrationPlan:
        return OrchestrationPlan(
            root_agent_id=target_agent_id,
            actions=[
                OrchestrationAction(
                    action_type="run_agent",
                    target_agent_id=target_agent_id,
                    payload=event.payload,
                    reason=f"event target is {target_agent_id}",
                )
            ],
        )
