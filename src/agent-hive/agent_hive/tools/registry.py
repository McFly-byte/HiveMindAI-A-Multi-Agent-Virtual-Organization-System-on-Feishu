from __future__ import annotations

from agent_hive.tools.providers.base import ToolProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ToolProvider] = {}

    def register(self, provider: ToolProvider) -> None:
        self._providers[provider.provider_name] = provider

    def get(self, provider_name: str) -> ToolProvider:
        if provider_name not in self._providers:
            raise KeyError(f"unknown tool provider: {provider_name}")
        return self._providers[provider_name]

    def names(self) -> list[str]:
        return sorted(self._providers)
