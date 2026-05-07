from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class BitableSchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class FieldDef:
    field_name: str
    field_type: str
    required: bool = False
    write_allowed: bool = True
    enum_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableDef:
    key: str
    display_name: str
    table_id: str
    view_id: str | None
    description: str
    primary_key_field: str
    fields: tuple[FieldDef, ...]

    @property
    def writable_field_names(self) -> set[str]:
        return {f.field_name for f in self.fields if f.write_allowed}

    @property
    def required_writable_field_names(self) -> set[str]:
        return {f.field_name for f in self.fields if f.required and f.write_allowed}

    def field(self, name: str) -> FieldDef | None:
        for f in self.fields:
            if f.field_name == name:
                return f
        return None


@dataclass(frozen=True)
class BitableSchema:
    app_token: str
    tables: dict[str, TableDef]

    def get_table(self, key: str) -> TableDef:
        raw = str(key or '').strip()
        if raw in self.tables:
            return self.tables[raw]
        lowered = raw.lower()
        for name, table in self.tables.items():
            if name.lower() == lowered or table.display_name.lower() == lowered:
                return table
        raise BitableSchemaError(f'未知多维表格表：{key}')

    def llm_catalog(self) -> dict[str, Any]:
        return {
            'app_token_source': 'FEISHU_BITABLE_APP_TOKEN 或 schema.app_token',
            'tables': {
                key: {
                    'display_name': table.display_name,
                    'table_id': table.table_id,
                    'description': table.description,
                    'primary_key_field': table.primary_key_field,
                    'writable_fields': [
                        {
                            'field_name': f.field_name,
                            'field_type': f.field_type,
                            'required': f.required,
                            **({'enum_values': list(f.enum_values)} if f.enum_values else {}),
                        }
                        for f in table.fields
                        if f.write_allowed
                    ],
                }
                for key, table in self.tables.items()
            },
        }


def load_bitable_schema(path: str | Path | None = None, *, app_token: str | None = None) -> BitableSchema:
    schema_path = Path(path or os.getenv('FEISHU_BITABLE_SCHEMA_PATH', 'config/bitable_schema.yaml'))
    if not schema_path.exists():
        raise BitableSchemaError(f'找不到多维表格 schema yaml：{schema_path}')
    data = yaml.safe_load(schema_path.read_text(encoding='utf-8')) or {}
    if not isinstance(data, dict):
        raise BitableSchemaError('多维表格 schema yaml 根节点必须是 object')
    resolved_app_token = str(app_token or data.get('app_token') or os.getenv('FEISHU_BITABLE_APP_TOKEN', '')).strip()
    if not resolved_app_token:
        raise BitableSchemaError('缺少多维表格 app_token：请设置 FEISHU_BITABLE_APP_TOKEN，或在 yaml 根节点写 app_token')
    raw_tables = data.get('tables')
    if not isinstance(raw_tables, dict) or not raw_tables:
        raise BitableSchemaError('多维表格 schema yaml 缺少 tables')
    tables: dict[str, TableDef] = {}
    for key, raw_table in raw_tables.items():
        if not isinstance(raw_table, dict):
            continue
        fields: list[FieldDef] = []
        for raw_field in raw_table.get('fields') or []:
            if not isinstance(raw_field, dict):
                continue
            fields.append(FieldDef(
                field_name=str(raw_field.get('field_name') or '').strip(),
                field_type=str(raw_field.get('field_type') or '').strip(),
                required=bool(raw_field.get('required', False)),
                write_allowed=bool(raw_field.get('write_allowed', True)),
                enum_values=tuple(str(x) for x in (raw_field.get('enum_values') or []) if str(x)),
            ))
        table_id = str(raw_table.get('table_id') or '').strip()
        if not table_id:
            raise BitableSchemaError(f'表 {key} 缺少 table_id')
        tables[str(key)] = TableDef(
            key=str(key),
            display_name=str(raw_table.get('display_name') or key),
            table_id=table_id,
            view_id=raw_table.get('view_id'),
            description=str(raw_table.get('description') or ''),
            primary_key_field=str(raw_table.get('primary_key_field') or ''),
            fields=tuple(fields),
        )
    return BitableSchema(app_token=resolved_app_token, tables=tables)


def clean_record_fields(table: TableDef, fields: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    if not isinstance(fields, dict):
        raise BitableSchemaError('fields 必须是 object')
    writable = table.writable_field_names
    cleaned: dict[str, Any] = {}
    unknown: list[str] = []
    forbidden: list[str] = []
    for key, value in fields.items():
        name = str(key)
        field = table.field(name)
        if field is None:
            unknown.append(name)
            continue
        if not field.write_allowed:
            forbidden.append(name)
            continue
        if value is None or value == '':
            continue
        if field.enum_values and str(value) not in field.enum_values:
            raise BitableSchemaError(f'字段 {name} 的值 {value!r} 不在枚举范围：{list(field.enum_values)}')
        cleaned[name] = value
    if strict and unknown:
        raise BitableSchemaError(f'表 {table.key} 不存在字段：{unknown}')
    if strict and forbidden:
        raise BitableSchemaError(f'表 {table.key} 字段不允许写入：{forbidden}')
    missing = [name for name in table.required_writable_field_names if name not in cleaned]
    if missing:
        raise BitableSchemaError(f'表 {table.key} 缺少必填可写字段：{missing}')
    return cleaned
