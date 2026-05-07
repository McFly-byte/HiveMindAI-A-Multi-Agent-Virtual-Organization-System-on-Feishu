from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests


class JsonLLMClient(Protocol):
    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        ...


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_sec: float = 30.0


def load_dotenv(path: str | Path = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_llm_client(env_file: str | Path = ".env") -> JsonLLMClient:
    load_dotenv(env_file)
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise LLMError("缺少 LLM_API_KEY：当前版本要求真实 LLM")
    default_base = {
        "deepseek": "https://api.deepseek.com",
        "openai": "https://api.openai.com/v1",
        "openai_compatible": "http://127.0.0.1:11434/v1",
    }.get(provider, "https://api.deepseek.com")
    base_url = os.getenv("LLM_BASE_URL", default_base).strip().rstrip("/")
    timeout = float(os.getenv("LLM_TIMEOUT_SEC", "30"))
    return OpenAICompatibleLLMClient(LLMConfig(provider, model, api_key, base_url, timeout))


class OpenAICompatibleLLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.base_url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}, json=payload, timeout=self.config.timeout_sec)
        except requests.RequestException as exc:
            raise LLMError(f"LLM 请求失败：{exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"LLM 返回格式异常：{resp.text[:500]}") from exc
        obj = _parse_json_object(content)
        _validate_schema(obj, schema)
        return obj


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fenced:
            value = json.loads(fenced.group(1))
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end < start:
                raise LLMError(f"LLM 没有返回 JSON 对象：{content[:300]}")
            value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise LLMError("LLM JSON 输出不是 object")
    return value


def _validate_schema(obj: dict[str, Any], schema: dict[str, Any]) -> None:
    for key in schema.get("required", []):
        if key not in obj:
            raise LLMError(f"LLM JSON 缺少字段：{key}")
    for key, spec in schema.get("properties", {}).items():
        if key not in obj:
            continue
        expected = spec.get("type")
        value = obj[key]
        if expected == "string" and not isinstance(value, str):
            raise LLMError(f"LLM JSON 字段 {key} 应为 string")
        if expected == "integer" and not isinstance(value, int):
            raise LLMError(f"LLM JSON 字段 {key} 应为 integer")
        if expected == "number" and not isinstance(value, (int, float)):
            raise LLMError(f"LLM JSON 字段 {key} 应为 number")
        if expected == "array" and not isinstance(value, list):
            raise LLMError(f"LLM JSON 字段 {key} 应为 array")
        if expected == "object" and not isinstance(value, dict):
            raise LLMError(f"LLM JSON 字段 {key} 应为 object")
