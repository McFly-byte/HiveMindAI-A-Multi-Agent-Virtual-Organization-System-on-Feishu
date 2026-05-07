from __future__ import annotations

from agent_hive.config.models import MemoryPolicy


def can_write_memory_type(policy: MemoryPolicy, memory_type: str) -> bool:
    return memory_type in policy.write_types
