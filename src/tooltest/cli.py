from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from textual.app import App as TextualApp, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Input, Log, Markdown, TabbedContent, TabPane

from .agent import AgentSpec, TestAgent
from .events import Event, EventBus
from .loader import env_config, load_dotenv_for_spec, load_spec, scan_tool_dirs
from .metatools import register_runtime_metatools
from .runtime import ToolRuntime
from .sources import FeishuWsSource
from .tools import ToolRegistry
from . import ui


class Harness:
    def __init__(self, spec_path: Path):
        self.spec_path = spec_path
        self.sources = []
        self._build()

    def _build(self):
        self.spec = load_spec(self.spec_path)
        load_dotenv_for_spec(self.spec)
        self.event_bus = EventBus()
        self.event_bus.subscribe("ui_events", ["*"])
        self.registry = ToolRegistry()
        self.runtime = None

        base_dir = self.spec_path.parent
        scan_tool_dirs(self.registry, self.spec.get("tool_dirs", ["tools"]), base_dir, event_bus=self.event_bus)
        self.runtime = ToolRuntime(self.registry, self.event_bus, env_config())
        register_runtime_metatools(self.registry, lambda: self.runtime)

        agent_cfg = self.spec.get("agent", {})
        all_tool_names = [t.spec.name for t in self.registry.list()]
        meta_tool_names = {t.spec.name for t in self.registry.list() if t.spec.kind == "meta"}
        allowed_tools_cfg = agent_cfg.get("allowed_tools")
        allowed_toolsets_cfg = agent_cfg.get("allowed_toolsets", [])
        disabled_toolsets_cfg = set(agent_cfg.get("disabled_toolsets", []))

        if allowed_tools_cfg is None:
            allowed_tools = set(all_tool_names)
        else:
            allowed_tools = set(allowed_tools_cfg)

        if allowed_toolsets_cfg:
            allowed_tools &= self.registry.tools_in_toolsets(allowed_toolsets_cfg)
            # Keep runtime metatools available by default when filtering by toolsets.
            allowed_tools |= meta_tool_names

        if disabled_toolsets_cfg:
            allowed_tools -= self.registry.tools_in_toolsets(list(disabled_toolsets_cfg))

        self.agent_spec = AgentSpec(
            name=agent_cfg.get("name", "ToolTestAgent"),
            allowed_tools=sorted(allowed_tools),
            initial_messages=agent_cfg.get("initial_messages", []),
            persistent_prompt=agent_cfg.get("persistent_prompt", {"role": "system", "content": "Answer based on tool results."}),
            event_subscriptions=agent_cfg.get("event_subscriptions", ["*"]),
            event_policy=agent_cfg.get("event_policy", {}),
        )
        self.agent = TestAgent(self.agent_spec, self.registry, self.runtime, self.event_bus)

    def reload(self):
        self.stop()
        self.sources = []
        self._build()
        self.start_sources()

    def start_sources(self):
        for source in self.spec.get("event_sources", []):
            if not source.get("enabled", False):
                continue
            if source.get("type") == "feishu.websocket":
                src = FeishuWsSource(
                    name=source.get("name", "feishu_ws"),
                    event_bus=self.event_bus,
                    app_id_env=source.get("app_id_env", "FEISHU_APP_ID"),
                    app_secret_env=source.get("app_secret_env", "FEISHU_APP_SECRET"),
                )
                self.sources.append(src)
                src.start()

    def stop(self):
        for src in self.sources:
            src.stop()
        if self.runtime:
            self.runtime.shutdown()


