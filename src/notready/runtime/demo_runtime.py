from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from agents.config_loader import load_agent_definitions
from agents.frontdesk_agent import FrontDeskAgent
from agents.feishu_ops_agent import FeishuOpsAgent
from agents.registry import AgentRegistry
from agents.project_plan_agent import ProjectPlanAgent
from agents.risk_agent import RiskAgent
from agents.weekly_report_agent import WeeklyReportAgent
from llm.client import JsonLLMClient, build_llm_client, LLMError
from adapters.feishu_channel_adapter import FeishuChannelAdapter
from runtime.event_bus import EventBus
from runtime.models import Event, now_hms
from stores.dialogue_store import DialogueStore
from stores.event_log import EventLog
from stores.session_store import SessionStore
from stores.todo_store import TodoStore
from tools.executor import ToolExecutor
from tools.feishu_tools import FeishuToolAdapter
from tools.registry import ToolRegistry
from tools.spec import ToolCallRequest


class DemoRuntime:
    def __init__(
        self,
        *,
        ui_event_hook: Callable[[str, dict], None] | None = None,
        state_hook: Callable[[str, dict], None] | None = None,
        agent_config_dir: str | Path | None = None,
        llm_client: JsonLLMClient | None = None,
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
        self.llm_client = llm_client
        self.llm_enabled = False
        self.llm_init_error: str | None = None
        if self.llm_client is not None:
            self.llm_enabled = True
        else:
            try:
                self.llm_client = build_llm_client()
                self.llm_enabled = self.llm_client is not None
            except LLMError as exc:
                self.llm_init_error = str(exc)
                self.llm_client = None
                self.llm_enabled = False
        self.business_agents = self._load_business_agents()

        self.tool_registry = ToolRegistry()
        self.feishu_tools = FeishuToolAdapter()
        self.feishu_tools.register(self.tool_registry)
        self.tool_executor = ToolExecutor(self.tool_registry, log_hook=self._tool_log)

        self.bus = EventBus(on_enqueue=self._on_enqueue, on_processed=self._on_processed)
        self.feishu_adapter = FeishuChannelAdapter()
        self._seen_feishu_message_ids: set[str] = set()
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
        self._last_reply_by_event_id: dict[str, str] = {}
        self._announce_agents()
        loaded = ", ".join(sorted(self.agent_definitions)) or "无"
        llm_status = "LLM 已启用" if self.llm_enabled else "LLM 未启用，使用规则 fallback"
        if self.llm_init_error:
            llm_status += f"（{self.llm_init_error}）"
        feishu_mode = "真实 Feishu OpenAPI 已启用" if self.feishu_tools.real_api_enabled else "Feishu 工具为 mock 模式"
        self._event_system(f"Runtime 已启动：已加载 Agent 配置：{loaded}。FrontDeskAgent / BusinessAgent / TodoService / ToolExecutor 已就绪。{llm_status}。{feishu_mode}。")

    def handle_web_message(self, text: str, *, user_id: str = "local_user") -> str:
        event = Event(
            event_type="channel.message.received",
            source="web_channel",
            target="frontdesk_agent",
            session_id=f"web:{user_id}",
            payload={"channel": "web", "user_id": user_id, "text": text},
        )
        return self.handle_channel_event(event)

    def handle_channel_event(self, event: Event) -> str:
        with self.lock:
            self.bus.publish(event)
            return self._last_reply_by_event_id.get(event.event_id, "")

    def handle_feishu_im_event(self, raw_event: dict) -> str:
        """Parse and dispatch a Feishu/Lark IM event body through Runtime.

        The channel boundary handles parsing, de-duplication and Session mapping.
        Reply delivery is delegated to ToolExecutor -> feishu.im.send_message, which
        can run in mock mode or real Feishu OpenAPI mode based on `.env`.
        """
        event = self.feishu_adapter.handle_im_event(raw_event)
        payload = event.payload or {}
        msg_key = payload.get("message_id") or payload.get("feishu_event_id")

        with self.lock:
            if not payload.get("process", True):
                reason = payload.get("ignore_reason") or "飞书消息被通道适配器忽略。"
                self._event_system(f"FeishuChannelAdapter 忽略消息：{reason}")
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
                "tools": [x.name for x in self.tool_registry.list()],
                "agent_definitions": {k: v.to_public_dict() for k, v in self.agent_definitions.items()},
                "llm": {"enabled": self.llm_enabled, "init_error": self.llm_init_error},
                "feishu": {
                    "real_api_enabled": self.feishu_tools.real_api_enabled,
                    "api_base": self.feishu_tools.config.api_base,
                    "has_app_id": bool(self.feishu_tools.config.app_id),
                    "has_app_secret": bool(self.feishu_tools.config.app_secret),
                    "has_verification_token": bool(self.feishu_tools.config.verification_token),
                    "has_encrypt_key": bool(self.feishu_tools.config.encrypt_key),
                    "bitable_default_configured": bool(self.feishu_tools.config.bitable_app_token and self.feishu_tools.config.bitable_table_id),
                    "bitable_aliases": sorted((self.feishu_tools.config.bitable_table_aliases or {}).keys()),
                },
            }

    def _load_business_agents(self) -> dict[str, Any]:
        registry = AgentRegistry(self.agent_definitions)
        for agent in [RiskAgent(llm_client=self.llm_client), WeeklyReportAgent(), ProjectPlanAgent(), FeishuOpsAgent(llm_client=self.llm_client)]:
            registry.register(agent)
        self.agent_registry = registry
        return registry.as_dict()

    def agent_metas(self) -> list[dict[str, Any]]:
        agents = [{
            "key": "frontdesk_agent",
            "name": "前台 Agent",
            "tag": "入口 Agent",
            "lead": "唯一直接面对用户：维护 Session、选择业务 Agent、追问、汇总和回复。",
            "icon": "前",
            "accent": "#0d9488",
            "chips": ["session", "routing", "reply"],
        }]
        agents.extend(agent.ui_meta() for agent in self.business_agents.values())
        agents.append({
            "key": "todo_service",
            "name": "TodoService",
            "tag": "服务",
            "lead": "只负责待办创建、查询、状态更新和轻量 TaskContext。",
            "icon": "待",
            "accent": "#ca8a04",
            "chips": ["pending", "running", "done"],
        })
        agents.append({
            "key": "tool_executor",
            "name": "ToolExecutor",
            "tag": "工具层",
            "lead": "统一执行工具、记录工具日志、隔离 Feishu API 细节。",
            "icon": "工",
            "accent": "#0891b2",
            "chips": ["feishu tools", "side-effect boundary"],
        })
        agents.append({
            "key": "llm_client",
            "name": "LLMClient",
            "tag": "生成层",
            "lead": "可选接入 OpenAI-compatible / DeepSeek，用于业务 Agent 生成结构化 JSON；失败自动 fallback。",
            "icon": "模",
            "accent": "#7c3aed",
            "chips": ["enabled" if self.llm_enabled else "fallback", "json schema"],
        })
        return agents

    def call_tool(self, tool_name: str, args: dict, *, requested_by: str = "debug_api") -> dict:
        with self.lock:
            return self._tool_call_from_agent(tool_name, args, requested_by, None, None)

    def _tool_call_from_agent(self, tool_name: str, args: dict, requested_by: str, session_id: str | None, dialogue_id: str | None) -> dict:
        if tool_name == "__tool_plan__":
            return self._execute_tool_plan(args, requested_by, session_id, dialogue_id)
        return self._execute_single_tool(tool_name, args, requested_by, session_id, dialogue_id)

    def _execute_single_tool(self, tool_name: str, args: dict, requested_by: str, session_id: str | None, dialogue_id: str | None) -> dict:
        self.bus.publish(Event(
            "tool.call.request",
            source=requested_by,
            target="tool_executor",
            session_id=session_id,
            dialogue_id=dialogue_id,
            payload={"tool_name": tool_name, "args": args},
        ))
        result = self.tool_executor.call(ToolCallRequest(
            tool_name=tool_name,
            args=args,
            requested_by=requested_by,
            session_id=session_id,
            dialogue_id=dialogue_id,
        ))
        self.bus.publish(Event(
            "tool.call.result" if result.ok else "tool.call.error",
            source="tool_executor",
            target=requested_by,
            session_id=session_id,
            dialogue_id=dialogue_id,
            payload={"tool_name": tool_name, "ok": result.ok, "data": result.data, "error": result.error},
        ))
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

    def _resolve_plan_args(self, value, results: list[dict[str, object]]):
        if isinstance(value, dict):
            return {k: self._resolve_plan_args(v, results) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_plan_args(v, results) for v in value]
        if isinstance(value, str) and value.startswith("$steps["):
            resolved = self._lookup_plan_reference(value, results)
            return resolved if resolved is not None else value
        return value

    @staticmethod
    def _lookup_plan_reference(ref: str, results: list[dict[str, object]]):
        import re
        m = re.match(r"^\$steps\[(\d+)\](?:\.(.*))?$", ref)
        if not m:
            return None
        idx = int(m.group(1))
        if idx < 0 or idx >= len(results):
            return None
        cur = results[idx]
        path = m.group(2) or ""
        if not path:
            return cur
        parts = []
        for part in path.split('.'):
            mm = re.match(r"^(\w+)\[(\d+)\]$", part)
            if mm:
                parts.append(mm.group(1))
                parts.append(int(mm.group(2)))
            else:
                parts.append(part)
        for part in parts:
            if isinstance(part, int):
                if not isinstance(cur, list) or part >= len(cur):
                    return None
                cur = cur[part]
            else:
                if not isinstance(cur, dict) or part not in cur:
                    return None
                cur = cur[part]
        return cur

    def _handle_channel_message(self, event: Event) -> None:
        self._agent_action("frontdesk_agent", f"收到用户消息：{event.payload.get('text', '')}")
        reply = self.frontdesk.handle_channel_message(event)
        channel = event.payload.get("channel", "web")
        send_event = Event(
            event_type="channel.message.send",
            source="frontdesk_agent",
            target=channel,
            session_id=event.session_id,
            payload={
                "text": reply,
                "channel": channel,
                "chat_id": event.payload.get("chat_id"),
                "chat_type": event.payload.get("chat_type"),
                "user_id": event.payload.get("user_id"),
                "reply_to_message_id": event.payload.get("message_id"),
            },
        )
        self.bus.publish(send_event)
        if channel == "feishu":
            receive_id = event.payload.get("chat_id") or event.payload.get("user_id") or "unknown_chat"
            self._tool_call_from_agent(
                "feishu.im.send_message",
                {
                    "receive_id": receive_id,
                    "text": reply,
                    "reply_to_message_id": event.payload.get("message_id"),
                    "chat_type": event.payload.get("chat_type"),
                },
                "feishu_channel_adapter",
                event.session_id,
                event.dialogue_id,
            )
        self._last_reply_by_event_id[event.event_id] = reply

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
        if event.event_type == "agent.capability.probe":
            self._agent_action("frontdesk_agent", "广播 capability.probe，询问业务 Agent 谁能处理")
        elif event.event_type == "agent.capability.bid":
            can = event.payload.get("can_handle")
            conf = event.payload.get("confidence")
            self._agent_action(event.source or "business_agent", f"返回 capability.bid：can_handle={can}, confidence={conf}")
        elif event.event_type == "agent.business.request":
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
        self._ui("agent_action", {
            "agent_key": agent_key,
            "message": message,
            "status": "running",
            "idle_after_ms": 1800,
            "time": now_hms(),
        })

    def _ui(self, event_type: str, payload: dict) -> None:
        if self.ui_event_hook:
            self.ui_event_hook(event_type, payload)

    def _state(self, event_type: str, payload: dict) -> None:
        if self.state_hook:
            self.state_hook(event_type, payload)
