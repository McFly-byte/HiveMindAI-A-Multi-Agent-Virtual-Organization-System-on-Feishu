from __future__ import annotations

import json
import re
from datetime import datetime
from math import ceil
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from agent_runtime.config import AgentConfig
from agent_runtime.loop import AgentLoopState
from agent_runtime.session import AgentSession, SessionMemoryItem


ContextItemKind = Literal[
    "input",
    "output",
    "prompt",
    "project",
    "memory",
    "tool_result",
    "scratchpad",
    "compact_summary",
    "note",
]

_SPACE_RE = re.compile(r"\s+")


def estimate_tokens(value: str) -> int:
    """Cheap deterministic token estimate for budget decisions."""

    if not value:
        return 0
    return max(1, ceil(len(value) / 4))


def normalize_context_content(value: Any) -> str:
    """Render arbitrary runtime payloads into compactable text."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    try:
        return json.dumps(value, ensure_ascii=False, default=str, indent=2)
    except TypeError:
        return str(value)


def _preview(text: str, max_chars: int) -> str:
    normalized = _SPACE_RE.sub(" ", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


class ContextBudget(BaseModel):
    """Runtime context budget and compaction knobs.

    The runtime uses this budget for deterministic compaction. It is deliberately
    independent from provider tokenizers so local tests and non-LLM handlers stay
    reproducible.
    """

    max_tokens: int = 6000
    compact_trigger_ratio: float = 0.85
    compact_target_ratio: float = 0.55
    keep_recent_items: int = 4
    max_summary_chars: int = 4000
    per_item_summary_chars: int = 600

    @model_validator(mode="after")
    def validate_budget(self) -> "ContextBudget":
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not 0 < self.compact_target_ratio <= self.compact_trigger_ratio <= 1:
            raise ValueError("compact ratios must satisfy 0 < target <= trigger <= 1")
        if self.keep_recent_items < 0:
            raise ValueError("keep_recent_items must be non-negative")
        if self.max_summary_chars <= 0 or self.per_item_summary_chars <= 0:
            raise ValueError("summary char limits must be positive")
        return self

    @property
    def compact_trigger_tokens(self) -> int:
        return max(1, int(self.max_tokens * self.compact_trigger_ratio))

    @property
    def compact_target_tokens(self) -> int:
        return max(1, int(self.max_tokens * self.compact_target_ratio))


class ContextItem(BaseModel):
    """One piece of prompt/runtime context managed by AgentContext."""

    item_id: str = Field(default_factory=lambda: f"ctx_{uuid4().hex[:12]}")
    kind: ContextItemKind
    key: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    pinned: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    estimated_tokens: int = 0

    @model_validator(mode="after")
    def fill_estimated_tokens(self) -> "ContextItem":
        if self.estimated_tokens <= 0:
            self.estimated_tokens = estimate_tokens(self.content)
        return self


class CompactSummary(BaseModel):
    """Summary generated after compacting older context items."""

    summary_id: str = Field(default_factory=lambda: f"cmp_{uuid4().hex[:12]}")
    reason: str
    source_item_count: int
    source_keys: list[str]
    original_estimated_tokens: int
    compacted_estimated_tokens: int
    summary: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DeterministicCompactStrategy:
    """Local compactor used until an LLM summarizer is wired in."""

    def summarize(
        self,
        *,
        items: list[ContextItem],
        reason: str,
        budget: ContextBudget,
    ) -> str:
        lines = [
            f"Compact summary generated for reason={reason}; source_items={len(items)}.",
        ]
        for item in items:
            content = _preview(item.content, budget.per_item_summary_chars)
            lines.append(f"- [{item.kind}:{item.key}] {content}")
        summary = "\n".join(lines)
        if len(summary) <= budget.max_summary_chars:
            return summary
        return summary[: budget.max_summary_chars - 3].rstrip() + "..."


class AgentContext:
    """Runtime context manager for one AgentSession.

    ``AgentSession`` remains the run state of record. ``AgentContext`` manages the
    context view assembled for a handler or LLM call: input payloads, retrieved
    memories, scratchpad notes, tool result summaries, outputs, and compacted
    summaries.
    """

    def __init__(
        self,
        *,
        session: AgentSession,
        agent_config: AgentConfig | None = None,
        budget: ContextBudget | None = None,
        compact_strategy: DeterministicCompactStrategy | None = None,
    ) -> None:
        self.session = session
        self.agent_config = agent_config
        self.budget = budget or ContextBudget()
        self.compact_strategy = compact_strategy or DeterministicCompactStrategy()
        self.loop_state: AgentLoopState | None = None
        self.items: list[ContextItem] = []
        self.compact_summaries: list[CompactSummary] = []
        if agent_config is not None:
            self.add_item(
                kind="prompt",
                key="agent_config",
                content=agent_config.model_dump_json(indent=2),
                priority=8,
                pinned=True,
                metadata={"agent_name": str(agent_config.agent_name)},
            )

    @property
    def estimated_tokens(self) -> int:
        return sum(item.estimated_tokens for item in self.items)

    def add_item(
        self,
        *,
        kind: ContextItemKind,
        key: str,
        content: Any,
        metadata: dict[str, Any] | None = None,
        priority: int = 0,
        pinned: bool = False,
    ) -> ContextItem:
        item = ContextItem(
            kind=kind,
            key=key,
            content=normalize_context_content(content),
            metadata=metadata or {},
            priority=priority,
            pinned=pinned,
        )
        self.items.append(item)
        return item

    def add_payload(self, payload: Any, *, key: str = "input_payload") -> ContextItem:
        return self.add_item(
            kind="input",
            key=key,
            content=payload,
            priority=6,
            metadata={"run_id": self.session.run_id},
        )

    def add_output(self, output: Any, *, summary: str | None = None) -> ContextItem:
        return self.add_item(
            kind="output",
            key=f"{self.session.agent_name}_output",
            content=summary or output,
            priority=7,
            metadata={"run_id": self.session.run_id},
        )

    def add_scratchpad(self, key: str, value: Any) -> ContextItem:
        item = self.add_item(kind="scratchpad", key=key, content=value, priority=5)
        self.compact_if_needed(reason=f"scratchpad:{key}")
        return item

    def needs_compact(self) -> bool:
        return self.estimated_tokens > self.budget.compact_trigger_tokens

    def compact_if_needed(self, *, reason: str = "budget") -> CompactSummary | None:
        if not self.needs_compact():
            return None
        return self.compact(reason=reason, force=False)

    def compact(self, *, reason: str = "manual", force: bool = True) -> CompactSummary | None:
        candidates = [
            item for item in self.items if not item.pinned and item.kind != "compact_summary"
        ]
        if not candidates:
            return None

        selected = candidates
        if not force:
            protected_ids: set[str] = set()
            if self.budget.keep_recent_items:
                protected_ids = {
                    item.item_id for item in candidates[-self.budget.keep_recent_items :]
                }
            selectable = [item for item in candidates if item.item_id not in protected_ids]
            projected_tokens = self.estimated_tokens
            selected = []
            for item in selectable:
                selected.append(item)
                projected_tokens -= item.estimated_tokens
                if projected_tokens <= self.budget.compact_target_tokens:
                    break
        if not selected:
            return None

        selected_ids = {item.item_id for item in selected}
        original_tokens = self.estimated_tokens
        summary_text = self.compact_strategy.summarize(
            items=selected,
            reason=reason,
            budget=self.budget,
        )
        self.items = [item for item in self.items if item.item_id not in selected_ids]
        summary_item = ContextItem(
            kind="compact_summary",
            key=f"compact_summary:{len(self.compact_summaries) + 1}",
            content=summary_text,
            metadata={"reason": reason, "source_item_count": len(selected)},
            priority=10,
            pinned=True,
        )
        self.items.insert(0, summary_item)

        compact_summary = CompactSummary(
            reason=reason,
            source_item_count=len(selected),
            source_keys=[item.key for item in selected],
            original_estimated_tokens=original_tokens,
            compacted_estimated_tokens=self.estimated_tokens,
            summary=summary_text,
        )
        self.compact_summaries.append(compact_summary)
        self.session.memory.append(
            SessionMemoryItem(
                key="context_compact_summary",
                value=compact_summary.model_dump(mode="json"),
                summary=_preview(summary_text, 500),
            )
        )
        return compact_summary

    async def load_relevant_memories(
        self,
        *,
        tool_executor: Any,
        query: str,
        top_k: int = 5,
        memory_type: str = "all",
    ) -> list[dict[str, Any]]:
        """Retrieve long-term memories through the formal tool executor."""

        out = await tool_executor.call_tool(
            "memory_search",
            {
                "query": query,
                "top_k": top_k,
                "memory_type": memory_type,
                "agent_id": str(self.session.agent_name),
                "project_id": self.session.project_id,
            },
            self.session,
        )
        result = out.get("result") if out.get("ok") else {}
        records = result.get("results", []) if isinstance(result, dict) else []
        self.add_item(
            kind="memory",
            key=f"memory_search:{query[:48]}",
            content=records,
            priority=4,
            metadata={"query": query, "top_k": top_k, "memory_type": memory_type},
        )
        self.compact_if_needed(reason="memory_retrieval")
        return records

    def render(self, *, max_chars: int | None = None) -> str:
        parts: list[str] = []
        for item in sorted(self.items, key=lambda i: (not i.pinned, -i.priority, i.created_at)):
            parts.append(f"## {item.kind}: {item.key}\n{item.content}")
        rendered = "\n\n".join(parts)
        if max_chars is None or len(rendered) <= max_chars:
            return rendered
        return rendered[: max_chars - 3].rstrip() + "..."

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.session.run_id,
            "agent_name": str(self.session.agent_name),
            "project_id": self.session.project_id,
            "estimated_tokens": self.estimated_tokens,
            "budget": self.budget.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in self.items],
            "compact_summaries": [
                summary.model_dump(mode="json") for summary in self.compact_summaries
            ],
        }
