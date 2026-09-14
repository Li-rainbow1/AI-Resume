from typing import Protocol


class AgentRuntimePort(Protocol):
    def decorate_interview_reply(self, reply: str) -> str: ...
