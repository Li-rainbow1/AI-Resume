"""复合文档图片视觉解析、文本向量化和失败重试用例。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock
from typing import Any
from uuid import uuid4

from app.application.ports.embedding_port import EmbeddingPort
from app.application.ports.image_markdown_ocr_port import ImageMarkdownOcrPort
from app.application.ports.rag_document_repository_port import RagDocumentRepositoryPort
from app.domain.exceptions.rag_exceptions import (
    EmbeddingChunkRetryableError,
    EmbeddingError,
    ImageEnrichmentLeaseLostError,
    RagDocumentNotFoundError,
    VectorStoreError,
    safe_rag_log_value,
)
from app.domain.models.rag_document import ExtractedDocument, RagChunk
from app.domain.models.rag_image import DocumentImageCandidate, ImageAnalysisResult
from app.domain.services.rag_embedding_context_service import chunk_embedding_text, image_embedding_context

ImageProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class ImageEnrichmentSummary:
    candidate_count: int = 0
    analyzed_count: int = 0
    indexed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    processing_count: int = 0
    chunk_count: int = 0
    status: str = "not_applicable"
    message: str | None = None
    retryable: bool = False
    attempt_token: str | None = None
    task_id: str | None = None
    queued_at: str | None = None
    current_position: int = 0


def enrich_rag_document_images(
    *,
    document_id: str,
    candidates: list[DocumentImageCandidate],
    repository: RagDocumentRepositoryPort,
    image_ocr: ImageMarkdownOcrPort,
    embedding_client: EmbeddingPort,
    chunking_service: Any,
    embedding_model: str,
    trace_id: str | None = None,
    progress_callback: ImageProgressCallback | None = None,
    file_index: int | None = None,
    total_files: int | None = None,
    file_name: str | None = None,
    retry_only: bool = False,
    preflight_error: str | None = None,
    preflight_retryable: bool = True,
    task_id: str | None = None,
    concurrency: int = 1,
) -> ImageEnrichmentSummary:
    """处理图片候选，图片增强失败只更新增强状态，不回滚 ready 正文。"""
    document = repository.get_document(document_id)
    if document is None:
        raise RagDocumentNotFoundError("知识库文件不存在")
    try:
        document_attempt_token = repository.begin_image_enrichment(
            document_id,
            task_id=task_id,
        )
        if not document_attempt_token:
            summary = _summary_from_rows(repository.list_document_image_extractions(document_id))
            summary.status = str(document.get("image_enrichment_status") or "processing")
            summary.message = str(
                document.get("image_enrichment_message") or "图片增强正在处理中，请稍后查看"
            )
            summary.retryable = False
            summary.task_id = str(document.get("image_enrichment_task_id") or "").strip() or task_id
            summary.current_position = _safe_int(document.get("image_enrichment_current_position"))
            return summary
    except Exception as exc:
        _log_enrichment(
            trace_id,
            "领取图片增强任务失败，正文保持可用",
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        return ImageEnrichmentSummary(
            status="failed",
            message="图片增强任务暂时不可用，可稍后重试图片解析",
            retryable=True,
        )

    safe_candidates = [candidate for candidate in candidates if candidate.image_bytes]
    if preflight_error and not safe_candidates:
        summary = ImageEnrichmentSummary(
            status="failed",
            message=preflight_error,
            retryable=preflight_retryable,
            attempt_token=document_attempt_token,
        )
        _finish_image_enrichment(repository, document_id, summary, trace_id, document_attempt_token)
        return summary

    try:
        existing_rows = repository.list_document_image_extractions(document_id)
    except Exception as exc:
        _log_enrichment(
            trace_id,
            "读取图片解析状态失败，正文保持可用",
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        summary = ImageEnrichmentSummary(
            status="failed",
            message="图片解析状态读取失败，可稍后重试图片解析",
            retryable=True,
            attempt_token=document_attempt_token,
        )
        _finish_image_enrichment(repository, document_id, summary, trace_id, document_attempt_token)
        return summary

    existing_by_source = {
        (
            str(row.get("source_kind") or "").strip(),
            str(row.get("source_locator") or "").strip(),
        ): row
        for row in existing_rows
    }
    analysis_cache = _ThreadSafeAnalysisCache()
    worker_count = min(3, max(1, int(concurrency)))
    _log_enrichment(
        trace_id,
        "开始图片增强",
        document_id=document_id,
        candidate_count=len(safe_candidates),
        retry_only=retry_only,
        concurrency=worker_count,
    )
    futures = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="rag-image") as executor:
        for candidate_index, candidate in enumerate(safe_candidates, start=1):
            source_key = (candidate.source_kind.strip(), candidate.source_locator.strip())
            existing = existing_by_source.get(source_key)
            existing_status = str(existing.get("status") or "") if existing else ""
            if retry_only and existing is not None and existing_status not in {"failed", "processing"}:
                continue
            futures.append(
                executor.submit(
                    _process_image_candidate,
                    document=document,
                    document_id=document_id,
                    candidate=candidate,
                    candidate_index=candidate_index,
                    image_count=len(safe_candidates),
                    existing=existing,
                    repository=repository,
                    image_ocr=image_ocr,
                    embedding_client=embedding_client,
                    chunking_service=chunking_service,
                    embedding_model=embedding_model,
                    analysis_cache=analysis_cache,
                    trace_id=trace_id,
                    progress_callback=progress_callback,
                    file_index=file_index,
                    total_files=total_files,
                    file_name=file_name,
                )
            )
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                # 单张图片已在 worker 内尽量落 failed；这里兜住线程边界，不能取消其他图片。
                _log_enrichment(
                    trace_id,
                    "图片线程异常，其他图片继续处理",
                    document_id=document_id,
                    error_type=type(exc).__name__,
                )

    try:
        summary = _summary_from_rows(repository.list_document_image_extractions(document_id))
        current_document = repository.get_document(document_id) or {}
        summary.chunk_count = max(0, int(current_document.get("image_chunk_count") or 0))
        summary.candidate_count = max(summary.candidate_count, len(safe_candidates))
        summary.current_position = len(safe_candidates)
    except Exception as exc:
        _log_enrichment(
            trace_id,
            "读取图片增强汇总失败，正文仍保持可用",
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        summary = ImageEnrichmentSummary(
            candidate_count=len(safe_candidates),
            status="failed",
            message="图片增强汇总失败，可稍后重试图片解析",
            retryable=True,
            attempt_token=document_attempt_token,
        )
    if preflight_error:
        summary.status = "partial_failed" if summary.indexed_count or summary.skipped_count else "failed"
        summary.message = preflight_error
        summary.retryable = preflight_retryable
    summary.attempt_token = document_attempt_token
    summary.task_id = task_id or str(document.get("image_enrichment_task_id") or "").strip() or None
    summary = _finish_image_enrichment(
        repository,
        document_id,
        summary,
        trace_id,
        document_attempt_token,
    )
    _log_enrichment(
        trace_id,
        "图片增强完成",
        document_id=document_id,
        candidate_count=summary.candidate_count,
        indexed_count=summary.indexed_count,
        skipped_count=summary.skipped_count,
        failed_count=summary.failed_count,
        chunk_count=summary.chunk_count,
    )
    return summary


class _ThreadSafeAnalysisCache:
    """同一任务内按图片摘要复用 Vision 结果，避免并发线程重复请求。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, tuple[Event, ImageAnalysisResult | BaseException | None]] = {}

    def get_or_compute(self, key: str, callback: Callable[[], ImageAnalysisResult]) -> ImageAnalysisResult:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                event = Event()
                self._entries[key] = (event, None)
                owner = True
            else:
                event, _ = entry
                owner = False
        if owner:
            try:
                result = callback()
            except BaseException as exc:
                with self._lock:
                    self._entries[key] = (event, exc)
                    event.set()
                raise
            with self._lock:
                self._entries[key] = (event, result)
                event.set()
            return result

        event.wait()
        with self._lock:
            _, result = self._entries[key]
        if isinstance(result, BaseException):
            raise result
        if not isinstance(result, ImageAnalysisResult):
            raise VectorStoreError("图片视觉解析缓存结果非法")
        return result


