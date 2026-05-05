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


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _field_type_id(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        key = v.strip().lower()
        if key in FIELD_TYPE_MAP:
            return FIELD_TYPE_MAP[key]
    raise ValueError(f"unsupported field_type: {v!r}")


def _build_field_payload(field: dict[str, Any]) -> dict[str, Any]:
    name = field.get("field_name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"invalid field_name: {name!r}")
    ftype = _field_type_id(field.get("field_type"))

    payload: dict[str, Any] = {
        "field_name": name,
        "type": ftype,
    }

    enum_values = field.get("enum_values")
    if isinstance(enum_values, list) and ftype in (3, 4):
        payload["property"] = {
            "options": [{"name": str(v), "color": 0} for v in enum_values]
        }

    return payload


def _create_app_for_project(project_id: str, dry_run: bool) -> str:
    if dry_run:
        return f"DRYRUN_APP_TOKEN_{project_id}"

    data = feishu_request(
        "POST",
        "/open-apis/bitable/v1/apps",
        body={"name": project_id},
    )
    token = data.get("app", {}).get("app_token") if isinstance(data.get("app"), dict) else data.get("app_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"create app succeeded but app_token missing: {project_id}")
    return token


def _create_table_with_fields(app_token: str, table_key: str, table_spec: dict[str, Any], dry_run: bool) -> str:
    table_name = table_spec.get("display_name") if isinstance(table_spec.get("display_name"), str) else table_key
    fields = table_spec.get("fields", [])
    if not isinstance(fields, list):
        raise ValueError(f"table {table_key} fields must be a list")

    if dry_run:
        return f"DRYRUN_TABLE_{table_key}"

    # Pre-clean existing table with same name to guarantee id overwrite semantics.
    list_data = feishu_request(
        "GET",
        f"/open-apis/bitable/v1/apps/{app_token}/tables",
        queries={"page_size": 500},
    )
    items = list_data.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            existed_id = item.get("table_id")
            existed_name = item.get("name")
            if isinstance(existed_id, str) and existed_id and isinstance(existed_name, str) and existed_name == table_name:
                feishu_request(
                    "POST",
                    f"/open-apis/bitable/v1/apps/{app_token}/tables/batch_delete",
                    body={"table_ids": [existed_id]},
                )
                print(f"deleted existing table: {table_name} [{existed_id}]")

    create_table_data = feishu_request(
        "POST",
        f"/open-apis/bitable/v1/apps/{app_token}/tables",
        body={"table": {"name": table_name}},
    )
    table = create_table_data.get("table", {}) if isinstance(create_table_data.get("table"), dict) else create_table_data
    table_id = table.get("table_id") if isinstance(table, dict) else None
    if not isinstance(table_id, str) or not table_id:
        raise RuntimeError(f"table created but table_id missing: {table_key}")

    for field in fields:
        if not isinstance(field, dict):
            continue
        # Link fields are created in phase-2 after all table_ids are known.
        if _field_type_id(field.get("field_type")) == 18:
            continue
        payload = _build_field_payload(field)
        feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            body=payload,
        )

    return table_id


def _move_app_to_wiki(
    space_id: str,
    app_token: str,
    parent_wiki_token: str,
    apply: bool,
    dry_run: bool,
) -> dict[str, str]:
    if dry_run:
        return {"wiki_token": "DRYRUN_WIKI_TOKEN", "task_id": "DRYRUN_TASK_ID"}

    body: dict[str, Any] = {
        "obj_token": app_token,
        "obj_type": "bitable",
        "apply": apply,
    }
    if parent_wiki_token:
        body["parent_wiki_token"] = parent_wiki_token

    data = feishu_request(
        "POST",
        f"/open-apis/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki",
        body=body,
    )
    return {
        "wiki_token": str(data.get("wiki_token", "")),
        "task_id": str(data.get("task_id", "")),
    }


def _process_project(
    project_dir: Path,
    space_id: str,
    parent_wiki_token: str,
    apply: bool,
    dry_run: bool,
) -> None:
    project_state_path = project_dir / "project_state.yaml"
    table_manifest_path = project_dir / "table_manifest.yaml"
    if not project_state_path.exists() or not table_manifest_path.exists():
        return

    project_state = _read_yaml(project_state_path)
    table_manifest = _read_yaml(table_manifest_path)

    project_id = project_state.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError(f"missing project_id in {project_state_path}")

    tables = table_manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError(f"tables must be mapping: {table_manifest_path}")

    print(f"\n=== project: {project_id} ===")
    app_token = _create_app_for_project(project_id, dry_run)
    print(f"app_token: {app_token}")
    move_result = _move_app_to_wiki(space_id, app_token, parent_wiki_token, apply, dry_run)
    print(f"moved_to_wiki: wiki_token={move_result['wiki_token']} task_id={move_result['task_id']}")

    project_state["base_app_token"] = app_token

    created_table_ids: dict[str, str] = {}
    for table_key, table_spec in tables.items():
        if not isinstance(table_spec, dict):
            continue
        table_id = _create_table_with_fields(app_token, table_key, table_spec, dry_run)
        table_spec["table_id"] = table_id
        created_table_ids[table_key] = table_id
        print(f"table {table_key} -> {table_id}")

    # Phase-2: create link fields after all tables have concrete table_id.
    for table_key, table_spec in tables.items():
        if not isinstance(table_spec, dict):
            continue
        fields = table_spec.get("fields", [])
        if not isinstance(fields, list):
            continue
        cur_table_id = str(table_spec.get("table_id", ""))
        if not cur_table_id:
            continue
        for field in fields:
            if not isinstance(field, dict):
                continue
            if _field_type_id(field.get("field_type")) != 18:
                continue
            field_name = field.get("field_name")
            if not isinstance(field_name, str) or not field_name.strip():
                continue

            target_table_id = ""
            prop = field.get("property")
            if isinstance(prop, dict) and isinstance(prop.get("table_id"), str) and prop.get("table_id"):
                target_table_id = prop["table_id"]
            elif isinstance(field.get("link_table_id"), str) and field.get("link_table_id"):
                target_table_id = field["link_table_id"]
            elif isinstance(field.get("link_table_key"), str) and field.get("link_table_key"):
                target_table_id = created_table_ids.get(field["link_table_key"], "")
            elif isinstance(field.get("link_table_name"), str) and field.get("link_table_name"):
                target_table_id = created_table_ids.get(field["link_table_name"], "")
            elif table_key == "Tasks" and field_name == "所属项目":
                target_table_id = created_table_ids.get("Projects", "")
                if target_table_id:
                    print("auto link target: Tasks.所属项目 -> Projects")

            if not target_table_id:
                raise ValueError(
                    f"link field '{field_name}' in table '{table_key}' requires one of: "
                    "property.table_id / link_table_id / link_table_key / link_table_name"
                )

            if dry_run:
                print(f"create link field (dry-run): {table_key}.{field_name} -> {target_table_id}")
                continue

            feishu_request(
                "POST",
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{cur_table_id}/fields",
                body={
                    "field_name": field_name,
                    "type": 18,
                    "property": {"table_id": target_table_id},
                },
            )
            print(f"create link field: {table_key}.{field_name} -> {target_table_id}")

    if dry_run:
        return

    _write_yaml(project_state_path, project_state)
    _write_yaml(table_manifest_path, table_manifest)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create Feishu Bitable app per project and overwrite base_app_token/table_id in YAMLs."
    )
    parser.add_argument("--projects-dir", default="projects", help="Projects root directory")
    parser.add_argument("--project-ids", nargs="*", default=[], help="Only process these project_id values")
    parser.add_argument("--space-id", default="", help="Target Feishu wiki space_id")
    parser.add_argument("--parent-wiki-token", default="", help="Optional target parent wiki node token")
    parser.add_argument("--apply", action="store_true", help="Pass apply=true when moving app into wiki")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--yes", action="store_true", help="Confirm write and remote creation")
    args = parser.parse_args()

    load_dotenv_if_present(_ROOT)

    projects_dir = (_ROOT / args.projects_dir).resolve()
    if not projects_dir.exists():
        raise ValueError(f"projects dir not found: {projects_dir}")
    space_id = args.space_id.strip()
    if not space_id:
        raise ValueError("missing --space-id (target wiki space_id)")

    targets: list[Path] = []
    wanted = set(args.project_ids)
    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir():
            continue
        ps = child / "project_state.yaml"
        if not ps.exists():
            continue
        data = _read_yaml(ps)
        pid = data.get("project_id")
        if isinstance(pid, str) and (not wanted or pid in wanted):
            targets.append(child)

    if not targets:
        raise ValueError("no project matched")

    if not args.dry_run and not args.yes:
        names = ", ".join(p.name for p in targets)
        print(f"About to create remote bitable apps/tables and overwrite YAML for: {names}")
        print("Re-run with --yes to continue.")
        return 2

    for project_dir in targets:
        _process_project(project_dir, space_id, args.parent_wiki_token.strip(), bool(args.apply), args.dry_run)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FeishuApiError as e:
        print(f"FeishuApiError: {e}", file=sys.stderr)
        raise SystemExit(1)
