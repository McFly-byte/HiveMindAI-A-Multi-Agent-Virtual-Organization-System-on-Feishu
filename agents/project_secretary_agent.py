from agent_runtime.result import ToolResult
from agent_runtime.session import AgentSession
from agents.base_agent import BaseAgent


class ProjectSecretaryAgent(BaseAgent):
    """项目秘书巡检占位：后续标记任务异常并创建追问。"""
    agent_name = "ProjectSecretaryAgent"
    input_tables = ['Projects', 'Tasks', 'Milestones']
    output_tables = ['Tasks', 'FollowUps', 'AgentRuns']

    async def _act(self, session: AgentSession) -> ToolResult:
        session.output_tables.extend(self.output_tables)
        return ToolResult(tool_name="ProjectSecretaryAgent.placeholder_action", success=True, inputs_summary=",".join(self.input_tables), outputs_summary="项目秘书巡检占位：后续标记任务异常并创建追问。")
