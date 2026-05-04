from uuid import uuid4

from agent_runtime.config import RuntimeConfig
from agent_runtime.enums import AgentRunStatus, ErrorType
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent
from agent_runtime.interfaces import (
    AgentRegistryProtocol,
    QualityGateProtocol,
    ToolExecutorProtocol,
    TraceSinkProtocol,
)
from agent_runtime.session import AgentSession, RuntimeErrorInfo


class AgentRuntime:
    """Lightweight lifecycle skeleton for agent execution."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        agent_registry: AgentRegistryProtocol,
        tool_executor: ToolExecutorProtocol,
        quality_gate: QualityGateProtocol,
        trace_sink: TraceSinkProtocol,
    ) -> None:
        self.runtime_config = runtime_config
        self.agent_registry = agent_registry
        self.tool_executor = tool_executor
        self.quality_gate = quality_gate
        self.trace_sink = trace_sink

    def create_session(
        self,
        event: AgentTriggerEvent,
        agent_name: str,
        parent_run_id: str | None = None,
    ) -> AgentSession:
        agent_config = self.agent_registry.get_config(agent_name)
        input_record_ids = [record.record_id for record in event.input_records]
        return AgentSession(
            session_id=str(uuid4()),
            run_id=str(uuid4()),
            parent_run_id=parent_run_id,
            trace_id=str(uuid4()),
            project_id=event.project_id,
            agent_name=agent_config.agent_name,
            trigger_type=event.trigger_type,
            trigger_user=event.trigger_user,
            status=AgentRunStatus.CREATED,
            max_steps=agent_config.runtime_limits.max_steps,
            input_record_ids=input_record_ids,
        )

    async def run_agent(self, request: AgentCallRequest) -> AgentSession:
        session = self.create_session(
            event=request.event,
            agent_name=request.agent_name,
            parent_run_id=request.parent_run_id,
        )
        handler = self.agent_registry.get_handler(request.agent_name)
        session.status = AgentRunStatus.RUNNING

        try:
            await self.trace_sink.on_session_start(session)
            output = await handler.run(session, request.input_payload_ref)
            summary = getattr(output, "summary", None)
            session.mark_success(summary=summary)
            return session
        except Exception as exc:
            error = RuntimeErrorInfo(
                error_type=ErrorType.UNKNOWN,
                message=str(exc),
                detail=exc.__class__.__name__,
                retryable=False,
            )
            session.mark_failed(error)
            await self.trace_sink.on_error(session, exc)
            return session
        finally:
            await self.trace_sink.on_session_end(session)
