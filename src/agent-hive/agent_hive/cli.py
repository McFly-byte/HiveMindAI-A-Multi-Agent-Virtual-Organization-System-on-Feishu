from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from feishu_adapter.event_bridge import to_hive_event

from agent_hive.config.env import load_dotenv_if_present
from agent_hive.config.loader import load_runtime_config
from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.cron_loader import load_cron_jobs
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
    return parser


async def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root)
    load_dotenv_if_present(project_root)
    configure_logging(debug=args.debug or None)
    logger.info("agent-hive command=%s project_root=%s", args.command, args.project_root)
    config = load_runtime_config(project_root)
    logger.debug("runtime config loaded agents=%s memory_db=%s", sorted(config.agents), config.memory_db_path)

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
            result = await runtime.dispatch(event)
            print(json.dumps({"root": result.root_output.model_dump(mode="json"), "children": [item.model_dump(mode="json") for item in result.child_outputs]}, ensure_ascii=False, indent=2))
            return 0 if result.root_session.status == "success" else 1
        finally:
            await runtime.shutdown()

    if args.command == "serve":
        runtime = HiveRuntime.from_config(config)
        daemon = EventDaemon(runtime)
        if args.stdin:
            daemon.add_source(StdinEventSource())

        provider = runtime.providers.get("feishu")
        provider.enable_im_websocket()  # type: ignore[attr-defined]
        provider.load()  # type: ignore[attr-defined]

        for job in load_cron_jobs(project_root / "multi_agents/default/cron"):
            daemon.add_source(
                ScheduledEventSource(
                    name=job.name,
                    event_type=job.event_type,
                    project_id=args.project_id,
                    target_agent_id=job.target_agent_id,
                    payload_factory=lambda payload=job.payload: dict(payload),
                    interval_seconds=job.interval_seconds,
                    run_on_start=job.run_on_start,
                )
            )

        if not daemon.sources:
            raise SystemExit("serve requires at least one source: --stdin or cron jobs in multi_agents/default/cron")

        async def _bridge_feishu_events() -> None:
            while True:
                for raw_event in provider.drain_events():  # type: ignore[attr-defined]
                    evt = to_hive_event(raw_event, project_id=args.project_id, target_agent_id="orchestrator")
                    if evt is not None:
                        await runtime.dispatch(HiveEvent.model_validate(evt))
                await asyncio.sleep(0.5)

        bridge_task = asyncio.create_task(_bridge_feishu_events())
        try:
            await daemon.run_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            await daemon.stop()
            return 130
        finally:
            bridge_task.cancel()
            await runtime.shutdown()
        return 0

    return 1
