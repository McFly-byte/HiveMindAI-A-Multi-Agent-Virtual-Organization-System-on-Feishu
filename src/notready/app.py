from __future__ import annotations

import itertools
import json
import re
import threading
import time
from copy import deepcopy
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from adapters.feishu_channel_adapter import FeishuChannelAdapter
from feishu.event_verify import FeishuEventVerifyError, verify_event_token
from runtime.app_runtime import AppRuntime

BASE_DIR = Path(__file__).resolve().parent
FRONT_FILE = "front_dynamic.html"

app = Flask(__name__, static_folder=None)

state_lock = threading.RLock()
state: dict[str, Any] = {
    "agents": [],
    "agent_logs": [],
    "tool_logs": [],
    "event_logs": [],
    "event_stats": {"pending": 0, "total": 0},
    "todos": [],
}

subscribers_lock = threading.RLock()
subscribers: set[Queue] = set()
seq = itertools.count(1)
MAX_LOGS = 500


def now_ms() -> int:
    return int(time.time() * 1000)


def now_hms() -> str:
    return time.strftime("%H:%M:%S")


def ok(data: Optional[dict] = None, status: int = 200):
    payload = {"ok": True}
    if data:
        payload.update(data)
    return jsonify(payload), status


def fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def read_json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def normalize_key(raw: Any) -> str:
    text = safe_str(raw).strip()
    if not text:
        return ""
    return re.sub(r"[\x00-\x1f\x7f]", "", text)


def push_log(bucket: str, item: dict) -> None:
    with state_lock:
        state[bucket].append(item)
        if len(state[bucket]) > MAX_LOGS:
            del state[bucket][0 : len(state[bucket]) - MAX_LOGS]


def sse_pack(event: str, data: Any) -> str:
    return "event: {event}\ndata: {data}\n\n".format(
        event=event,
        data=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    )


def ui_broadcast(event_type: str, payload: dict) -> dict:
    msg = {
        "id": next(seq),
        "type": event_type,
        "ts": now_ms(),
        "payload": payload,
    }
    with subscribers_lock:
        dead = []
        for q in subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            subscribers.discard(q)
    return msg


def state_hook(event_type: str, payload: dict) -> None:
    if event_type == "agent_log":
        push_log("agent_logs", payload)
    elif event_type == "tool_log":
        push_log("tool_logs", payload)


def ui_hook(event_type: str, payload: dict) -> None:
    with state_lock:
        if event_type == "agents_updated":
            state["agents"] = deepcopy(payload.get("agents", []))
        elif event_type == "event_log":
            state["event_logs"].append(deepcopy(payload))
            if len(state["event_logs"]) > MAX_LOGS:
                del state["event_logs"][0 : len(state["event_logs"]) - MAX_LOGS]
        elif event_type == "event_stats":
            state["event_stats"] = deepcopy(payload)
        elif event_type == "todos_updated":
            state["todos"] = deepcopy(payload.get("todos", []))
    ui_broadcast(event_type, payload)


runtime = AppRuntime(ui_event_hook=ui_hook, state_hook=state_hook)
with state_lock:
    snap = runtime.snapshot()
    state["agents"] = snap["agents"]
    state["event_stats"] = snap["event_stats"]
    state["event_logs"] = snap["event_logs"]
    state["todos"] = snap["todos"]


def snapshot() -> dict:
    with state_lock:
        data = deepcopy(state)
    # runtime 是事实源；这里补一次，避免 UI 状态和运行时状态不同步。
    rs = runtime.snapshot()
    data["agents"] = rs["agents"]
    data["event_stats"] = rs["event_stats"]
    data["event_logs"] = rs["event_logs"]
    data["todos"] = rs["todos"]
    data["tools"] = rs["tools"]
    data["agent_definitions"] = rs.get("agent_definitions", {})
    data["feishu"] = rs.get("feishu", {})
    return data


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, FRONT_FILE)


@app.get("/api/ui/stream")
def ui_stream():
    q: Queue = Queue(maxsize=300)
    with subscribers_lock:
        subscribers.add(q)

    @stream_with_context
    def gen():
        try:
            yield sse_pack("snapshot", snapshot())
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield sse_pack(msg["type"], msg)
                except Empty:
                    yield sse_pack("heartbeat", {"ts": now_ms()})
        finally:
            with subscribers_lock:
                subscribers.discard(q)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runtime/snapshot")
def runtime_snapshot():
    return ok({"snapshot": snapshot()})


@app.get("/api/agents/definitions")
def agent_definitions():
    return ok({"agent_definitions": runtime.snapshot().get("agent_definitions", {})})


@app.get("/api/todos")
def list_todos():
    return ok({"todos": runtime.snapshot()["todos"]})


@app.get("/api/feishu/status")
def feishu_status():
    return ok({"feishu": runtime.snapshot().get("feishu", {})})




@app.post("/api/feishu/events")
def feishu_events():
    raw_event = read_json()
    try:
        verify_event_token(raw_event, runtime.feishu_tools.config)
    except FeishuEventVerifyError as exc:
        return fail(str(exc), 401)
    challenge = FeishuChannelAdapter.extract_challenge(raw_event)
    if challenge is not None:
        return jsonify({"challenge": challenge}), 200
    if not FeishuChannelAdapter.is_message_event(raw_event):
        return ok({"ignored": True, "reason": "unsupported_feishu_event"})
    reply = runtime.handle_feishu_im_event(raw_event)
    snap = runtime.snapshot()
    with state_lock:
        state["todos"] = snap["todos"]
        state["event_logs"] = snap["event_logs"]
        state["event_stats"] = snap["event_stats"]
    return ok({"reply": reply, "feishu": runtime.snapshot().get("feishu", {})})






@app.post("/api/chat/stream")
@app.post("/api/chat")
def chat_message():
    data = read_json()
    message = safe_str(data.get("message")).strip()
    if not message:
        return fail("字段 message 不能为空")
    try:
        reply = runtime.handle_web_message(message)
    except Exception as exc:
        return fail(str(exc), 500)
    return ok({"reply": reply, "mode": "non_stream", "sent_by": "channel_ops_agent"})



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
