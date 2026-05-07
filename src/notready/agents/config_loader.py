from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AgentCapabilityConfig:
    intent: str
    description: str = ""
    examples: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    required_slots: list[str] = field(default_factory=list)


@dataclass
class AgentDefinition:
    id: str
    type: str = "business_agent"
    name: str = "业务 Agent"
    tag: str = "业务 Agent"
    lead: str = "处理结构化业务请求。"
    icon: str = "A"
    accent: str = ""
    md_path: str | None = None
    system_prompt: str = ""
    routing: dict[str, Any] = field(default_factory=dict)
    capabilities: list[AgentCapabilityConfig] = field(default_factory=list)

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(str(x) for x in (self.routing.get("domains") or []))

    @property
    def routing_keywords(self) -> tuple[str, ...]:
        values: list[str] = []
        for item in self.routing.get("keywords") or []:
            text = str(item).strip()
            if text:
                values.append(text)
        for cap in self.capabilities:
            values.extend(x for x in cap.keywords if x)
        return tuple(dict.fromkeys(values))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "tag": self.tag,
            "lead": self.lead,
            "icon": self.icon,
            "accent": self.accent,
            "domains": list(self.domains),
            "routing": self.routing,
            "capabilities": [
                {
                    "intent": c.intent,
                    "description": c.description,
                    "examples": c.examples,
                    "keywords": c.keywords,
                    "required_slots": c.required_slots,
                }
                for c in self.capabilities
            ],
            "system_prompt_preview": self.system_prompt.strip()[:240],
            "md_path": self.md_path,
        }


def load_agent_definitions(config_dir: str | Path) -> dict[str, AgentDefinition]:
    root = Path(config_dir)
    definitions: dict[str, AgentDefinition] = {}
    if not root.exists():
        return definitions

    for yaml_path in sorted(root.glob("*/Agent.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        agent_id = str(raw.get("id") or yaml_path.parent.name).strip()
        if not agent_id:
            continue
        md_path = yaml_path.with_name("Agent.md")
        system_prompt = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        capabilities: list[AgentCapabilityConfig] = []
        for item in raw.get("capabilities") or []:
            if not isinstance(item, dict):
                continue
            capabilities.append(AgentCapabilityConfig(
                intent=str(item.get("intent") or "").strip(),
                description=str(item.get("description") or "").strip(),
                examples=[str(x) for x in item.get("examples") or []],
                keywords=[str(x) for x in item.get("keywords") or []],
                required_slots=[str(x) for x in item.get("required_slots") or []],
            ))
        definitions[agent_id] = AgentDefinition(
            id=agent_id,
            type=str(raw.get("type") or "business_agent"),
            name=str(raw.get("name") or agent_id),
            tag=str(raw.get("tag") or "业务 Agent"),
            lead=str(raw.get("lead") or "处理结构化业务请求。"),
            icon=str(raw.get("icon") or agent_id[:1].upper()),
            accent=str(raw.get("accent") or ""),
            md_path=str(md_path.relative_to(root.parent)) if md_path.exists() else None,
            system_prompt=system_prompt,
            routing=raw.get("routing") if isinstance(raw.get("routing"), dict) else {},
            capabilities=capabilities,
        )
    return definitions
