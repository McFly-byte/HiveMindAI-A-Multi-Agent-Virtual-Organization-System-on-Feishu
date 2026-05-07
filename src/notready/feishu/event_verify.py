from __future__ import annotations

from typing import Any

from feishu.client import FeishuConfig


class FeishuEventVerifyError(RuntimeError):
    pass


def verify_event_token(raw_event: dict[str, Any], config: FeishuConfig) -> None:
    """Verify Feishu event token when configured.

    This runtime supports normal, unencrypted callback bodies. If ENCRYPT_KEY is set,
    we fail fast with a clear message rather than pretending encrypted callbacks
    are supported.
    """
    if config.encrypt_key:
        raise FeishuEventVerifyError("当前版本尚未实现 encrypt_key 加密事件解密；请先关闭飞书事件加密或补充解密实现。")
    expected = config.verification_token
    if not expected:
        return
    token = None
    if isinstance(raw_event.get("header"), dict):
        token = raw_event["header"].get("token")
    token = token or raw_event.get("token")
    if token != expected:
        raise FeishuEventVerifyError("飞书事件 verification token 不匹配。")
