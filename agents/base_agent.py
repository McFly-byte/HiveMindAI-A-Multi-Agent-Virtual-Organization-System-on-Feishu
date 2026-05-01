from abc import ABC
from agent_runtime.result import AgentResult, ToolResult
from agent_runtime.session import AgentSession
from schemas.base import RunRequest
from services.trace_service import TraceService
from tools.agent_run_log_tool import AgentRunLogTool


class BaseAgent(ABC):
    """Base class for MVP Agents using Observe / Think / Act / Verify / Log."""
    agent_name: str = "BaseAgent"
    input_tables: list[str] = []
    output_tables: list[str] = []

    def __init__(self, max_steps: int = 5) -> None:
        self.max_steps = max_steps
        self.trace_service = TraceService()
        self.agent_run_log_tool = AgentRunLogTool()

    async def run(self, request: RunRequest) -> AgentResult:
        """Run a scaffold Agent with explicit lifecycle steps and structured output."""
        session = AgentSession(project_id=request.project_id, agent_name=self.agent_name, trigger_type=request.trigger_type, max_steps=self.max_steps, input_tables=list(self.input_tables), input_record_ids=list(request.input_record_ids))
        session.trace_id = session.run_id
        try:
            await self._trace(session, "observe", "success", "占位读取飞书 Base 上下文，真实实现待接入 BaseQueryTool。")
            session.observations.append("scaffold: no real Base query executed")
            await self._trace(session, "think", "success", "占位规则判断，真实实现待接入规则和 LLM。")
            session.rule_results.append("scaffold: no business decision persisted")
            session.tool_results.append(await self._act(session))
            await self._trace(session, "verify", "success", "占位校验完成，未写入真实业务数据。")
            session.finish("success")
        except Exception as exc:  # noqa: BLE001
            session.errors.append(str(exc))
            session.finish("failed")
            await self._trace(session, "error", "failed", str(exc))
        log_result = self.agent_run_log_tool.run(session)
        session.tool_results.append(log_result)
        await self._trace(session, "log", "success" if log_result.success else "failed", log_result.outputs_summary)
        return AgentResult(run_id=session.run_id, agent_name=self.agent_name, status=session.status, message=f"{self.agent_name} scaffold run completed; real Feishu writes are TODO.", output_record_ids=session.output_record_ids, tool_results=session.tool_results, errors=session.errors)

    async def _act(self, session: AgentSession) -> ToolResult:
        """Return a placeholder ToolResult. Concrete Agents override for richer scaffold notes."""
        return ToolResult(tool_name=f"{self.agent_name}.placeholder_action", success=True, inputs_summary=",".join(session.input_tables), outputs_summary="未执行真实写回；后续通过 Tool Layer 写入 Base。")

    async def _trace(self, session: AgentSession, step: str, status: str, summary: str) -> None:
        self.trace_service.write_step(session=session, step=step, status=status, summary=summary)