def _process_image_candidate(
    *,
    document: dict[str, Any],
    document_id: str,
    candidate: DocumentImageCandidate,
    candidate_index: int,
    image_count: int,
    existing: dict[str, Any] | None,
    repository: RagDocumentRepositoryPort,
    image_ocr: ImageMarkdownOcrPort,
    embedding_client: EmbeddingPort,
    chunking_service: Any,
    embedding_model: str,
    analysis_cache: _ThreadSafeAnalysisCache,
    trace_id: str | None,
    progress_callback: ImageProgressCallback | None,
    file_index: int | None,
    total_files: int | None,
    file_name: str | None,
) -> None:
    extraction_id = str(existing.get("extraction_id") or uuid4().hex) if existing else uuid4().hex
    active: tuple[str, str] | None = None
    extraction_base: dict[str, Any] | None = None
    analysis: ImageAnalysisResult | None = None
    try:
        claim = repository.claim_image_extraction(
            document_id,
            _build_extraction_payload(
                document_id=document_id,
                extraction_id=extraction_id,
                candidate=candidate,
            ),
        )
        if claim is None:
            _log_enrichment(
                trace_id,
                "图片解析任务已被其他调用方处理，跳过当前候选",
                document_id=document_id,
                source_locator=candidate.source_locator,
            )
            return
        extraction_id = str(claim.get("extraction_id") or "").strip()
        extraction_attempt_token = str(claim.get("attempt_token") or "").strip()
        if not extraction_id or not extraction_attempt_token:
            raise VectorStoreError("图片解析任务租约信息不完整")
        active = (extraction_id, extraction_attempt_token)
        extraction_base = _build_extraction_payload(
            document_id=document_id,
            extraction_id=extraction_id,
            candidate=candidate,
        )
        _emit_image_progress(
            progress_callback,
            file_index=file_index,
            total_files=total_files,
            file_name=file_name or str(document.get("original_filename") or "document"),
            image_index=candidate_index,
            image_count=image_count,
            source_locator=candidate.source_locator,
            stage="图片解析",
            message="正在进行图片分类与 OCR/视觉解析",
        )
        cache_key = candidate.sha256 or hashlib.sha256(candidate.image_bytes).hexdigest()
        analysis = analysis_cache.get_or_compute(
            cache_key,
            lambda: image_ocr.analyze_image(
                image_bytes=candidate.image_bytes,
                file_name=candidate.file_name,
                content_type=candidate.content_type,
            ),
        )
        content = _analysis_content(analysis)
        if not content:
            _persist_image_result(
                repository=repository,
                document_id=document_id,
                extraction={
                    **extraction_base,
                    "classification": analysis.classification,
                    "ocr_text": analysis.ocr_text,
                    "description": analysis.description,
                    "confidence": analysis.confidence,
                    "status": "skipped",
                },
                attempt_token=extraction_attempt_token,
                trace_id=trace_id,
                source_locator=candidate.source_locator,
                embedding_model=embedding_model,
            )
            return

        chunks = _build_image_chunks(
            document,
            candidate,
            analysis,
            content,
            extraction_id,
            chunking_service,
        )
        _emit_image_progress(
            progress_callback,
            file_index=file_index,
            total_files=total_files,
            file_name=file_name or str(document.get("original_filename") or "document"),
            image_index=candidate_index,
            image_count=image_count,
            source_locator=candidate.source_locator,
            stage="图片 Embedding",
            message="正在将图片解析文本加入知识库",
        )
        embedded_chunks, embeddings = _embed_image_chunks(
            chunks=chunks,
            embedding_client=embedding_client,
            chunking_service=chunking_service,
        )
        _persist_image_result(
            repository=repository,
            document_id=document_id,
            extraction={
                **extraction_base,
                "classification": analysis.classification,
                "ocr_text": analysis.ocr_text,
                "description": analysis.description,
                "confidence": analysis.confidence,
                "status": "indexed",
            },
            image_chunks=[
                {
                    "source_id": chunk.source_id,
                    "content": chunk.content,
                    "metadata": chunk.metadata,
                }
                for chunk in embedded_chunks
            ],
            embeddings=embeddings,
            embedding_model=embedding_model,
            attempt_token=extraction_attempt_token,
            trace_id=trace_id,
            source_locator=candidate.source_locator,
        )
    except ImageEnrichmentLeaseLostError:
        _log_enrichment(
            trace_id,
            "图片解析任务租约已被接管，丢弃当前结果",
            document_id=document_id,
            source_locator=candidate.source_locator,
        )
    except Exception as exc:
        if active is not None and extraction_base is not None:
            failure = {
                **extraction_base,
                "classification": analysis.classification if analysis else None,
                "ocr_text": analysis.ocr_text if analysis else "",
                "description": analysis.description if analysis else "",
                "confidence": analysis.confidence if analysis else None,
                "status": "failed",
                "error_message": "图片增强失败，可重试图片解析",
            }
            try:
                _persist_image_result(
                    repository=repository,
                    document_id=document_id,
                    extraction=failure,
                    attempt_token=active[1],
                    trace_id=trace_id,
                    source_locator=candidate.source_locator,
                    embedding_model=embedding_model,
                )
            except ImageEnrichmentLeaseLostError:
                return
            except Exception:
                _mark_active_extraction_failed(
                    repository=repository,
                    document_id=document_id,
                    extraction_id=active[0],
                    attempt_token=active[1],
                    trace_id=trace_id,
                )
        _log_enrichment(
            trace_id,
            "单张图片增强失败，正文保持可用",
            document_id=document_id,
            source_locator=candidate.source_locator,
            error_type=type(exc).__name__,
        )


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def retry_rag_document_image_enrichment(
    document_id: str,
    trace_id: str | None = None,
    progress_callback: ImageProgressCallback | None = None,
) -> ImageEnrichmentSummary:
    """兼容旧调用方；新语义只登记重试任务并立即返回。"""
    _ = progress_callback
    from app.application.services.rag_image_enrichment_queue_service import (
        enqueue_image_enrichment_task,
    )

    return enqueue_image_enrichment_task(
        document_id,
        trace_id=trace_id,
        force=False,
    )


