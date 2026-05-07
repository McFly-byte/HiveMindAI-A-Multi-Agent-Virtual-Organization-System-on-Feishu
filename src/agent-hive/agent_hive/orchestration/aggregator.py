from __future__ import annotations

from agent_hive.schemas.agent import AgentOutput


def aggregate_summaries(outputs: list[AgentOutput]) -> str:
    return "\n".join(output.summary for output in outputs if output.summary)
