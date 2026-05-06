from __future__ import annotations

import asyncio

from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.feishu_im import FeishuIMEventSource
from agent_hive.runtime.daemon import EventDaemon


class FakeRuntime:
    def __init__(self) -> None:
        self.events: list[HiveEvent] = []

    async def dispatch(self, event: HiveEvent) -> None:
        self.events.append(event)


def test_event_daemon_dispatches_queued_messages() -> None:
    async def _run() -> None:
        runtime = FakeRuntime()
        daemon = EventDaemon(runtime)  # type: ignore[arg-type]
        event = HiveEvent(event_type="feishu.im.message.received", project_id="p1", payload={"text": "hello"})

        await daemon.emit(event)
        await daemon.run_until_idle(timeout_seconds=1)

        assert runtime.events == [event]
        assert daemon.stats.received_events == 1
        assert daemon.stats.dispatched_events == 1
        assert daemon.stats.failed_events == 0

    asyncio.run(_run())


def test_feishu_im_source_normalizes_message_payload() -> None:
    source = FeishuIMEventSource(FakeRuntime(), project_id="p1")  # type: ignore[arg-type]

    event = source._normalize(
        {
            "event_type": "feishu.im.message.received",
            "source": "feishu.ws",
            "payload": {
                "raw": {
                    "event": {
                        "message": {
                            "chat_id": "oc_1",
                            "chat_type": "p2p",
                            "content": "{\"text\":\"你好\"}",
                            "message_id": "om_1",
                            "message_type": "text",
                        },
                        "sender": {
                            "sender_id": {"open_id": "ou_1", "user_id": "u_1"},
                            "sender_type": "user",
                        },
                    }
                }
            },
        }
    )

    assert event is not None
    assert event.payload["text"] == "你好"
    assert event.payload["chat_id"] == "oc_1"
    assert event.payload["message_id"] == "om_1"
    assert event.payload["sender_open_id"] == "ou_1"
