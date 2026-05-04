from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_runtime.enums import BaseTableName


class FieldManifest(BaseModel):
    """Field-level metadata for a Base table."""

    field_name: str
    field_key: str | None = None
    field_type: str
    required: bool = False
    description: str | None = None
    enum_values: list[str] = Field(default_factory=list)
    write_allowed: bool = False


class TableManifest(BaseModel):
    """Table-level metadata used by runtime and tool adapters."""

    table_name: BaseTableName
    display_name: str
    table_id: str
    view_id: str | None = None
    description: str
    primary_key_field: str | None = None
    fields: list[FieldManifest] = Field(default_factory=list)


class ProjectMember(BaseModel):
    """Project member metadata for prompt context and role resolution."""

    user_id: str | None = None
    name: str
    role_type: str
    responsibilities: list[str] = Field(default_factory=list)
    feishu_open_id: str | None = None
    email: str | None = None


class ProjectWorkspace(BaseModel):
    """Filesystem view for one project runtime workspace."""

    project_id: str
    project_name: str
    workspace_path: Path
    project_md_path: Path
    project_state_yaml_path: Path
    table_manifest_yaml_path: Path


class ProjectManifest(BaseModel):
    """Project configuration view; Base remains the source of truth."""

    project_id: str
    project_name: str
    description: str
    base_app_token: str
    default_time_range_days: int = 14
    tables: dict[BaseTableName, TableManifest]
    members: list[ProjectMember] = Field(default_factory=list)
    allowed_agents: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectRuntimeState(BaseModel):
    """Lightweight runtime snapshot metadata for a project."""

    project_id: str
    project_name: str
    description: str
    current_period: str | None = None
    start_date: date | None = None
    target_release_date: date | None = None
    table_descriptions: dict[str, str] = Field(default_factory=dict)
    role_descriptions: dict[str, str] = Field(default_factory=dict)
    latest_snapshot_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
