from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_runtime.enums import BaseTableName, EvidenceSourceType


class BaseFieldRef(BaseModel):
    """Reference to a field in Feishu Base metadata."""

    table_name: BaseTableName
    field_name: str
    field_key: str | None = None
    field_type: str | None = None


class BaseRecordRef(BaseModel):
    """Reference to a Base record used as input or output."""

    table_name: BaseTableName
    record_id: str
    display_name: str | None = None
    url: str | None = None


class BaseCellRef(BaseModel):
    """Reference to a specific Base cell snapshot."""

    table_name: BaseTableName
    record_id: str
    field_name: str
    field_key: str | None = None
    value_snapshot: Any | None = None


class EvidenceRef(BaseModel):
    """Traceable evidence supporting an agent conclusion or write proposal."""

    evidence_id: str
    source_type: EvidenceSourceType
    summary: str
    table_name: BaseTableName | None = None
    record_id: str | None = None
    field_name: str | None = None
    value_snapshot: Any | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RecordReadSet(BaseModel):
    """Summary of Base records read during an agent run."""

    table_name: BaseTableName
    record_ids: list[str] = Field(default_factory=list)
    filter_summary: str | None = None
    fields: list[str] = Field(default_factory=list)


class RecordWriteSet(BaseModel):
    """Summary of write operations proposed or executed during a run."""

    table_name: BaseTableName
    record_ids: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    operation: Literal["create", "update", "delete", "noop"]


class RecordPatch(BaseModel):
    """Structured update proposal for a Base record."""

    table_name: BaseTableName
    record_id: str
    fields: dict[str, Any]
    idempotency_key: str | None = None
    reason: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class RecordCreate(BaseModel):
    """Structured create proposal for a Base record."""

    table_name: BaseTableName
    fields: dict[str, Any]
    idempotency_key: str | None = None
    reason: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class OutputRecordRef(BaseModel):
    """Reference to a record produced by a runtime action."""

    table_name: BaseTableName
    record_id: str
    operation: Literal["created", "updated", "skipped", "failed"]
    summary: str | None = None


class IdempotencyKey(BaseModel):
    """Business key used to prevent duplicate writes."""

    project_id: str
    table_name: BaseTableName
    business_key: str
    period: str | None = None
