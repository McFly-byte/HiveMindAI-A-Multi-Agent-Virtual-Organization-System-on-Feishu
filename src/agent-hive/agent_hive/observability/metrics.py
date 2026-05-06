from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CounterSet:
    values: dict[str, int] = field(default_factory=dict)

    def inc(self, name: str, value: int = 1) -> None:
        self.values[name] = self.values.get(name, 0) + value
