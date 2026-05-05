# Tool Agent Harness UV Demo

A small tool testing harness with:

- `tooltest feishuapi.spec.py` command
- uv project
- dotenv support
- `tools/` auto scan
- Rich CLI
- sync / async / switchable tools
- JobRuntime
- runtime metatools
- optional Feishu WebSocket event source
- mock and live Feishu message tools

## Install

```powershell
uv sync
cp .env.example .env
```

## Local smoke test

```powershell
uv run tooltest feishuapi.spec.py
```

## CLI

```powershell
uv run tooltest feishuapi.spec.py
```

Useful commands:

```text
/tools
/alltools
/toolsets
/reload
/call add_numbers {"a":3,"b":5}
/call slow_counter {"total":3,"interval":0.5}
/call switchable_counter {"total":5,"interval":0.7}
/jobs
/job <job_id>
/wait <job_id> 5
/background <job_id>
/cancel <job_id>
/events
/sources
/history
/quit
```

## Tool Integration

Tool integration means wrapping an existing API/adaptor as a `ToolSpec` so the harness can register it, expose it to the LLM, and execute it through `ToolRuntime`.

The current flow is:

```text
*.spec.py -> scan_tool_dirs() -> tools/*.py register() -> ToolRegistry -> LLM tools -> ToolRuntime.invoke()
```

To add or connect a tool:

1. Put the implementation in a Python file under `tools/`.
2. Add `def register(registry, event_bus=None):` in that file.
3. Register each callable with `@registry.register(ToolSpec(...))`.
4. Use the standard function signature:

```python
def my_tool(args: dict, ctx) -> dict:
    ...
```

5. Keep registration side-effect free. Do not connect to Feishu, start websocket listeners, or call external APIs inside `register()`. Do that inside the tool function when it is actually invoked.
6. Make sure the returned dict matches `output_schema`; otherwise `ToolRuntime` will mark the call as failed.
7. Enable the toolset in `feishuapi.spec.py` or another spec via `agent.allowed_toolsets`.

Example direct call that does not require Feishu network access:

```text
/call feishu_bitable_parse_url {"url":"https://example.feishu.cn/base/appABC123?table=tblXYZ&view=vew999"}
```

Useful verification commands:

```text
/toolsets
/tools
/alltools
/call <tool_name> <json_args>
```

## DeepSeek / OpenAI-compatible

Fill `.env`:

```env
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Then run `uv run tooltest feishuapi.spec.py` and type natural language.

## Feishu

Fill `.env`:

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

Then in `feishuapi.spec.py`, set the `feishu.websocket` event source `enabled` to `True`.

Live send/reply tools are present but not included in `allowed_tools` by default. Use mock tools first.

## Notes

`switchable` means: async job + foreground wait. If it finishes quickly, it returns as if sync. If not, it returns a `job_id` and continues in background.
