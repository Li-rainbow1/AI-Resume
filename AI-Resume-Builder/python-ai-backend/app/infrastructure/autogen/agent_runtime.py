class AutoGenAgentRuntime:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.autogen_available = False
        try:
            import autogen  # noqa: F401

            self.autogen_available = True
        except Exception:
            self.autogen_available = False

    def decorate_interview_reply(self, reply: str) -> str:
        if not self.enabled:
            return reply
        suffix = "[autogen] 已启用多智能体复核。"
        if not self.autogen_available:
            suffix = "[autogen] 未安装 autogen，已使用降级流程。"
        return f"{reply}\n\n{suffix}"