def _build_extraction_payload(
    *,
    document_id: str,
    extraction_id: str,
    candidate: DocumentImageCandidate,
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "extraction_id": extraction_id,
        "asset_id": candidate.asset_id,
        "image_sha256": candidate.sha256,
        "file_name": candidate.file_name,
        "content_type": candidate.content_type,
        "source_kind": candidate.source_kind,
        "source_locator": candidate.source_locator,
        "page_number": candidate.page_number,
        "paragraph_index": candidate.paragraph_index,
        "image_index": candidate.image_index,
    }


def _build_image_chunks(
    document: dict[str, Any],
    candidate: DocumentImageCandidate,
    analysis: ImageAnalysisResult,
    content: str,
    extraction_id: str,
    chunking_service: Any,
) -> list[RagChunk]:
    original_filename = str(document.get("original_filename") or "document")
    original_content_type = str(
        document.get("original_content_type") or "application/octet-stream"
    )
    metadata: dict[str, Any] = {
        "originalFilename": original_filename,
        "originalContentType": original_content_type,
        "sourceType": "image",
        "ingestSource": "image_vision",
        "imageContentType": "diagram_description" if analysis.classification == "diagram" else "ocr_text",
        "imageClassification": analysis.classification,
        "imageSourceKind": candidate.source_kind,
        "imageSourceLocator": candidate.source_locator,
        "imageExtractionId": extraction_id,
        "confidence": analysis.confidence,
    }
    if candidate.page_number is not None:
        metadata["pageNumber"] = candidate.page_number
    if candidate.paragraph_index is not None:
        metadata["paragraphIndex"] = candidate.paragraph_index
    if candidate.relative_path:
        metadata["relativePath"] = candidate.relative_path
    # 使用已入库预览中的实际标题和图片名称，不将归属信息写入 OCR 正文。
    context = image_embedding_context(
        str(document.get("preview_text") or ""), candidate.relative_path or ""
    )
    if context:
        metadata["embeddingContext"] = context
    source_id = str(document.get("source_id") or Path(original_filename).stem)
    image_document = ExtractedDocument(
        source_id=source_id,
        original_filename=original_filename,
        original_content_type=original_content_type,
        source_type="image",
        ingest_source="image_vision",
        content=content,
        metadata=metadata,
    )
    chunks = chunking_service.chunk_document(image_document)
    for chunk in chunks:
        chunk.metadata.update(metadata)
    return chunks


