from __future__ import annotations

import re
from typing import Any, Callable

from agents.base import infer_project_name, infer_time_range, wants_todo
from llm.client import JsonLLMClient, LLMError
from llm.frontdesk_intent import FrontdeskIntentParser, FrontdeskIntentSuggestion
from runtime.models import (
    BusinessRequest,
    BusinessResponse,
    CapabilityProbe,
    Event,
    SessionState,
    new_id,
)
from stores.dialogue_store import DialogueStore
from stores.session_store import SessionStore
from stores.todo_store import TodoStore


class FrontDeskAgent:
    id = "frontdesk_agent"
    name = "前台 Agent"

    def __init__(
        self,
        *,
        business_agents: dict[str, Any],
        session_store: SessionStore,
        dialogue_store: DialogueStore,
        todo_store: TodoStore,
        emit: Callable[[Event], None],
        agent_log: Callable[[str], None],
        todo_changed: Callable[[], None],
        tool_call: Callable[[str, dict, str, str | None, str | None], dict] | None = None,
        llm_client: JsonLLMClient,
    ) -> None:
        self.business_agents = business_agents
        self.sessions = session_store
        self.dialogues = dialogue_store
        self.todos = todo_store
        self.emit = emit
        self.agent_log = agent_log
        self.todo_changed = todo_changed
        self.tool_call = tool_call
        self.intent_parser = FrontdeskIntentParser(llm_client)

    def handle_channel_message(self, event: Event) -> str:
        payload = event.payload
        text = str(payload.get("text", "")).strip()
        channel = str(payload.get("channel", "web"))
        user_id = str(payload.get("user_id", "local_user"))
        chat_id = payload.get("chat_id")
        session = self.sessions.get_or_create(channel=channel, user_id=user_id, chat_id=chat_id)

        self.agent_log(f"FrontDeskAgent 收到用户消息：{text}")

        if self._is_todo_list(text):
            return self._reply_todo_list()
        if self._is_todo_execute(text):
            return self._execute_todo_from_text(session, text)
        if self._is_cancel_dialogue(text):
            if session.active_dialogue_id:
                self.dialogues.set_status(session.active_dialogue_id, "cancelled")
                self.sessions.set_active_dialogue(session.session_id, None)
            return "已取消当前对话任务。"

        active = self.dialogues.get(session.active_dialogue_id)
        if active and active.status == "waiting_user_input":
            return self._continue_dialogue(session, active.dialogue_id, text)

        return self._start_new_business(session, text)

    def _start_new_business(self, session: SessionState, text: str) -> str:
        return self._try_start_with_llm_intent(session, text)

    def _try_start_with_llm_intent(self, session: SessionState, text: str) -> str:
        try:
            suggestion = self.intent_parser.parse(user_text=text, agent_summaries=self._agent_summaries_for_llm())
        except LLMError as exc:
            self.emit(Event(
                "llm.intent.error",
                source="frontdesk_agent",
                target="llm_client",
                session_id=session.session_id,
                payload={"error": str(exc), "user_text": text},
            ))
            return f"LLM 前台对话解析失败：{exc}"

        self.emit(Event(
            "llm.dialogue.parsed",
            source="llm_client",
            target="frontdesk_agent",
            session_id=session.session_id,
            payload=suggestion.to_payload(),
        ))

        action = suggestion.dialogue_action
        if action == "chat_reply":
            return suggestion.reply_text
        if action == "ask_clarification":
            return suggestion.reply_text
        if action == "list_todos":
            return self._reply_todo_list()
        if action == "execute_todo":
            return self._execute_todo_from_text(session, text)

        if suggestion.selected_agent_id not in self.business_agents:
            self.emit(Event(
                "llm.intent.error",
                source="frontdesk_agent",
                target="llm_client",
                session_id=session.session_id,
                payload={"error": f"unknown agent: {suggestion.selected_agent_id}", "user_text": text},
            ))
            return f"LLM 返回了未知 Agent：{suggestion.selected_agent_id}"

        slots = self._infer_slots(text, session=session)
        for key, value in suggestion.slots.items():
            if value not in (None, "", []):
                slots[key] = value
        if action == "tool_request" and suggestion.tool_goal:
            slots.setdefault("tool_goal", suggestion.tool_goal)
        mode = suggestion.mode or ("tool" if action == "tool_request" else ("create_todo" if wants_todo(text) else "answer"))
        dlg = self.dialogues.create(
            session_id=session.session_id,
            business_agent=suggestion.selected_agent_id,
            intent=suggestion.intent,
            slots=slots,
            mode=mode,
            user_goal=suggestion.tool_goal or text,
            dialogue_summary=f"LLM 解析用户目标：{text}；action={action}；reason={suggestion.reason}",
            status="collecting",
        )
        self.agent_log(
            f"FrontDeskAgent 采用 LLM 对话决策：action={action} agent={suggestion.selected_agent_id} intent={suggestion.intent} confidence={suggestion.confidence:.2f}"
        )
        return self._dispatch_business(session, dlg.dialogue_id, raw_user_text=suggestion.tool_goal or text)

    def _agent_summaries_for_llm(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for agent_id, agent in self.business_agents.items():
            definition = getattr(agent, "definition", None)
            intents: list[str] = []
            keywords: list[str] = []
            if definition:
                intents = [cap.intent for cap in definition.capabilities]
                keywords = list(definition.routing_keywords)
            items.append({
                "id": agent_id,
                "name": getattr(agent, "name", agent_id),
                "lead": getattr(agent, "lead", ""),
                "domains": list(getattr(agent, "domains", ()) or []),
                "intents": intents,
                "keywords": keywords[:20],
            })
        return items

    def _continue_dialogue(self, session: SessionState, dialogue_id: str, text: str) -> str:
        updates = self._infer_slots(text, session=session, allow_project_direct=True)
        dlg = self.dialogues.update_slots(dialogue_id, updates)
        self.agent_log(f"FrontDeskAgent 将用户补充信息写入 Dialogue：{updates}")
        return self._dispatch_business(session, dlg.dialogue_id, raw_user_text=text)

    def _dispatch_business(self, session: SessionState, dialogue_id: str, raw_user_text: str) -> str:
        dlg = self.dialogues.get(dialogue_id)
        assert dlg is not None
        agent = self.business_agents[dlg.business_agent]
        req = BusinessRequest(
            intent=dlg.intent,
            mode=dlg.mode,
            user_goal=dlg.user_goal,
            raw_user_text=raw_user_text,
            known_slots=dict(dlg.slots),
            constraints={"send_to_group": False},
            session_id=session.session_id,
            dialogue_id=dialogue_id,
        )
        self.emit(Event("agent.business.request", source=self.id, target=agent.id, session_id=session.session_id, dialogue_id=dialogue_id, payload=req.__dict__))
        self.agent_log(f"FrontDeskAgent 派单给 {agent.name}: {req.intent}")
        resp: BusinessResponse = agent.handle(req)
        self.emit(Event("agent.business.response", source=agent.id, target=self.id, session_id=session.session_id, dialogue_id=dialogue_id, payload=resp.__dict__))
        return self._handle_business_response(session, dialogue_id, resp)

    def _handle_business_response(self, session: SessionState, dialogue_id: str, resp: BusinessResponse) -> str:
        if resp.message_type == "need_user_input":
            self.dialogues.set_status(dialogue_id, "waiting_user_input")
            self.sessions.set_active_dialogue(session.session_id, dialogue_id)
            return resp.suggested_question or "还缺少一些信息，请补充。"

        if resp.message_type == "todo_proposal":
            assert resp.todo is not None
            payload = dict(resp.todo)
            payload["created_by"] = {"user_id": session.user_id, "channel": session.channel}
            payload["source"] = {"session_id": session.session_id, "dialogue_id": dialogue_id}
            self.emit(Event("todo.create.request", source=self.id, target="todo_service", session_id=session.session_id, dialogue_id=dialogue_id, payload=payload))
            todo = self.todos.create(payload)
            self.emit(Event("todo.created", source="todo_service", target=self.id, session_id=session.session_id, dialogue_id=dialogue_id, payload=todo.to_dict()))
            self.todo_changed()
            self.dialogues.set_status(dialogue_id, "done")
            self.sessions.set_active_dialogue(session.session_id, None)
            project = todo.action_args.get("project_name")
            self.sessions.set_last_project(session.session_id, project)
            return f"已加入待办：{todo.user_visible_summary}。你可以说“查看待办”或“执行第一个待办”。"

        if resp.message_type == "tool_request":
            return self._handle_tool_request(session, dialogue_id, resp)

        if resp.message_type == "business_result":
            self.dialogues.set_status(dialogue_id, "done")
            self.sessions.set_active_dialogue(session.session_id, None)
            result = resp.result or {}
            data = result.get("data") or {}
            self.sessions.set_last_project(session.session_id, data.get("project_name"))
            title = result.get("title", "业务结果")
            summary = result.get("summary", "已完成。")
            persist_note = self._persist_risk_result_if_needed(session, dialogue_id, resp, result)
            if persist_note:
                return f"{title}\n\n{summary}\n\n{persist_note}"
            return f"{title}\n\n{summary}"

        if resp.message_type == "error":
            self.dialogues.set_status(dialogue_id, "done")
            self.sessions.set_active_dialogue(session.session_id, None)
            return f"处理失败：{(resp.error or {}).get('message', '未知错误')}"

        return "当前运行时收到了暂未实现的业务响应类型。"



    def _persist_risk_result_if_needed(self, session: SessionState, dialogue_id: str, resp: BusinessResponse, result: dict[str, Any]) -> str:
        if resp.agent_id != "risk_agent":
            return ""
        feishu_agent = self.business_agents.get("feishu_ops_agent")
        if feishu_agent is None:
            return "风险结果未写入多维表格：缺少 feishu_ops_agent。"
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        project_name = data.get("project_name") or session.last_project or "未命名项目"
        time_range = data.get("time_range") or "this_week"
        req = BusinessRequest(
            intent="feishu.bitable.write_risks",
            mode="tool",
            user_goal="把风险分析结果写入配置好的 Risks 风险问题表",
            raw_user_text="write risk result to configured Risks table",
            known_slots={
                "operation": "write_risks_to_bitable",
                "project_name": project_name,
                "time_range": time_range,
                "risk_result": result,
            },
            constraints={},
            session_id=session.session_id,
            dialogue_id=dialogue_id,
        )
        self.emit(Event("agent.business.request", source=self.id, target="feishu_ops_agent", session_id=session.session_id, dialogue_id=dialogue_id, payload=req.__dict__))
        self.agent_log("FrontDeskAgent 将风险结果交给 FeishuOpsAgent 写入 Risks 表")
        tool_resp: BusinessResponse = feishu_agent.handle(req)
        self.emit(Event("agent.business.response", source="feishu_ops_agent", target=self.id, session_id=session.session_id, dialogue_id=dialogue_id, payload=tool_resp.__dict__))
        if tool_resp.message_type != "tool_request":
            return "风险结果未写入多维表格：FeishuOpsAgent 未返回工具请求。"
        return self._handle_tool_request(session, dialogue_id, tool_resp)

    def _handle_tool_request(self, session: SessionState, dialogue_id: str, resp: BusinessResponse) -> str:
        tool = resp.tool or {}
        tool_name = str(tool.get("name") or "").strip()
        args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
        if not self.tool_call:
            self.dialogues.set_status(dialogue_id, "done")
            self.sessions.set_active_dialogue(session.session_id, None)
            return "工具执行器尚未接入。"
        result = self.tool_call(tool_name, args, resp.agent_id, session.session_id, dialogue_id)
        self.dialogues.set_status(dialogue_id, "done")
        self.sessions.set_active_dialogue(session.session_id, None)
        if result.get("ok"):
            msg = str(tool.get("success_message") or "工具已执行。")
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            if tool_name == "__tool_plan__":
                step_lines = []
                for i, step in enumerate(data.get("steps", []) if isinstance(data.get("steps"), list) else [], 1):
                    if isinstance(step, dict):
                        step_lines.append(f"{i}. {step.get('tool_name')} · {'ok' if step.get('ok') else 'failed'}")
                detail = "\n".join(step_lines) or "工具计划已完成。"
                return f"{msg}\n\n工具计划：{data.get('goal') or ''}\n{detail}"
            return f"{msg}\n\n工具：{tool_name}\n结果：{result.get('data')}"
        return f"工具执行失败：{result.get('error') or '未知错误'}"

    def _execute_todo_from_text(self, session: SessionState, text: str) -> str:
        idx = 1
        m = re.search(r"第\s*(\d+)\s*个", text)
        if m:
            idx = int(m.group(1))
        todo = self.todos.get_by_index(idx)
        if not todo:
            return "没有找到可执行的待办。你可以先说“查看待办”。"
        return self.execute_todo(session, todo.todo_id)

    def execute_todo(self, session: SessionState, todo_id: str) -> str:
        todo = next((x for x in self.todos.list() if x.todo_id == todo_id), None)
        if not todo:
            return "待办不存在。"
        if todo.status not in {"pending", "failed"}:
            return f"这个待办当前状态是 {todo.status}，不能重复执行。"
        self.emit(Event("todo.update_status.request", source=self.id, target="todo_service", session_id=session.session_id, payload={"todo_id": todo.todo_id, "status": "running"}))
        self.todos.update_status(todo.todo_id, "running")
        self.todo_changed()
        agent = self.business_agents[todo.assigned_agent]
        req = BusinessRequest(
            intent=todo.action_type,
            mode="answer",
            user_goal=todo.user_visible_summary,
            raw_user_text="execute todo",
            known_slots=dict(todo.action_args),
            constraints=todo.task_context.get("constraints", {}),
            session_id=session.session_id,
            dialogue_id=todo.source.get("dialogue_id"),
            task_context=todo.task_context,
        )
        self.emit(Event("agent.business.request", source=self.id, target=agent.id, session_id=session.session_id, dialogue_id=req.dialogue_id, payload=req.__dict__))
        self.agent_log(f"FrontDeskAgent 执行待办 {todo.todo_id}，派给 {agent.name}")
        resp = agent.handle(req)
        self.emit(Event("agent.business.response", source=agent.id, target=self.id, session_id=session.session_id, dialogue_id=req.dialogue_id, payload=resp.__dict__))
        if resp.message_type == "business_result":
            result = resp.result or {}
            persist_note = self._persist_risk_result_if_needed(session, str(req.dialogue_id or ""), resp, result)
            saved_result = dict(result)
            if persist_note:
                saved_result["persist_note"] = persist_note
            self.todos.update_status(todo.todo_id, "done", result=saved_result)
            self.emit(Event("todo.updated", source="todo_service", target=self.id, session_id=session.session_id, payload={"todo_id": todo.todo_id, "status": "done"}))
            self.todo_changed()
            suffix = f"\n\n{persist_note}" if persist_note else ""
            return f"待办已完成：{todo.title}\n\n{result.get('title', '结果')}\n{result.get('summary', '')}{suffix}"
        error = (resp.error or {}).get("message", "业务 Agent 未返回可执行结果")
        self.todos.update_status(todo.todo_id, "failed", error=error)
        self.todo_changed()
        return f"待办执行失败：{error}"

    def _reply_todo_list(self) -> str:
        todos = self.todos.list()
        if not todos:
            return "当前没有待办。"
        lines = ["当前待办："]
        for i, todo in enumerate(todos, 1):
            lines.append(f"{i}. [{todo.status}] {todo.user_visible_summary} · {todo.assigned_agent}")
        return "\n".join(lines)

    def _infer_slots(self, text: str, *, session: SessionState, allow_project_direct: bool = False) -> dict[str, Any]:
        project = infer_project_name(text)
        if allow_project_direct and not project:
            candidate = text.strip(" ，。,.；;：:")
            if 2 <= len(candidate) <= 30:
                project = candidate
        return {
            "project_name": project or session.last_project,
            "time_range": infer_time_range(text) or "this_week",
        }

    @staticmethod
    def _is_todo_list(text: str) -> bool:
        return any(x in text for x in ("查看待办", "待办列表", "有哪些待办", "list todo"))

    @staticmethod
    def _is_todo_execute(text: str) -> bool:
        return any(x in text for x in ("执行第", "执行待办", "执行第一个待办", "开始第", "run todo"))

    @staticmethod
    def _is_cancel_dialogue(text: str) -> bool:
        return text in {"取消", "算了", "取消当前任务", "退出"}
