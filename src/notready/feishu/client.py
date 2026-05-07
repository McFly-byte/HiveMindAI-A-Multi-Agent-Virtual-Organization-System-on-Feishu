from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


class FeishuAPIError(RuntimeError):
    pass


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


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    api_base: str = "https://open.feishu.cn/open-apis"
    verification_token: str = ""
    encrypt_key: str = ""
    timeout_sec: float = 20.0
    default_receive_id_type: str = "chat_id"
    reply_in_thread: bool = False
    bitable_app_token: str = ""
    bitable_table_id: str = ""
    bitable_table_aliases: dict[str, tuple[str, str]] | None = None

    @property
    def ready(self) -> bool:
        return bool(self.app_id and self.app_secret)


def load_feishu_config(env_file: str | Path = ".env") -> FeishuConfig:
    load_dotenv(env_file)
    aliases: dict[str, tuple[str, str]] = {}
    for key, value in os.environ.items():
        if not key.startswith("FEISHU_BITABLE_") or not key.endswith("_APP_TOKEN"):
            continue
        alias = key.removeprefix("FEISHU_BITABLE_").removesuffix("_APP_TOKEN").lower()
        table_id = os.getenv(f"FEISHU_BITABLE_{alias.upper()}_TABLE_ID", "").strip()
        app_token = value.strip()
        if alias and app_token and table_id:
            aliases[alias] = (app_token, table_id)
    config = FeishuConfig(
        app_id=os.getenv("FEISHU_APP_ID", "").strip(),
        app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
        api_base=os.getenv("FEISHU_API_BASE", "https://open.feishu.cn/open-apis").strip().rstrip("/"),
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip(),
        encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", "").strip(),
        timeout_sec=float(os.getenv("FEISHU_TIMEOUT_SEC", "20")),
        default_receive_id_type=os.getenv("FEISHU_DEFAULT_RECEIVE_ID_TYPE", "chat_id").strip() or "chat_id",
        reply_in_thread=os.getenv("FEISHU_REPLY_IN_THREAD", "false").strip().lower() in {"1", "true", "yes", "on"},
        bitable_app_token=os.getenv("FEISHU_BITABLE_APP_TOKEN", "").strip(),
        bitable_table_id=os.getenv("FEISHU_BITABLE_TABLE_ID", "").strip(),
        bitable_table_aliases=aliases,
    )
    if not config.ready:
        raise FeishuAPIError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET：当前版本只使用真实飞书应用 OpenAPI")
    return config


class FeishuClient:
    def __init__(self, config: FeishuConfig | None = None) -> None:
        self.config = config or load_feishu_config()
        self._tenant_access_token: str | None = None
        self._tenant_token_expires_at = 0.0

    @property
    def ready(self) -> bool:
        return self.config.ready

    def tenant_access_token(self) -> str:
        if not self.ready:
            raise FeishuAPIError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        now = time.time()
        if self._tenant_access_token and now < self._tenant_token_expires_at - 120:
            return self._tenant_access_token
        data = self._request_without_auth(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            json_body={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
        )
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuAPIError(f"tenant_access_token 响应缺少 token：{data}")
        self._tenant_access_token = str(token)
        self._tenant_token_expires_at = now + int(data.get("expire", 7200))
        return self._tenant_access_token

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.tenant_access_token()
        try:
            resp = requests.request(
                method.upper(),
                self._url(path),
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
                timeout=self.config.timeout_sec,
            )
        except requests.RequestException as exc:
            raise FeishuAPIError(f"飞书 OpenAPI 请求失败：{exc}") from exc
        return self._parse_response(resp)

    def send_text_message(self, *, receive_id: str, text: str, receive_id_type: str | None = None, uuid: str | None = None) -> dict[str, Any]:
        receive_id_type = receive_id_type or infer_receive_id_type(receive_id, self.config.default_receive_id_type)
        body: dict[str, Any] = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        if uuid:
            body["uuid"] = uuid
        return self.request("POST", "/im/v1/messages", params={"receive_id_type": receive_id_type}, json_body=body)

    def reply_text_message(self, *, message_id: str, text: str, uuid: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        if self.config.reply_in_thread:
            body["reply_in_thread"] = True
        if uuid:
            body["uuid"] = uuid
        return self.request("POST", f"/im/v1/messages/{message_id}/reply", json_body=body)

    def search_users(self, *, query: str, page_size: int = 10, user_id_type: str = "user_id") -> dict[str, Any]:
        return self.request(
            "POST",
            "/contact/v3/users/search",
            params={"user_id_type": user_id_type},
            json_body={"query": query, "page_size": page_size},
        )

    def create_chat(self, *, user_id_list: list[str], name: str = "私聊", user_id_type: str = "user_id", chat_mode: str = "p2p") -> dict[str, Any]:
        return self.request("POST", "/im/v1/chats", params={"user_id_type": user_id_type}, json_body={"chat_mode": chat_mode, "name": name, "user_id_list": user_id_list})

    def create_bitable_record(self, *, app_token: str, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", json_body={"fields": fields})

    def list_bitable_records(self, *, app_token: str, table_id: str, page_size: int = 20, page_token: str | None = None, field_names: list[str] | None = None, filter_expr: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        if field_names:
            params["field_names"] = json.dumps(field_names, ensure_ascii=False)
        if filter_expr:
            params["filter"] = filter_expr
        return self.request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", params=params)

    def update_bitable_record(self, *, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}", json_body={"fields": fields})

    def resolve_bitable(self, table: str | None = None, app_token: str | None = None, table_id: str | None = None) -> tuple[str, str, str]:
        if app_token and table_id:
            return app_token, table_id, table or "custom"
        table_key = (table or "default").strip().lower()
        aliases = self.config.bitable_table_aliases or {}
        if table_key in aliases:
            a, t = aliases[table_key]
            return a, t, table_key
        if self.config.bitable_app_token and self.config.bitable_table_id:
            return self.config.bitable_app_token, self.config.bitable_table_id, table_key
        raise FeishuAPIError("缺少 Bitable 配置：请在工具参数传 app_token/table_id，或设置 FEISHU_BITABLE_APP_TOKEN/FEISHU_BITABLE_TABLE_ID")

    def _request_without_auth(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = requests.request(method.upper(), self._url(path), json=json_body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=self.config.timeout_sec)
        except requests.RequestException as exc:
            raise FeishuAPIError(f"飞书 OpenAPI 请求失败：{exc}") from exc
        return self._parse_response(resp)

    def _parse_response(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise FeishuAPIError(f"飞书 OpenAPI HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise FeishuAPIError(f"飞书 OpenAPI 返回非 JSON：{resp.text[:500]}") from exc
        code = data.get("code", 0)
        if code not in (0, None):
            raise FeishuAPIError(f"飞书 OpenAPI code={code}, msg={data.get('msg') or data.get('error')}, data={data}")
        return data

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.config.api_base}/{path.lstrip('/')}"


def infer_receive_id_type(receive_id: str, default: str = "chat_id") -> str:
    rid = str(receive_id or "")
    if rid.startswith("oc_") or rid.startswith("g_"):
        return "chat_id"
    if rid.startswith("ou_"):
        return "open_id"
    if rid.startswith("on_"):
        return "union_id"
    return default
