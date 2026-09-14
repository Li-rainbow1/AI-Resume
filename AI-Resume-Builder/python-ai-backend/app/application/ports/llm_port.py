from collections.abc import Iterator
from typing import Protocol


class ChatClientPort(Protocol):
    def chat(self, message: str, system_prompt: str | None = None) -> str: ...

    def stream_chat(self, message: str, system_prompt: str | None = None) -> Iterator[str]: ...