def _analysis_content(analysis: ImageAnalysisResult) -> str:
    if analysis.classification in {"decorative", "empty"}:
        return ""
    if analysis.classification == "diagram":
        return analysis.description.strip() or analysis.ocr_text.strip()
    return analysis.ocr_text.strip()


def _embed_image_chunks(
    *,
    chunks: list[RagChunk],
    embedding_client: EmbeddingPort,
    chunking_service: Any,
) -> tuple[list[RagChunk], list[list[float]]]:
    current_chunks = list(chunks)
    fallback_round = 0
    while True:
        try:
            embeddings = embedding_client.embed_texts([chunk_embedding_text(chunk) for chunk in current_chunks])
            if len(embeddings) != len(current_chunks):
                raise EmbeddingError("图片文本向量数量与 Chunk 数量不一致")
            return current_chunks, embeddings
        except Exception as exc:
            failed_index = _find_failed_embedding_chunk_index(exc)
            if failed_index is None or not 0 <= failed_index < len(current_chunks):
                raise EmbeddingError("图片文本向量化失败") from exc
            split_chunks = chunking_service.split_chunk_for_embedding(current_chunks[failed_index])
            if len(split_chunks) <= 1 or fallback_round >= 8:
                raise EmbeddingError("图片文本向量化失败") from exc
            fallback_round += 1
            current_chunks[failed_index : failed_index + 1] = split_chunks


