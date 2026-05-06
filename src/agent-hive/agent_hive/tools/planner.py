from __future__ import annotations

from typing import Any

from agent_hive.schemas.tool import ToolIntent, ToolPlan, ToolStep


FIELD_TYPE_HINTS = {
    "text": 1,
    "number": 2,
    "single_select": 3,
    "multi_select": 4,
    "date": 5,
    "checkbox": 7,
    "user": 11,
    "url": 15,
    "relation": 18,
}


class ToolPlanner:
    """Intent-to-plan translator owned by the delegated tool agent."""

    def plan(self, intent: ToolIntent) -> ToolPlan:
        if intent.domain == "feishu.bitable" and intent.action in {"add_field", "create_field"}:
            return self._plan_bitable_add_field(intent)
        return ToolPlan(intent_id=intent.intent_id, domain=intent.domain, action=intent.action, summary="generic intent")

    def _plan_bitable_add_field(self, intent: ToolIntent) -> ToolPlan:
        args = intent.arguments
        field_type = args.get("field_type") or FIELD_TYPE_HINTS.get(str(args.get("field_type_hint") or "text"), 1)
        property_payload = _field_property(args)
        return ToolPlan(
            intent_id=intent.intent_id,
            domain=intent.domain,
            action=intent.action,
            summary=f"Add field {args.get('field_name')} to Feishu Bitable",
            steps=[
                ToolStep(
                    provider="feishu",
                    tool_name="feishu_bitable_create_field",
                    arguments={
                        "app_token": args.get("app_token", ""),
                        "table_id": intent.target.get("table_id") or args.get("table_id", ""),
                        "field_name": args.get("field_name", ""),
                        "field_type": int(field_type),
                        "property": property_payload,
                    },
                    purpose="create Feishu Bitable field",
                )
            ],
        )


def _field_property(args: dict[str, Any]) -> dict[str, Any]:
    raw_options = args.get("options")
    if isinstance(raw_options, list) and raw_options:
        return {"options": [{"name": str(item)} for item in raw_options]}
    prop = args.get("property")
    return prop if isinstance(prop, dict) else {}
