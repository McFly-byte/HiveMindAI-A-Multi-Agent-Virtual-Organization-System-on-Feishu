from agent_runtime.result import ToolResult
from agent_runtime.session import AgentSession
from agents.base_agent import BaseAgent


class RiskAnalysisAgent(BaseAgent):
    """风险识别占位：后续执行稳定规则和 LLM 解释。"""
    agent_name = "RiskAnalysisAgent"
    input_tables = ['Projects', 'Tasks', 'Milestones', 'FollowUps', 'Risks']
    output_tables = ['Risks', 'Projects', 'Tasks', 'AgentRuns']

    async def _act(self, session: AgentSession) -> ToolResult:
        session.output_tables.extend(self.output_tables)
        return ToolResult(tool_name="RiskAnalysisAgent.placeholder_action", success=True, inputs_summary=",".join(self.input_tables), outputs_summary="风险识别占位：后续执行稳定规则和 LLM 解释。")
