from __future__ import annotations

import json
from typing import Any

from agent_hive.agents.registry import AgentRegistry
from agent_hive.context.manager import AgentContextManager
from agent_hive.events.models import HiveEvent
from agent_hive.memory.manager import MemoryManager
from agent_hive.observability.logging import get_logger
from agent_hive.runtime.session import AgentSession
from agent_hive.schemas.agent import AgentOutput


logger = get_logger("runtime.agent_runner")


class AgentRunner:
    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        context_manager: AgentContextManager,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.context_manager = context_manager
        self.memory_manager = memory_manager

    async def run(
        self,
        *,
        agent_id: str,
        event: HiveEvent,
        payload: dict[str, Any] | None = None,
        parent_session: AgentSession | None = None,
    ) -> tuple[AgentSession, AgentOutput]:
        config = self.agent_registry.get_config(agent_id)
        session = AgentSession(
            parent_run_id=parent_session.run_id if parent_session else None,
            project_id=event.project_id,
            agent_id=agent_id,
            input_event_id=event.event_id,
            input_payload=payload or event.payload,
        )
        session.mark_running()
        logger.info(
            "agent run started run_id=%s agent_id=%s event_id=%s event_type=%s parent_run_id=%s",
            session.run_id,
            agent_id,
            event.event_id,
            event.event_type,
            session.parent_run_id,
        )
        try:
            context = await self.context_manager.build(session=session, agent_config=config, event=event)
            logger.debug(
                "agent context built run_id=%s agent_id=%s item_count=%s size_chars=%s",
                session.run_id,
                agent_id,
                len(context.items),
                context.size_chars,
            )
            agent = self.agent_registry.get_agent(agent_id)
            output = await agent.run(context, payload or event.payload)
            session.add_output(output.model_dump(mode="json"))
            session.mark_success(output.summary)
            logger.info(
                "agent run finished run_id=%s agent_id=%s status=%s summary=%s orchestration_actions=%s tool_intents=%s tool_results=%s output_payload=%s",
                session.run_id,
                agent_id,
                session.status,
                output.summary,
                len(output.orchestration_actions),
                len(output.tool_intents),
                len(output.tool_results),
                _truncate_json(output.payload),
            )
            if config.memory.enabled and config.memory.auto_write_run_summary and self.memory_manager and output.summary:
                await self.memory_manager.write(
                    content=output.summary,
                    agent_id=agent_id,
                    project_id=session.project_id,
                    run_id=session.run_id,
                    tags=["agent_run", agent_id],
                    metadata={"status": output.status, "event_id": event.event_id},
                )
            return session, output
        except Exception as exc:
            session.mark_failed(str(exc))
            logger.exception("agent run failed run_id=%s agent_id=%s error=%s", session.run_id, agent_id, exc)
            raise


def _truncate_json(value: Any, *, limit: int = 1200) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
