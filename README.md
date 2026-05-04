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
uv run python run_local_smoke.py
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
