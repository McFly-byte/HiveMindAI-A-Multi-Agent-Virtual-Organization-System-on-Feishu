from typing import Any, Protocol

from agent_runtime.config import AgentConfig
from agent_runtime.context import AgentContext
from agent_runtime.quality_gate import QualityGateRequest, QualityGateResult
from agent_runtime.session import AgentSession


class AgentHandlerProtocol(Protocol):
    async def run(self, session: AgentSession, input_payload: Any) -> Any:
        ...


class AgentContextAwareHandlerProtocol(Protocol):
    async def run_with_context(self, context: AgentContext, input_payload: Any) -> Any:
        ...


class ToolExecutorProtocol(Protocol):
    async def call_tool(
        self, tool_name: str, payload: dict[str, Any], session: AgentSession
    ) -> dict[str, Any]:
        ...


class LLMClientProtocol(Protocol):
    async def generate_json(self, prompt: str, schema_name: str, session: AgentSession) -> dict[str, Any]:
        ...


class TraceSinkProtocol(Protocol):
    async def on_session_start(self, session: AgentSession) -> None:
        ...

    async def on_session_end(self, session: AgentSession) -> None:
        ...

    async def on_error(self, session: AgentSession, error: Exception) -> None:
        ...


class QualityGateProtocol(Protocol):
    async def verify(self, request: QualityGateRequest) -> QualityGateResult:
        ...


class AgentRegistryProtocol(Protocol):
    def get_config(self, agent_name: str) -> AgentConfig:
        ...

    def get_handler(self, agent_name: str) -> AgentHandlerProtocol:
        ...
