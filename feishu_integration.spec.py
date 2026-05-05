SPEC = {
    "dotenv": ".env",
    "tool_dirs": ["tool_integrations"],
    "event_sources": [
        {
            "name": "feishu_ws",
            "type": "feishu.websocket",
            "enabled": False,
            "app_id_env": "FEISHU_APP_ID",
            "app_secret_env": "FEISHU_APP_SECRET",
        }
    ],
    "agent": {
        "name": "FeishuIntegrationAgent",
        "allowed_toolsets": ["feishu_integration"],
        "event_subscriptions": [
            "cli.user.message",
            "feishu.im.message.received",
            "tool.job.started",
            "tool.job.checkpoint",
            "tool.job.switched_to_background",
            "tool.job.finished",
            "tool.job.failed",
            "tool.job.cancelled",
        ],
        "initial_messages": [
            {
                "role": "system",
                "content": """
你是 FeishuIntegrationAgent。你的职责是通过封装层调用飞书工具。
不要编造工具结果；只有工具调用成功后，才可以说操作完成。
涉及发送消息、回复消息等会影响他人的动作时，必须根据用户明确指令执行。
""".strip(),
            }
        ],
        "persistent_prompt": {
            "role": "system",
            "content": "只根据用户输入、事件和工具结果回答。工具失败时说明 error_type/message。",
        },
        "event_policy": {
            "cli.user.message": {
                "print_to_cli": False,
                "append_to_history": True,
                "trigger_llm": True,
                "render_as": "plain_user_message",
            },
            "feishu.im.message.received": {
                "print_to_cli": True,
                "append_to_history": True,
                "trigger_llm": True,
                "render_as": "structured_event",
            },
            "tool.job.started": {"print_to_cli": True, "append_to_history": False, "trigger_llm": False},
            "tool.job.checkpoint": {"print_to_cli": True, "append_to_history": False, "trigger_llm": False},
            "tool.job.switched_to_background": {"print_to_cli": True, "append_to_history": True, "trigger_llm": True},
            "tool.job.finished": {"print_to_cli": True, "append_to_history": True, "trigger_llm": True},
            "tool.job.failed": {"print_to_cli": True, "append_to_history": True, "trigger_llm": True},
            "tool.job.cancelled": {"print_to_cli": True, "append_to_history": True, "trigger_llm": True},
        },
    },
}
