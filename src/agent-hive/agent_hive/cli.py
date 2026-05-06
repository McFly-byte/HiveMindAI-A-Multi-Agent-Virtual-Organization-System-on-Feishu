from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agent_hive.config.env import load_dotenv_if_present
from agent_hive.config.loader import load_runtime_config
from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.feishu_im import FeishuIMEventSource
from agent_hive.events.sources.schedule import ScheduledEventSource
from agent_hive.events.sources.stdin import StdinEventSource
from agent_hive.observability.logging import configure_logging, get_logger
from agent_hive.runtime.daemon import EventDaemon
from agent_hive.runtime.hive_runtime import HiveRuntime


logger = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-hive")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--debug", action="store_true", help="Enable verbose agent-hive debug logs.")
    sub = parser.add_subparsers(dest="command", required=True)
    list_agents = sub.add_parser("list-agents")
    list_agents.add_argument("--debug", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    run = sub.add_parser("run")
    run.add_argument("--debug", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    run.add_argument("--event-type", required=True)
    run.add_argument("--project-id", required=True)
    run.add_argument("--target-agent")
    run.add_argument("--payload", default="{}")
    serve = sub.add_parser("serve")
    serve.add_argument("--debug", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    serve.add_argument("--project-id", required=True)
    serve.add_argument("--stdin", action="store_true", help="Read newline-delimited HiveEvent JSON from stdin.")
    serve.add_argument(
        "--feishu-im",
        action="store_true",
        help="Start and bridge Feishu IM WebSocket adapter events.",
    )
    serve.add_argument("--target-agent", default="orchestrator")
    serve.add_argument("--fr02-inspection", action="store_true", help="Enable FR-02 scheduled task/data-gap inspection.")
    serve.add_argument("--fr02-interval-seconds", type=float, default=3600.0)
    serve.add_argument("--fr02-no-run-on-start", action="store_true")
    return parser


async def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root)
    load_dotenv_if_present(project_root)
    configure_logging(debug=args.debug or None)
    logger.info("agent-hive command=%s project_root=%s", args.command, args.project_root)
    config = load_runtime_config(project_root)
    logger.debug(
        "runtime config loaded agents=%s memory_db=%s",
        sorted(config.agents),
        config.memory_db_path,
    )
    if args.command == "list-agents":
        for agent_id in sorted(config.agents):
            print(agent_id)
        return 0
    if args.command == "run":
        runtime = HiveRuntime.from_config(config)
        try:
            payload = json.loads(args.payload)
            event = HiveEvent(
                event_type=args.event_type,
                project_id=args.project_id,
                target_agent_id=args.target_agent,
                payload=payload if isinstance(payload, dict) else {"value": payload},
            )
            logger.info(
                "dispatch one-shot event event_id=%s event_type=%s target_agent=%s",
                event.event_id,
                event.event_type,
                event.target_agent_id,
            )
            result = await runtime.dispatch(event)
            print(
                json.dumps(
                    {
                        "root": result.root_output.model_dump(mode="json"),
                        "children": [item.model_dump(mode="json") for item in result.child_outputs],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if result.root_session.status == "success" else 1
        finally:
            await runtime.shutdown()
    if args.command == "serve":
        runtime = HiveRuntime.from_config(config)
        daemon = EventDaemon(runtime)
        if args.stdin:
            daemon.add_source(StdinEventSource())
        if args.feishu_im:
            provider = runtime.providers.get("feishu")
            provider.enable_im_websocket()  # type: ignore[attr-defined]
            provider.load()  # type: ignore[attr-defined]
            daemon.add_source(
                FeishuIMEventSource(
                    provider,  # type: ignore[arg-type]
                    project_id=args.project_id,
                    target_agent_id=args.target_agent,
                )
            )
        if args.fr02_inspection:
            daemon.add_source(
                ScheduledEventSource(
                    name="fr02_inspection",
                    event_type="fr02.inspection.requested",
                    project_id=args.project_id,
                    target_agent_id="orchestrator",
                    payload_factory=_fr02_payload,
                    interval_seconds=args.fr02_interval_seconds,
                    run_on_start=not args.fr02_no_run_on_start,
                )
            )
        if not daemon.sources:
            raise SystemExit("serve requires at least one source: --stdin or --feishu-im")
        logger.info(
            "agent-hive daemon starting project_id=%s target_agent=%s sources=%s",
            args.project_id,
            args.target_agent,
            [source.name for source in daemon.sources],
        )
        try:
            await daemon.run_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("agent-hive daemon interrupted; shutting down")
            await daemon.stop()
            return 130
        finally:
            await runtime.shutdown()
        return 0
    return 1


def _fr02_payload() -> dict[str, object]:
    return {
        "summary": "FR-02 任务巡检与数据缺口识别",
        "inspection_type": "fr02_task_data_gap",
        "use_llm": False,
        "knowledge_space_name": "项目中枢",
        "base_name": "enterprise_rag表",
        "table_names": {
            "project": "项目表",
            "task": "任务表",
            "milestone": "里程碑表",
            "meeting_minutes": "会议纪要表",
            "risk": "风险表",
        },
        "loop_decision": {
            "decision": "finish",
            "thought": "scheduled FR-02 inspection should delegate Feishu data lookup to feishu_tool_agent",
            "summary": "发起 FR-02 任务巡检与数据缺口识别",
            "tool_calls": [
                {
                    "call_type": "feishu_intent",
                    "reason": "巡检任务、里程碑和会议纪要同步状态",
                    "intent": {
                        "domain": "feishu.bitable",
                        "action": "inspect_data_gaps",
                        "target": {
                            "knowledge_space_name": "项目中枢",
                            "base_name": "enterprise_rag表",
                        },
                        "arguments": {
                            "table_names": {
                                "project": "项目表",
                                "task": "任务表",
                                "milestone": "里程碑表",
                                "meeting_minutes": "会议纪要表",
                                "risk": "风险表",
                            },
                            "stale_days": 7,
                        },
                        "constraints": {"remember_discovered_resources": True},
                    },
                }
            ],
            "final_payload": {"inspection_type": "fr02_task_data_gap"},
        },
    }
