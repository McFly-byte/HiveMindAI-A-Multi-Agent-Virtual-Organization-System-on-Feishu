from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from agent_hive.events.models import HiveEvent


EventEmitFn = Callable[[HiveEvent], Awaitable[None]]


class EventSource(Protocol):
    name: str

    async def run(self, emit: EventEmitFn) -> None:
        ...

    async def stop(self) -> None:
        ...
