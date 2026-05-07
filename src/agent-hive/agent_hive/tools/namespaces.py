def toolset_for_name(tool_name: str) -> str:
    if "." in tool_name:
        return tool_name.split(".", 1)[0]
    if tool_name.startswith("memory_"):
        return "memory"
    if tool_name.startswith("feishu_"):
        return "feishu"
    return "unknown"
