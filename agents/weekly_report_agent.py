from agent_runtime.result import ToolResult
from agent_runtime.session import AgentSession
from agents.base_agent import BaseAgent


class WeeklyReportAgent(BaseAgent):
    """周报生成占位：后续汇总项目状态并写入周报。"""
    agent_name = "WeeklyReportAgent"
    input_tables = ['Projects', 'Tasks', 'Milestones', 'Risks', 'FollowUps']
    output_tables = ['WeeklyReports', 'AgentRuns']

    async def _act(self, session: AgentSession) -> ToolResult:
        session.output_tables.extend(self.output_tables)
        return ToolResult(tool_name="WeeklyReportAgent.placeholder_action", success=True, inputs_summary=",".join(self.input_tables), outputs_summary="周报生成占位：后续汇总项目状态并写入周报。")
