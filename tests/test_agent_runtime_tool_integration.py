from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agent_runtime.agent_io import AbnormalSignal, ProjectRecordSnapshot, ProjectStateOutput
from agent_runtime.base_refs import EvidenceRef
from agent_runtime.context import AgentContext
from agent_runtime.config import RuntimeConfig
from agent_runtime.enums import (
    AgentName,
    AgentStepType,
    BaseTableName,
    EventType,
    EvidenceSourceType,
    Priority,
    ProjectStatus,
    RiskLevel,
    TriggerType,
)
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent
from agent_runtime.loaders import load_agent_config
from agent_runtime.mvp.handlers import RiskAnalysisHandler
from agent_runtime.mvp.trace_sink import LocalJsonlTraceSink
from agent_runtime.quality_gate import QualityGateRequest, QualityGateResult
from agent_runtime.runtime import AgentRuntime
from agent_runtime.session import AgentSession

from tool_integration.executor import ToolIntegrationExecutor


class _PassGate:
    async def verify(self, request: QualityGateRequest) -> QualityGateResult:
        return QualityGateResult.passed_result()


class _NoopTrace:
    async def on_session_start(self, session: AgentSession) -> None:
        return None

    async def on_session_end(self, session: AgentSession) -> None:
        return None

    async def on_error(self, session: AgentSession, error: Exception) -> None:
        return None


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


class _RecordingTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, payload: dict[str, Any], session: AgentSession) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        if tool_name == "memory_search":
            return {
                "ok": True,
                "tool": tool_name,
                "result": {
                    "results": [
                        {
                            "memory_id": "mem-1",
                            "content": "历史阻塞任务需要先追问负责人。",
                        }
                    ]
                },
            }
        return {"ok": True, "tool": tool_name, "result": {}}


class _LoopOutput(BaseModel):
    summary: str


class _LoopHandler:
    async def observe(self, context: AgentContext, payload: object) -> dict[str, Any]:
        return {"observed": True, "memory_items": len([item for item in context.items if item.kind == "memory"])}

    async def think(self, context: AgentContext, observation: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "act", "observation": observation}

    async def act(self, context: AgentContext, thought: dict[str, Any], payload: object) -> _LoopOutput:
        context.add_scratchpad("act_note", thought)
        return _LoopOutput(summary="loop_ok")


class _LoopReg:
    def __init__(self, cfg: RuntimeConfig, handler: _LoopHandler) -> None:
        self._cfg = cfg
        self._handler = handler

    def get_config(self, agent_name: str) -> object:
        return self._cfg.agents[AgentName(agent_name)]

    def get_handler(self, agent_name: str) -> object:
        return self._handler


def test_runtime_executes_five_phase_loop_with_context_and_memory_tools() -> None:
    async def _run() -> None:
        root = Path(__file__).resolve().parents[1]
        cfg = load_agent_config(root / "agents" / "coordinator" / "agent.yaml")
        runtime_config = RuntimeConfig(agents={cfg.agent_name: cfg})
        tools = _RecordingTools()
        runtime = AgentRuntime(
            runtime_config,
            _LoopReg(runtime_config, _LoopHandler()),
            tools,
            _PassGate(),
            _NoopTrace(),
        )
        event = AgentTriggerEvent(
            event_id="event-loop",
            event_type=EventType.RUN_FULL_DEMO_CHAIN,
            trigger_type=TriggerType.MANUAL,
            project_id="enterprise_rag",
        )
        req = AgentCallRequest(
            agent_name=AgentName.COORDINATOR,
            event=event,
            reason="loop_test",
            input_payload={"task": "scan"},
        )

        session = await runtime.run_agent(req)

        assert session.status.value == "success"
        assert [step.step_type for step in session.steps] == [
            AgentStepType.OBSERVE,
            AgentStepType.THINK,
            AgentStepType.ACT,
            AgentStepType.VERIFY,
            AgentStepType.LOG,
        ]
        assert {name for name, _ in tools.calls} >= {"memory_search", "memory_write", "process_log"}
        assert session.final_summary == "loop_ok"
        assert any(item.key == "coordinator_output" for item in session.memory)

    asyncio.run(_run())


