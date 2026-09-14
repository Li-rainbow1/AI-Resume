"""从私有对象存储抽取 PDF、DOCX 或 Markdown 的图片候选。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.ports.file_parser_port import FileParserPort
from app.application.ports.object_storage_port import ObjectStoragePort
from app.application.ports.rag_document_repository_port import RagDocumentRepositoryPort
from app.domain.exceptions.rag_exceptions import ObjectStorageError
from app.domain.models.rag_image import DocumentImageCandidate


def load_document_image_candidates(
    document: dict[str, Any],
    repository: RagDocumentRepositoryPort,
    parser: FileParserPort,
    object_storage: ObjectStoragePort,
) -> tuple[list[DocumentImageCandidate], str | None]:
    """只读取任务对应的原文和已登记的 Markdown 附件。"""
    document_id = str(document.get("document_id") or "").strip()
    file_name = str(document.get("original_filename") or "document")
    extension = Path(file_name).suffix.lower()
    if extension == ".md":
        candidates: list[DocumentImageCandidate] = []
        for index, asset in enumerate(repository.list_document_assets([document_id])):
            object_key = str(asset.get("object_key") or "").strip()
            relative_path = str(asset.get("relative_path") or "").strip()
            if not object_key or not relative_path:
                continue
            stored = object_storage.get_object(object_key)
            candidates.append(
                DocumentImageCandidate(
                    image_bytes=stored.content,
                    file_name=Path(relative_path).name or "attachment.png",
                    content_type=str(asset.get("content_type") or stored.content_type),
                    source_kind="markdown_attachment",
                    source_locator=relative_path,
                    image_index=index,
                    relative_path=relative_path,
                    asset_id=str(asset.get("asset_id") or "").strip() or None,
                    sha256=str(asset.get("sha256") or "").strip(),
                )
            )
        return candidates, None

    object_key = str(document.get("object_key") or "").strip()
    if not object_key:
        raise ObjectStorageError("知识库原始文件对象不存在")
    stored = object_storage.get_object(object_key)
    extraction = parser.extract_images(
        file_bytes=stored.content,
        file_name=file_name,
        content_type=str(document.get("original_content_type") or stored.content_type),
    )
    return extraction.candidates, extraction.error_message
