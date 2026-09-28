from typing import Any, Protocol


class RerankerPort(Protocol):
    def rerank(self, query: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
