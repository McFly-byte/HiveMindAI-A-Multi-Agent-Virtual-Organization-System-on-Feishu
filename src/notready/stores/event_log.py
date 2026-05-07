from __future__ import annotations

from typing import Any

from runtime.models import Event, now_hms


class EventLog:
    """Small in-memory event log for the runtime panel.

    It deliberately stores human-readable lines rather than raw JSON dumps so the
    presentation can explain the runtime flow without opening a debugger.
    """

    def __init__(self, max_items: int = 500) -> None:
        self.max_items = max_items
        self.items: list[dict[str, Any]] = []
        self.total_enqueued = 0
        self.pending = 0

    def on_enqueue(self, event: Event) -> dict[str, Any]:
        self.total_enqueued += 1
        self.pending += 1
        return self._append("enqueue", "入队", self.describe_event(event, phase="enqueue"), event)

    def on_processed(self, event: Event, message: str | None = None) -> dict[str, Any]:
        self.pending = max(0, self.pending - 1)
        return self._append("processed", "已处理", message or self.describe_event(event, phase="processed"), event)

    def system(self, message: str) -> dict[str, Any]:
        return self._append("system", "系统", message, None)

    def _append(self, level: str, tag: str, message: str, event: Event | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "level": level,
            "tag": tag,
            "message": " " + message,
            "readable_message": message,
            "time": now_hms(),
        }
        if event is not None:
            item.update({
                "event_id": event.event_id,
                "event_type": event.event_type,
                "source": event.source,
                "target": event.target,
                "session_id": event.session_id,
                "dialogue_id": event.dialogue_id,
            })
        self.items.append(item)
        if len(self.items) > self.max_items:
            del self.items[: len(self.items) - self.max_items]
        return item

    @staticmethod
    def describe_event(event: Event, *, phase: str = "enqueue") -> str:
        payload = event.payload or {}
        route = _route(event)
        et = event.event_type

        if et == "channel.message.received":
            text = _clip(payload.get("text", ""))
            return f"用户消息进入系统：{text} · {route}"
        if et == "channel.message.send":
            text = _clip(payload.get("text", ""), 80)
            return f"准备向用户回复：{text} · {route}"
        if et == "llm.intent.parsed":
            return (
                f"LLM 完成前台意图解析：agent={payload.get('selected_agent_id') or '-'} "
                f"mode={payload.get('mode') or '-'} confidence={payload.get('confidence') or '-'} "
                f"slots={_short_dict(payload.get('slots') or {})}"
            )
        if et == "llm.intent.error":
            return f"LLM 前台意图解析失败：{_clip(payload.get('error'), 100)}"
        if et == "agent.capability.probe":
            text = _clip(payload.get("user_text", ""))
            return f"FrontDeskAgent 广播 capability.probe：谁能处理“{text}”？"
        if et == "agent.capability.bid":
            can = payload.get("can_handle")
            conf = payload.get("confidence")
            intent = payload.get("matched_intent") or "-"
            reason = _clip(payload.get("reason", ""), 50)
            return f"{event.source or 'BusinessAgent'} 返回 bid：can_handle={can} confidence={conf} intent={intent} · {reason}"
        if et == "agent.business.request":
            intent = payload.get("intent") or "-"
            slots = payload.get("known_slots") or {}
            return f"FrontDeskAgent 派发 business.request 给 {event.target or '-'}：intent={intent} slots={_short_dict(slots)}"
        if et == "agent.business.response":
            mt = payload.get("message_type") or "-"
            if mt == "need_user_input":
                missing = payload.get("missing_slots") or []
                q = _clip(payload.get("suggested_question", ""), 70)
                return f"{event.source or 'BusinessAgent'} 需要用户补充：missing={missing} question={q}"
            if mt == "todo_proposal":
                todo = payload.get("todo") or {}
                return f"{event.source or 'BusinessAgent'} 返回 todo_proposal：{todo.get('user_visible_summary') or todo.get('title') or '-'}"
            if mt == "business_result":
                result = payload.get("result") or {}
                return f"{event.source or 'BusinessAgent'} 返回 business_result：{result.get('title') or '-'}"
            if mt == "error":
                err = payload.get("error") or {}
                return f"{event.source or 'BusinessAgent'} 返回 error：{err.get('message') or '-'}"
            return f"{event.source or 'BusinessAgent'} 返回 business.response：{mt}"
        if et == "todo.create.request":
            return f"FrontDeskAgent 请求 TodoService 创建待办：{payload.get('user_visible_summary') or payload.get('title') or '-'}"
        if et == "todo.created":
            return f"TodoService 已创建待办：{payload.get('todo_id') or '-'} · {payload.get('user_visible_summary') or payload.get('title') or '-'}"
        if et == "todo.update_status.request":
            return f"请求更新待办状态：{payload.get('todo_id') or '-'} → {payload.get('status') or '-'}"
        if et == "todo.updated":
            return f"TodoService 已更新待办：{payload.get('todo_id') or '-'} → {payload.get('status') or '-'}"
        if et == "tool.plan.started":
            return f"ToolExecutor 开始工具计划：{_clip(payload.get('goal'), 80)} · steps={payload.get('step_count') or '-'}"
        if et == "tool.plan.step":
            return f"ToolExecutor 执行计划步骤 {payload.get('index')}：{payload.get('tool_name') or '-'} args={_short_dict(payload.get('args') or {})}"
        if et == "tool.plan.finished":
            return f"ToolExecutor 完成工具计划：{_clip(payload.get('goal'), 80)} · steps={payload.get('step_count') or '-'}"
        if et == "tool.plan.failed":
            return f"ToolExecutor 工具计划失败：step={payload.get('failed_step') or '-'} error={_clip(payload.get('error'), 100)}"
        if et == "tool.call.request":
            return f"ToolExecutor 收到工具请求：{payload.get('tool_name') or '-'} args={_short_dict(payload.get('args') or {})} · {route}"
        if et == "tool.call.result":
            return f"ToolExecutor 工具执行成功：{payload.get('tool_name') or '-'} result={_short_dict(payload.get('data') or {})}"
        if et == "tool.call.error":
            return f"ToolExecutor 工具执行失败：{payload.get('tool_name') or '-'} error={_clip(payload.get('error'), 80)}"
        if et == "system.error":
            return f"系统错误：{_clip(payload.get('message', ''), 100)}"

        return f"{et} · {route}"

    def stats(self) -> dict[str, int]:
        return {"pending": self.pending, "total": self.total_enqueued}


def _route(event: Event) -> str:
    target = f" → {event.target}" if event.target else ""
    return f"{event.source or 'system'}{target}"


def _clip(value: Any, limit: int = 60) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short_dict(value: dict[str, Any], limit: int = 80) -> str:
    pairs = [f"{k}={v}" for k, v in value.items()]
    return _clip("{" + ", ".join(pairs) + "}", limit)
