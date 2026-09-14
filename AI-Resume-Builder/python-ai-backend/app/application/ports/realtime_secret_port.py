from typing import Any, Protocol


class RealtimeSecretPort(Protocol):
    def create_client_secret(self, model: str | None = None, language: str | None = None) -> dict[str, Any]: ...
