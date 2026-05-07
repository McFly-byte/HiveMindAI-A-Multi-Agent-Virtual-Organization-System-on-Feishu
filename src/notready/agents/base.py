from __future__ import annotations

import re
from typing import Any

from agents.config_loader import AgentDefinition
from runtime.models import BusinessRequest, BusinessResponse, CapabilityBid, CapabilityProbe


class BusinessAgent:
    id: str = "business_agent"
    name: str = "业务 Agent"
    tag: str = "业务 Agent"
    lead: str = "处理结构化业务请求。"
    icon: str = "A"
    accent: str = ""
    domains: tuple[str, ...] = ()
    definition: AgentDefinition | None = None

    def apply_definition(self, definition: AgentDefinition) -> None:
        """Attach Agent.yaml/Agent.md metadata without replacing Python runtime behavior."""
        self.definition = definition
        self.id = definition.id
        self.name = definition.name
        self.tag = definition.tag
        self.lead = definition.lead
        self.icon = definition.icon
        self.accent = definition.accent
        if definition.domains:
            self.domains = definition.domains

    def ui_meta(self) -> dict[str, Any]:
        chips = [f"domain · {x}" for x in self.domains]
        if self.definition:
            chips.extend(f"intent · {c.intent}" for c in self.definition.capabilities[:2])
        return {
            "key": self.id,
            "name": self.name,
            "tag": self.tag,
            "lead": self.lead,
            "icon": self.icon,
            "accent": self.accent,
            "chips": chips or ["business agent"],
        }

    def configured_keywords(self) -> tuple[str, ...]:
        if not self.definition:
            return ()
        return self.definition.routing_keywords

    def configured_intent(self, *, create_todo: bool, answer_intent: str, todo_intent: str) -> str:
        if not self.definition:
            return todo_intent if create_todo else answer_intent
        suffix = "create_todo" if create_todo else "generate"
        for cap in self.definition.capabilities:
            if cap.intent.endswith(suffix):
                return cap.intent
        return todo_intent if create_todo else answer_intent

    def can_handle(self, probe: CapabilityProbe) -> CapabilityBid:
        raise NotImplementedError

    def handle(self, request: BusinessRequest) -> BusinessResponse:
        raise NotImplementedError


def contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def infer_time_range(text: str) -> str | None:
    if "本周" in text or "这周" in text:
        return "this_week"
    if "下周" in text:
        return "next_week"
    if "今天" in text:
        return "today"
    if "明天" in text:
        return "tomorrow"
    if "本月" in text or "这个月" in text:
        return "this_month"
    return None


def wants_todo(text: str) -> bool:
    return contains_any(text, ("加入待办", "加到待办", "放到待办", "稍后", "待办", "todo", "TODO"))


def infer_project_name(text: str) -> str | None:
    clean_text = text.strip(" ，。,.；;：:")

    # 明确写法："项目：虚拟 PMO" / "项目 虚拟 PMO"
    explicit = re.search(r"(?:项目|工程|课题)[：: ]+([\u4e00-\u9fa5A-Za-z0-9_\- ]{2,30})", clean_text)
    if explicit:
        value = explicit.group(1).strip(" ，。,.；;：:")
        if _valid_project_candidate(value):
            return value if value.endswith("项目") else value + "项目"

    # 常见写法："虚拟 PMO 项目本周风险" / "帮我整理虚拟 PMO 项目"
    for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9_\- ]{2,30}?项目)", clean_text):
        value = m.group(1).strip(" ，。,.；;：:")
        
        prev = None
        while prev != value:
            prev = value
            value = re.sub(r"^(帮我|请|麻烦|给我|生成|整理|拆解|创建|关于|把|做一个|做|分析)", "", value).strip()
        if _valid_project_candidate(value):
            return value

    # 用户在追问后直接回答项目名时常见："虚拟 PMO 项目"
    if _valid_project_candidate(clean_text) and any(x in clean_text for x in ("项目", "PMO", "系统")):
        return clean_text
    return None


def _valid_project_candidate(value: str) -> bool:
    value = value.strip(" ，。,.；;：:")
    if not (2 <= len(value) <= 30):
        return False
    bad_exact = {"本周", "这周", "下周", "本月", "这个", "哪个", "风险", "周报", "计划", "本周项目", "下周项目", "这个项目"}
    if value in bad_exact:
        return False
    bad_contains = ("本周项目", "下周项目", "这个项目", "哪个项目")
    if any(x in value for x in bad_contains):
        return False
    return True

def missing_required(slots: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    return [k for k in required if not slots.get(k)]
