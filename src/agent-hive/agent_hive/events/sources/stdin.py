from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.base import EventEmitFn


class StdinEventSource:
    """Reads newline-delimited HiveEvent JSON from stdin.

    Useful for local daemon smoke tests:
    ``echo '{"event_type":"manual","project_id":"p1","payload":{}}' | ... serve --stdin``.
    """

    name = "stdin"

    def __init__(self) -> None:
        self._stopped = asyncio.Event()

    async def run(self, emit: EventEmitFn) -> None:
        while not self._stopped.is_set():
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                return
            text = line.strip()
            if not text:
                continue
            event = _event_from_json(text)
            await emit(event)

    async def stop(self) -> None:
        self._stopped.set()


def _event_from_json(text: str) -> HiveEvent:
    data: Any = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("stdin event must be a JSON object")
    return HiveEvent.model_validate(data)
