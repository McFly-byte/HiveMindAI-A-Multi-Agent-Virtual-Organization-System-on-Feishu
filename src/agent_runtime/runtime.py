import inspect
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from agent_runtime.config import RuntimeConfig
from agent_runtime.context import AgentContext, ContextBudget
from agent_runtime.enums import AgentRunStatus, AgentStepType, ErrorType
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent
from agent_runtime.interfaces import (
    AgentRegistryProtocol,
    QualityGateProtocol,
    ToolExecutorProtocol,
    TraceSinkProtocol,
)
from agent_runtime.loaders import load_agent_prompt
from agent_runtime.loop import AgentLoopDecision, AgentLoopState
from agent_runtime.session import AgentSession, AgentStepRecord, RuntimeErrorInfo, SessionMemoryItem


class AgentRuntime:
    """Agent runtime with an explicit Observe -> Think -> Act -> Verify -> Log loop."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        agent_registry: AgentRegistryProtocol,
        tool_executor: ToolExecutorProtocol,
        quality_gate: QualityGateProtocol,
        trace_sink: TraceSinkProtocol,
        context_budget: ContextBudget | None = None,
    ) -> None:
        self.runtime_config = runtime_config
        self.agent_registry = agent_registry
        self.tool_executor = tool_executor
        self.quality_gate = quality_gate
        self.trace_sink = trace_sink
        self.context_budget = context_budget or ContextBudget()

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

    def create_context(self, session: AgentSession, input_payload: object | None = None) -> AgentContext:
        agent_config = self.agent_registry.get_config(str(session.agent_name))
        context = AgentContext(
            session=session,
            agent_config=agent_config,
            budget=self.context_budget,
        )
        if input_payload is not None:
            context.add_payload(input_payload)
            context.compact_if_needed(reason="input_payload")
        try:
            prompt = load_agent_prompt(agent_config)
            context.add_item(
                kind="prompt",
                key="agent_prompt",
                content=prompt,
                priority=9,
                pinned=True,
                metadata={
                    "agent_name": str(agent_config.agent_name),
                    "prompt_version": agent_config.prompt.prompt_version,
                    "output_schema": agent_config.prompt.output_schema_name,
                },
            )
        except Exception as exc:
            context.add_scratchpad("agent_prompt_load_failed", {"error": str(exc)})
        return context

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
            payload = request.input_payload
            if payload is None:
                payload = request.input_payload_ref
            context = self.create_context(session, payload)
            output = await self._run_loop(handler, context, payload)
            summary = getattr(output, "summary", None) or getattr(output, "stop_reason", None)
            if isinstance(output, BaseModel):
                session.memory.append(
                    SessionMemoryItem(
                        key=f"{session.agent_name}_output",
                        value=output.model_dump(mode="json"),
                        summary=summary,
                    )
                )
            if summary is not None or isinstance(output, BaseModel):
                context.add_output(output, summary=summary)
                context.compact_if_needed(reason="agent_output")
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

    async def _run_loop(self, handler: Any, context: AgentContext, payload: Any) -> Any:
        state = AgentLoopState(
            goal=_summarize(payload, limit=300) or f"run {context.session.agent_name}",
            max_iterations=max(1, context.session.max_steps),
        )
        context.loop_state = state
        output: Any = None

        while True:
            context.add_scratchpad(
                "agent_loop_iteration",
                {
                    "loop_id": state.loop_id,
                    "iteration": state.iteration,
                    "max_iterations": state.max_iterations,
                    "completed_actions": len(state.action_results),
                },
            )
            observation = await self._run_phase(
                context,
                AgentStepType.OBSERVE,
                "observe",
                handler,
                self._default_observe,
                payload,
            )
            state.record_observation(observation)

            thought = await self._run_phase(
                context,
                AgentStepType.THINK,
                "think",
                handler,
                self._default_think,
                observation,
            )
            state.record_thought(thought)

            output = await self._run_phase(
                context,
                AgentStepType.ACT,
                "act",
                handler,
                self._default_act,
                thought,
                payload,
            )
            state.record_action_result(output)

            verify_result = await self._run_phase(
                context,
                AgentStepType.VERIFY,
                "verify",
                handler,
                self._default_verify,
                output,
            )

            should_continue = await self._should_continue_loop(
                handler,
                context,
                state,
                observation,
                thought,
                output,
                verify_result,
            )
            if not should_continue:
                if not state.finished:
                    state.finish(output, reason=_output_stop_reason(output))
                break

            state.iteration += 1
            if state.iteration >= state.max_iterations:
                state.finish(output, reason=f"max_iterations reached: {state.max_iterations}")
                break

        finalized = await self._finalize_loop(handler, context, state, output)
        await self._run_phase(
            context,
            AgentStepType.LOG,
            "log",
            handler,
            self._default_log,
            finalized,
        )
        return finalized

    async def _run_phase(
        self,
        context: AgentContext,
        step_type: AgentStepType,
        method_name: str,
        handler: Any,
        default_method: Any,
        *args: Any,
    ) -> Any:
        step = AgentStepRecord(
            step_id=f"stp_{uuid4().hex[:12]}",
            step_type=step_type,
            step_index=len(context.session.steps),
            input_summary=_summarize(args[0] if args else None),
        )
        context.session.add_step(step)
        try:
            if step_type == AgentStepType.OBSERVE:
                await self._load_relevant_memory(context, args[0] if args else None)
            method = getattr(handler, method_name, None)
            if callable(method):
                result = method(context, *args)
            else:
                result = default_method(handler, context, *args)
            if inspect.isawaitable(result):
                result = await result
            if step_type == AgentStepType.LOG:
                await self._persist_run_memory(context, args[0] if args else result)
            step.output_summary = _summarize(result)
            step.ended_at = datetime.utcnow()
            context.add_item(
                kind="scratchpad",
                key=f"{step_type.value}_{step.step_index}",
                content=step.output_summary or "",
            )
            context.compact_if_needed(reason=f"phase:{step_type.value}")
            return result
        except Exception as exc:
            err = RuntimeErrorInfo(
                error_type=ErrorType.UNKNOWN,
                message=str(exc),
                detail=exc.__class__.__name__,
                retryable=False,
            )
            step.errors.append(err)
            step.ended_at = datetime.utcnow()
            raise

    async def _default_observe(self, handler: Any, context: AgentContext, payload: Any) -> dict[str, Any]:
        return {
            "project_id": context.session.project_id,
            "agent_name": str(context.session.agent_name),
            "payload": payload,
            "context_tokens": context.estimated_tokens,
        }

    async def _default_think(self, handler: Any, context: AgentContext, observation: Any) -> dict[str, Any]:
        return {
            "decision": "invoke_handler",
            "reason": "handler has no custom think phase; runtime will execute the configured handler in ACT",
            "observation": observation,
        }

    async def _default_act(self, handler: Any, context: AgentContext, thought: Any, payload: Any) -> Any:
        run_with_context = getattr(handler, "run_with_context", None)
        if callable(run_with_context):
            return await run_with_context(context, payload)
        run = getattr(handler, "run", None)
        if callable(run):
            return await run(context.session, payload)
        raise RuntimeError(f"Agent handler {handler!r} has no act/run method")

    async def _default_verify(self, handler: Any, context: AgentContext, output: Any) -> dict[str, Any]:
        if isinstance(output, BaseModel):
            output.model_dump(mode="json")
        return {
            "passed": True,
            "output_type": output.__class__.__name__,
            "summary": getattr(output, "summary", None) or getattr(output, "stop_reason", None),
        }

    async def _should_continue_loop(
        self,
        handler: Any,
        context: AgentContext,
        state: AgentLoopState,
        observation: Any,
        thought: Any,
        output: Any,
        verify_result: Any,
    ) -> bool:
        if state.finished or state.blocked:
            return False
        method = getattr(handler, "should_continue", None)
        if callable(method):
            result = method(context, state, observation, thought, output, verify_result)
            if inspect.isawaitable(result):
                result = await result
            return bool(result) and state.can_continue

        decision = _coerce_loop_decision(thought)
        if decision is not None:
            if decision.decision == "blocked":
                state.block(decision.blocked_reason or decision.reason, output=output)
                return False
            if decision.decision == "finish":
                state.finish(output, reason=decision.finish_reason or decision.reason)
                return False
            return state.can_continue

        return False

    async def _finalize_loop(
        self,
        handler: Any,
        context: AgentContext,
        state: AgentLoopState,
        output: Any,
    ) -> Any:
        method = getattr(handler, "finalize", None)
        if callable(method):
            result = method(context, state, output)
            if inspect.isawaitable(result):
                result = await result
            return result
        return state.final_output if state.final_output is not None else output

    async def _default_log(self, handler: Any, context: AgentContext, output: Any) -> dict[str, Any]:
        return {
            "logged": True,
            "run_id": context.session.run_id,
            "summary": getattr(output, "summary", None) or getattr(output, "stop_reason", None),
        }

    async def _load_relevant_memory(self, context: AgentContext, payload: Any) -> None:
        if not self._tool_allowed(context, "memory_search"):
            return
        query = self._memory_query(context, payload)
        try:
            records = await context.load_relevant_memories(
                tool_executor=self.tool_executor,
                query=query,
                top_k=5,
                memory_type="all",
            )
            context.add_scratchpad("observe_memory_count", {"count": len(records), "query": query})
        except Exception as exc:
            context.add_scratchpad("memory_search_failed", {"error": str(exc), "query": query})

    async def _persist_run_memory(self, context: AgentContext, output: Any) -> None:
        summary = getattr(output, "summary", None) or getattr(output, "stop_reason", None) or _summarize(output)
        if self._tool_allowed(context, "memory_write") and summary:
            try:
                await self.tool_executor.call_tool(
                    "memory_write",
                    {
                        "content": summary,
                        "memory_type": "episodic",
                        "agent_id": str(context.session.agent_name),
                        "project_id": context.session.project_id,
                        "run_id": context.session.run_id,
                        "tags": ["agent_run", str(context.session.agent_name)],
                        "importance": 1.0,
                        "confidence": 1.0,
                        "metadata": {
                            "trace_id": context.session.trace_id,
                            "status": str(context.session.status),
                            "context_tokens": context.estimated_tokens,
                        },
                    },
                    context.session,
                )
            except Exception as exc:
                context.add_scratchpad("memory_write_failed", {"error": str(exc)})
        if self._tool_allowed(context, "process_log"):
            try:
                await self.tool_executor.call_tool(
                    "process_log",
                    {
                        "event_type": "agent_loop_log",
                        "message": summary or "agent loop completed",
                        "project_id": context.session.project_id,
                        "agent_id": str(context.session.agent_name),
                        "run_id": context.session.run_id,
                        "payload": {
                            "trace_id": context.session.trace_id,
                            "step_count": len(context.session.steps),
                            "context_tokens": context.estimated_tokens,
                        },
                    },
                    context.session,
                )
            except Exception as exc:
                context.add_scratchpad("process_log_failed", {"error": str(exc)})

    def _tool_allowed(self, context: AgentContext, tool_name: str) -> bool:
        cfg = context.agent_config
        if cfg is None:
            return False
        if tool_name in cfg.tool_policy.denied_tools:
            return False
        allowed = cfg.tool_policy.allowed_tools
        return not allowed or tool_name in allowed

    def _memory_query(self, context: AgentContext, payload: Any) -> str:
        payload_summary = _summarize(payload, limit=260)
        return (
            f"project_id={context.session.project_id}; "
            f"agent={context.session.agent_name}; "
            f"trigger={context.session.trigger_type}; "
            f"payload={payload_summary}"
        )


def _summarize(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, BaseModel):
        text = value.model_dump_json()
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _coerce_loop_decision(value: Any) -> AgentLoopDecision | None:
    if isinstance(value, AgentLoopDecision):
        return value
    if isinstance(value, dict) and value.get("decision") in {"continue", "finish", "blocked"}:
        return AgentLoopDecision.model_validate(value)
    return None


def _output_stop_reason(output: Any) -> str | None:
    return getattr(output, "stop_reason", None) or getattr(output, "summary", None)
