"""图片增强任务的入队、状态读取和补偿编排。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from app.application.ports.image_enrichment_queue_port import ImageEnrichmentQueuePort
from app.application.ports.rag_document_repository_port import RagDocumentRepositoryPort
from app.application.use_cases.enrich_rag_document_images import ImageEnrichmentSummary
from app.bootstrap.container import (
    build_image_enrichment_queue,
    build_rag_document_repository,
    resolve_settings,
)
from app.domain.exceptions.rag_exceptions import (
    RagDocumentConflictError,
    RagDocumentNotFoundError,
    VectorStoreError,
    safe_rag_log_value,
)


def enqueue_image_enrichment_task(
    document_id: str,
    *,
    repository: RagDocumentRepositoryPort | None = None,
    queue: ImageEnrichmentQueuePort | None = None,
    trace_id: str | None = None,
    force: bool = False,
) -> ImageEnrichmentSummary:
    """先持久化 queued 状态，再把轻量任务消息写入 Redis。"""
    settings = resolve_settings()
    document_repository = repository or build_rag_document_repository(settings)
    safe_document_id = (document_id or "").strip()
    document = document_repository.get_document(safe_document_id)
    if document is None:
        raise RagDocumentNotFoundError("知识库文件不存在")
    if str(document.get("status") or "") != "ready":
        raise RagDocumentConflictError("文件当前不是可处理图片增强的 ready 状态")

    image_status = str(document.get("image_enrichment_status") or "not_applicable")
    if image_status in {"queued", "processing"}:
        return image_enrichment_summary_from_document(document)
    if not force and not bool(document.get("image_enrichment_retryable")):
        raise RagDocumentConflictError("当前文件没有可重试的图片解析任务")

    task_id = uuid4().hex
    if not document_repository.queue_image_enrichment(safe_document_id, task_id, force=force):
        refreshed = document_repository.get_document(safe_document_id)
        if refreshed is None:
            raise RagDocumentNotFoundError("知识库文件不存在")
        return image_enrichment_summary_from_document(refreshed)

    owned_queue = queue is None
    image_queue = queue or build_image_enrichment_queue(settings)
    try:
        image_queue.ensure_consumer_group()
        image_queue.enqueue(safe_document_id, task_id, trace_id)
        document_repository.mark_image_enrichment_enqueued(safe_document_id, task_id)
    except Exception as exc:
        # 数据库中的 queued/failed 记录会被 Worker 扫描补偿，不回滚正文和正文向量。
        document_repository.mark_image_enrichment_queue_failed(
            safe_document_id,
            task_id,
            "图片增强任务暂时无法入队，系统将自动重试",
        )
        _log_queue_service(
            "图片增强任务入队失败，已登记补偿状态",
            trace_id=trace_id,
            document_id=safe_document_id,
            error_type=type(exc).__name__,
        )
    finally:
        if owned_queue:
            image_queue.close()

    refreshed = document_repository.get_document(safe_document_id)
    if refreshed is None:
        raise RagDocumentNotFoundError("知识库文件不存在")
    return image_enrichment_summary_from_document(refreshed)


def get_rag_document_image_enrichment(document_id: str) -> ImageEnrichmentSummary:
    """读取文档主表中的图片增强状态，不触发处理。"""
    repository = build_rag_document_repository()
    document = repository.get_document((document_id or "").strip())
    if document is None:
        raise RagDocumentNotFoundError("知识库文件不存在")
    return image_enrichment_summary_from_document(document)


def image_enrichment_summary_from_document(document: dict[str, Any]) -> ImageEnrichmentSummary:
    """把持久化状态映射为上传、状态查询和重试共用的应用模型。"""
    return ImageEnrichmentSummary(
        candidate_count=_to_non_negative_int(document.get("image_candidate_count")),
        analyzed_count=_to_non_negative_int(document.get("image_analyzed_count")),
        indexed_count=_to_non_negative_int(document.get("image_indexed_count")),
        skipped_count=_to_non_negative_int(document.get("image_skipped_count")),
        failed_count=_to_non_negative_int(document.get("image_failed_count")),
        chunk_count=_to_non_negative_int(document.get("image_chunk_count")),
        status=str(document.get("image_enrichment_status") or "not_applicable"),
        message=str(document.get("image_enrichment_message") or "").strip() or None,
        retryable=bool(document.get("image_enrichment_retryable")),
        task_id=str(document.get("image_enrichment_task_id") or "").strip() or None,
        queued_at=_format_datetime(document.get("image_enrichment_queued_at")),
        current_position=_to_non_negative_int(document.get("image_enrichment_current_position")),
    )


def _to_non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _format_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _log_queue_service(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][图片队列编排] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
