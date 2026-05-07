from __future__ import annotations


def normalize_error(error: Exception) -> dict[str, str]:
    return {"type": error.__class__.__name__, "message": str(error)}
