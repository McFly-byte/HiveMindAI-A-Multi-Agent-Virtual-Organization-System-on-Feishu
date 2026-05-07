from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class AsyncRateLimiter:
    min_interval_seconds: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_call: float = 0.0

    async def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = self.min_interval_seconds - (now - self._last_call)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_call = loop.time()
