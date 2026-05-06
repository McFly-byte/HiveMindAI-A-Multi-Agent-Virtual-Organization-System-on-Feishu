"""OpenAI-compatible LLM tool for the Agent Runtime."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from tool_integration.tools import ToolContext, ToolRegistry, ToolSpec


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _provider_defaults(provider: str) -> tuple[str, str, str]:
    key = provider.strip().lower()
    if key == "aihubmix":
        return "AIHUBMIX_API_KEY", "https://aihubmix.com/v1", "gpt-4o-mini"
    if key == "openai":
        return "OPENAI_API_KEY", os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1", "gpt-4o-mini"
    if key == "deepseek":
        return "DEEPSEEK_API_KEY", "https://api.deepseek.com", "deepseek-chat"
    if key == "qwen":
        return "QWEN_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"
    if key == "glm":
        return "GLM_API_KEY", "https://open.bigmodel.cn/api/paas/v4", "glm-4"
    return f"{key.upper()}_API_KEY", os.environ.get(f"{key.upper()}_BASE_URL") or "", ""


def _api_key(provider: str, args: dict[str, Any]) -> str:
    if args.get("api_key"):
        return str(args["api_key"])
    env_name, _, _ = _provider_defaults(provider)
    candidates = [
        env_name,
        f"{provider.strip().upper()}_API_KEY",
        "HIVEMIND_LLM_API_KEY",
    ]
    if provider.strip().lower() == "qwen":
        candidates.append("DASHSCOPE_API_KEY")
    for name in candidates:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError(f"missing LLM API key; set {env_name} in .env")


def _base_url(provider: str, args: dict[str, Any]) -> str:
    if args.get("base_url"):
        return str(args["base_url"]).rstrip("/")
    _, default, _ = _provider_defaults(provider)
    value = os.environ.get("HIVEMIND_LLM_BASE_URL") or os.environ.get(f"{provider.strip().upper()}_BASE_URL") or default
    if not value:
        raise RuntimeError(f"missing LLM base URL for provider={provider!r}")
    return str(value).rstrip("/")


def _model(provider: str, args: dict[str, Any]) -> str:
    if args.get("model"):
        return str(args["model"])
    _, _, default = _provider_defaults(provider)
    return os.environ.get("HIVEMIND_LLM_MODEL") or default


def _messages(args: dict[str, Any]) -> list[dict[str, str]]:
    raw = args.get("messages")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("messages must be a non-empty list")
    messages: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError("each message must be an object")
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role not in {"developer", "system", "user", "assistant"}:
            raise RuntimeError(f"unsupported message role: {role!r}")
        messages.append({"role": role, "content": content})
    return messages


def _extract_json_object(text: str) -> dict[str, Any]:
    candidates: list[str] = []
    match = _JSON_BLOCK.search(text)
    if match:
        candidates.append(match.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    candidates.append(text.strip())

    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:  # noqa: BLE001 - report all parse failures as tool error.
            last_error = exc
    raise RuntimeError(f"LLM output is not a valid JSON object: {last_error}")


def _chat_completion(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    provider = str(args.get("provider") or "aihubmix")
    model = _model(provider, args)
    base_url = _base_url(provider, args)
    timeout = int(args.get("timeout_seconds") or 60)
    json_mode = bool(args.get("json_mode", True))

    body: dict[str, Any] = {
        "model": model,
        "messages": _messages(args),
        "temperature": float(args.get("temperature", 0.2)),
        "max_tokens": int(args.get("max_tokens") or 2048),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for key in ("top_p", "frequency_penalty", "presence_penalty"):
        if key in args:
            body[key] = float(args[key])
    if "seed" in args:
        body["seed"] = int(args["seed"])

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key(provider, args)}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured provider endpoint.
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API HTTP {exc.code}: {detail[:800]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM API network error: {exc}") from exc

    data = json.loads(raw)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM response missing choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise RuntimeError("LLM response missing message")
    content = str(message.get("content") or "")
    parsed = _extract_json_object(content) if json_mode else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    finish_reason = str(first.get("finish_reason") or "") if isinstance(first, dict) else ""

    ctx.emit(
        "llm.chat.completed",
        {
            "provider": provider,
            "model": model,
            "finish_reason": finish_reason,
            "json_valid": bool(parsed) if json_mode else None,
            "usage": usage,
        },
    )
    return {
        "provider": provider,
        "model": model,
        "content": content,
        "json": parsed,
        "usage": usage,
        "finish_reason": finish_reason,
    }


def register(registry: ToolRegistry, **kwargs: Any) -> str:
    @registry.register(
        ToolSpec(
            name="llm_chat_json",
            description="Call an OpenAI-compatible chat completion endpoint and parse a JSON object response.",
            mode="sync",
            kind="meta",
            input_schema={
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "model": {"type": "string"},
                    "base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["role", "content"],
                        },
                    },
                    "temperature": {"type": "number"},
                    "max_tokens": {"type": "integer"},
                    "timeout_seconds": {"type": "integer"},
                    "json_mode": {"type": "boolean"},
                    "top_p": {"type": "number"},
                    "frequency_penalty": {"type": "number"},
                    "presence_penalty": {"type": "number"},
                    "seed": {"type": "integer"},
                },
                "required": ["messages"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "model": {"type": "string"},
                    "content": {"type": "string"},
                    "json": {"type": "object"},
                    "usage": {"type": "object"},
                    "finish_reason": {"type": "string"},
                },
                "required": ["provider", "model", "content", "json"],
            },
        )
    )
    def llm_chat_json(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return _chat_completion(args, ctx)

    return "llm"
