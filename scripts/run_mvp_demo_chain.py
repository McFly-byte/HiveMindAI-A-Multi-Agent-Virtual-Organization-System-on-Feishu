"""MVP demo: project_secretary -> risk_analysis -> followup -> weekly_report -> coordinator (optional write)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC.resolve()) not in sys.path:
    sys.path.insert(0, str(_SRC.resolve()))

# Work around noisy ProactorEventLoop shutdown traces on Windows (Python 3.13).
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Load .env as early as possible so modules that read env at import-time can see it.
from tool_integration.loader import load_dotenv_if_present  # noqa: E402

def _load_dotenv() -> None:
    if os.environ.get("HIVEMIND_SKIP_DOTENV", "").lower() in {"1", "true", "yes"}:
        return
    load_dotenv_if_present(_ROOT)


_load_dotenv()

def _ensure_runtime_deps() -> None:
    """Fail fast with a clear hint when ``pip`` and ``python`` are different interpreters."""

    missing: list[str] = []
    for mod in ("pydantic", "yaml"):
        try:
            __import__(mod)
        except ModuleNotFoundError:
            missing.append(mod)

    if not missing:
        return

    print("Current interpreter cannot import project dependencies (often because `pip` installed into a different Python than this script uses).\n", file=sys.stderr, flush=True)
    print(f"  Python executable: {sys.executable}", file=sys.stderr, flush=True)
    print(f"  Version: {sys.version.splitlines()[0]}", file=sys.stderr, flush=True)
    print(f"  Missing modules: {', '.join(missing)}\n", file=sys.stderr, flush=True)
    print(
        "Use the same interpreter shown above to install dependencies from the repo root:\n"
        f"  {sys.executable} -m pip install -r requirements.txt\n"
        "or:\n"
        f"  {sys.executable} -m pip install -e .\n"
        "On Windows, check that `where python` and `where pip` point to the same installation.\n",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(1)


_ensure_runtime_deps()

from agent_runtime.enums import AgentName, EventType, TriggerType  # noqa: E402
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent  # noqa: E402
from agent_runtime.loaders import load_project_manifest  # noqa: E402
from agent_runtime.mvp.builder import build_runtime_with_tool_integration  # noqa: E402
from agent_runtime.mvp.project_env import expand_env_value, feishu_demo_chain_env_missing  # noqa: E402
from agent_runtime.project_state import ProjectManifest  # noqa: E402
from feishu_adapter.feishu_client import feishu_request  # noqa: E402


WRITEBACK_TABLES = {"Tasks", "Risks", "FollowUps", "WeeklyReports", "AgentRuns"}
AGENT_RUNS_COMPAT_FIELDS = {"ID", "Agent名称", "触发时间", "操作描述", "输入来源", "输出结果", "执行状态"}

FIELD_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "Risks": {
        "风险标题": ("ID", "标题"),
        "由哪个 Agent 创建": ("由哪个 Agent创建", "创建 Agent", "创建Agent"),
    },
    "FollowUps": {
        "追问标题": ("ID", "标题"),
    },
    "WeeklyReports": {
        "由哪个 Agent 创建": ("由哪个 Agent创建", "创建 Agent", "创建Agent"),
    },
}


def _load_expanded_manifest(project_id: str) -> ProjectManifest:
    manifest = load_project_manifest(_ROOT / "projects" / project_id)
    return ProjectManifest.model_validate(expand_env_value(manifest.model_dump(mode="json")))


def _remote_field_names(app_token: str, table_id: str) -> set[str]:
    data = feishu_request(
        "GET",
        f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        queries={"page_size": 500},
    )
    items = data.get("items", [])
    if not isinstance(items, list):
        return set()
    return {str(item.get("field_name")) for item in items if isinstance(item, dict) and item.get("field_name")}


def _field_exists(table_name: str, field_name: str, actual: set[str]) -> bool:
    return field_name in actual or any(alias in actual for alias in FIELD_ALIASES.get(table_name, {}).get(field_name, ()))


def _writeback_schema_missing(project_id: str) -> dict[str, list[str]]:
    manifest = _load_expanded_manifest(project_id)
    missing: dict[str, list[str]] = {}
    for table_name, table in manifest.tables.items():
        if str(table_name) not in WRITEBACK_TABLES:
            continue
        actual = _remote_field_names(manifest.base_app_token, table.table_id)
        if str(table_name) == "AgentRuns":
            required = sorted(AGENT_RUNS_COMPAT_FIELDS)
        else:
            required = [field.field_name for field in table.fields if field.write_allowed]
        gaps = [field_name for field_name in required if not _field_exists(str(table_name), field_name, actual)]
        if gaps:
            missing[str(table_name)] = gaps
    return missing


async def _run() -> int:
    parser = argparse.ArgumentParser(description="HiveMindAI MVP agent chain (Feishu Base)")
    parser.add_argument("--project-id", default="enterprise_rag", help="Logical project id (default: enterprise_rag)")
    parser.add_argument(
        "--skip-coordinator-write",
        action="store_true",
        help="Run coordinator without proposed Base creates (still runs trace + quality path smoke).",
    )
    parser.add_argument(
        "--check-schema-only",
        action="store_true",
        help="Only verify remote Feishu Base writeback fields against table_manifest.yaml.",
    )
    args = parser.parse_args()

    _load_dotenv()
    missing = feishu_demo_chain_env_missing(_ROOT)
    if missing:
        print("Cannot start MVP chain: missing required environment variables:", flush=True)
        for name in missing:
            print(f"  - {name}", flush=True)
        print(
            "\nHint: provide FEISHU_APP_ID, FEISHU_APP_SECRET, and all FEISHU_BASE_APP_TOKEN/FEISHU_TABLE_* referenced by project manifests.",
            flush=True,
        )
        return 2

    if not args.skip_coordinator_write or args.check_schema_only:
        schema_missing = _writeback_schema_missing(args.project_id)
        if schema_missing:
            print("Remote Feishu Base schema is missing fields required for writeback:", flush=True)
            for table, fields in schema_missing.items():
                print(f"  - {table}: {', '.join(fields)}", flush=True)
            print(
                "\nFix: run `uv run python scripts/ensure_bitable_fields.py --project-id "
                f"{args.project_id} --tables Tasks Risks FollowUps WeeklyReports AgentRuns --yes` "
                "or add these fields manually in Feishu Base.",
                flush=True,
            )
            return 3
        if args.check_schema_only:
            print("Remote Feishu Base schema check passed.", flush=True)
            return 0

    runtime, executor = build_runtime_with_tool_integration(_ROOT)
    event = AgentTriggerEvent(
        event_id=str(uuid4()),
        event_type=EventType.RUN_FULL_DEMO_CHAIN,
        trigger_type=TriggerType.MANUAL,
        project_id=args.project_id,
    )

    try:
        coord_req = AgentCallRequest(
            agent_name=AgentName.COORDINATOR,
            event=event,
            reason="mvp_demo_chain_orchestrate",
            input_payload={
                "orchestrate": True,
                "writeback": not args.skip_coordinator_write,
                "period": "MVP-DEMO",
                "demo_full_chain": True,
            },
        )
        session = await runtime.run_agent(coord_req)
        print(
            f"[{AgentName.COORDINATOR}] run_id={session.run_id} status={session.status} summary={session.final_summary or ''}",
            flush=True,
        )
        if session.status.value != "success":
            print(f"  errors: {session.errors}", flush=True)
            return 1

        return 0
    finally:
        await executor.shutdown()


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
