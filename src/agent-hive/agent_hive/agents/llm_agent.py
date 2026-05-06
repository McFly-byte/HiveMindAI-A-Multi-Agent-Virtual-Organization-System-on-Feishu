from __future__ import annotations

import json
from typing import Any

from agent_hive.agents.base import BaseAgent
from agent_hive.context.manager import AgentContext
from agent_hive.observability.logging import get_logger
from agent_hive.schemas.agent import AgentLoopDecision, AgentLoopToolCall, AgentOutput
from agent_hive.schemas.tool import ToolIntent, ToolResult


logger = get_logger("agents.llm_agent")


class LLMAgent(BaseAgent):
    """Generic business agent with an Observe -> Think -> Act -> Verify -> Log loop.

    It may use memory directly through ``MemoryManager`` but never receives the
    low-level Feishu executor. Feishu work must be expressed as ``ToolIntent``.
    """

    async def run(self, context: AgentContext, payload: dict[str, Any]) -> AgentOutput:
        logger.info(
            "agent loop started run_id=%s agent_id=%s event_type=%s",
            context.session.run_id,
            self.config.agent_id,
            context.event.event_type,
        )
        observations = await self.observe(context, payload)
        all_intents: list[ToolIntent] = []
        all_tool_results: list[ToolResult] = []
        final_payload: dict[str, Any] = {}
        summary = ""
        iterations = 0

        for iteration in range(self.config.runtime_limits.max_steps):
            iterations = iteration + 1
            decision = await self.think(context, payload, observations, all_tool_results, iteration)
            action_result = await self.act(context, decision)
            all_intents.extend(action_result.tool_intents)
            all_tool_results.extend(action_result.tool_results)
            await self.verify(context, decision, action_result)
            logger.info(
                "agent loop iteration run_id=%s agent_id=%s iteration=%s decision=%s memory_results=%s delegated_intents=%s",
                context.session.run_id,
                self.config.agent_id,
                iterations,
                decision.decision,
                len(action_result.tool_results),
                len(action_result.tool_intents),
            )

            if decision.final_payload:
                final_payload.update(decision.final_payload)
            if decision.summary:
                summary = decision.summary
            if decision.decision in {"finish", "blocked"}:
                if decision.decision == "blocked":
                    summary = decision.blocked_reason or decision.summary or "agent loop blocked"
                break
            observations = {
                "previous_observation": observations,
                "last_tool_results": [item.model_dump(mode="json") for item in action_result.tool_results],
                "delegated_tool_intents": [item.model_dump(mode="json") for item in action_result.tool_intents],
            }

        summary = summary or str(payload.get("summary") or f"{self.config.agent_id} completed")
        await self.log(context, summary, all_tool_results, all_intents, iterations)
        logger.info(
            "agent loop finished run_id=%s agent_id=%s iterations=%s tool_results=%s delegated_intents=%s",
            context.session.run_id,
            self.config.agent_id,
            iterations,
            len(all_tool_results),
            len(all_intents),
        )
        return AgentOutput(
            agent_id=self.config.agent_id,
            run_id=context.session.run_id,
            status="success",
            summary=summary,
            payload=final_payload or payload,
            tool_intents=all_intents,
            tool_results=all_tool_results,
            loop_iterations=iterations,
        )

    async def observe(self, context: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
        step = context.session.add_step("observe", "llm_agent.observe", _summarize(payload))
        observation = {
            "agent_id": self.config.agent_id,
            "project_id": context.session.project_id,
            "payload": payload,
            "context": context.snapshot(),
        }
        step.finish(_summarize(observation))
        context.add_item(kind="observation", key=f"observe_{len(context.session.steps)}", content=observation, priority=4)
        return observation

    async def think(
        self,
        context: AgentContext,
        payload: dict[str, Any],
        observation: dict[str, Any],
        tool_results: list[ToolResult],
        iteration: int,
    ) -> AgentLoopDecision:
        step = context.session.add_step("think", f"llm_agent.think.{iteration}", _summarize(observation))
        raw_decision = _decision_from_payload(payload, iteration)
        if raw_decision is None and self.llm_provider and self.config.model and payload.get("use_llm"):
            raw_decision = await self.llm_provider.generate_json(
                model_config=self.config.model,
                messages=[
                    {"role": "system", "content": self._loop_system_prompt(context)},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "payload": payload,
                                "observation": observation,
                                "tool_results": [item.model_dump(mode="json") for item in tool_results],
                                "iteration": iteration,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
            )
        if raw_decision is None:
            raw_decision = _fallback_decision(payload, default_requester=self.config.agent_id)

        decision = _coerce_decision(raw_decision, default_requester=self.config.agent_id)
        logger.debug(
            "think result run_id=%s agent_id=%s iteration=%s decision=%s tool_call_count=%s",
            context.session.run_id,
            self.config.agent_id,
            iteration,
            decision.decision,
            len(decision.tool_calls),
        )
        step.finish(decision.summary or decision.thought, {"decision": decision.decision, "tool_call_count": len(decision.tool_calls)})
        context.add_item(kind="thought", key=f"think_{iteration}", content=decision.model_dump(mode="json"), priority=4)
        return decision

    async def act(self, context: AgentContext, decision: AgentLoopDecision) -> AgentOutput:
        step = context.session.add_step("act", "llm_agent.act", _summarize(decision.model_dump(mode="json")))
        tool_intents: list[ToolIntent] = []
        tool_results: list[ToolResult] = []
        for tool_call in decision.tool_calls:
            if tool_call.call_type == "memory":
                logger.info(
                    "executing direct memory tool run_id=%s agent_id=%s tool=%s",
                    context.session.run_id,
                    self.config.agent_id,
                    tool_call.tool_name,
                )
                result = await self._execute_memory_tool(context, tool_call)
                tool_results.append(result)
                context.add_item(
                    kind="tool_result",
                    key=tool_call.output_key or tool_call.tool_name or f"memory_{len(tool_results)}",
                    content=result.model_dump(mode="json"),
                    priority=5,
                )
            elif tool_call.call_type == "feishu_intent":
                intent = _tool_call_to_intent(tool_call, default_requester=self.config.agent_id)
                logger.info(
                    "created delegated Feishu intent run_id=%s agent_id=%s intent_id=%s domain=%s action=%s",
                    context.session.run_id,
                    self.config.agent_id,
                    intent.intent_id,
                    intent.domain,
                    intent.action,
                )
                tool_intents.append(intent)
                context.add_item(
                    kind="delegated_tool_intent",
                    key=intent.intent_id,
                    content=intent.model_dump(mode="json"),
                    priority=5,
                )
            else:
                tool_results.append(
                    ToolResult(ok=False, summary="unsupported tool call", error=f"unsupported call_type: {tool_call.call_type}")
                )
        step.finish(
            decision.summary or f"executed {len(decision.tool_calls)} tool calls",
            {"tool_result_count": len(tool_results), "tool_intent_count": len(tool_intents)},
        )
        return AgentOutput(
            agent_id=self.config.agent_id,
            run_id=context.session.run_id,
            summary=decision.summary,
            payload=decision.final_payload,
            tool_intents=tool_intents,
            tool_results=tool_results,
        )

    async def verify(self, context: AgentContext, decision: AgentLoopDecision, output: AgentOutput) -> None:
        step = context.session.add_step("verify", "llm_agent.verify", decision.decision)
        failed = [item for item in output.tool_results if not item.ok]
        if failed:
            step.finish("tool result failed", {"failed_count": len(failed)})
            return
        step.finish("passed", {"decision": decision.decision})

    async def log(
        self,
        context: AgentContext,
        summary: str,
        tool_results: list[ToolResult],
        tool_intents: list[ToolIntent],
        iterations: int,
    ) -> None:
        step = context.session.add_step("log", "llm_agent.log", summary)
        context.add_item(
            kind="agent_loop_log",
            key=f"log_{context.session.run_id}",
            content={
                "summary": summary,
                "iterations": iterations,
                "tool_result_count": len(tool_results),
                "delegated_tool_intent_count": len(tool_intents),
            },
            priority=3,
        )
        step.finish(summary, {"iterations": iterations})

    async def _execute_memory_tool(self, context: AgentContext, tool_call: AgentLoopToolCall) -> ToolResult:
        if self.memory_manager is None:
            return ToolResult(ok=False, summary="memory unavailable", error="memory manager is not configured")
        tool_name = (tool_call.tool_name or "").replace("_", ".")
        args = dict(tool_call.arguments)
        args.setdefault("agent_id", self.config.agent_id)
        args.setdefault("project_id", context.session.project_id)
        args.setdefault("run_id", context.session.run_id)
        try:
            if tool_name == "memory.search":
                data = {
                    "results": await self.memory_manager.search(
                        query=str(args.get("query") or ""),
                        agent_id=args["agent_id"],
                        project_id=args.get("project_id"),
                        run_id=args.get("run_id"),
                        top_k=int(args.get("top_k") or self.config.memory.max_search_results),
                        scopes=args.get("scopes") or self.config.memory.read_scopes,
                        memory_type=args.get("memory_type") or "all",
                    )
                }
            elif tool_name == "memory.write":
                data = await self.memory_manager.write(
                    content=str(args.get("content") or ""),
                    agent_id=args["agent_id"],
                    memory_type=str(args.get("memory_type") or "episodic"),
                    project_id=args.get("project_id"),
                    run_id=args.get("run_id"),
                    tags=args.get("tags") if isinstance(args.get("tags"), list) else ["agent_loop"],
                    metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
                )
            elif tool_name == "memory.reflect":
                data = await self.memory_manager.reflect(
                    topic=str(args.get("topic") or ""),
                    agent_id=args["agent_id"],
                    project_id=args.get("project_id"),
                    lookback=int(args.get("lookback") or 20),
                )
            else:
                return ToolResult(ok=False, summary="memory tool denied", error=f"unsupported direct memory tool: {tool_call.tool_name}")
            return ToolResult(ok=True, summary=tool_call.reason or tool_name, data=data)
        except Exception as exc:
            return ToolResult(ok=False, summary=tool_call.reason or tool_name, error=str(exc))

    def _loop_system_prompt(self, context: AgentContext) -> str:
        return (
            f"{context.render(max_chars=12000)}\n\n"
            "You are running inside an agent loop. Return one JSON object only.\n"
            "Schema:\n"
            "{\n"
            '  "decision": "continue|finish|blocked",\n'
            '  "thought": "short reasoning",\n'
            '  "tool_calls": [\n'
            '    {"call_type":"memory","tool_name":"memory.search|memory.write|memory.reflect","arguments":{}},\n'
            '    {"call_type":"feishu_intent","intent":{"domain":"feishu.bitable","action":"add_field","target":{},"arguments":{},"constraints":{}}}\n'
            "  ],\n"
            '  "summary": "current or final summary",\n'
            '  "final_payload": {}\n'
            "}\n"
            "Business agents may call memory directly. Business agents must never call Feishu tools directly; "
            "they must emit feishu_intent instead."
        )


def _parse_tool_intents(payload: dict[str, Any], *, default_requester: str) -> list[ToolIntent]:
    raw = payload.get("tool_intents") or payload.get("feishu_intents") or []
    if isinstance(payload.get("feishu_intent"), dict):
        raw = [payload["feishu_intent"], *raw]
    intents: list[ToolIntent] = []
    if not isinstance(raw, list):
        return intents
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        data.setdefault("requested_by_agent_id", default_requester)
        if data.get("domain") == "bitable":
            data["domain"] = "feishu.bitable"
        intents.append(ToolIntent.model_validate(data))
    return intents


def _decision_from_payload(payload: dict[str, Any], iteration: int) -> dict[str, Any] | None:
    raw = payload.get("loop_decisions")
    if isinstance(raw, list) and iteration < len(raw) and isinstance(raw[iteration], dict):
        return raw[iteration]
    if isinstance(payload.get("loop_decision"), dict) and iteration == 0:
        return payload["loop_decision"]
    return None


def _fallback_decision(payload: dict[str, Any], *, default_requester: str) -> dict[str, Any]:
    tool_calls: list[dict[str, Any]] = []
    if payload.get("memory_write"):
        tool_calls.append(
            {
                "call_type": "memory",
                "tool_name": "memory.write",
                "arguments": {
                    "content": str(payload["memory_write"]),
                    "memory_type": "episodic",
                    "tags": ["business_agent"],
                    "metadata": {"source": "llm_agent_payload"},
                },
                "reason": "payload requested memory_write",
            }
        )
    for intent in _parse_tool_intents(payload, default_requester=default_requester):
        tool_calls.append(
            {
                "call_type": "feishu_intent",
                "intent": intent.model_dump(mode="json"),
                "reason": intent.reason,
            }
        )
    return {
        "decision": "finish",
        "thought": "deterministic fallback decision",
        "tool_calls": tool_calls,
        "summary": str(payload.get("summary") or "agent loop completed"),
        "final_payload": payload,
    }


def _coerce_decision(value: dict[str, Any], *, default_requester: str) -> AgentLoopDecision:
    data = dict(value)
    raw_calls = data.get("tool_calls") or []
    if not raw_calls:
        raw_intents = []
        if isinstance(data.get("feishu_intent"), dict):
            raw_intents.append(data["feishu_intent"])
        if isinstance(data.get("feishu_intents"), list):
            raw_intents.extend(item for item in data["feishu_intents"] if isinstance(item, dict))
        raw_calls = [
            {
                "call_type": "feishu_intent",
                "intent": {**item, "requested_by_agent_id": item.get("requested_by_agent_id") or default_requester},
            }
            for item in raw_intents
        ]
    data["tool_calls"] = [_coerce_tool_call(item, default_requester=default_requester) for item in raw_calls if isinstance(item, dict)]
    data.setdefault("decision", "finish")
    return AgentLoopDecision.model_validate(data)


def _coerce_tool_call(value: dict[str, Any], *, default_requester: str) -> dict[str, Any]:
    data = dict(value)
    call_type = data.get("call_type") or data.get("type")
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    if not call_type:
        call_type = "memory" if tool_name.startswith("memory") else "feishu_intent"
    data["call_type"] = call_type
    if call_type == "feishu_intent":
        if "intent" not in data:
            data["intent"] = {
                "domain": data.get("domain", "feishu.bitable"),
                "action": data.get("action", ""),
                "target": data.get("target", {}),
                "arguments": data.get("arguments", {}),
                "constraints": data.get("constraints", {}),
                "reason": data.get("reason", ""),
            }
        if isinstance(data["intent"], dict):
            data["intent"].setdefault("requested_by_agent_id", default_requester)
    return data


def _tool_call_to_intent(tool_call: AgentLoopToolCall, *, default_requester: str) -> ToolIntent:
    if tool_call.intent is not None:
        intent = tool_call.intent
        if intent.requested_by_agent_id:
            return intent
        return intent.model_copy(update={"requested_by_agent_id": default_requester})
    return ToolIntent(
        domain="feishu.bitable",
        action=tool_call.arguments.get("action", ""),
        target=tool_call.arguments.get("target", {}),
        arguments=tool_call.arguments.get("arguments", {}),
        constraints=tool_call.arguments.get("constraints", {}),
        reason=tool_call.reason,
        requested_by_agent_id=default_requester,
    )


def _summarize(value: Any, limit: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."
