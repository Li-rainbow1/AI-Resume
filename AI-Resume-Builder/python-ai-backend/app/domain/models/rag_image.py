"""知识库复合文档中的图片候选和视觉解析结果。"""

from dataclasses import dataclass, field
from typing import Any, Literal

ImageClassification = Literal["text", "table", "diagram", "decorative", "empty"]


@dataclass(slots=True)
class DocumentImageCandidate:
    """待交给视觉模型判断的图片，不保存原始字节到数据库。"""

    image_bytes: bytes
    file_name: str
    content_type: str
    source_kind: str
    source_locator: str
    image_index: int
    page_number: int | None = None
    paragraph_index: int | None = None
    relative_path: str | None = None
    asset_id: str | None = None
    sha256: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentImageExtractionResult:
    """复合文档图片抽取结果，保留候选和抽取阶段的失败信息。"""

    candidates: list[DocumentImageCandidate] = field(default_factory=list)
    error_message: str | None = None


@dataclass(slots=True)
class ImageAnalysisResult:
    """视觉模型一次请求返回的分类、OCR 和图示说明。"""

    classification: ImageClassification
    ocr_text: str = ""
    description: str = ""
    confidence: float | None = None
