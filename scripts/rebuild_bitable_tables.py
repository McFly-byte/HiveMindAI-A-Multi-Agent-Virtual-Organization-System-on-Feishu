from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC.resolve()) not in sys.path:
    sys.path.insert(0, str(_SRC.resolve()))

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

AUTO_FIELD_TYPES = {1001, 1002, 1003, 1004, 1005}


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root must be an object: {path}")
    return raw


def _field_type_id(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        key = v.strip().lower()
        if key in FIELD_TYPE_MAP:
            return FIELD_TYPE_MAP[key]
    raise ValueError(f"unsupported field_type: {v!r}")


def _list_all_records(app_token: str, table_id: str) -> list[str]:
    ids: list[str] = []
    page_token = ""
    while True:
        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            queries={"page_size": 500, "page_token": page_token},
            body={},
        )
        items = data.get("items", [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("record_id"), str):
                    ids.append(item["record_id"])
        has_more = bool(data.get("has_more", False))
        page_token = str(data.get("page_token", ""))
        if not has_more:
            break
    return ids


def _batch_delete_records(app_token: str, table_id: str, record_ids: list[str], dry_run: bool) -> int:
    deleted = 0
    for i in range(0, len(record_ids), 500):
        chunk = record_ids[i : i + 500]
        if not dry_run:
            feishu_request(
                "POST",
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                body={"records": chunk},
            )
        deleted += len(chunk)
    return deleted


def _list_fields(app_token: str, table_id: str) -> list[dict[str, Any]]:
    data = feishu_request(
        "GET",
        f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        queries={"page_size": 500},
    )
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def _rebuild_table(app_token: str, table_name: str, spec: dict[str, Any], dry_run: bool) -> None:
    table_id = spec.get("table_id")
    if not isinstance(table_id, str) or not table_id:
        raise ValueError(f"table {table_name} missing table_id")

    desired_fields = spec.get("fields", [])
    if not isinstance(desired_fields, list):
        raise ValueError(f"table {table_name} fields must be a list")

    print(f"\n=== {table_name} ({table_id}) ===")

    record_ids = _list_all_records(app_token, table_id)
    deleted_records = _batch_delete_records(app_token, table_id, record_ids, dry_run)
    print(f"records deleted: {deleted_records}")

    old_fields = _list_fields(app_token, table_id)
    deletable = [
        f for f in old_fields
        if isinstance(f, dict)
        and isinstance(f.get("field_id"), str)
        and int(f.get("type", 0)) not in AUTO_FIELD_TYPES
    ]

    for field in deletable:
        field_id = str(field["field_id"])
        field_name = str(field.get("field_name", ""))
        if dry_run:
            print(f"delete field (dry-run): {field_name} [{field_id}]")
            continue
        feishu_request(
            "DELETE",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
        )
        print(f"delete field: {field_name} [{field_id}]")

    for item in desired_fields:
        if not isinstance(item, dict):
            continue
        field_name = item.get("field_name")
        if not isinstance(field_name, str) or not field_name.strip():
            continue
        ftype = _field_type_id(item.get("field_type"))

        prop: dict[str, Any] = {}
        enum_values = item.get("enum_values")
        if isinstance(enum_values, list) and ftype in (3, 4):
            prop["options"] = [{"name": str(v), "color": 0} for v in enum_values]

        body = {
            "field_name": field_name,
            "type": ftype,
        }
        if prop:
            body["property"] = prop

        if dry_run:
            print(f"create field (dry-run): {field_name} type={ftype}")
            continue

        feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            body=body,
        )
        print(f"create field: {field_name} type={ftype}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Feishu Bitable table fields from YAML and purge records.")
    parser.add_argument("--manifest", default="projects/enterprise_rag/table_manifest.yaml", help="Path to table manifest yaml")
    parser.add_argument("--app-token", default="", help="Override app token. If omitted, use FEISHU_BASE_APP_TOKEN")
    parser.add_argument("--tables", nargs="*", default=[], help="Only rebuild these table keys in YAML")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write")
    parser.add_argument("--yes", action="store_true", help="Skip safety confirmation")
    args = parser.parse_args()

    load_dotenv_if_present(_ROOT)

    app_token = args.app_token.strip() or __import__("os").getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise RuntimeError("missing app token: --app-token or FEISHU_BASE_APP_TOKEN")

    manifest_path = (_ROOT / args.manifest).resolve()
    manifest = _read_yaml(manifest_path)
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("manifest.tables must be a mapping")

    selected = set(args.tables)
    targets = [(name, spec) for name, spec in tables.items() if not selected or name in selected]
    if not targets:
        raise ValueError("no matched tables to rebuild")

    if not args.dry_run and not args.yes:
        names = ", ".join(name for name, _ in targets)
        print(f"About to purge records and rebuild fields for: {names}")
        print("Re-run with --yes to confirm.")
        return 2

    for name, spec in targets:
        if not isinstance(spec, dict):
            continue
        _rebuild_table(app_token, name, spec, args.dry_run)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FeishuApiError as e:
        print(f"FeishuApiError: {e}", file=sys.stderr)
        raise SystemExit(1)
