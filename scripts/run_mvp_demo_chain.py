"""MVP demo: project_secretary -> risk_analysis -> followup -> weekly_report -> coordinator (optional write)."""

from __future__ import annotations

import argparse
import asyncio
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

load_dotenv_if_present(_ROOT)

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
    print(f"  缂哄け妯″潡: {', '.join(missing)}\n", file=sys.stderr, flush=True)
    print(
        "璇风敤**涓婇潰杩欎竴琛?*瀵瑰簲鐨?pip 瀹夎渚濊禆锛堜繚璇?pip 涓?python 涓€鑷达級锛屽湪浠撳簱鏍圭洰褰曟墽琛岋細\n"
        f"  {sys.executable} -m pip install -r requirements.txt\n"
        "鎴栵細\n"
        f"  {sys.executable} -m pip install -e .\n"
        "Windows 鍙鏌ワ細`where python`銆乣where pip` 鏄惁鎸囧悜鍚屼竴瀹夎鐩綍銆俓n",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(1)


_ensure_runtime_deps()

from agent_runtime.agent_io import (  # noqa: E402
    FollowUpInput,
    FollowUpOutput,
    ProjectStateOutput,
    RiskAnalysisInput,
    RiskAnalysisOutput,
    WeeklyReportInput,
)
from agent_runtime.base_refs import RecordCreate  # noqa: E402
from agent_runtime.enums import AgentName, BaseTableName, EventType, TriggerType  # noqa: E402
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent  # noqa: E402
from agent_runtime.loaders import load_project_manifest  # noqa: E402
from agent_runtime.mvp.builder import build_runtime_with_tool_integration  # noqa: E402
from agent_runtime.mvp.project_env import feishu_demo_chain_env_missing  # noqa: E402
from feishu_adapter.feishu_client import feishu_request  # noqa: E402


def _memory_output(session: object, agent: AgentName) -> dict | None:
    key = f"{agent}_output"
    for item in reversed(getattr(session, "memory", [])):
        if getattr(item, "key", None) == key:
            return item.value  # type: ignore[no-any-return]
    return None


def _first_writable_field_name(app_token: str, table_id: str) -> str:
    data = feishu_request(
        "GET",
        f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        queries={"page_size": 100},
    )
    items = data.get("items", [])
    if not isinstance(items, list):
        return ""
    auto_types = {1001, 1002, 1003, 1004, 1005}
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("field_name")
        ftype = int(it.get("type", 0))
        if isinstance(name, str) and name and ftype not in auto_types:
            return name
    return ""


async def _run() -> int:
    parser = argparse.ArgumentParser(description="HiveMindAI MVP agent chain (Feishu Base)")
    parser.add_argument("--project-id", default="enterprise_rag", help="Logical project id (default: enterprise_rag)")
    parser.add_argument(
        "--skip-coordinator-write",
        action="store_true",
        help="Run coordinator without proposed Base creates (still runs trace + quality path smoke).",
    )
    args = parser.parse_args()

    load_dotenv_if_present(_ROOT)
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

    runtime, executor = build_runtime_with_tool_integration(_ROOT)
    event = AgentTriggerEvent(
        event_id=str(uuid4()),
        event_type=EventType.RUN_FULL_DEMO_CHAIN,
        trigger_type=TriggerType.MANUAL,
        project_id=args.project_id,
    )

    chain: list[tuple[AgentName, dict | None]] = [
        (AgentName.PROJECT_SECRETARY, {}),
    ]

    try:
        for agent_name, payload in chain:
            req = AgentCallRequest(
                agent_name=agent_name,
                event=event,
                reason="mvp_demo_chain",
                input_payload=payload,
            )
            session = await runtime.run_agent(req)
            summary = session.final_summary or ""
            print(
                f"[{agent_name}] run_id={session.run_id} status={session.status} summary={summary[:200]}",
                flush=True,
            )
            if session.status.value != "success":
                print(f"  errors: {session.errors}", flush=True)
                return 1

        raw_ps = _memory_output(session, AgentName.PROJECT_SECRETARY)
        if not raw_ps:
            print("project_secretary output not found", flush=True)
            return 1
        project_state = ProjectStateOutput.model_validate(raw_ps)

        risk_req = AgentCallRequest(
            agent_name=AgentName.RISK_ANALYSIS,
            event=event,
            reason="mvp_demo_chain",
            input_payload=RiskAnalysisInput(
                run_id=session.run_id,
                project_id=args.project_id,
                project_state=project_state,
            ).model_dump(mode="json"),
        )
        s2 = await runtime.run_agent(risk_req)
        print(
            f"[{AgentName.RISK_ANALYSIS}] run_id={s2.run_id} status={s2.status} summary={s2.final_summary or ''}",
            flush=True,
        )
        if s2.status.value != "success":
            return 1
        raw_risk = _memory_output(s2, AgentName.RISK_ANALYSIS)
        risk_out = RiskAnalysisOutput.model_validate(raw_risk or {})

        fu_req = AgentCallRequest(
            agent_name=AgentName.FOLLOWUP,
            event=event,
            reason="mvp_demo_chain",
            input_payload=FollowUpInput(
                run_id=s2.run_id,
                project_id=args.project_id,
                missing_fields=project_state.missing_fields,
                risk_candidates=risk_out.risk_candidates,
            ).model_dump(mode="json"),
        )
        s3 = await runtime.run_agent(fu_req)
        print(
            f"[{AgentName.FOLLOWUP}] run_id={s3.run_id} status={s3.status} summary={s3.final_summary or ''}",
            flush=True,
        )
        if s3.status.value != "success":
            return 1
        raw_fu = _memory_output(s3, AgentName.FOLLOWUP)
        follow_out = FollowUpOutput.model_validate(raw_fu or {})

        wr_req = AgentCallRequest(
            agent_name=AgentName.WEEKLY_REPORT,
            event=event,
            reason="mvp_demo_chain",
            input_payload=WeeklyReportInput(
                run_id=s3.run_id,
                project_id=args.project_id,
                period="MVP-DEMO",
                project_state=project_state,
                risks=risk_out.risk_candidates,
                followups=follow_out.followup_requests,
            ).model_dump(mode="json"),
        )
        s4 = await runtime.run_agent(wr_req)
        print(
            f"[{AgentName.WEEKLY_REPORT}] run_id={s4.run_id} status={s4.status} summary={s4.final_summary or ''}",
            flush=True,
        )
        if s4.status.value != "success":
            return 1

        coord_payload: dict = {}
        if not args.skip_coordinator_write:
            manifest = load_project_manifest(_ROOT / "projects" / args.project_id)
            agent_runs = manifest.tables[BaseTableName.AGENT_RUNS]
            field_names = [f.field_name for f in agent_runs.fields]
            run_id_field = _first_writable_field_name(manifest.base_app_token, agent_runs.table_id) or agent_runs.primary_key_field or (field_names[0] if field_names else "run_id")
            fields: dict[str, str] = {run_id_field: s4.run_id}
            if len(field_names) > 1:
                fields[field_names[1]] = args.project_id
            coord_payload = {
                "proposed_creates": [
                    RecordCreate(
                        table_name=BaseTableName.AGENT_RUNS,
                        fields=fields,
                        idempotency_key=f"agent_run_{s4.run_id}",
                        reason="mvp_demo_chain_agent_run_log",
                    ).model_dump(mode="json")
                ]
            }
        coord_req = AgentCallRequest(
            agent_name=AgentName.COORDINATOR,
            event=event,
            reason="mvp_demo_chain_finalize",
            input_payload=coord_payload,
        )
        s5 = await runtime.run_agent(coord_req)
        print(
            f"[{AgentName.COORDINATOR}] run_id={s5.run_id} status={s5.status} summary={s5.final_summary or ''}",
            flush=True,
        )
        if s5.status.value != "success":
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

