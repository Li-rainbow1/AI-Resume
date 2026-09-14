from typing import Protocol

from app.domain.models.rag_image import DocumentImageExtractionResult
from app.domain.models.rag_document import ExtractedDocument


class FileParserPort(Protocol):
    # 文档类文件解析抽象。
    # use case 只依赖这个接口，不关心底层究竟是 pypdf、python-docx 还是别的实现。
    def parse(
        self,
        file_bytes: bytes,
        file_name: str,
        content_type: str | None = None,
    ) -> ExtractedDocument: ...

    def extract_images(
        self,
        file_bytes: bytes,
        file_name: str,
        content_type: str | None = None,
    ) -> DocumentImageExtractionResult: ...
