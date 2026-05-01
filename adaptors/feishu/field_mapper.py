from pathlib import Path
from typing import Any
import yaml
from adaptors.feishu.errors import BaseFieldValidationError, BaseTableNotFoundError
from config.settings import get_settings


class FieldMapper:
    """Validate table and field names against the configured Base whitelist."""
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or get_settings().feishu_base_table_config
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        with self.config_path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    @property
    def allowed_tables(self) -> dict[str, Any]:
        return self._config.get("allowed_tables", {})

    def get_table_id(self, table_name: str) -> str:
        table = self.allowed_tables.get(table_name)
        if not table:
            raise BaseTableNotFoundError(f"Table is not configured: {table_name}")
        return str(table.get("table_id", ""))

    def get_allowed_fields(self, table_name: str) -> set[str]:
        table = self.allowed_tables.get(table_name)
        if not table:
            raise BaseTableNotFoundError(f"Table is not configured: {table_name}")
        return set(table.get("fields", []))

    def validate_fields(self, table_name: str, fields: dict[str, Any]) -> None:
        unknown = set(fields) - self.get_allowed_fields(table_name)
        if unknown:
            raise BaseFieldValidationError(f"Fields are not allowed for {table_name}: {sorted(unknown)}")
