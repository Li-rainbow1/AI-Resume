from dataclasses import dataclass, field
from typing import Any


# 文件经过“文本解析 / 图片 OCR”之后的统一中间形态。
# 后续分块、Embedding、向量存储都基于这个对象继续处理。
@dataclass(slots=True)
class ExtractedDocument:
    source_id: str
    original_filename: str
    original_content_type: str
    source_type: str
    ingest_source: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


# 最终进入向量库的最小文本单元。
@dataclass(slots=True)
class RagChunk:
    source_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
