from __future__ import annotations

from agent_hive.context.manager import AgentContext


def assemble_context_text(context: AgentContext, *, max_chars: int | None = None) -> str:
    return context.render(max_chars=max_chars)
