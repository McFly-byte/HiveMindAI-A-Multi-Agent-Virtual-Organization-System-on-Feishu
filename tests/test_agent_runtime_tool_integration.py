from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_runtime.config import RuntimeConfig
from agent_runtime.enums import AgentName, EventType, TriggerType
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent
from agent_runtime.loaders import load_agent_config
from agent_runtime.mvp.trace_sink import LocalJsonlTraceSink
from agent_runtime.quality_gate import QualityGateRequest, QualityGateResult
from agent_runtime.runtime import AgentRuntime

from tool_integration.executor import ToolIntegrationExecutor


class _PassGate:
    async def verify(self, request: QualityGateRequest) -> QualityGateResult:
        return QualityGateResult.passed_result()


class _TraceHandler:
    def __init__(self, tools: ToolIntegrationExecutor) -> None:
        self._tools = tools

    async def run(self, session: object, payload: object) -> object:
        await self._tools.call_tool("trace_tool", {"message": "ping"}, session)  # type: ignore[arg-type]

        class _Out:
            summary = "trace_ok"

        return _Out()


class _MiniReg:
    def __init__(self, cfg: RuntimeConfig, tools: ToolIntegrationExecutor) -> None:
        self._cfg = cfg
        self._tools = tools

    def get_config(self, agent_name: str) -> object:
        return self._cfg.agents[AgentName(agent_name)]

    def get_handler(self, agent_name: str) -> object:
        return _TraceHandler(self._tools)


def test_run_agent_records_tool_call(tmp_path: Path) -> None:
    async def _run() -> None:
        root = Path(__file__).resolve().parents[1]
        sec = load_agent_config(root / "agents" / "project_secretary" / "agent.yaml")
        sec = sec.model_copy(
            update={
                "tool_policy": sec.tool_policy.model_copy(
                    update={"allowed_tools": ["trace_tool"], "denied_tools": []},
                )
            }
        )
        cfg = RuntimeConfig(agents={sec.agent_name: sec})
        executor = ToolIntegrationExecutor(cfg, root, tool_dirs=["tool_integrations"])
        trace = LocalJsonlTraceSink(tmp_path / "traces")
        runtime = AgentRuntime(cfg, _MiniReg(cfg, executor), executor, _PassGate(), trace)

        event = AgentTriggerEvent(
            event_id="e1",
            event_type=EventType.CHECK_PROJECT_STATE,
            trigger_type=TriggerType.MANUAL,
            project_id="test_project",
        )
        req = AgentCallRequest(agent_name=AgentName.PROJECT_SECRETARY, event=event, reason="integration_test")
        session = await runtime.run_agent(req)

        assert session.status.value == "success"
        tool_names = [tc.tool_name for step in session.steps for tc in step.tool_calls]
        assert "trace_tool" in tool_names
        await executor.shutdown()

    asyncio.run(_run())
