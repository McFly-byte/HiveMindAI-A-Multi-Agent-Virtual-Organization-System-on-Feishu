from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from adapters.feishu_channel_adapter import FeishuChannelAdapter
from agents.config_loader import load_agent_definitions
from agents.channel_ops_agent import ChannelOpsAgent
from agents.feishu_ops_agent import FeishuOpsAgent
from agents.frontdesk_agent import FrontDeskAgent
from agents.project_plan_agent import ProjectPlanAgent
from agents.registry import AgentRegistry
from agents.risk_agent import RiskAgent
from agents.weekly_report_agent import WeeklyReportAgent
from llm.client import JsonLLMClient, build_llm_client
from runtime.event_bus import EventBus
from runtime.models import Event, now_hms
from stores.dialogue_store import DialogueStore
from stores.event_log import EventLog
from stores.session_store import SessionStore
from stores.todo_store import TodoStore
from tools.executor import ToolExecutor
from tools.channel_tools import WebChannelToolAdapter
from tools.feishu_tools import FeishuToolAdapter
from tools.registry import ToolRegistry
from tools.spec import ToolCallRequest


class AppRuntime:
    def __init__(
        self,
        *,
        ui_event_hook: Callable[[str, dict], None] | None = None,
        state_hook: Callable[[str, dict], None] | None = None,
        agent_config_dir: str | Path | None = None,
        llm_client: JsonLLMClient | None = None,
        feishu_tools: FeishuToolAdapter | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.ui_event_hook = ui_event_hook
        self.state_hook = state_hook
        self.event_log = EventLog()
        self.sessions = SessionStore()
        self.dialogues = DialogueStore()
        self.todos = TodoStore()
        self.agent_config_dir = Path(agent_config_dir or Path(__file__).resolve().parent.parent / "agents_config")
        self.agent_definitions = load_agent_definitions(self.agent_config_dir)

        self.llm_client = llm_client or build_llm_client()
        self.tool_registry = ToolRegistry()
        self.web_channel_tools = WebChannelToolAdapter()
        self.web_channel_tools.register(self.tool_registry)
        self.feishu_tools = feishu_tools or FeishuToolAdapter()
        self.feishu_tools.register(self.tool_registry)
        self.tool_executor = ToolExecutor(self.tool_registry, log_hook=self._tool_log)
        self.channel_ops_agent = ChannelOpsAgent(tool_call=self._tool_call_from_agent)
        self.business_agents = self._load_business_agents()

        self.bus = EventBus(on_enqueue=self._on_enqueue, on_processed=self._on_processed)
        self.feishu_adapter = FeishuChannelAdapter()
        self._seen_feishu_message_ids: set[str] = set()
        self._last_reply_by_event_id: dict[str, str] = {}
        self.feishu_tools.bind_inbound_handler(self.handle_feishu_im_event)
        self.frontdesk = FrontDeskAgent(
            business_agents=self.business_agents,
            session_store=self.sessions,
            dialogue_store=self.dialogues,
            todo_store=self.todos,
            emit=self.bus.publish,
            agent_log=self._agent_log,
            todo_changed=self._todo_changed,
            tool_call=self._tool_call_from_agent,
            llm_client=self.llm_client,
        )
        self.bus.subscribe("channel.message.received", self._handle_channel_message)
        self._announce_agents()
        loaded = ", ".join(sorted(self.agent_definitions)) or "无"
        self._event_system(f"Runtime 已启动：已加载 Agent 配置：{loaded}。真实 LLM、真实飞书应用工具、FrontDeskAgent、TodoService、ToolExecutor 已就绪。")

    def handle_web_message(self, text: str, *, user_id: str = "local_user") -> str:
        event = Event(event_type="channel.message.received", source="web_channel", target="frontdesk_agent", session_id=f"web:{user_id}", payload={"channel": "web", "user_id": user_id, "text": text})
        return self.handle_channel_event(event)

    def handle_channel_event(self, event: Event) -> str:
        with self.lock:
            self.bus.publish(event)
            return self._last_reply_by_event_id.get(event.event_id, "")

    def handle_feishu_im_event(self, raw_event: dict) -> str:
        event = self.feishu_adapter.handle_im_event(raw_event)
        payload = event.payload or {}
        msg_key = payload.get("message_id") or payload.get("feishu_event_id")
        with self.lock:
            if not payload.get("process", True):
                self._event_system(f"FeishuChannelAdapter 忽略消息：{payload.get('ignore_reason') or '消息不需要处理'}")
                return ""
            if msg_key:
                key = str(msg_key)
                if key in self._seen_feishu_message_ids:
                    self._event_system(f"FeishuChannelAdapter 去重：已处理过 message/event id={key}。")
                    return ""
                self._seen_feishu_message_ids.add(key)
            self.bus.publish(event)
            return self._last_reply_by_event_id.get(event.event_id, "")

    def run_idle_once(self) -> str:
        with self.lock:
            todo = self.todos.pull_next()
            if not todo:
                self._event_system("Orchestrator 空闲检查：没有 pending 待办。")
                return "当前没有 pending 待办。"
            session_id = str(todo.source.get("session_id") or "system:idle")
            session = self.sessions.get(session_id) or self.sessions.get_or_create(channel="system", user_id="idle")
            self._event_system(f"Orchestrator 空闲检查：拉取待办 {todo.todo_id}，交给 {todo.assigned_agent}。")
            return self.frontdesk.execute_todo(session, todo.todo_id)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "agents": self.agent_metas(),
                "event_stats": self.event_log.stats(),
                "event_logs": list(self.event_log.items),
                "todos": self.todos.to_list(),
                "tools": [tool.name for tool in self.tool_registry.list_app_tools()],
                "agent_definitions": {k: v.to_public_dict() for k, v in self.agent_definitions.items()},
                "llm": {"enabled": True},
                "feishu": {
                    "ready": self.feishu_tools.ready,
                    "api_base": self.feishu_tools.config.api_base,
                    "has_app_id": bool(self.feishu_tools.config.app_id),
                    "has_app_secret": bool(self.feishu_tools.config.app_secret),
                    "has_verification_token": bool(self.feishu_tools.config.verification_token),
                    "has_encrypt_key": bool(self.feishu_tools.config.encrypt_key),
                    "bitable_default_configured": bool(self.feishu_tools.config.bitable_app_token and self.feishu_tools.config.bitable_table_id),
                    "bitable_aliases": sorted((self.feishu_tools.config.bitable_table_aliases or {}).keys()),
                    "adapter_tool_count": len(getattr(self.feishu_tools, "tool_infos", {})),
                    "websocket_env": os.environ.get("FEISHU_ENABLE_IM_WS", ""),
                },
            }

    def _load_business_agents(self) -> dict[str, Any]:
        registry = AgentRegistry(self.agent_definitions)
        for agent in [
            RiskAgent(llm_client=self.llm_client),
            WeeklyReportAgent(),
            ProjectPlanAgent(),
            FeishuOpsAgent(llm_client=self.llm_client, tool_catalog=self.tool_registry.llm_catalog()),
        ]:
            registry.register(agent)
        self.agent_registry = registry
        return registry.as_dict()

    def agent_metas(self) -> list[dict[str, Any]]:
        agents = [{"key": "frontdesk_agent", "name": "前台 Agent", "tag": "入口 Agent", "lead": "唯一直接面对用户：维护 Session、选择业务 Agent、追问、汇总和回复。", "icon": "前", "accent": "#0d9488", "chips": ["session", "routing", "reply"]}]
        agents.extend(agent.ui_meta() for agent in self.business_agents.values())
        agents.append(self.channel_ops_agent.ui_meta())
        agents.append({"key": "todo_service", "name": "TodoService", "tag": "服务", "lead": "负责待办创建、查询、状态更新和轻量 TaskContext。", "icon": "待", "accent": "#ca8a04", "chips": ["pending", "running", "done"]})
        agents.append({"key": "tool_executor", "name": "ToolExecutor", "tag": "应用工具层", "lead": "只执行应用身份工具，记录工具日志，隔离飞书 OpenAPI 细节。", "icon": "工", "accent": "#0891b2", "chips": ["app tools only", "side-effect boundary"]})
        agents.append({"key": "llm_client", "name": "LLMClient", "tag": "生成与规划层", "lead": "真实 OpenAI-compatible / DeepSeek LLM，用于意图解析、业务生成和工具规划。", "icon": "模", "accent": "#7c3aed", "chips": ["required", "json schema"]})
        return agents

    def _tool_call_from_agent(self, tool_name: str, args: dict, requested_by: str, session_id: str | None, dialogue_id: str | None) -> dict:
        if tool_name == "__tool_plan__":
            return self._execute_tool_plan(args, requested_by, session_id, dialogue_id)
        return self._execute_single_tool(tool_name, args, requested_by, session_id, dialogue_id)

    def _execute_single_tool(self, tool_name: str, args: dict, requested_by: str, session_id: str | None, dialogue_id: str | None) -> dict:
        self.bus.publish(Event("tool.call.request", source=requested_by, target="tool_executor", session_id=session_id, dialogue_id=dialogue_id, payload={"tool_name": tool_name, "args": args}))
        result = self.tool_executor.call(ToolCallRequest(tool_name=tool_name, args=args, requested_by=requested_by, session_id=session_id, dialogue_id=dialogue_id))
        self.bus.publish(Event("tool.call.result" if result.ok else "tool.call.error", source="tool_executor", target=requested_by, session_id=session_id, dialogue_id=dialogue_id, payload={"tool_name": tool_name, "ok": result.ok, "data": result.data, "error": result.error}))
        return {"ok": result.ok, "data": result.data, "error": result.error}

    def _execute_tool_plan(self, args: dict, requested_by: str, session_id: str | None, dialogue_id: str | None) -> dict:
        steps = args.get("steps") if isinstance(args.get("steps"), list) else []
        if not steps:
            return {"ok": False, "error": "tool plan 缺少 steps"}
        self.bus.publish(Event("tool.plan.started", source=requested_by, target="tool_executor", session_id=session_id, dialogue_id=dialogue_id, payload={"goal": args.get("goal"), "step_count": len(steps)}))
        results: list[dict[str, object]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                return {"ok": False, "error": f"tool plan 第 {index + 1} 步不是 object", "steps": results}
            tool_name = str(step.get("tool_name") or "").strip()
            step_args = step.get("args") if isinstance(step.get("args"), dict) else {}
            resolved_args = self._resolve_plan_args(step_args, results)
            step_id = str(step.get("id") or f"step_{index + 1}")
            self.bus.publish(Event("tool.plan.step", source=requested_by, target="tool_executor", session_id=session_id, dialogue_id=dialogue_id, payload={"index": index, "id": step_id, "tool_name": tool_name, "args": resolved_args}))
            result = self._execute_single_tool(tool_name, resolved_args, requested_by, session_id, dialogue_id)
            result["id"] = step_id
            result["tool_name"] = tool_name
            result["args"] = resolved_args
            results.append(result)
            if not result.get("ok"):
                self.bus.publish(Event("tool.plan.failed", source="tool_executor", target=requested_by, session_id=session_id, dialogue_id=dialogue_id, payload={"goal": args.get("goal"), "failed_step": step_id, "error": result.get("error")}))
                return {"ok": False, "error": f"tool plan 在步骤 {step_id} 失败：{result.get('error')}", "steps": results}
        data = {"goal": args.get("goal"), "success_message": args.get("success_message"), "steps": results, "final": results[-1].get("data") if results else None}
        self.bus.publish(Event("tool.plan.finished", source="tool_executor", target=requested_by, session_id=session_id, dialogue_id=dialogue_id, payload={"goal": args.get("goal"), "step_count": len(results)}))
        return {"ok": True, "data": data}

    def _resolve_plan_args(self, value: Any, results: list[dict[str, object]]) -> Any:
        if isinstance(value, dict):
            return {k: self._resolve_plan_args(v, results) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_plan_args(v, results) for v in value]
        if isinstance(value, str) and value.startswith("$steps["):
            resolved = self._lookup_plan_reference(value, results)
            return resolved if resolved is not None else value
        return value

    @staticmethod
    def _lookup_plan_reference(ref: str, results: list[dict[str, object]]) -> Any:
        match = re.match(r"^\$steps\[(\d+)\](?:\.(.*))?$", ref)
        if not match:
            return None
        index = int(match.group(1))
        if index < 0 or index >= len(results):
            return None
        current: Any = results[index]
        path = match.group(2) or ""
        for part in path.split(".") if path else []:
            array_match = re.match(r"^(\w+)\[(\d+)\]$", part)
            if array_match:
                key, item_index = array_match.group(1), int(array_match.group(2))
                if not isinstance(current, dict) or key not in current:
                    return None
                current = current[key]
                if not isinstance(current, list) or item_index >= len(current):
                    return None
                current = current[item_index]
            else:
                if not isinstance(current, dict) or part not in current:
                    return None
                current = current[part]
        return current

    def _handle_channel_message(self, event: Event) -> None:
        self._agent_action("frontdesk_agent", f"收到用户消息：{event.payload.get('text', '')}")
        reply = self.frontdesk.handle_channel_message(event)
        channel = str(event.payload.get("channel", "web") or "web")
        self.bus.publish(Event(
            "agent.business.request",
            source="frontdesk_agent",
            target="channel_ops_agent",
            session_id=event.session_id,
            dialogue_id=event.dialogue_id,
            payload={
                "intent": "channel.reply_current",
                "channel": channel,
                "text": reply,
                "chat_id": event.payload.get("chat_id"),
                "user_id": event.payload.get("user_id"),
                "reply_to_message_id": event.payload.get("message_id"),
            },
        ))
        self._agent_action("channel_ops_agent", f"发送 {channel} 回复：{reply[:80]}")
        result = self.channel_ops_agent.send_reply(
            channel=channel,
            text=reply,
            session_id=event.session_id,
            dialogue_id=event.dialogue_id,
            chat_id=event.payload.get("chat_id"),
            user_id=event.payload.get("user_id"),
            reply_to_message_id=event.payload.get("message_id"),
        )
        self.bus.publish(Event(
            "agent.business.response",
            source="channel_ops_agent",
            target="frontdesk_agent",
            session_id=event.session_id,
            dialogue_id=event.dialogue_id,
            payload={"message_type": "tool_result", "ok": result.get("ok"), "result": result.get("data"), "error": result.get("error")},
        ))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        self._last_reply_by_event_id[event.event_id] = str(data.get("text") or reply)

    def _on_enqueue(self, event: Event) -> None:
        item = self.event_log.on_enqueue(event)
        self._ui("event_log", item)
        self._ui("event_stats", self.event_log.stats())
        self._event_to_agent_action(event)

    def _on_processed(self, event: Event) -> None:
        item = self.event_log.on_processed(event)
        self._ui("event_log", item)
        self._ui("event_stats", self.event_log.stats())

    def _event_to_agent_action(self, event: Event) -> None:
        if event.event_type == "agent.business.request":
            self._agent_action(event.target or "business_agent", f"收到 business.request：{event.payload.get('intent')}")
        elif event.event_type == "agent.business.response":
            self._agent_action(event.source or "business_agent", f"返回 business.response：{event.payload.get('message_type')}")
        elif event.event_type == "todo.create.request":
            self._agent_action("todo_service", "收到创建待办请求")
        elif event.event_type in {"todo.created", "todo.updated"}:
            self._agent_action("todo_service", f"待办状态更新：{event.event_type}")

    def _announce_agents(self) -> None:
        self._ui("agents_updated", {"agents": self.agent_metas()})

    def _agent_log(self, message: str) -> None:
        item = {"message": message, "time": now_hms()}
        self._state("agent_log", item)
        self._ui("agent_log", item)

    def _tool_log(self, item: dict) -> None:
        self._state("tool_log", item)
        self._ui("tool_log", item)

    def _event_system(self, message: str) -> None:
        item = self.event_log.system(message)
        self._ui("event_log", item)
        self._ui("event_stats", self.event_log.stats())

    def _todo_changed(self) -> None:
        self._ui("todos_updated", {"todos": self.todos.to_list()})

    def _agent_action(self, agent_key: str, message: str) -> None:
        self._ui("agent_action", {"agent_key": agent_key, "message": message, "status": "running", "idle_after_ms": 1800, "time": now_hms()})

    def _ui(self, event_type: str, payload: dict) -> None:
        if self.ui_event_hook:
            self.ui_event_hook(event_type, payload)

    def _state(self, event_type: str, payload: dict) -> None:
        if self.state_hook:
            self.state_hook(event_type, payload)
