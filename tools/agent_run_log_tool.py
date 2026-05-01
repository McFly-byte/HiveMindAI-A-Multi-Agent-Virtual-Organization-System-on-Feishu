from agent_runtime.result import ToolResult
from agent_runtime.session import AgentSession


class AgentRunLogTool:
    """Log AgentRuns. Real Base write is TODO; scaffold keeps a structured ToolResult."""
    tool_name = "AgentRunLogTool"

    def run(self, session: AgentSession) -> ToolResult:
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary=f"run_id={session.run_id}, agent={session.agent_name}", outputs_summary="TODO: write AgentRuns to Feishu Base; scaffold recorded local trace only.")
