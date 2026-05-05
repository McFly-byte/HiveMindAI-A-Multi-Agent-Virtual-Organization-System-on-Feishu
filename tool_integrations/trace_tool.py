"""Minimal meta tool for auditable no-op traces (MVP).

Registered as ``trace_tool`` so ``tool_policy.allowed_tools`` can reference it
alongside Feishu Bitable tools from ``src/feishu_adapter``.
"""

from __future__ import annotations

from typing import Any

from tool_integration.tools import ToolContext, ToolRegistry, ToolSpec


def register(registry: ToolRegistry, **kwargs: Any) -> str:
    @registry.register(
        ToolSpec(
            name="trace_tool",
            description="Append-only runtime trace marker (no side effects).",
            mode="sync",
            kind="meta",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "level": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string"}},
                "required": ["ok", "echo"],
                "additionalProperties": False,
            },
        )
    )
    def trace_impl(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        ctx.emit(
            "hivemind.trace",
            {"message": args["message"], "level": args.get("level", "info"), "payload": args.get("payload")},
        )
        return {"ok": True, "echo": args["message"]}

    return "trace_tool"
