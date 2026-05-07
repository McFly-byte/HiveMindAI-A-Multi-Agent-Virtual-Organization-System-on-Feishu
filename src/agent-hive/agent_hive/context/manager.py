from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_hive.config.loader import load_agent_prompt
from agent_hive.config.models import AgentConfig
from agent_hive.events.models import HiveEvent
from agent_hive.memory.manager import MemoryManager
from agent_hive.runtime.session import AgentSession


class ContextBudget(BaseModel):
    max_chars: int = 24000
    keep_recent_items: int = 12
    max_summary_chars: int = 3000


class ContextItem(BaseModel):
    kind: str
    key: str
    content: str
    pinned: bool = False
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentContext(BaseModel):
    session: AgentSession
    agent_config: AgentConfig
    event: HiveEvent
    budget: ContextBudget = Field(default_factory=ContextBudget)
    items: list[ContextItem] = Field(default_factory=list)
    scratchpad: dict[str, Any] = Field(default_factory=dict)

    def add_item(
        self,
        *,
        kind: str,
        key: str,
        content: Any,
        pinned: bool = False,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        self.items.append(
            ContextItem(
                kind=kind,
                key=key,
                content=text,
                pinned=pinned,
                priority=priority,
                metadata=metadata or {},
            )
        )

    def add_scratchpad(self, key: str, value: Any) -> None:
        self.scratchpad[key] = value
        self.add_item(kind="scratchpad", key=key, content=value, priority=1)

    @property
    def size_chars(self) -> int:
        return sum(len(item.content) for item in self.items)

    def compact_if_needed(self) -> None:
        if self.size_chars <= self.budget.max_chars:
            return
        pinned = [item for item in self.items if item.pinned]
        movable = [item for item in self.items if not item.pinned]
        keep = movable[-self.budget.keep_recent_items :]
        compacted = movable[: max(0, len(movable) - len(keep))]
        if not compacted:
            return
        summary = "\n".join(f"[{item.kind}:{item.key}] {item.content}" for item in compacted)
        if len(summary) > self.budget.max_summary_chars:
            summary = summary[: self.budget.max_summary_chars - 3] + "..."
        compact_item = ContextItem(
            kind="compact_summary",
            key="context_compact_summary",
            content=summary,
            pinned=True,
            priority=8,
            metadata={"source_item_count": len(compacted)},
        )
        self.items = [*pinned, compact_item, *keep]

    def render(self, max_chars: int | None = None) -> str:
        self.compact_if_needed()
        text = "\n\n".join(f"## {item.kind}:{item.key}\n{item.content}" for item in self.items)
        if max_chars is not None and len(text) > max_chars:
            return text[: max_chars - 3] + "..."
        return text

    def snapshot(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_config.agent_id,
            "project_id": self.session.project_id,
            "item_count": len(self.items),
            "size_chars": self.size_chars,
            "scratchpad_keys": sorted(self.scratchpad),
        }


class AgentContextManager:
    def __init__(self, memory_manager: MemoryManager | None = None, budget: ContextBudget | None = None) -> None:
        self.memory_manager = memory_manager
        self.budget = budget or ContextBudget()

    async def build(
        self,
        *,
        session: AgentSession,
        agent_config: AgentConfig,
        event: HiveEvent,
    ) -> AgentContext:
        context = AgentContext(session=session, agent_config=agent_config, event=event, budget=self.budget)
        context.add_item(kind="event", key=event.event_id, content=event.model_dump(mode="json"), pinned=True, priority=7)
        try:
            prompt = load_agent_prompt(agent_config)
            context.add_item(kind="prompt", key="system", content=prompt, pinned=True, priority=9)
        except Exception as exc:
            context.add_scratchpad("prompt_load_error", {"error": str(exc)})

        if agent_config.memory.enabled and self.memory_manager is not None:
            query = _memory_query(event)
            memories = await self.memory_manager.search(
                query=query,
                agent_id=agent_config.agent_id,
                project_id=session.project_id,
                top_k=agent_config.memory.max_search_results,
                scopes=agent_config.memory.read_scopes,
            )
            for index, memory in enumerate(memories):
                context.add_item(
                    kind="memory",
                    key=str(memory.get("id") or f"memory_{index}"),
                    content=memory.get("content") or memory,
                    priority=5,
                    metadata={"score": memory.get("score")},
                )
        context.compact_if_needed()
        return context


def _memory_query(event: HiveEvent) -> str:
    return json.dumps(
        {
            "event_type": event.event_type,
            "project_id": event.project_id,
            "payload": event.payload,
        },
        ensure_ascii=False,
        default=str,
    )
