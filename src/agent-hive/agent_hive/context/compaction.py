from __future__ import annotations

from agent_hive.context.manager import AgentContext


def compact_context(context: AgentContext) -> AgentContext:
    context.compact_if_needed()
    return context
