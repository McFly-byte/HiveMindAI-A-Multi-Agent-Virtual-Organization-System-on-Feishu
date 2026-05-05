# tools/feishu_client.py
from __future__ import annotations

import json
import os
import threading
from typing import Any


_LOCK = threading.Lock()
_LARK: Any | None = None
_CLIENT: Any | None = None


class FeishuApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        msg: str | None = None,
        log_id: str | None = None,
        raw: Any | None = None,
    ):
        self.code = code
        self.msg = msg
        self.log_id = log_id
        self.raw = raw

        detail = message
        if code is not None:
            detail += f" code={code}"
        if msg:
            detail += f" msg={msg}"
        if log_id:
            detail += f" log_id={log_id}"

        super().__init__(detail)


def get_feishu_client() -> tuple[Any, Any]:
    global _LARK, _CLIENT

    if _LARK is not None and _CLIENT is not None:
        return _LARK, _CLIENT

    with _LOCK:
        if _LARK is not None and _CLIENT is not None:
            return _LARK, _CLIENT

        try:
            import lark_oapi as lark
        except Exception as e:
            raise RuntimeError("lark-oapi is not installed. Run: uv add lark-oapi") from e

        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")

        if not app_id or not app_secret:
            raise RuntimeError("missing FEISHU_APP_ID / FEISHU_APP_SECRET in .env")

        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        _LARK = lark
        _CLIENT = client
        return _LARK, _CLIENT


def _decode_response_body(resp: Any) -> dict[str, Any]:
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None

    if not content:
        return {}

    try:
        return json.loads(content.decode("utf-8"))
    except Exception as e:
        text = content.decode("utf-8", errors="replace")
        raise FeishuApiError("invalid Feishu response JSON", raw=text) from e


def feishu_request(
    method: str,
    uri: str,
    *,
    queries: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    user_access_token: str | None = None,
) -> dict[str, Any]:
    lark, client = get_feishu_client()

    http_method = getattr(lark.HttpMethod, method.upper())

    req_builder = (
        lark.BaseRequest.builder()
        .http_method(http_method)
        .uri(uri)
    )

    headers: dict[str, str] = {}
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"

    if user_access_token:
        headers["Authorization"] = f"Bearer {user_access_token}"
    else:
        req_builder = req_builder.token_types({lark.AccessTokenType.TENANT})

    if headers:
        req_builder = req_builder.headers(headers)

    if queries:
        req_builder = req_builder.queries(
            [(k, str(v)) for k, v in queries.items() if v is not None and v != ""]
        )

    if body is not None:
        req_builder = req_builder.body(body)

    resp = client.request(req_builder.build())
    raw_body = _decode_response_body(resp)

    code = raw_body.get("code", getattr(resp, "code", None))
    msg = raw_body.get("msg", getattr(resp, "msg", None))
    log_id = resp.get_log_id() if hasattr(resp, "get_log_id") else None

    if not resp.success() or code not in (None, 0):
        raise FeishuApiError(
            "Feishu API request failed",
            code=code,
            msg=msg,
            log_id=log_id,
            raw=raw_body,
        )

    return raw_body.get("data") or {}