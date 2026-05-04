from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_runtime.base_refs import EvidenceRef, RecordCreate, RecordPatch
from agent_runtime.enums import ActionType, QualityGateStatus


class QualityCheckItem(BaseModel):
    """Single check result produced by a QualityGate implementation."""

    check_name: str
    passed: bool
    severity: str = "error"
    reason: str | None = None


class QualityGateRequest(BaseModel):
    """Structured verification request before writeback actions."""

    run_id: str
    project_id: str
    action_type: ActionType
    payload: dict[str, Any]
    proposed_creates: list[RecordCreate] = Field(default_factory=list)
    proposed_patches: list[RecordPatch] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    schema_name: str
    requested_at: datetime = Field(default_factory=datetime.utcnow)


class QualityGateResult(BaseModel):
    """Result of QualityGate verification."""

    status: QualityGateStatus
    passed: bool
    checks: list[QualityCheckItem] = Field(default_factory=list)
    sanitized_payload: dict[str, Any] | None = None
    blocked_reason: str | None = None
    suggested_next_action: str | None = None

    @classmethod
    def passed_result(
        cls,
        checks: list[QualityCheckItem] | None = None,
        sanitized_payload: dict[str, Any] | None = None,
    ) -> "QualityGateResult":
        return cls(
            status=QualityGateStatus.PASSED,
            passed=True,
            checks=checks or [],
            sanitized_payload=sanitized_payload,
        )

    @classmethod
    def failed_result(
        cls,
        blocked_reason: str,
        suggested_next_action: str | None = None,
        checks: list[QualityCheckItem] | None = None,
    ) -> "QualityGateResult":
        return cls(
            status=QualityGateStatus.FAILED,
            passed=False,
            checks=checks or [],
            blocked_reason=blocked_reason,
            suggested_next_action=suggested_next_action,
        )
