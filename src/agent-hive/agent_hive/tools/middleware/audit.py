from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolAuditLog:
    records: list[dict[str, Any]] = field(default_factory=list)

    def append(self, record: dict[str, Any]) -> None:
        self.records.append(record)