class _MultiLoopHandler:
    async def observe(self, context: AgentContext, payload: object) -> dict[str, Any]:
        assert context.loop_state is not None
        return {"iteration": context.loop_state.iteration, "payload": payload}

    async def think(self, context: AgentContext, observation: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "continue", "iteration": observation["iteration"]}

    async def act(self, context: AgentContext, thought: dict[str, Any], payload: object) -> _LoopOutput:
        assert context.loop_state is not None
        context.loop_state.data["last_iteration"] = thought["iteration"]
        return _LoopOutput(summary=f"iteration_{thought['iteration']}")

    async def should_continue(
        self,
        context: AgentContext,
        state: object,
        observation: object,
        thought: object,
        output: object,
        verify_result: object,
    ) -> bool:
        assert context.loop_state is not None
        return context.loop_state.iteration < 2

    async def finalize(self, context: AgentContext, state: object, output: object) -> _LoopOutput:
        assert context.loop_state is not None
        return _LoopOutput(summary=f"multi_loop_done:{len(context.loop_state.action_results)}")


def test_runtime_can_repeat_observe_think_act_verify_until_agent_stops() -> None:
    async def _run() -> None:
        root = Path(__file__).resolve().parents[1]
        cfg = load_agent_config(root / "agents" / "coordinator" / "agent.yaml")
        runtime_config = RuntimeConfig(agents={cfg.agent_name: cfg})
        tools = _RecordingTools()
        runtime = AgentRuntime(
            runtime_config,
            _LoopReg(runtime_config, _MultiLoopHandler()),  # type: ignore[arg-type]
            tools,
            _PassGate(),
            _NoopTrace(),
        )
        event = AgentTriggerEvent(
            event_id="event-multi-loop",
            event_type=EventType.RUN_FULL_DEMO_CHAIN,
            trigger_type=TriggerType.MANUAL,
            project_id="enterprise_rag",
        )
        req = AgentCallRequest(
            agent_name=AgentName.COORDINATOR,
            event=event,
            reason="multi_loop_test",
            input_payload={"task": "scan"},
        )

        session = await runtime.run_agent(req)

        assert session.status.value == "success"
        step_types = [step.step_type for step in session.steps]
        assert step_types.count(AgentStepType.OBSERVE) == 3
        assert step_types.count(AgentStepType.THINK) == 3
        assert step_types.count(AgentStepType.ACT) == 3
        assert step_types.count(AgentStepType.VERIFY) == 3
        assert step_types[-1] == AgentStepType.LOG
        assert session.final_summary == "multi_loop_done:3"

    asyncio.run(_run())


class _MvpRecordingTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], AgentStepType | None]] = []

    async def call_tool(self, tool_name: str, payload: dict[str, Any], session: AgentSession) -> dict[str, Any]:
        step_type = session.steps[-1].step_type if session.steps else None
        self.calls.append((tool_name, payload, step_type))
        if tool_name == "memory_search":
            return {"ok": True, "tool": tool_name, "result": {"results": []}}
        if tool_name in {"trace_tool", "memory_write", "process_log"}:
            return {"ok": True, "tool": tool_name, "result": {}}
        if tool_name == "feishu_bitable_search_records":
            return {"ok": True, "tool": tool_name, "result": {"items": []}}
        if tool_name == "llm_chat_json":
            raw_payload = "{}"
            messages = payload.get("messages")
            if isinstance(messages, list) and messages:
                raw_payload = str(messages[-1].get("content") or "{}")
            try:
                llm_payload = json.loads(raw_payload)
            except Exception:
                llm_payload = {}
            if isinstance(llm_payload, dict) and llm_payload.get("rule_candidates"):
                candidates = llm_payload["rule_candidates"]
                key = candidates[0]["idempotency_key"]
                data = {
                    "risk_candidates": [
                        {
                            "idempotency_key": key,
                            "risk_title": "LLM refined delay risk",
                            "risk_level": "高",
                            "trigger_reason": "LLM 基于任务截止时间证据判断该任务已延期。",
                            "suggested_actions": ["请负责人确认剩余工作量", "同步新的完成日期"],
                            "need_followup": True,
                        }
                    ]
                }
            else:
                data = {
                    "decision": "invoke_handler",
                    "reason": "LLM 决定执行风险识别 handler",
                    "focus_items": ["延期任务"],
                    "expected_output": "风险候选",
                    "confidence": 0.9,
                }
            return {
                "ok": True,
                "tool": tool_name,
                "result": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "content": json.dumps(data, ensure_ascii=False),
                    "json": data,
                },
            }
        return {"ok": True, "tool": tool_name, "result": {}}


