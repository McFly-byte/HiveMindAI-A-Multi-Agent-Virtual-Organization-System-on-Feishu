from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")


async def retry_async(fn: Callable[[], Awaitable[T]], *, max_attempts: int = 3, backoff_seconds: float = 0.5) -> T:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - re-raised after bounded retries.
            last_error = exc
            if attempt + 1 < max_attempts:
                await asyncio.sleep(backoff_seconds * (attempt + 1))
    assert last_error is not None
    raise last_error
