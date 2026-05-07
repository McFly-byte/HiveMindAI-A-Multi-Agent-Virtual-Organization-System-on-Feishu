from __future__ import annotations

from dataclasses import dataclass

from agent_hive.agents.registry import AgentRegistry
from agent_hive.config.env import load_dotenv_if_present
from agent_hive.config.models import RuntimeConfig
from agent_hive.context.manager import AgentContextManager
from agent_hive.events.models import HiveEvent
from agent_hive.events.router import EventRouter
from agent_hive.memory.manager import MemoryManager
from agent_hive.observability.logging import get_logger
from agent_hive.runtime.agent_runner import AgentRunner
from agent_hive.runtime.session import AgentSession
from agent_hive.schemas.agent import AgentOutput
from agent_hive.schemas.orchestration import OrchestrationAction
from agent_hive.schemas.tool import ToolIntent
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.policy import ToolPolicyEngine
from agent_hive.tools.providers.feishu import FeishuProvider
from agent_hive.tools.providers.llm import LLMProvider
from agent_hive.tools.providers.memory import MemoryProvider
from agent_hive.tools.providers.trace import TraceProvider
from agent_hive.tools.registry import ProviderRegistry


logger = get_logger("runtime.hive_runtime")


@dataclass
class HiveRunResult:
    root_session: AgentSession
    root_output: AgentOutput
    child_sessions: list[AgentSession]
    child_outputs: list[AgentOutput]


class HiveRuntime:
    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig,
        memory_manager: MemoryManager,
        providers: ProviderRegistry,
        agent_registry: AgentRegistry,
        runner: AgentRunner,
    ) -> None:
        self.runtime_config = runtime_config
        self.memory_manager = memory_manager
        self.providers = providers
        self.agent_registry = agent_registry
        self.runner = runner
        self.router = EventRouter(runtime_config)
        self.policy = ToolPolicyEngine()

    @classmethod
    def from_config(cls, runtime_config: RuntimeConfig) -> "HiveRuntime":
        load_dotenv_if_present(runtime_config.project_root)
        logger.info("building HiveRuntime project_root=%s", runtime_config.project_root)
        memory_manager = MemoryManager(runtime_config.memory_db_path)
        providers = ProviderRegistry()
        providers.register(MemoryProvider(memory_manager))
        providers.register(FeishuProvider())
        providers.register(TraceProvider())
        tool_executor = ToolExecutor(providers)
        llm_provider = LLMProvider()
        agent_registry = AgentRegistry(
            runtime_config,
            memory_manager=memory_manager,
            tool_executor=tool_executor,
            llm_provider=llm_provider,
        )
        context_manager = AgentContextManager(memory_manager)
        runner = AgentRunner(agent_registry=agent_registry, context_manager=context_manager, memory_manager=memory_manager)
        return cls(
            runtime_config=runtime_config,
            memory_manager=memory_manager,
            providers=providers,
            agent_registry=agent_registry,
            runner=runner,
        )

    async def dispatch(self, event: HiveEvent) -> HiveRunResult:
        root_agent_id = event.target_agent_id or ("orchestrator" if "orchestrator" in self.runtime_config.agents else None)
        if root_agent_id is None:
            subscribers = self.router.subscribers_for(event)
            if not subscribers:
                raise KeyError(f"no subscribers for event {event.event_type!r}")
            root_agent_id = subscribers[0].agent_id

        logger.info(
            "runtime dispatch started event_id=%s event_type=%s root_agent=%s source=%s",
            event.event_id,
            event.event_type,
            root_agent_id,
            event.source,
        )
        root_session, root_output = await self.runner.run(agent_id=root_agent_id, event=event)
        child_sessions: list[AgentSession] = []
        child_outputs: list[AgentOutput] = []
        for action in root_output.orchestration_actions[: self.runtime_config.get_agent(root_agent_id).runtime_limits.max_child_runs]:
            child_session, child_output = await self._dispatch_orchestration_action(action, event, root_session)
            child_sessions.append(child_session)
            child_outputs.append(child_output)
            for intent in child_output.tool_intents:
                tool_session, tool_output = await self._dispatch_tool_intent(
                    intent,
                    event,
                    child_session,
                    source_agent_id=child_session.agent_id,
                )
                child_sessions.append(tool_session)
                child_outputs.append(tool_output)
        for intent in root_output.tool_intents:
            logger.info(
                "delegating tool intent intent_id=%s domain=%s action=%s requested_by=%s",
                intent.intent_id,
                intent.domain,
                intent.action,
                intent.requested_by_agent_id or root_agent_id,
            )
            child_session, child_output = await self._dispatch_tool_intent(intent, event, root_session, source_agent_id=root_agent_id)
            child_sessions.append(child_session)
            child_outputs.append(child_output)
        logger.info(
            "runtime dispatch finished event_id=%s root_run_id=%s child_runs=%s",
            event.event_id,
            root_session.run_id,
            len(child_sessions),
        )
        return HiveRunResult(
            root_session=root_session,
            root_output=root_output,
            child_sessions=child_sessions,
            child_outputs=child_outputs,
        )

    async def run_agent(self, agent_id: str, event: HiveEvent, payload: dict | None = None) -> tuple[AgentSession, AgentOutput]:
        return await self.runner.run(agent_id=agent_id, event=event, payload=payload)

    async def _dispatch_orchestration_action(
        self,
        action: OrchestrationAction,
        event: HiveEvent,
        parent_session: AgentSession,
    ) -> tuple[AgentSession, AgentOutput]:
        if action.action_type != "run_agent":
            raise ValueError(f"unsupported orchestration action: {action.action_type}")
        if not action.target_agent_id:
            raise ValueError("run_agent orchestration action requires target_agent_id")
        if action.target_agent_id == parent_session.agent_id:
            raise ValueError(f"orchestration action cannot recursively run {action.target_agent_id}")
        self.runtime_config.get_agent(action.target_agent_id)
        logger.info(
            "dispatching orchestration action action_id=%s target_agent=%s reason=%s",
            action.action_id,
            action.target_agent_id,
            action.reason,
        )
        return await self.runner.run(
            agent_id=action.target_agent_id,
            event=event,
            payload=action.payload,
            parent_session=parent_session,
        )

    async def _dispatch_tool_intent(
        self,
        intent: ToolIntent,
        event: HiveEvent,
        parent_session: AgentSession,
        *,
        source_agent_id: str,
    ) -> tuple[AgentSession, AgentOutput]:
        requester = intent.requested_by_agent_id or source_agent_id
        requester_config = self.runtime_config.get_agent(requester)
        target_agent_id = intent.target_agent_id or self.policy.delegated_agent_for(requester_config, intent)
        payload = {"intent": intent.model_dump(mode="json")}
        logger.debug(
            "tool intent target resolved intent_id=%s requester=%s target_agent=%s",
            intent.intent_id,
            requester,
            target_agent_id,
        )
        return await self.runner.run(agent_id=target_agent_id, event=event, payload=payload, parent_session=parent_session)

    async def shutdown(self) -> None:
        logger.info("runtime shutdown started")
        await self.memory_manager.close()
        logger.info("runtime shutdown finished")