class _MvpReg:
    def __init__(self, cfg: RuntimeConfig, handler: object) -> None:
        self._cfg = cfg
        self._handler = handler

    def get_config(self, agent_name: str) -> object:
        return self._cfg.agents[AgentName(agent_name)]

    def get_handler(self, agent_name: str) -> object:
        return self._handler


def test_mvp_business_agent_calls_llm_in_think_and_refine_steps() -> None:
    async def _run() -> None:
        root = Path(__file__).resolve().parents[1]
        cfg = load_agent_config(root / "agents" / "risk_analysis" / "agent.yaml")
        runtime_config = RuntimeConfig(agents={cfg.agent_name: cfg})
        tools = _MvpRecordingTools()
        handler = RiskAnalysisHandler(tools, root)
        runtime = AgentRuntime(
            runtime_config,
            _MvpReg(runtime_config, handler),
            tools,
            _PassGate(),
            _NoopTrace(),
        )
        project_state = ProjectStateOutput(
            run_id="secretary-run",
            project_id="enterprise_rag",
            agent_name=AgentName.PROJECT_SECRETARY,
            summary="发现 1 个延期任务",
            project=ProjectRecordSnapshot(
                project_record_id="rec_project",
                project_name="enterprise_rag",
                status=ProjectStatus.IN_PROGRESS,
                priority=Priority.P1,
            ),
            abnormal_signals=[
                AbnormalSignal(
                    signal_type="task_overdue",
                    table_name=BaseTableName.TASKS,
                    record_id="rec_task_1",
                    severity="high",
                    description="任务已超过截止时间 3 天",
                    evidence_refs=[
                        EvidenceRef(
                            evidence_id="ev1",
                            source_type=EvidenceSourceType.BASE_RECORD,
                            summary="截止时间已过",
                            table_name=BaseTableName.TASKS,
                            record_id="rec_task_1",
                            field_name="截止时间",
                        )
                    ],
                )
            ],
        )
        event = AgentTriggerEvent(
            event_id="event-risk-llm-loop",
            event_type=EventType.ANALYZE_RISK,
            trigger_type=TriggerType.MANUAL,
            project_id="enterprise_rag",
        )
        req = AgentCallRequest(
            agent_name=AgentName.RISK_ANALYSIS,
            event=event,
            reason="llm_loop_test",
            input_payload={
                "run_id": "secretary-run",
                "project_id": "enterprise_rag",
                "project_state": project_state.model_dump(mode="json"),
            },
        )

        session = await runtime.run_agent(req)

        assert session.status.value == "success"
        think_llm_calls = [
            call
            for step in session.steps
            if step.step_type == AgentStepType.THINK
            for call in step.llm_calls
        ]
        act_llm_calls = [
            call
            for step in session.steps
            if step.step_type == AgentStepType.ACT
            for call in step.llm_calls
        ]
        assert [call.prompt_summary for call in think_llm_calls] == ["risk_analysis_think"]
        assert [call.prompt_summary for call in act_llm_calls] == ["risk_analysis_refine_candidates"]
        output = next(item for item in session.memory if item.key == "risk_analysis_output").value
        first_risk = output["risk_candidates"][0]
        assert first_risk["risk_title"] == "LLM refined delay risk"
        assert first_risk["risk_level"] == RiskLevel.HIGH.value
        assert any(
            tool_name == "llm_chat_json" and step_type == AgentStepType.THINK
            for tool_name, _, step_type in tools.calls
        )

    asyncio.run(_run())
