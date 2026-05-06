from __future__ import annotations


def estimate_chars(parts: list[str]) -> int:
    return sum(len(item) for item in parts)


def trim_to_budget(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."
