SPEC = {
    "dotenv": ".env",
    "tool_dirs": ["tools"],

    # Keep disabled by default for local tests. Set enabled=True after filling FEISHU_APP_ID/SECRET
    # and configuring Feishu event subscription/robot permissions.
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
        "name": "FeishuBotTester",
        # Tool enablement is now controlled by ToolSet.
        # Default ToolSet names come from each tool file name unless that file's
        # register(...) returns explicit toolset names.
        "allowed_toolsets": [
            "feishu_message_tools",
            "feishu_drive",
            "feishu_wiki",
            "feishu_docx",
            "feishu_contact",
            "feishu_im_group",
            "feishu_im_message",
            "feishu_bitable",
            "eval",
        ],
        # Optional fine-grained override. Keep commented for now.
        # "allowed_tools": ["feishu_send_text", "feishu_reply_text"],
        # Optional deny list by ToolSet.
        # "disabled_toolsets": ["demo_tools"],
        "event_subscriptions": [
            "cli.user.message",
            "feishu.ws.started",
            "feishu.ws.error",
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
你是 FeishuBotTester，一个工具测试 Agent。你的目标是测试工具调用、工具事件、switchable 工具、runtime metatools，以及飞书消息 API 的基本闭环。
你不是正式业务 Agent，不使用 RAG，不编造工具结果。
""".strip(),
            },
            {
                "role": "system",
                "content": """
工具语义：
- sync 工具返回 ok=true 且 async=false 时，代表工具已经完成。
- async 工具返回 job_id 只代表任务已启动，不代表完成。
- switchable 工具可能快速完成，也可能返回 switched_to_async=true，此时代表任务已转后台。
- 只有收到 tool.job.finished 或 runtime_read_job 显示 state=succeeded，才可以说后台任务完成。
- 可以使用 runtime_read_job/runtime_wait_job 查询或等待任务。
- 不要随意 runtime_cancel_job，除非用户明确要求取消。
""".strip(),
            },
            {
                "role": "system",
                "content": """
飞书测试语义：
- 收到 feishu.im.message.received 表示机器人收到了一条飞书消息。
""".strip(),
            },
        ],
        "persistent_prompt": {
            "role": "system",
            "content": """
持久规则：
1. 只根据用户输入、事件和工具结果回答。
2. 不要假装工具已经完成；后台任务必须等 finished/succeeded。
3. 如果工具返回 switched_to_async=true，说明已经转后台，应该告知用户等待后续事件或使用 runtime_wait_job。
4. 工具失败时说明 error_type/message。
5. 回答重点是测试结论，简短直接。
""".strip(),
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
            "feishu.ws.started": {"print_to_cli": True, "append_to_history": False, "trigger_llm": False},
            "feishu.ws.error": {"print_to_cli": True, "append_to_history": True, "trigger_llm": False},
        },
    },
}
