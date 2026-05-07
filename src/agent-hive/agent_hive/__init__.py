"""New HiveMindAI multi-agent runtime.

This package is intentionally independent from the legacy ``agent_runtime``.
Only the existing memory store and Feishu adapter are used as provider
dependencies.
"""

from agent_hive.config.loader import load_runtime_config
from agent_hive.runtime.hive_runtime import HiveRuntime

__all__ = ["HiveRuntime", "load_runtime_config"]