def _find_failed_embedding_chunk_index(error: BaseException) -> int | None:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, EmbeddingChunkRetryableError):
            return current.item_index - 1
        current = current.__cause__ or current.__context__
    return None


def _persist_image_result(
    *,
    repository: RagDocumentRepositoryPort,
    document_id: str,
    extraction: dict[str, Any],
    attempt_token: str,
    trace_id: str | None,
    source_locator: str,
    image_chunks: list[dict[str, Any]] | None = None,
    embeddings: list[list[float]] | None = None,
    embedding_model: str = "",
) -> bool:
    try:
        repository.save_image_extraction_result(
            document_id=document_id,
            extraction=extraction,
            image_chunks=image_chunks or [],
            embeddings=embeddings or [],
            embedding_model=embedding_model,
            attempt_token=attempt_token,
        )
    except ImageEnrichmentLeaseLostError:
        _log_enrichment(
            trace_id,
            "图片解析任务租约已被接管，丢弃当前结果",
            document_id=document_id,
            source_locator=source_locator,
        )
        return False
    except Exception as exc:
        _log_enrichment(
            trace_id,
            "图片解析结果写入失败",
            document_id=document_id,
            source_locator=source_locator,
            error_type=type(exc).__name__,
        )
        raise
    return True


def _mark_active_extraction_failed(
    *,
    repository: RagDocumentRepositoryPort,
    document_id: str,
    extraction_id: str,
    attempt_token: str,
    trace_id: str | None,
) -> None:
    """异常离开单图处理时释放 processing 状态，避免重试被租约卡住。"""
    try:
        marked = repository.mark_image_extraction_failed(
            document_id=document_id,
            extraction_id=extraction_id,
            attempt_token=attempt_token,
            message="图片解析或结果写入失败，可重试图片解析",
        )
        if not marked:
            _log_enrichment(
                trace_id,
                "当前图片失败状态未更新，可能已被其他任务接管",
                document_id=document_id,
                extraction_id=extraction_id,
            )
    except Exception as exc:
        _log_enrichment(
            trace_id,
            "当前图片失败状态写入异常",
            document_id=document_id,
            extraction_id=extraction_id,
            error_type=type(exc).__name__,
        )


