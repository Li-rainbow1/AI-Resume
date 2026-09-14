"""Worker 使用的图片增强处理编排，负责从私有存储重建候选。"""

from __future__ import annotations

from pathlib import Path

from app.application.services.rag_image_candidate_loader import load_document_image_candidates
from app.application.use_cases.enrich_rag_document_images import (
    ImageEnrichmentSummary,
    enrich_rag_document_images,
)
from app.bootstrap.container import (
    build_document_chunking_service,
    build_embedding_client,
    build_file_parser,
    build_image_markdown_ocr_client,
    build_rag_document_repository,
    build_rag_object_storage,
    build_vector_store,
    resolve_settings,
)
from app.domain.exceptions.rag_exceptions import (
    RagDocumentConflictError,
    RagDocumentNotFoundError,
    safe_rag_log_value,
)


def process_rag_document_image_enrichment(
    document_id: str,
    task_id: str,
    trace_id: str | None = None,
) -> ImageEnrichmentSummary:
    """处理一条队列消息；正文已 ready 时只补充图片 Chunk。"""
    settings = resolve_settings()
    repository = build_rag_document_repository(settings)
    document = repository.get_document((document_id or "").strip())
    if document is None:
        raise RagDocumentNotFoundError("知识库文件不存在")
    if str(document.get("status") or "") != "ready":
        raise RagDocumentConflictError("文件已删除或当前不可处理图片增强")

    parser = build_file_parser(settings)
    object_storage = build_rag_object_storage(settings)
    try:
        candidates, extraction_error = load_document_image_candidates(
            document,
            repository,
            parser,
            object_storage,
        )
    except Exception as exc:
        _log_processor(
            "读取图片候选失败，正文保持可用",
            trace_id=trace_id,
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        candidates = []
        extraction_error = "图片原文件或附件读取失败，可重试图片解析"

    file_name = str(document.get("original_filename") or "document")
    preflight_error = extraction_error
    preflight_retryable = True
    if Path(file_name).suffix.lower() == ".md" and int(document.get("missing_image_count") or 0) > 0:
        preflight_error = "Markdown 图片附件未关联，需删除后携带附件重新上传"
        preflight_retryable = False

    vector_store = build_vector_store(settings)
    return enrich_rag_document_images(
        document_id=document_id,
        candidates=candidates,
        repository=repository,
        image_ocr=build_image_markdown_ocr_client(settings),
        embedding_client=build_embedding_client(settings),
        chunking_service=build_document_chunking_service(settings),
        embedding_model=getattr(vector_store, "embedding_model_name", settings.embedding_model_name),
        trace_id=trace_id,
        file_name=file_name,
        retry_only=True,
        preflight_error=preflight_error,
        preflight_retryable=preflight_retryable,
        task_id=task_id,
        concurrency=settings.rag_image_enrichment_concurrency,
    )


def _log_processor(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][图片Worker] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
