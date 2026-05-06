from __future__ import annotations

from pydantic import BaseModel


class QualityGateResult(BaseModel):
    passed: bool
    reason: str = ""


class QualityGate:
    async def verify(self, payload: dict) -> QualityGateResult:
        return QualityGateResult(passed=True)
