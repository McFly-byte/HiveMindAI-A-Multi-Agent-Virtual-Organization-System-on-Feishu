"""Memory tools registered into the formal ToolIntegration runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.memory import MemoryStore, MemoryToolset, NullVectorBackend, TOOL_SCHEMAS
from tool_integration.tools import ToolContext, ToolRegistry, ToolSpec


def _db_path(ctx: ToolContext) -> Path:
    raw = str(ctx.config.get("HIVEMIND_MEMORY_DB_PATH") or "runtime/memory.db")
    return Path(raw)


def _dispatch_memory_tool(tool_name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    store = MemoryStore(db_path=_db_path(ctx), vector_backend=NullVectorBackend())
    try:
        return MemoryToolset(store).dispatch(tool_name, args)
    finally:
        store.close()


def register(registry: ToolRegistry, **kwargs: Any) -> str:
    for schema in TOOL_SCHEMAS:
        name = schema["name"]
        description = schema.get("description") or f"Memory tool: {name}"
        input_schema = schema.get("input_schema") or {"type": "object"}

        @registry.register(
            ToolSpec(
                name=name,
                description=description,
                mode="sync",
                kind="meta",
                input_schema=input_schema,
                output_schema={"type": "object"},
            )
        )
        def memory_tool_impl(
            args: dict[str, Any],
            ctx: ToolContext,
            _tool_name: str = name,
        ) -> dict[str, Any]:
            return _dispatch_memory_tool(_tool_name, args, ctx)

    return "memory"
