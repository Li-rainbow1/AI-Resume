"""图片增强队列端口，application 层不感知 Redis 具体实现。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class ImageEnrichmentJob:
    document_id: str
    task_id: str
    trace_id: str | None = None
    message_id: str | None = None


class ImageEnrichmentQueuePort(Protocol):
    def ensure_consumer_group(self) -> None: ...

    def enqueue(self, document_id: str, task_id: str, trace_id: str | None = None) -> str: ...

    def read(self, consumer_name: str, block_millis: int = 1000) -> list[ImageEnrichmentJob]: ...

    def acknowledge(self, job: ImageEnrichmentJob) -> None: ...

    def close(self) -> None: ...
