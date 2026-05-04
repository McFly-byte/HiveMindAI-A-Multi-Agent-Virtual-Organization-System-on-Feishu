from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any

from .events import EventBus, Event
from .runtime import ToolRuntime
from .tools import ToolRegistry
from . import ui


@dataclass
class AgentSpec:
    name: str
    allowed_tools: list[str]
    initial_messages: list[dict[str, str]]
    persistent_prompt: dict[str, str]
    event_subscriptions: list[str]
    event_policy: dict[str, dict[str, Any]]


class TestAgent:
    def __init__(self, spec: AgentSpec, registry: ToolRegistry, runtime: ToolRuntime, event_bus: EventBus):
        self.spec = spec
        self.registry = registry
        self.runtime = runtime
        self.event_bus = event_bus
        self.messages: list[dict[str, Any]] = list(spec.initial_messages)
        subscriptions = list(spec.event_subscriptions)
        if "*" not in subscriptions and "cli.chat.input" not in subscriptions:
            subscriptions.append("cli.chat.input")
        self.event_bus.subscribe(spec.name, subscriptions)
        self.client = None
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if api_key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=api_key, base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
            except Exception as e:
                ui.warn(f"OpenAI-compatible client is unavailable: {e}")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self._llm_lock = threading.Lock()
        self._llm_running = False
        self._llm_rerun = False

    def available_tools(self):
        return self.registry.to_llm_tools(self.spec.allowed_tools)

    def add_cli_message(self, text: str):
        self.messages.append({"role": "user", "content": text})
        self.run_llm_turn()

    def drain_events(self):
        events = self.event_bus.drain(self.spec.name)
        should_run = False
        for event in events:
            if event.type == "cli.chat.input":
                self.messages.append({"role": "user", "content": str(event.payload.get("text", ""))})
                should_run = True
                continue
            policy = self.spec.event_policy.get(event.type, {})
            if policy.get("print_to_cli", True):
                ui.event_panel(event)
            if policy.get("append_to_history", False):
                self.messages.append(self.render_event_message(event, policy.get("render_as", "structured_event")))
            if policy.get("trigger_llm", False):
                should_run = True
        if should_run:
            self.run_llm_turn()

    def render_event_message(self, event: Event, render_as: str) -> dict[str, Any]:
        if event.type == "cli.user.message" and render_as == "plain_user_message":
            return {"role": "user", "content": str(event.payload.get("text", ""))}
        return {"role": "user", "content": "[EVENT]\n" + json.dumps(event.to_dict(), ensure_ascii=False)}

    def build_messages(self) -> list[dict[str, Any]]:
        # Persistent prompt is appended only for the current model call, not saved into history.
        return self.messages + [self.spec.persistent_prompt]

    def run_llm_turn(self):
        with self._llm_lock:
            if self._llm_running:
                self._llm_rerun = True
                return
            self._llm_running = True
        threading.Thread(target=self._run_llm_turn_worker, daemon=True).start()

    def _run_llm_turn_worker(self):
        if not self.client:
            ui.warn("LLM is not configured. Set DEEPSEEK_API_KEY in .env, or use /call for direct tool tests.")
            with self._llm_lock:
                self._llm_running = False
            return
        try:
            while True:
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.build_messages(),
                    tools=self.available_tools(),
                    stream=True,
                )
                ui.debug_json("LLM_INPUT", {"model": self.model, "messages": self.build_messages(), "tools": self.available_tools()})
                ui.stream_start()
                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict[str, Any]] = {}
                for chunk in stream:
                    choice = (chunk.choices or [None])[0]
                    if not choice:
                        continue
                    delta = choice.delta
                    if getattr(delta, "content", None):
                        content_parts.append(delta.content)
                    for tc in (getattr(delta, "tool_calls", None) or []):
                        idx = tc.index
                        acc = tool_calls_acc.setdefault(idx, {"id": tc.id, "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                acc["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                acc["function"]["arguments"] += tc.function.arguments

                msg_dict: dict[str, Any] = {"role": "assistant"}
                final_content = "".join(content_parts)
                if final_content:
                    msg_dict["content"] = final_content
                if tool_calls_acc:
                    msg_dict["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                self.messages.append(msg_dict)
                ui.stream_end(final_content, title="Assistant")
                self.event_bus.publish(Event(type="chat.tool.output", source=self.spec.name, payload={"text": final_content}))
                tool_calls = msg_dict.get("tool_calls", [])
                if not tool_calls:
                    break
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    ui.print_json(f"TOOL_CALL {name}", args, "blue")
                    result = self.runtime.invoke(name, args)
                    ui.print_json(f"TOOL_RESULT {name}", result, "green" if result.get("ok") else "red")
                    self.messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result, ensure_ascii=False)})
        finally:
            rerun = False
            with self._llm_lock:
                rerun = self._llm_rerun
                self._llm_rerun = False
                self._llm_running = False
            if rerun:
                self.run_llm_turn()
