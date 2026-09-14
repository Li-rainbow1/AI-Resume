"""知识库上传的文件准备、进度通知和 Embedding 回退辅助职责。"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.application.dto.rag_dto import RagUploadAssetDto, RagUploadFileResultDto
from app.domain.exceptions.rag_exceptions import (
    EmbeddingChunkRetryableError,
    FileParseError,
    FileTooLargeError,
    RagUploadCancelledError,
    UnsupportedFileTypeError,
    public_rag_error_message,
    safe_rag_log_value,
)
from app.domain.models.rag_document import ExtractedDocument, RagChunk
from app.domain.services.rag_embedding_context_service import chunk_embedding_text

_SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
_UPLOAD_STAGE_PROGRESS = {
    "排队": 0.0,
    "开始": 0.08,
    "读取": 0.16,
    "校验": 0.25,
    "解析": 0.38,
    "规范化": 0.50,
    "逻辑文档拆分": 0.60,
    "切块": 0.72,
    "Embedding": 0.84,
    "入库": 0.94,
    "完成": 1.0,
}
_MAX_EMBEDDING_FALLBACK_ROUNDS = 8
RagUploadProgressCallback = Callable[[dict[str, Any]], None]


def validate_assets(assets: list[RagUploadAssetDto]) -> None:
    if not assets:
        raise FileParseError("至少需要上传一个文件")


def validate_asset(asset: RagUploadAssetDto, max_file_size_mb: int) -> None:
    byte_limit = max(1, max_file_size_mb) * 1024 * 1024
    extension = Path(asset.file_name).suffix.lower()
    file_bytes = require_file_bytes(asset)
    if extension not in _SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(f"暂不支持的文件类型: {extension or asset.content_type}")
    if not file_bytes:
        raise FileParseError(f"文件 {asset.file_name} 不能为空")
    if len(file_bytes) > byte_limit:
        raise FileTooLargeError(f"文件 {asset.file_name} 超过大小限制 {max_file_size_mb}MB")


def extract_document(asset: RagUploadAssetDto, file_parser: Any, image_ocr: Any) -> ExtractedDocument:
    file_bytes = require_file_bytes(asset)
    if detect_source_type(asset.file_name) == "image":
        markdown = image_ocr.extract_markdown(
            image_bytes=file_bytes,
            file_name=asset.file_name,
            content_type=asset.content_type,
        )
        return apply_asset_metadata(
            ExtractedDocument(
                source_id=Path(asset.file_name).stem.strip() or asset.file_name,
                original_filename=asset.file_name,
                original_content_type=asset.content_type,
                source_type="image",
                ingest_source="image_ocr_text",
                content=markdown,
            ),
            asset,
        )
    return apply_asset_metadata(
        file_parser.parse(
            file_bytes=file_bytes,
            file_name=asset.file_name,
            content_type=asset.content_type,
        ),
        asset,
    )


def apply_asset_metadata(document: ExtractedDocument, asset: RagUploadAssetDto) -> ExtractedDocument:
    source_id = str(asset.source_id or document.source_id or "").strip() or document.source_id
    metadata = {**document.metadata, **(asset.metadata or {})}
    return ExtractedDocument(
        source_id=source_id,
        original_filename=document.original_filename,
        original_content_type=document.original_content_type,
        source_type=document.source_type,
        ingest_source=document.ingest_source,
        content=document.content,
        metadata=metadata,
    )


def materialize_asset(asset: RagUploadAssetDto, max_file_size_mb: int) -> RagUploadAssetDto:
    if asset.file_bytes is not None:
        return asset
    return RagUploadAssetDto(
        file_name=asset.file_name,
        content_type=asset.content_type,
        file_bytes=read_file_bytes_with_limit(asset=asset, max_file_size_mb=max_file_size_mb),
        source_id=asset.source_id,
        metadata=dict(asset.metadata or {}),
    )


def read_file_bytes_with_limit(asset: RagUploadAssetDto, max_file_size_mb: int) -> bytes:
    if asset.file_stream is None:
        raise FileParseError(f"文件 {asset.file_name} 缺少可读取内容")

    byte_limit = max(1, max_file_size_mb) * 1024 * 1024
    if hasattr(asset.file_stream, "seek"):
        asset.file_stream.seek(0)

    buffer = bytearray()
    while True:
        next_chunk_limit = min(_UPLOAD_READ_CHUNK_SIZE, (byte_limit - len(buffer)) + 1)
        chunk = asset.file_stream.read(next_chunk_limit)
        if not chunk:
            break
        if len(buffer) + len(chunk) > byte_limit:
            raise FileTooLargeError(f"文件 {asset.file_name} 超过大小限制 {max_file_size_mb}MB")
        buffer.extend(chunk)
    return bytes(buffer)


def require_file_bytes(asset: RagUploadAssetDto) -> bytes:
    if asset.file_bytes is None:
        raise FileParseError(f"文件 {asset.file_name} 缺少可解析字节内容")
    return asset.file_bytes


def cleanup_failed_upload(
    *,
    document_repository: Any,
    object_storage: Any,
    document_id: str | None,
    object_key: str | None,
    trace_id: str,
    file_name: str,
) -> None:
    """上传任一阶段失败后清理对象和不可检索的文件记录。"""
    if not document_id:
        return
    try:
        # DeleteObject 对不存在的键是幂等的，因此即使 put_object 只完成了一部分
        # 也按同一对象键尝试清理，避免把孤儿对象留在 Bucket 中。
        if object_key:
            object_storage.delete_object(object_key)
        document = document_repository.get_document(document_id)
        if document is None:
            return
        status = str(document.get("status") or "")
        if status == "processing":
            document_repository.delete_unpublished_document(document_id)
        elif status == "ready":
            deleting = document_repository.mark_deleting(document_id)
            if deleting is not None:
                document_repository.delete_document_chunks(deleting)
                document_repository.finalize_document_deletion(document_id)
    except Exception as cleanup_error:
        try:
            document_repository.mark_cleanup_failed(document_id)
        except Exception:
            pass
        log_upload(
            trace_id,
            "清理",
            "失败文件清理未完成",
            file_name=file_name,
            document_id=document_id,
            error_type=type(cleanup_error).__name__,
        )


def detect_source_type(file_name: str) -> str:
    extension = Path(file_name).suffix.lower()
    return "image" if extension in _IMAGE_EXTENSIONS else "document"


def asset_source_id(asset: RagUploadAssetDto) -> str:
    return str(asset.source_id or Path(asset.file_name).stem.strip() or asset.file_name).strip()


def default_ingest_source(source_type: str) -> str:
    return "image_ocr_text" if source_type == "image" else "text_document"


def emit_file_stage(
    progress_callback: RagUploadProgressCallback | None,
    trace_id: str,
    file_index: int,
    total_files: int,
    file_name: str,
    stage: str,
    message: str,
) -> None:
    emit_progress(
        progress_callback,
        "file-stage",
        trace_id=trace_id,
        file_index=file_index,
        total_files=total_files,
        file_name=file_name,
        stage=stage,
        status="uploading",
        file_progress_percent=file_stage_progress_percent(stage),
        progress_percent=overall_file_progress_percent(file_index, total_files, stage),
        message=message,
    )


def emit_file_result(
    progress_callback: RagUploadProgressCallback | None,
    trace_id: str,
    file_index: int,
    total_files: int,
    result: RagUploadFileResultDto,
    stage: str = "完成",
) -> None:
    emit_progress(
        progress_callback,
        "file-result",
        trace_id=trace_id,
        file_index=file_index,
        total_files=total_files,
        file_name=result.file_name,
        stage=stage,
        status=result.status,
        file_progress_percent=100,
        progress_percent=overall_file_result_progress_percent(file_index, total_files),
        result=upload_result_to_progress_payload(result),
        message="文件处理成功" if result.status == "success" else (result.error_message or "文件处理失败"),
    )


def upload_result_to_progress_payload(result: RagUploadFileResultDto) -> dict[str, object]:
    return {
        "file_name": result.file_name,
        "content_type": result.content_type,
        "source_type": result.source_type,
        "ingest_source": result.ingest_source,
        "chunk_count": result.chunk_count,
        "inserted_count": result.inserted_count,
        "status": result.status,
        "error_message": result.error_message,
        "document_id": result.document_id,
        "referenced_image_count": result.referenced_image_count,
        "matched_image_count": result.matched_image_count,
        "missing_image_count": result.missing_image_count,
        "reused_image_count": result.reused_image_count,
        "image_candidate_count": result.image_candidate_count,
        "image_analyzed_count": result.image_analyzed_count,
        "image_indexed_count": result.image_indexed_count,
        "image_skipped_count": result.image_skipped_count,
        "image_failed_count": result.image_failed_count,
        "image_chunk_count": result.image_chunk_count,
        "image_enrichment_status": result.image_enrichment_status,
        "image_enrichment_message": result.image_enrichment_message,
        "image_retry_available": result.image_retry_available,
    }


def emit_progress(
    progress_callback: RagUploadProgressCallback | None,
    event: str,
    **payload: object,
) -> None:
    if progress_callback is None:
        return
    progress_callback({"event": event, **payload})


def file_stage_progress_percent(stage: str) -> int:
    stage_progress = _UPLOAD_STAGE_PROGRESS.get(stage)
    if stage_progress is None:
        stage_progress = 0.35
    return clamp_percent(round(stage_progress * 100))


def overall_file_progress_percent(file_index: int, total_files: int, stage: str) -> int:
    if total_files <= 0:
        return 0
    stage_progress = _UPLOAD_STAGE_PROGRESS.get(stage, 0.35)
    progress = round(((max(file_index, 1) - 1) + stage_progress) / total_files * 100)
    return min(99, max(1, clamp_percent(progress)))


def overall_file_result_progress_percent(file_index: int, total_files: int) -> int:
    if total_files <= 0:
        return 100
    progress = round(max(file_index, 1) / total_files * 100)
    return clamp_percent(progress)


def clamp_percent(value: int) -> int:
    return min(100, max(0, value))


def emit_embedding_timing_log(
    *,
    trace_id: str,
    event: str,
    file_name: str,
    source_type: str,
    ingest_source: str,
    chunk_count: int,
    total_chars: int,
    file_size_bytes: int,
    elapsed_ms: float | None = None,
    vector_count: int | None = None,
    embedding_model: str | None = None,
    embedding_base_url: str | None = None,
    error_type: str | None = None,
) -> None:
    extra: dict[str, object] = {
        "event": event,
        "file_name": file_name,
        "source_type": source_type,
        "ingest_source": ingest_source,
        "chunk_count": chunk_count,
        "total_chars": total_chars,
        "file_size_bytes": file_size_bytes,
    }
    if elapsed_ms is not None:
        extra["elapsed_ms"] = f"{elapsed_ms:.2f}"
    if vector_count is not None:
        extra["vector_count"] = vector_count
    if embedding_model:
        extra["embedding_model"] = embedding_model
    if embedding_base_url:
        extra["embedding_base_url"] = embedding_base_url
    if error_type:
        extra["error_type"] = error_type
    log_upload(trace_id, "Embedding", "Embedding 阶段日志", **extra)


def normalize_trace_id(raw_trace_id: str | None) -> str:
    safe = (raw_trace_id or "").strip()
    if safe:
        return safe
    return uuid4().hex[:8]


def log_upload(trace_id: str, log_stage: str, message: str, **extra: object) -> None:
    parts = [f"[知识库上传][trace={trace_id}][{log_stage}] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)


def assign_global_chunk_indexes(chunks: list[RagChunk]) -> None:
    for index, chunk in enumerate(chunks):
        metadata = getattr(chunk, "metadata", None)
        if isinstance(metadata, dict):
            metadata["chunkIndex"] = index


def embed_chunks_with_semantic_fallback(
    chunks: list[RagChunk],
    embedding_client: Any,
    chunking_service: Any,
    trace_id: str,
    file_name: str,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[RagChunk], list[list[float]]]:
    """只对定位到的超时 Chunk 做局部切分，保持向量与入库文档一一对应。"""
    current_chunks = list(chunks)
    fallback_round = 0

    while True:
        raise_if_upload_cancelled(cancel_check)
        try:
            vectors = embedding_client.embed_texts([chunk_embedding_text(chunk) for chunk in current_chunks])
            return current_chunks, vectors
        except Exception as exc:
            failed_index = find_failed_embedding_chunk_index(exc)
            if failed_index is None or not 0 <= failed_index < len(current_chunks):
                raise

            failed_chunk = current_chunks[failed_index]
            raise_if_upload_cancelled(cancel_check)
            split_chunks = chunking_service.split_chunk_for_embedding(failed_chunk)
            if len(split_chunks) <= 1 or fallback_round >= _MAX_EMBEDDING_FALLBACK_ROUNDS:
                raise

            fallback_round += 1
            log_upload(
                trace_id,
                "Embedding",
                "单个 Chunk 超时，启动语义二次切分",
                file_name=file_name,
                failed_chunk_position=failed_index + 1,
                original_chars=len(failed_chunk.content),
                subchunk_count=len(split_chunks),
                fallback_round=fallback_round,
            )
            current_chunks[failed_index : failed_index + 1] = split_chunks
            assign_global_chunk_indexes(current_chunks)


def find_failed_embedding_chunk_index(error: BaseException) -> int | None:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, EmbeddingChunkRetryableError):
            return current.item_index - 1
        current = current.__cause__ or current.__context__
    return None


def raise_if_upload_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    """在耗时阶段之间检查客户端是否已断开或主动取消上传。"""
    if cancel_check is not None and cancel_check():
        raise RagUploadCancelledError()


__all__ = [
    "RagUploadProgressCallback",
    "apply_asset_metadata",
    "assign_global_chunk_indexes",
    "asset_source_id",
    "cleanup_failed_upload",
    "default_ingest_source",
    "detect_source_type",
    "emit_embedding_timing_log",
    "emit_file_result",
    "emit_file_stage",
    "embed_chunks_with_semantic_fallback",
    "extract_document",
    "file_stage_progress_percent",
    "log_upload",
    "materialize_asset",
    "normalize_trace_id",
    "overall_file_progress_percent",
    "raise_if_upload_cancelled",
    "require_file_bytes",
    "upload_result_to_progress_payload",
    "validate_asset",
    "validate_assets",
]
