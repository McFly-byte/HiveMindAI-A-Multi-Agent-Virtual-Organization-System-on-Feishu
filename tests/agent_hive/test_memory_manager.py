from __future__ import annotations

import asyncio
from pathlib import Path

from agent_hive.memory.manager import MemoryManager


def test_memory_manager_direct_write_and_search(tmp_path: Path) -> None:
    async def _run() -> None:
        memory = MemoryManager(tmp_path / "memory.db")
        try:
            await memory.write(
                content="Direct memory is available to business agents.",
                agent_id="risk_analysis",
                project_id="p1",
                tags=["test"],
            )
            results = await memory.search(
                query="Direct memory business agents",
                agent_id="risk_analysis",
                project_id="p1",
                top_k=3,
                scopes=["self", "project"],
            )
            assert results
            assert "Direct memory" in results[0]["content"]
        finally:
            await memory.close()

    asyncio.run(_run())
