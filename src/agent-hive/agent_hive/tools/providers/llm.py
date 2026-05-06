from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from agent_hive.config.models import ModelConfig


class LLMProvider:
    provider_name = "llm"

    async def generate_json(
        self,
        *,
        model_config: ModelConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_chat_json, model_config, messages)


def _chat_json(model_config: ModelConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    key_name = f"{model_config.provider.upper()}_API_KEY"
    api_key = os.environ.get(key_name) or os.environ.get("HIVEMIND_LLM_API_KEY")
    if not api_key:
        raise RuntimeError(f"missing LLM API key: {key_name}")
    base_url = os.environ.get(f"{model_config.provider.upper()}_BASE_URL") or _default_base_url(model_config.provider)
    body = {
        "model": model_config.name,
        "messages": messages,
        "temperature": model_config.temperature,
        "max_tokens": model_config.max_tokens,
    }
    if model_config.json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=model_config.timeout_seconds) as resp:  # noqa: S310 - configured provider.
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM network error: {exc}") from exc
    data = json.loads(raw)
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def _default_base_url(provider: str) -> str:
    provider = provider.lower()
    if provider == "deepseek":
        return "https://api.deepseek.com"
    if provider == "openai":
        return "https://api.openai.com/v1"
    return os.environ.get("HIVEMIND_LLM_BASE_URL", "")
