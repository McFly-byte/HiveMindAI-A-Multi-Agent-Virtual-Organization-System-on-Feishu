from __future__ import annotations

from agent_hive.schemas.tool import ToolResult, ToolStep
from agent_hive.observability.logging import get_logger
from agent_hive.tools.registry import ProviderRegistry


logger = get_logger("tools.executor")


class ToolExecutor:
    """Low-level tool executor.

    This object is only injected into ``tool_agent`` instances. Business agents
    do not receive it.
    """

    def __init__(self, providers: ProviderRegistry) -> None:
        self.providers = providers

    async def execute_step(self, step: ToolStep) -> ToolResult:
        provider = self.providers.get(step.provider)
        try:
            logger.info(
                "tool step started step_id=%s provider=%s tool=%s",
                step.step_id,
                step.provider,
                step.tool_name,
            )
            data = await provider.call(step.tool_name, step.arguments)
            logger.info(
                "tool step finished step_id=%s provider=%s tool=%s",
                step.step_id,
                step.provider,
                step.tool_name,
            )
            return ToolResult(ok=True, data=data, summary=step.purpose, step_results=[data])
        except Exception as exc:
            logger.exception("tool step failed step_id=%s provider=%s tool=%s error=%s", step.step_id, step.provider, step.tool_name, exc)
            return ToolResult(ok=False, error=str(exc), summary=step.purpose)
