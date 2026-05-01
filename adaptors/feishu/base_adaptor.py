from typing import Any
from adaptors.feishu.errors import FeishuNotConfiguredError
from adaptors.feishu.field_mapper import FieldMapper
from config.settings import get_settings


class FeishuBaseAdaptor:
    """Adaptor for Feishu Base records. Does not return fake business data."""
    def __init__(self, field_mapper: FieldMapper | None = None) -> None:
        self.settings = get_settings()
        self.field_mapper = field_mapper or FieldMapper()

    def _ensure_configured(self) -> None:
        if not self.settings.feishu_app_token:
            raise FeishuNotConfiguredError("FEISHU_APP_TOKEN is required for Base operations")

    def list_records(self, table_name: str, filter_expr: str | None = None, view_id: str | None = None, page_size: int = 100) -> list[dict[str, Any]]:
        self._ensure_configured(); self.field_mapper.get_table_id(table_name)
        raise NotImplementedError("TODO: call Feishu Base list records API")

    def batch_create_records(self, table_name: str, records: list[dict[str, Any]], idempotency_key: str | None = None) -> list[str]:
        self._ensure_configured()
        for record in records:
            self.field_mapper.validate_fields(table_name, record)
        raise NotImplementedError("TODO: call Feishu Base batch create API")

    def batch_update_records(self, table_name: str, updates: list[dict[str, Any]]) -> list[str]:
        self._ensure_configured()
        for update in updates:
            self.field_mapper.validate_fields(table_name, update.get("fields", update))
        raise NotImplementedError("TODO: call Feishu Base batch update API")

    def get_field_schema(self, table_name: str) -> dict[str, Any]:
        self.field_mapper.get_table_id(table_name)
        return {"fields": sorted(self.field_mapper.get_allowed_fields(table_name))}

    def validate_fields(self, table_name: str, fields: dict[str, Any]) -> None:
        self.field_mapper.validate_fields(table_name, fields)
