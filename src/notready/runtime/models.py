from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_ts() -> float:
    return time.time()


def now_hms() -> str:
    return time.strftime("%H:%M:%S")


@dataclass
class Event:
    event_type: str
    payload: dict[str, Any]
    source: str | None = None
    target: str | None = None
    session_id: str | None = None
    dialogue_id: str | None = None
    event_id: str = field(default_factory=lambda: new_id("evt"))
    created_at: float = field(default_factory=now_ts)


@dataclass
class SessionState:
    session_id: str
    channel: str
    user_id: str
    chat_id: str | None = None
    active_dialogue_id: str | None = None
    last_project: str | None = None
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)


@dataclass
class DialogueState:
    dialogue_id: str
    session_id: str
    status: Literal["collecting", "waiting_user_input", "ready", "done", "cancelled"]
    business_agent: str
    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    mode: str = "answer"
    user_goal: str = ""
    dialogue_summary: str = ""
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)


@dataclass
class CapabilityProbe:
    user_text: str
    session_id: str
    dialogue_id: str | None = None
    constraints: dict[str, Any] = field(default_factory=lambda: {
        "no_side_effects": True,
        "do_not_call_tools": True,
        "do_not_write_memory": True,
    })


@dataclass
class CapabilityBid:
    agent_id: str
    can_handle: bool
    confidence: float
    matched_intent: str | None = None
    missing_slots: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class BusinessRequest:
    intent: str
    mode: str
    user_goal: str
    raw_user_text: str
    known_slots: dict[str, Any]
    constraints: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    dialogue_id: str | None = None
    task_context: dict[str, Any] | None = None


@dataclass
class BusinessResponse:
    message_type: Literal["need_user_input", "todo_proposal", "business_result", "tool_request", "error"]
    agent_id: str
    missing_slots: list[str] = field(default_factory=list)
    suggested_question: str | None = None
    todo: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    tool: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass
class TodoItem:
    todo_id: str
    title: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    created_by: dict[str, Any]
    source: dict[str, Any]
    assigned_agent: str
    action_type: str
    action_args: dict[str, Any]
    task_context: dict[str, Any]
    user_visible_summary: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.todo_id,
            "title": self.title,
            "status": self.status,
            "created_by": self.created_by,
            "source": self.source,
            "assigned_agent": self.assigned_agent,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "task_context": self.task_context,
            "user_visible_summary": self.user_visible_summary,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
