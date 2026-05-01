from agents.base_agent import BaseAgent
from agent_runtime.result import AgentResult
from schemas.base import RunRequest


async def run_agent(agent: BaseAgent, request: RunRequest) -> AgentResult:
    """Run one Agent through the lightweight runtime."""
    return await agent.run(request)
