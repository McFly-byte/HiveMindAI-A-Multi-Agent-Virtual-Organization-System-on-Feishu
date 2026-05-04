from __future__ import annotations

import json
from typing import Any, Callable

from rich.console import Console
from rich.json import JSON
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()
_live: Live | None = None
_frame_idx = 0
_frames = ["|", "/", "-", "\\"]
_sink: Callable[[str], None] | None = None


def set_sink(sink: Callable[[str], None] | None):
    global _sink
    _sink = sink


def _emit(line: str):
    if _sink is not None:
        _sink(line)


def banner(name: str, spec_path: str):
    console.print(Panel.fit(f"[bold cyan]{name}[/bold cyan]\\n[dim]{spec_path}[/dim]", title="ToolTest"))
    _emit(f"[BANNER] {name} ({spec_path})")


def info(message: str):
    console.print(Panel(message, title="Info", border_style="cyan"))
    _emit(f"[INFO] {message}")


def warn(message: str):
    console.print(Panel(message, title="Warning", border_style="yellow"))
    _emit(f"[WARN] {message}")


def error(message: str):
    console.print(Panel(message, title="Error", border_style="red"))
    _emit(f"[ERROR] {message}")


def print_json(title: str, data: Any, style: str = "cyan"):
    console.print(Panel(JSON.from_data(data), title=title, border_style=style))
    _emit(f"[{title}] {json.dumps(data, ensure_ascii=False)}")


def debug_json(title: str, data: Any):
    _emit(f"[DEBUG {title}] {json.dumps(data, ensure_ascii=False)}")


def tools_table(tools):
    table = Table(title="Registered Tools")
    table.add_column("Name", style="cyan")
    table.add_column("Kind")
    table.add_column("Mode")
    table.add_column("Description")
    for tool in tools:
        table.add_row(tool.spec.name, tool.spec.kind, tool.spec.mode, tool.spec.description)
    console.print(table)
    _emit("[TOOLS] listed")


def jobs_table(jobs):
    table = Table(title="Jobs")
    table.add_column("Job ID", style="cyan")
    table.add_column("Tool")
    table.add_column("State")
    table.add_column("View")
    table.add_column("Progress")
    table.add_column("Latest")
    for job in jobs:
        progress = "" if job.progress is None else f"{job.progress:.0%}"
        latest = "" if not job.latest_output else str(job.latest_output)[:60]
        table.add_row(job.job_id, job.tool_name, job.state, job.view_mode, progress, latest)
    console.print(table)
    _emit("[JOBS] listed")


def event_panel(event):
    console.print(Panel(JSON.from_data(event.to_dict()), title=f"EVENT {event.type}", border_style="magenta"))
    _emit(f"[EVENT {event.type}] {json.dumps(event.to_dict(), ensure_ascii=False)}")


def stream_start(title: str = "LLM Streaming"):
    global _live, _frame_idx
    _frame_idx = 0
    if _live is None:
        _live = Live(Panel("Waiting for first token...", title=title, border_style="cyan"), console=console, refresh_per_second=20)
        _live.start()
    _emit("[STREAM] start")


def stream_tick(preview: str, title: str = "LLM Streaming"):
    global _frame_idx
    if _live is None:
        return
    _frame_idx = (_frame_idx + 1) % len(_frames)
    text = f"{_frames[_frame_idx]} receiving stream\\n\\n{preview[-500:] if preview else '...'}"
    _live.update(Panel(text, title=title, border_style="cyan"))
    # Keep tick internal to visual animation; don't spam external log sinks.


def stream_end(final_text: str, title: str = "Assistant"):
    global _live
    if _live is not None:
        _live.stop()
        _live = None
    console.print(Panel(final_text or "", title=title, border_style="cyan"))
    _emit(f"[ASSISTANT] {final_text}")
