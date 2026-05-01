from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


@asynccontextmanager
async def agent_run_lock(lock_key: str) -> AsyncIterator[None]:
    """Placeholder run lock to prevent duplicate cron triggers in future."""
    _ = lock_key
    yield
