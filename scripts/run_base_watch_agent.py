"""Run the Coordinator as a long-lived Feishu Base polling agent.

This is intentionally separate from ``run_mvp_demo_chain.py``:

- ``run_mvp_demo_chain.py`` runs one manual demo chain and exits.
- This watcher keeps running, polls selected Base tables, and triggers the
  Coordinator when business data changes.

The watcher does not listen to AgentRuns by default. AgentRuns is written by the
agent itself, so watching it would create a feedback loop.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC.resolve()) not in sys.path:
    sys.path.insert(0, str(_SRC.resolve()))

from tool_integration.loader import load_dotenv_if_present  # noqa: E402


def _load_dotenv() -> None:
    if os.environ.get("HIVEMIND_SKIP_DOTENV", "").lower() in {"1", "true", "yes"}:
        return
    load_dotenv_if_present(_ROOT)


_load_dotenv()

from agent_runtime.enums import AgentName, EventType, TriggerType  # noqa: E402
from agent_runtime.base_refs import BaseRecordRef  # noqa: E402
from agent_runtime.enums import BaseTableName  # noqa: E402
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent, EventScope  # noqa: E402
from agent_runtime.loaders import load_project_manifest  # noqa: E402
from agent_runtime.mvp.builder import build_runtime_with_tool_integration  # noqa: E402
from agent_runtime.mvp.project_env import expand_env_value, feishu_demo_chain_env_missing  # noqa: E402
from agent_runtime.project_state import ProjectManifest  # noqa: E402
from feishu_adapter.feishu_client import feishu_request  # noqa: E402


DEFAULT_WATCH_TABLES = ["Projects", "Tasks", "Milestones", "FollowUps"]
OUTPUT_TABLES = {"Risks", "WeeklyReports", "AgentRuns"}


def _load_expanded_manifest(project_id: str) -> ProjectManifest:
    manifest = load_project_manifest(_ROOT / "projects" / project_id)
    return ProjectManifest.model_validate(expand_env_value(manifest.model_dump(mode="json")))


def _record_sort_key(item: dict[str, object]) -> str:
    return str(item.get("record_id") or "")


def _stable_snapshot(items: list[dict[str, object]]) -> str:
    normalized = sorted(items, key=_record_sort_key)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_hash(item: dict[str, object]) -> str:
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _search_table(app_token: str, table_id: str, *, page_size: int = 100) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    page_token = ""
    while True:
        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            queries={"page_size": page_size, "page_token": page_token},
            body={},
        )
        raw_items = data.get("items", [])
        if isinstance(raw_items, list):
            items.extend(item for item in raw_items if isinstance(item, dict))
        if not data.get("has_more"):
            return items
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return items


def _snapshot_tables(manifest: ProjectManifest, table_names: list[str]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table_name in table_names:
        table = manifest.tables.get(table_name)  # type: ignore[arg-type]
        if table is None:
            raise RuntimeError(f"table {table_name!r} is not declared in project manifest")
        items = _search_table(manifest.base_app_token, table.table_id)
        records = {
            str(item.get("record_id")): _record_hash(item)
            for item in items
            if item.get("record_id")
        }
        tables[table_name] = {
            "record_count": len(items),
            "records": records,
        }
    digest = _stable_snapshot([{"table": key, "records": value["records"]} for key, value in tables.items()])
    return {
        "version": 1,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "hash": digest,
        "tables": tables,
    }


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("hash") or "")


def _snapshot_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    tables = snapshot.get("tables")
    if not isinstance(tables, dict):
        return {}
    counts: dict[str, int] = {}
    for table_name, payload in tables.items():
        if isinstance(payload, dict):
            counts[str(table_name)] = int(payload.get("record_count") or 0)
    return counts


def _snapshot_record_hashes(snapshot: dict[str, Any], table_name: str) -> dict[str, str]:
    tables = snapshot.get("tables")
    if not isinstance(tables, dict):
        return {}
    table_payload = tables.get(table_name)
    if not isinstance(table_payload, dict):
        return {}
    records = table_payload.get("records")
    if not isinstance(records, dict):
        return {}
    return {str(k): str(v) for k, v in records.items()}


def _diff_snapshots(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_tables = set((previous.get("tables") or {}).keys()) if isinstance(previous.get("tables"), dict) else set()
    current_tables = set((current.get("tables") or {}).keys()) if isinstance(current.get("tables"), dict) else set()
    changed_tables: list[str] = []
    changed_records_by_table: dict[str, list[str]] = {}
    created_counts: dict[str, int] = {}
    updated_counts: dict[str, int] = {}
    deleted_counts: dict[str, int] = {}

    for table_name in sorted(previous_tables | current_tables):
        prev_records = _snapshot_record_hashes(previous, table_name)
        curr_records = _snapshot_record_hashes(current, table_name)
        created = sorted(set(curr_records) - set(prev_records))
        deleted = sorted(set(prev_records) - set(curr_records))
        updated = sorted(
            record_id
            for record_id in set(prev_records) & set(curr_records)
            if prev_records[record_id] != curr_records[record_id]
        )
        changed = created + updated + deleted
        if changed:
            changed_tables.append(table_name)
            changed_records_by_table[table_name] = changed
            created_counts[table_name] = len(created)
            updated_counts[table_name] = len(updated)
            deleted_counts[table_name] = len(deleted)

    return {
        "changed_tables": changed_tables,
        "changed_record_ids": sorted({rid for ids in changed_records_by_table.values() for rid in ids}),
        "changed_records_by_table": changed_records_by_table,
        "created_counts": created_counts,
        "updated_counts": updated_counts,
        "deleted_counts": deleted_counts,
    }


def _watch_state_path(project_id: str, table_names: list[str], explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    table_digest = hashlib.sha1(",".join(table_names).encode("utf-8")).hexdigest()[:10]
    return _ROOT / "runtime" / "base_watch_state" / f"{project_id}_{table_digest}.json"


def _load_watch_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_watch_state(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _input_records(change_summary: dict[str, Any]) -> list[BaseRecordRef]:
    refs: list[BaseRecordRef] = []
    by_table = change_summary.get("changed_records_by_table")
    if not isinstance(by_table, dict):
        return refs
    for table_name, record_ids in by_table.items():
        try:
            enum_table = BaseTableName(str(table_name))
        except ValueError:
            continue
        if not isinstance(record_ids, list):
            continue
        for record_id in record_ids[:50]:
            refs.append(BaseRecordRef(table_name=enum_table, record_id=str(record_id)))
    return refs


async def _trigger_coordinator(
    *,
    runtime: object,
    project_id: str,
    table_names: list[str],
    snapshot_hash: str,
    changed_tables: list[str],
    changed_record_ids: list[str],
    change_summary: dict[str, Any],
    writeback: bool,
    reason: str,
) -> str:
    event = AgentTriggerEvent(
        event_id=str(uuid4()),
        event_type=EventType.RUN_FULL_DEMO_CHAIN,
        trigger_type=TriggerType.RECORD_UPDATED,
        project_id=project_id,
        input_records=_input_records(change_summary),
        scope=EventScope(tables=table_names, record_ids=changed_record_ids, include_history=True),
        metadata={
            "watch_snapshot_hash": snapshot_hash,
            "watch_reason": reason,
            "change_summary": change_summary,
        },
    )
    req = AgentCallRequest(
        agent_name=AgentName.COORDINATOR,
        event=event,
        reason=reason,
        input_payload={
            "orchestrate": True,
            "writeback": writeback,
            "period": "BASE-WATCH",
            "watch_tables": table_names,
            "changed_tables": changed_tables,
            "changed_record_ids": changed_record_ids,
            "change_summary": change_summary,
            "snapshot_hash": snapshot_hash,
        },
    )
    session = await runtime.run_agent(req)  # type: ignore[attr-defined]
    status = session.status.value
    summary = session.final_summary or ""
    print(
        f"[watch] triggered run_id={session.run_id} status={status} summary={summary}",
        flush=True,
    )
    if status != "success":
        print(f"[watch] errors={session.errors}", flush=True)
    return status


async def _run() -> int:
    parser = argparse.ArgumentParser(description="HiveMindAI long-lived Base watcher")
    parser.add_argument("--project-id", default="enterprise_rag")
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--tables", nargs="*", default=DEFAULT_WATCH_TABLES)
    parser.add_argument("--include-output-tables", action="store_true", help="Also watch Risks/WeeklyReports. AgentRuns is still excluded unless explicitly listed.")
    parser.add_argument("--run-on-start", action="store_true", help="Trigger Coordinator once after the initial snapshot.")
    parser.add_argument("--state-file", default=None, help="Persist watch snapshots here; default is runtime/base_watch_state/<project>_<tables>.json.")
    parser.add_argument("--reset-state", action="store_true", help="Ignore the previous persisted snapshot and start from the current Base state.")
    parser.add_argument("--skip-writeback", action="store_true", help="Run analysis but do not write proposed records back to Base.")
    args = parser.parse_args()

    _load_dotenv()
    missing = feishu_demo_chain_env_missing(_ROOT)
    if missing:
        print("Cannot start Base watcher: missing required environment variables:", flush=True)
        for name in missing:
            print(f"  - {name}", flush=True)
        return 2

    table_names = list(dict.fromkeys(args.tables))
    if args.include_output_tables:
        for table_name in ("Risks", "WeeklyReports"):
            if table_name not in table_names:
                table_names.append(table_name)
    if "AgentRuns" in table_names:
        print("[watch] warning: AgentRuns is self-written by the agent; watching it can cause repeated triggers.", flush=True)

    manifest = _load_expanded_manifest(args.project_id)
    runtime, executor = build_runtime_with_tool_integration(_ROOT)
    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    state_path = _watch_state_path(args.project_id, table_names, args.state_file)
    current_snapshot = _snapshot_tables(manifest, table_names)
    persisted_snapshot = None if args.reset_state else _load_watch_state(state_path)
    previous_snapshot = persisted_snapshot or current_snapshot
    previous_hash = _snapshot_hash(previous_snapshot)
    current_hash = _snapshot_hash(current_snapshot)
    counts = _snapshot_counts(current_snapshot)
    _save_watch_state(state_path, current_snapshot)
    print(
        f"[watch] started project_id={args.project_id} tables={','.join(table_names)} "
        f"interval={args.interval_seconds}s initial_hash={current_hash[:12]} counts={counts} state_file={state_path}",
        flush=True,
    )

    try:
        if args.run_on_start:
            await _trigger_coordinator(
                runtime=runtime,
                project_id=args.project_id,
                table_names=table_names,
                snapshot_hash=current_hash,
                changed_tables=table_names,
                changed_record_ids=[],
                change_summary={
                    "startup": True,
                    "counts": counts,
                    "changed_records_by_table": {},
                },
                writeback=not args.skip_writeback,
                reason="base_watch_startup",
            )

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(1, args.interval_seconds))
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                break

            current_snapshot = _snapshot_tables(manifest, table_names)
            current_hash = _snapshot_hash(current_snapshot)
            counts = _snapshot_counts(current_snapshot)
            if current_hash == previous_hash:
                print(
                    f"[watch] {datetime.now().isoformat(timespec='seconds')} no_change hash={current_hash[:12]} counts={counts}",
                    flush=True,
                )
                continue

            change_summary = _diff_snapshots(previous_snapshot, current_snapshot)
            print(
                f"[watch] change_detected old={previous_hash[:12]} new={current_hash[:12]} "
                f"tables={','.join(change_summary['changed_tables'])} counts={counts}",
                flush=True,
            )
            previous_hash = current_hash
            previous_snapshot = current_snapshot
            _save_watch_state(state_path, current_snapshot)
            await _trigger_coordinator(
                runtime=runtime,
                project_id=args.project_id,
                table_names=table_names,
                snapshot_hash=current_hash,
                changed_tables=change_summary["changed_tables"],
                changed_record_ids=change_summary["changed_record_ids"],
                change_summary=change_summary,
                writeback=not args.skip_writeback,
                reason="base_watch_change_detected",
            )
    finally:
        await executor.shutdown()
        print("[watch] stopped", flush=True)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
