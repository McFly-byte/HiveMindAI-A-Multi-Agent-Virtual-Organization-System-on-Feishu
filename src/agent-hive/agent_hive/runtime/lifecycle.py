from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from agent_hive.runtime.session import AgentSession, StepRecord


@asynccontextmanager
async def runtime_step(session: AgentSession, *, kind: str, name: str, input_summary: str = "") -> AsyncIterator[StepRecord]:
    step = session.add_step(kind, name, input_summary)
    try:
        yield step
    finally:
        if step.ended_at is None:
            step.finish()
