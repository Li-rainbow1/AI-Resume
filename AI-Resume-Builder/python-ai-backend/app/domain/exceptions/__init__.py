"""Domain exceptions.

这里统一导出上传 / OCR / Embedding 相关异常，方便 API 层集中注册错误映射。
"""

from app.domain.exceptions.rag_exceptions import (
    EmbeddingError,
    FileParseError,
    FileTooLargeError,
    ImageOcrError,
    RagIngestError,
    UnsupportedFileTypeError,
    VectorStoreError,
)

__all__ = [
    "RagIngestError",
    "UnsupportedFileTypeError",
    "FileTooLargeError",
    "FileParseError",
    "ImageOcrError",
    "EmbeddingError",
    "VectorStoreError",
]
