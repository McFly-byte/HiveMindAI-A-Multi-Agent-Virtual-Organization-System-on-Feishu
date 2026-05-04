from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import importlib.util


@dataclass
class EventPolicy:
    print_to_cli: bool = True
    append_to_history: bool = False
    trigger_llm: bool = False


@dataclass
class LLMConfig:
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url_env: str = "DEEPSEEK_BASE_URL"
    model_env: str = "DEEPSEEK_MODEL"
    default_base_url: str = "https://api.deepseek.com"
    default_model: str = "deepseek-chat"


@dataclass
class EventSourceSpec:
    name: str
    type: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentSpec:
    name: str
    description: str = ""
    tool_dirs: list[str] = field(default_factory=lambda: ["tools"])
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    event_sources: list[EventSourceSpec] = field(default_factory=list)
    initial_messages: list[dict[str, str]] = field(default_factory=list)
    persistent_prompt: str | dict[str, str] | None = None
    event_subscriptions: list[str] = field(default_factory=lambda: ["tool.job.*"])
    event_policy: dict[str, EventPolicy] = field(default_factory=dict)
    llm: LLMConfig = field(default_factory=LLMConfig)

    def persistent_message(self) -> dict[str, str] | None:
        if not self.persistent_prompt:
            return None
        if isinstance(self.persistent_prompt, str):
            return {"role": "system", "content": self.persistent_prompt}
        return self.persistent_prompt

    def policy_for(self, event_type: str) -> EventPolicy:
        if event_type in self.event_policy:
            return self.event_policy[event_type]
        if event_type.endswith(".progress"):
            return EventPolicy(print_to_cli=True, append_to_history=False, trigger_llm=False)
        if event_type.endswith(".finished") or event_type.endswith(".failed"):
            return EventPolicy(print_to_cli=True, append_to_history=True, trigger_llm=True)
        return EventPolicy(print_to_cli=True, append_to_history=False, trigger_llm=False)


def _policy_from_dict(d: dict[str, Any]) -> EventPolicy:
    return EventPolicy(
        print_to_cli=bool(d.get("print_to_cli", True)),
        append_to_history=bool(d.get("append_to_history", False)),
        trigger_llm=bool(d.get("trigger_llm", False)),
    )


def _event_source_from_dict(d: dict[str, Any]) -> EventSourceSpec:
    known = {"name", "type", "enabled", "config"}
    config = dict(d.get("config", {}) or {})
    # Allow concise spec style:
    # {"name": "feishu_ws", "type": "feishu.websocket", "app_id_env": "..."}
    for k, v in d.items():
        if k not in known:
            config[k] = v
    return EventSourceSpec(
        name=d["name"],
        type=d["type"],
        enabled=bool(d.get("enabled", True)),
        config=config,
    )


def normalize_spec(raw: dict[str, Any] | AgentSpec) -> AgentSpec:
    if isinstance(raw, AgentSpec):
        return raw

    llm_raw = raw.get("llm", {}) or {}
    llm = LLMConfig(
        api_key_env=llm_raw.get("api_key_env", "DEEPSEEK_API_KEY"),
        base_url_env=llm_raw.get("base_url_env", "DEEPSEEK_BASE_URL"),
        model_env=llm_raw.get("model_env", "DEEPSEEK_MODEL"),
        default_base_url=llm_raw.get("default_base_url", "https://api.deepseek.com"),
        default_model=llm_raw.get("default_model", "deepseek-chat"),
    )

    policy_raw = raw.get("event_policy", {}) or {}
    policy = {k: _policy_from_dict(v) for k, v in policy_raw.items()}

    source_raw = raw.get("event_sources", []) or []
    sources = [_event_source_from_dict(v) for v in source_raw]

    return AgentSpec(
        name=raw["name"],
        description=raw.get("description", ""),
        tool_dirs=list(raw.get("tool_dirs", ["tools"])),
        allowed_tools=list(raw.get("allowed_tools", ["*"])),
        event_sources=sources,
        initial_messages=list(raw.get("initial_messages", [])),
        persistent_prompt=raw.get("persistent_prompt"),
        event_subscriptions=list(raw.get("event_subscriptions", ["tool.job.*"])),
        event_policy=policy,
        llm=llm,
    )


def load_spec_file(path: str | Path) -> tuple[AgentSpec, Path]:
    spec_path = Path(path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(f"spec file not found: {spec_path}")

    module_name = f"tooltest_user_spec_{abs(hash(str(spec_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, spec_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load spec file: {spec_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if hasattr(module, "get_spec"):
        raw = module.get_spec()
    elif hasattr(module, "AGENT_SPEC"):
        raw = module.AGENT_SPEC
    elif hasattr(module, "SPEC"):
        raw = module.SPEC
    else:
        raise RuntimeError("spec file should define AGENT_SPEC, SPEC, or get_spec()")

    return normalize_spec(raw), spec_path
