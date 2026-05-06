from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC.resolve()) not in sys.path:
    sys.path.insert(0, str(_SRC.resolve()))

from agent_runtime.loaders import load_project_manifest
from agent_runtime.mvp.project_env import expand_env_value
from agent_runtime.project_state import FieldManifest, ProjectManifest
from feishu_adapter.feishu_client import FeishuApiError, feishu_request
from tool_integration.loader import load_dotenv_if_present


FIELD_TYPE_MAP: dict[str, int] = {
    "text": 1,
    "number": 2,
    "single_select": 3,
    "multi_select": 4,
    "date": 5,
    "checkbox": 7,
    "user": 11,
    "phone": 13,
    "url": 15,
    "link": 18,
}


def _load_manifest(project_id: str) -> ProjectManifest:
    raw = load_project_manifest(_ROOT / "projects" / project_id).model_dump(mode="json")
    return ProjectManifest.model_validate(expand_env_value(raw))


def _field_type_id(field: FieldManifest) -> int:
    if field.field_type not in FIELD_TYPE_MAP:
        raise ValueError(f"unsupported field_type={field.field_type!r} for {field.field_name}")
    return FIELD_TYPE_MAP[field.field_type]


def _list_field_map(app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    data = feishu_request(
        "GET",
        f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        queries={"page_size": 500},
    )
    items = data.get("items", [])
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("field_name")): item
        for item in items
        if isinstance(item, dict) and item.get("field_name")
    }


def _field_body(field: FieldManifest) -> dict[str, Any]:
    ftype = _field_type_id(field)
    body: dict[str, Any] = {
        "field_name": field.field_name,
        "type": ftype,
    }
    if field.enum_values and ftype in (3, 4):
        body["property"] = {"options": [{"name": value, "color": 0} for value in field.enum_values]}
    return body


def _create_field(app_token: str, table_id: str, field: FieldManifest, dry_run: bool) -> None:
    body = _field_body(field)
    if dry_run:
        print(f"create field (dry-run): {field.field_name} type={field.field_type}")
        return
    feishu_request(
        "POST",
        f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        body=body,
    )
    print(f"created field: {field.field_name} type={field.field_type}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create missing Feishu Bitable fields from table_manifest.yaml without deleting records.")
    parser.add_argument("--project-id", default="enterprise_rag")
    parser.add_argument(
        "--tables",
        nargs="*",
        default=["Tasks", "Risks", "FollowUps", "WeeklyReports", "AgentRuns"],
        help="Table keys to check/create. Defaults to writeback tables.",
    )
    parser.add_argument("--all-fields", action="store_true", help="Create every manifest field, not only write_allowed fields.")
    parser.add_argument("--dry-run", action="store_true", help="Print missing fields but do not create them.")
    parser.add_argument("--yes", action="store_true", help="Actually create missing fields.")
    args = parser.parse_args()

    load_dotenv_if_present(_ROOT)
    manifest = _load_manifest(args.project_id)
    selected = set(args.tables)
    created = 0
    missing_total = 0

    if not args.dry_run and not args.yes:
        print("This will create missing fields in Feishu Base without deleting records.")
        print("Re-run with --yes to apply, or --dry-run to preview.")
        return 2

    for table_name, table in manifest.tables.items():
        key = str(table_name)
        if selected and key not in selected:
            continue
        actual = _list_field_map(manifest.base_app_token, table.table_id)
        desired = [
            field
            for field in table.fields
            if args.all_fields or field.write_allowed
        ]
        missing = [field for field in desired if field.field_name not in actual]
        type_mismatches = [
            (field, actual[field.field_name])
            for field in desired
            if field.field_name in actual and int(actual[field.field_name].get("type", 0)) != _field_type_id(field)
        ]
        for field, remote in type_mismatches:
            print(
                f"{key}: warning type mismatch {field.field_name} "
                f"manifest={field.field_type}/{_field_type_id(field)} remote={remote.get('type')}"
            )
        if not missing:
            print(f"{key}: ok")
            continue
        print(f"{key}: missing {', '.join(field.field_name for field in missing)}")
        missing_total += len(missing)
        for field in missing:
            _create_field(manifest.base_app_token, table.table_id, field, args.dry_run)
            if not args.dry_run:
                created += 1

    print(f"Done. missing={missing_total}, created={created}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FeishuApiError as exc:
        print(f"FeishuApiError: {exc}", file=sys.stderr)
        raise SystemExit(1)
