from __future__ import annotations


def require_keys(payload: dict, keys: list[str]) -> list[str]:
    return [key for key in keys if key not in payload]