class ToolTestTextualApp(TextualApp):
    CSS = """
    Screen { layout: vertical; }
    #tabs { height: 1fr; }
    #chat_view { height: 1fr; border: round $accent; overflow-y: auto; }
    #debug_log, #events_log { height: 1fr; border: round $accent; }
    #tools_table, #jobs_table { height: 1fr; }
    #job_actions { height: 3; }
    #input { dock: bottom; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, harness: Harness):
        super().__init__()
        self.harness = harness
        self._busy = False
        self._spin_idx = 0
        self._spin_frames = ["|", "/", "-", "\\"]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Chat", id="chat_tab"):
                yield Markdown("", id="chat_view")
            with TabPane("DebugLog", id="debug_tab"):
                yield Log(id="debug_log", highlight=True)
            with TabPane("Events", id="events_tab"):
                yield Log(id="events_log", highlight=True)
            with TabPane("Tools", id="tools_tab"):
                yield DataTable(id="tools_table")
            with TabPane("Jobs", id="jobs_tab"):
                yield DataTable(id="jobs_table")
                with Horizontal(id="job_actions"):
                    yield Input(placeholder="job_id", id="job_id_input")
                    yield Button("Background", id="bg_btn", variant="primary")
                    yield Button("Cancel", id="cancel_btn", variant="error")
        yield Input(placeholder="Type message or /command, then Enter", id="input")
        yield Footer()

    def on_mount(self):
        self.title = self.harness.agent_spec.name
        self.sub_title = "Status: idle"
        self.harness.start_sources()
        dlog = self.query_one("#debug_log", Log)
        self._init_tools_table()
        self._init_jobs_table()
        self._chat_blocks: list[str] = []
        def sink(line: str):
            if line == "[STREAM] start":
                self._busy = True
                return
            if line.startswith("[ASSISTANT]"):
                self._busy = False
            if line.startswith("[ASSISTANT] "):
                text = line[len("[ASSISTANT] "):]
                if threading.get_ident() == self._thread_id:
                    self._append_chat("assistant", text)
                else:
                    self.call_from_thread(self._append_chat, "assistant", text)
                return
            if threading.get_ident() == self._thread_id:
                dlog.write_line(line)
            else:
                self.call_from_thread(dlog.write_line, line)

        ui.set_sink(sink)
        dlog.write_line("Commands: /tools /alltools /toolsets /reload /call <tool> <json> /sources /jobs /job <id> /wait <id> [sec] /background <id> /cancel <id> /events /history /quit")
        self.set_interval(0.1, self._drain)
        self.set_interval(0.12, self._animate_status)
        self.set_interval(0.5, self._refresh_jobs_table)

    def _drain(self):
        self.harness.agent.drain_events()
        ui_events = self.harness.event_bus.drain("ui_events")
        for event in ui_events:
            if event.type == "runtime.agent.exit_requested":
                ui.info(f"Exit requested by tool: {event.payload.get('reason', 'unknown')}")
                self.exit()
                return
        self._drain_events_tab()

    def _drain_events_tab(self):
        elog = self.query_one("#events_log", Log)
        elog.clear()
        pending = self.harness.event_bus.pending_for(self.harness.agent_spec.name)
        elog.write_line(f"pending: {len(pending)}")
        for event in pending:
            payload = json.dumps(event.payload, ensure_ascii=False)
            elog.write_line(f"{event.type} | {event.source} | {payload}")

    def _animate_status(self):
        if self._busy:
            self._spin_idx = (self._spin_idx + 1) % len(self._spin_frames)
            self.sub_title = f"Status: {self._spin_frames[self._spin_idx]} LLM thinking"
        else:
            self.sub_title = "Status: idle"

    def _init_tools_table(self):
        table = self.query_one("#tools_table", DataTable)
        table.clear(columns=True)
        table.add_columns("Name", "Kind", "Mode", "Description")
        for t in self.harness.registry.list():
            table.add_row(t.spec.name, t.spec.kind, t.spec.mode, t.spec.description)

    def _init_jobs_table(self):
        table = self.query_one("#jobs_table", DataTable)
        table.clear(columns=True)
        table.add_columns("Job ID", "Tool", "State", "View", "Progress", "Latest")

    def _refresh_jobs_table(self):
        table = self.query_one("#jobs_table", DataTable)
        table.clear()
        for job in self.harness.runtime.jobs.list():
            progress = "" if job.progress is None else f"{job.progress:.0%}"
            latest = "" if not job.latest_output else str(job.latest_output)[:60]
            table.add_row(job.job_id, job.tool_name, job.state, job.view_mode, progress, latest)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "input":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self._append_chat("user", text)
        if text.startswith("/"):
            if self._handle_command(text):
                self.exit()
            return
        self.harness.event_bus.publish(Event(type="cli.chat.input", source="cli", payload={"text": text, "raw": text}))
        self.harness.agent.drain_events()

    def _append_chat(self, role: str, text: str):
        prefix = "You" if role == "user" else "Assistant"
        self._chat_blocks.append(f"### {prefix}\n\n{text}\n")
        md = self.query_one("#chat_view", Markdown)
        md.update("\n---\n".join(self._chat_blocks[-100:]))

    def on_button_pressed(self, event: Button.Pressed):
        job_id = self.query_one("#job_id_input", Input).value.strip()
        if not job_id:
            ui.warn("Enter job_id in Jobs tab first.")
            return
        if event.button.id == "bg_btn":
            result = self.harness.runtime.invoke("runtime_switch_job_to_background", {"job_id": job_id, "reason": "ui_requested"})
            ui.print_json("Background", result, "cyan")
        elif event.button.id == "cancel_btn":
            result = self.harness.runtime.invoke("runtime_cancel_job", {"job_id": job_id, "reason": "ui_requested"})
            ui.print_json("Cancel", result, "yellow")
        self._refresh_jobs_table()

    def _handle_command(self, text: str) -> bool:
        parts = text.split(maxsplit=2)
        cmd = parts[0]
        if cmd in ("/quit", "/exit"):
            return True
        if cmd == "/tools":
            allowed = set(self.harness.agent_spec.allowed_tools)
            ui.tools_table([t for t in self.harness.registry.list() if t.spec.name in allowed])
            return False
        if cmd == "/alltools":
            ui.tools_table(self.harness.registry.list())
            return False
        if cmd == "/toolsets":
            ui.print_json("ToolSets", self.harness.registry.list_toolsets(), "cyan")
            return False
        if cmd == "/reload":
            self.harness.reload()
            self.title = self.harness.agent_spec.name
            self._init_tools_table()
            self._init_jobs_table()
            ui.info("Reloaded spec, agent, and toolset.")
            return False
        if cmd == "/sources":
            ui.print_json("Event Sources", [s.to_dict() for s in self.harness.sources], "cyan")
            return False
        if cmd == "/jobs":
            ui.jobs_table(self.harness.runtime.jobs.list())
            return False
        if cmd == "/events":
            ui.print_json("Recent Events", [e.to_dict() for e in self.harness.event_bus.recent(30)], "magenta")
            return False
        if cmd == "/history":
            ui.print_json("Agent History", self.harness.agent.messages, "cyan")
            return False
        if cmd == "/job":
            if len(parts) < 2:
                ui.warn("Usage: /job <job_id>")
                return False
            result = self.harness.runtime.invoke("runtime_read_job", {"job_id": parts[1]})
            ui.print_json("Job", result, "cyan")
            return False
        if cmd == "/wait":
            args = text.split()
            if len(args) < 2:
                ui.warn("Usage: /wait <job_id> [timeout_seconds]")
                return False
            timeout = float(args[2]) if len(args) >= 3 else 5.0
            result = self.harness.runtime.invoke("runtime_wait_job", {"job_id": args[1], "timeout_seconds": timeout})
            ui.print_json("Wait Job", result, "cyan")
            return False
        if cmd == "/background":
            args = text.split(maxsplit=2)
            if len(args) < 2:
                ui.warn("Usage: /background <job_id> [reason]")
                return False
            result = self.harness.runtime.invoke("runtime_switch_job_to_background", {"job_id": args[1], "reason": args[2] if len(args) > 2 else "cli_requested"})
            ui.print_json("Background", result, "cyan")
            return False
        if cmd == "/cancel":
            args = text.split(maxsplit=2)
            if len(args) < 2:
                ui.warn("Usage: /cancel <job_id> [reason]")
                return False
            result = self.harness.runtime.invoke("runtime_cancel_job", {"job_id": args[1], "reason": args[2] if len(args) > 2 else "cli_requested"})
            ui.print_json("Cancel", result, "yellow")
            return False
        if cmd == "/call":
            if len(parts) < 2:
                ui.warn("Usage: /call <tool_name> <json_args>")
                return False
            tool_name = parts[1]
            raw_json = parts[2] if len(parts) >= 3 else "{}"
            try:
                args = json.loads(raw_json)
            except Exception as e:
                ui.error(f"Invalid JSON args: {e}")
                return False
            result = self.harness.runtime.invoke(tool_name, args)
            ui.print_json(f"Tool Result: {tool_name}", result, "green" if result.get("ok") else "red")
            time.sleep(0.05)
            self.harness.agent.drain_events()
            return False
        ui.warn(f"Unknown command: {cmd}")
        return False

    def on_unmount(self):
        ui.set_sink(None)
        self.harness.stop()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="tooltest")
    parser.add_argument("spec", help="Path to .spec.py file")
    args = parser.parse_args(argv)
    harness = Harness(Path(args.spec).resolve())
    ToolTestTextualApp(harness).run()


if __name__ == "__main__":
    main()