def _finish_image_enrichment(
    repository: RagDocumentRepositoryPort,
    document_id: str,
    summary: ImageEnrichmentSummary,
    trace_id: str | None,
    attempt_token: str,
) -> ImageEnrichmentSummary:
    """把图片增强结果显式写入文档主表，失败时也返回可观察的失败状态。"""
    if summary.processing_count > 0:
        had_explicit_message = bool(summary.message)
        summary.status = "partial_failed" if (
            summary.indexed_count or summary.skipped_count
        ) else "failed"
        summary.message = summary.message or "部分图片任务仍在处理中，请稍后重试图片解析"
        if not had_explicit_message:
            summary.retryable = True
    if summary.status == "not_applicable":
        summary.status = "completed" if summary.candidate_count else "not_applicable"
    if summary.failed_count > 0:
        summary.status = "partial_failed" if summary.indexed_count or summary.skipped_count else "failed"
        summary.message = summary.message or "部分图片解析失败，可重试图片解析"
        summary.retryable = True
    try:
        repository.finish_image_enrichment(
            document_id=document_id,
            status=summary.status,
            message=summary.message,
            retryable=summary.retryable,
            attempt_token=attempt_token,
            current_position=summary.current_position,
        )
    except Exception as exc:
        _log_enrichment(
            trace_id,
            "图片增强状态写入失败，正文仍保持可用",
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        summary.status = "failed"
        summary.message = "图片增强状态保存失败，可稍后重试图片解析"
        summary.retryable = True
    return summary


def _summary_from_rows(rows: list[dict[str, Any]]) -> ImageEnrichmentSummary:
    summary = ImageEnrichmentSummary(candidate_count=len(rows))
    for row in rows:
        status = str(row.get("status") or "")
        if status in {"indexed", "skipped"}:
            summary.analyzed_count += 1
        if status == "indexed":
            summary.indexed_count += 1
        elif status == "skipped":
            summary.skipped_count += 1
        elif status == "failed":
            summary.failed_count += 1
        elif status == "processing":
            summary.processing_count += 1
    summary.current_position = sum(
        1 for row in rows if str(row.get("status") or "") in {"indexed", "skipped", "failed"}
    )
    return summary


def _emit_image_progress(
    callback: ImageProgressCallback | None,
    *,
    file_index: int | None,
    total_files: int | None,
    file_name: str,
    image_index: int,
    image_count: int,
    source_locator: str,
    stage: str,
    message: str,
) -> None:
    if callback is None:
        return
    payload: dict[str, Any] = {
        "event": "file-stage",
        "file_name": file_name,
        "stage": stage,
        "status": "uploading",
        "image_index": image_index,
        "image_count": image_count,
        "image_source_locator": source_locator,
        "message": message,
    }
    if file_index is not None:
        payload["file_index"] = file_index
    if total_files is not None:
        payload["total_files"] = total_files
    callback(payload)


def _log_enrichment(trace_id: str | None, message: str, **extra: object) -> None:
    parts = [f"[知识库上传][图片增强][trace={trace_id or '-'}] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
