from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentDAG:
    edges: dict[str, set[str]] = field(default_factory=dict)

    def add_edge(self, source: str, target: str) -> None:
        self.edges.setdefault(source, set()).add(target)
