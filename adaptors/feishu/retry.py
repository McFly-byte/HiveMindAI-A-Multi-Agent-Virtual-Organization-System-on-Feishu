from collections.abc import Callable
from typing import TypeVar
T = TypeVar("T")


def retry_call(func: Callable[[], T], max_retries: int = 3) -> T:
    """Small retry placeholder; add backoff and Feishu error handling later."""
    last_error: Exception | None = None
    for _ in range(max_retries):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    assert last_error is not None
    raise last_error
