from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class StoredObjectDto:
    """对象存储返回给应用层的最小内容对象。"""

    content: bytes
    content_type: str


class ObjectStoragePort(Protocol):
    """原始知识库文件的对象存储端口。"""

    def put_object(self, object_key: str, content: bytes, content_type: str) -> None: ...

    def get_object(self, object_key: str) -> StoredObjectDto: ...

    def delete_object(self, object_key: str) -> None: ...
