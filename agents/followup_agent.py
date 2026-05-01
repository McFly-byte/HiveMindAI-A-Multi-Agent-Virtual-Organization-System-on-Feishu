from agent_runtime.result import ToolResult
from agent_runtime.session import AgentSession
from agents.base_agent import BaseAgent


class FollowUpAgent(BaseAgent):
    """追问处理占位：后续生成追问并回收人工回复。"""
    agent_name = "FollowUpAgent"
    input_tables = ['Tasks', 'Risks', 'FollowUps']
    output_tables = ['FollowUps', 'Tasks', 'Risks', 'AgentRuns']

    async def _act(self, session: AgentSession) -> ToolResult:
        session.output_tables.extend(self.output_tables)
        return ToolResult(tool_name="FollowUpAgent.placeholder_action", success=True, inputs_summary=",".join(self.input_tables), outputs_summary="追问处理占位：后续生成追问并回收人工回复。")
