from typing import Protocol

from app.domain.models.rag_image import ImageAnalysisResult

class ImageMarkdownOcrPort(Protocol):
    # 图片 OCR 抽象。
    # 约束输出必须是 Markdown，便于后面的分块和向量化统一处理。
    def extract_markdown(
        self,
        image_bytes: bytes,
        file_name: str,
        content_type: str | None = None,
    ) -> str: ...

    def analyze_image(
        self,
        image_bytes: bytes,
        file_name: str,
        content_type: str | None = None,
    ) -> ImageAnalysisResult: ...
