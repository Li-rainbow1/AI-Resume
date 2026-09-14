"""Markdown 正文和本地图片附件的组合上传用例。"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Callable

from app.application.dto.rag_dto import (
    RagMarkdownBundleAssetDto,
    RagMarkdownUploadRequestDto,
    RagUploadAssetDto,
    RagUploadFileResultDto,
    RagUploadResponseDto,
)
from app.application.services.rag_image_enrichment_queue_service import (
    enqueue_image_enrichment_task,
    image_enrichment_summary_from_document,
)
from app.application.use_cases.enrich_rag_document_images import ImageEnrichmentSummary
from app.application.use_cases.upload_and_ingest_rag_assets import upload_and_ingest_rag_assets
from app.bootstrap.container import (
    build_rag_document_repository,
    build_rag_object_storage,
    resolve_settings,
)
from app.domain.exceptions.rag_exceptions import (
    FileParseError,
    RagIngestError,
    RagUploadCancelledError,
    VectorStoreError,
    safe_rag_log_value,
)
from app.domain.services.markdown_attachment_service import (
    SUPPORTED_MARKDOWN_IMAGE_EXTENSIONS,
    analyze_markdown_attachments,
    normalize_markdown_attachment_path,
)

_READ_CHUNK_SIZE = 1024 * 1024


def upload_markdown_bundle(
    request: RagMarkdownUploadRequestDto,
    trace_id: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> RagUploadResponseDto:
    """完成 Markdown 正文入库并建立图片资源关联。

    入口意图：把浏览器提交的 Markdown 和公共附件目录拆成两条职责不同的链路，
    正文继续复用既有解析、切块、Embedding 和 pgvector 流程，图片资源进入 MinIO
    后再由统一图片增强流程完成分类、OCR/视觉说明和文本向量化。关键步骤是先
    校验并物化文件、分析正文引用，只处理实际引用的受支持图片。正文即使缺图或
    图片模型失败也照常入库，图片统计写入文档记录；删除时由文档管理用例按关联
    关系清理共享对象。输出仍使用现有上传响应和 SSE 进度契约。
    """
    if not request.documents:
        raise FileParseError("至少需要上传一个 Markdown 文件")
    settings = resolve_settings()
    documents = [_materialize_bundle_asset(item, settings.rag_max_file_size_mb, True) for item in request.documents]
    attachments: list[RagMarkdownBundleAssetDto] = []
    for item in request.attachments:
        normalized_path = normalize_markdown_attachment_path(item.relative_path)
        if normalized_path is None:
            raise FileParseError("Markdown 附件路径无效")
        if _is_supported_attachment_path(normalized_path):
            attachments.append(
                _materialize_bundle_asset(item, settings.rag_max_file_size_mb, False)
            )
    attachment_index = _build_attachment_index(attachments)
    analyses = [
        analyze_markdown_attachments(
            _decode_markdown(item.asset.file_bytes or b""),
            attachment_index.keys(),
        )
        for item in documents
    ]
    if cancel_check is not None and cancel_check():
        # 取消只发生在正文处理开始前，避免产生已经 ready 但没有资源统计的文档。
        raise RagUploadCancelledError()

    # application 用例先把正文传给既有入库链路，再对正文实际引用的图片执行增强。
    response = upload_and_ingest_rag_assets(
        [item.asset for item in documents],
        trace_id=trace_id,
        progress_callback=(
            (lambda event: _forward_markdown_body_progress(event, progress_callback))
            if progress_callback is not None
            else None
        ),
        cancel_check=cancel_check,
        emit_batch_complete=False,
    )
    repository = build_rag_document_repository()
    object_storage = build_rag_object_storage()

    for index, result in enumerate(response.files):
        if result.status != "success" or not result.document_id:
            continue
        analysis = analyses[index]
        matched_count = 0
        reused_count = 0
        links: list[dict[str, str]] = []
        prepared_asset_records: dict[str, dict[str, Any]] = {}
        matched_attachments: dict[str, RagMarkdownBundleAssetDto] = {}
        asset_error_message: str | None = None
        for relative_path in analysis.matched_paths:
            attachment = attachment_index.get(relative_path)
            if attachment is None or attachment.asset.file_bytes is None:
                continue
            try:
                asset_record, reused = _ensure_markdown_asset(
                    attachment=attachment,
                    repository=repository,
                    object_storage=object_storage,
                )
                asset_id = str(asset_record.get("asset_id") or "").strip()
                if not asset_id:
                    raise VectorStoreError("Markdown 图片资源记录缺少资源 ID")
                links.append({"relative_path": relative_path, "asset_id": asset_id})
                prepared_asset_records[asset_id] = {
                    **asset_record,
                    "_created_in_attempt": not reused,
                    "_attempt_token": str(asset_record.get("attempt_token") or "").strip(),
                }
                matched_attachments[relative_path] = attachment
                matched_count += 1
                if reused:
                    reused_count += 1
            except Exception as exc:
                asset_error_message = "Markdown 图片附件保存或关联失败，需删除后携带附件重新上传"
                _log_markdown_asset(
                    trace_id,
                    "图片资源处理失败，正文保持可用",
                    document_id=result.document_id,
                    relative_path=relative_path,
                    error_type=type(exc).__name__,
                )

        missing_count = len(analysis.missing_paths) + len(analysis.matched_paths) - matched_count
        try:
            repository.link_document_assets(
                document_id=result.document_id,
                links=links,
                referenced_image_count=len(analysis.referenced_paths),
                matched_image_count=matched_count,
                missing_image_count=missing_count,
            )
        except RagIngestError as exc:
            # 正文已在上一步发布，关联写入失败时保留正文可检索，但把图片全部视为未匹配。
            _log_markdown_asset(
                trace_id,
                "图片关联写入失败，正文保持可用",
                document_id=result.document_id,
                error_type=type(exc).__name__,
            )
            _compensate_unlinked_assets(
                repository,
                object_storage,
                prepared_asset_records,
                trace_id,
            )
            matched_count = 0
            reused_count = 0
            missing_count = len(analysis.referenced_paths)
            matched_attachments = {}
            asset_error_message = "Markdown 图片附件保存或关联失败，需删除后携带附件重新上传"
        image_summary = _schedule_markdown_image_enrichment(
            result=result,
            analysis=analysis,
            matched_attachments=matched_attachments,
            asset_error_message=asset_error_message,
            repository=repository,
            trace_id=trace_id,
        )

        result.referenced_image_count = len(analysis.referenced_paths)
        result.matched_image_count = matched_count
        result.missing_image_count = missing_count
        result.reused_image_count = reused_count
        result.image_candidate_count = image_summary.candidate_count
        result.image_analyzed_count = image_summary.analyzed_count
        result.image_indexed_count = image_summary.indexed_count
        result.image_skipped_count = image_summary.skipped_count
        result.image_failed_count = image_summary.failed_count
        result.image_chunk_count = image_summary.chunk_count
        result.image_enrichment_status = image_summary.status
        result.image_enrichment_message = image_summary.message
        result.image_retry_available = image_summary.retryable
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "file-result",
                    "trace_id": trace_id,
                    "file_index": index + 1,
                    "total_files": len(response.files),
                    "file_name": result.file_name,
                    "stage": "完成",
                    "status": result.status,
                    "file_progress_percent": 100,
                    "progress_percent": round((index + 1) / max(1, len(response.files)) * 100),
                    "result": _upload_result_to_progress_payload(result),
                    "message": "Markdown 正文和图片附件处理完成",
                }
            )

    _log_markdown_asset(
        trace_id,
        "Markdown 附件上传处理完成",
        total_files=response.total_files,
        succeeded_files=response.succeeded_files,
        failed_files=response.failed_files,
        inserted=response.inserted,
    )
    if progress_callback is not None:
        progress_callback(
            {
                "event": "batch-complete",
                "trace_id": trace_id,
                "total_files": response.total_files,
                "succeeded_files": response.succeeded_files,
                "failed_files": response.failed_files,
                "inserted": response.inserted,
                "stage": "完成",
                "status": "success" if response.failed_files == 0 else "failed",
                "progress_percent": 100,
                "files": [_upload_result_to_progress_payload(item) for item in response.files],
                "message": "Markdown 正文和图片附件处理完成",
            }
        )

    return response


def _schedule_markdown_image_enrichment(
    *,
    result: RagUploadFileResultDto,
    analysis: Any,
    matched_attachments: dict[str, RagMarkdownBundleAssetDto],
    asset_error_message: str | None,
    repository: Any,
    trace_id: str | None,
) -> ImageEnrichmentSummary:
    """正文发布后登记 Markdown 图片任务，不在上传请求内调用 Vision。"""
    if not analysis.referenced_paths:
        document = repository.get_document(result.document_id or "") or {}
        return image_enrichment_summary_from_document(document)

    if not matched_attachments:
        missing_message = asset_error_message or "Markdown 图片附件未上传，需删除后携带附件重新上传"
        try:
            repository.set_image_enrichment_terminal(
                result.document_id or "",
                status="failed",
                message=missing_message,
                retryable=False,
            )
        except Exception as exc:
            _log_markdown_asset(
                trace_id,
                "保存 Markdown 图片缺失状态失败，正文保持可用",
                document_id=result.document_id,
                error_type=type(exc).__name__,
            )
        document = repository.get_document(result.document_id or "") or {
            "image_enrichment_status": "failed",
            "image_enrichment_message": missing_message,
            "image_enrichment_retryable": False,
        }
        return image_enrichment_summary_from_document(document)

    try:
        return enqueue_image_enrichment_task(
            result.document_id or "",
            repository=repository,
            trace_id=trace_id,
            force=True,
        )
    except Exception as exc:
        _log_markdown_asset(
            trace_id,
            "Markdown 图片任务排队异常，正文保持可用",
            document_id=result.document_id,
            error_type=type(exc).__name__,
        )
        document = repository.get_document(result.document_id or "") or {}
        return image_enrichment_summary_from_document(document)


def _materialize_bundle_asset(
    item: RagMarkdownBundleAssetDto,
    max_file_size_mb: int,
    is_document: bool,
) -> RagMarkdownBundleAssetDto:
    normalized_path = normalize_markdown_attachment_path(item.relative_path)
    if normalized_path is None:
        raise FileParseError("Markdown 附件路径无效")
    extension = Path(normalized_path).suffix.lower()
    if is_document and extension != ".md":
        raise FileParseError("附件模式只允许上传 Markdown 正文")
    if not is_document and extension not in SUPPORTED_MARKDOWN_IMAGE_EXTENSIONS:
        return item
    file_bytes = item.asset.file_bytes
    if file_bytes is None:
        if item.asset.file_stream is None:
            raise FileParseError(f"文件 {item.asset.file_name} 缺少可读取内容")
        byte_limit = max(1, max_file_size_mb) * 1024 * 1024
        if hasattr(item.asset.file_stream, "seek"):
            item.asset.file_stream.seek(0)
        buffer = bytearray()
        while True:
            part = item.asset.file_stream.read(min(_READ_CHUNK_SIZE, byte_limit - len(buffer) + 1))
            if not part:
                break
            if len(buffer) + len(part) > byte_limit:
                raise FileParseError(f"文件 {item.asset.file_name} 超过大小限制 {max_file_size_mb}MB")
            buffer.extend(part)
        file_bytes = bytes(buffer)
    if not file_bytes:
        raise FileParseError(f"文件 {item.asset.file_name} 不能为空")
    file_name = Path(normalized_path).name or item.asset.file_name
    content_type = _resolve_content_type(file_name, item.asset.content_type)
    return RagMarkdownBundleAssetDto(
        asset=RagUploadAssetDto(
            file_name=file_name,
            content_type=content_type,
            file_bytes=file_bytes,
            source_id=item.asset.source_id,
            metadata=dict(item.asset.metadata or {}),
        ),
        relative_path=normalized_path,
    )


def _build_attachment_index(
    attachments: list[RagMarkdownBundleAssetDto],
) -> dict[str, RagMarkdownBundleAssetDto]:
    index: dict[str, RagMarkdownBundleAssetDto] = {}
    for item in attachments:
        normalized_path = normalize_markdown_attachment_path(item.relative_path)
        if normalized_path is None or normalized_path in index:
            if normalized_path in index:
                raise FileParseError(f"附件路径重复: {normalized_path}")
            continue
        index[normalized_path] = item
    return index


def _ensure_markdown_asset(attachment, repository, object_storage) -> tuple[dict[str, Any], bool]:
    file_bytes = attachment.asset.file_bytes or b""
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    object_key = f"rag-assets/{sha256}"
    claim = repository.claim_asset_object(
        {
            "sha256": sha256,
            "object_key": object_key,
            "content_type": attachment.asset.content_type,
            "file_size_bytes": len(file_bytes),
        }
    )
    if not claim.get("claimed"):
        if str(claim.get("status") or "") == "ready":
            return claim, True
        raise VectorStoreError("相同图片资源正在处理中，请稍后重试")

    asset_id = str(claim.get("asset_id") or "").strip()
    attempt_token = str(claim.get("attempt_token") or "").strip()
    try:
        object_storage.put_object(object_key, file_bytes, attachment.asset.content_type)
        return repository.mark_asset_ready(asset_id, attempt_token), False
    except Exception:
        _compensate_asset_attempt(
            repository=repository,
            object_storage=object_storage,
            asset_id=asset_id,
            object_key=object_key,
            attempt_token=attempt_token,
        )
        raise


def _compensate_asset_attempt(
    *,
    repository: Any,
    object_storage: Any,
    asset_id: str,
    object_key: str,
    attempt_token: str,
) -> None:
    """清理已登记但尚未发布的图片资源；清理失败保留可重试记录。"""
    try:
        if not repository.prepare_asset_cleanup(asset_id, attempt_token):
            _log_markdown_asset(
                None,
                "图片资源租约已失效，跳过补偿删除",
                asset_id=asset_id,
            )
            return
    except Exception as exc:
        # 无法确认租约归属时不能直接删除确定性对象键，交给后续任务按状态处理。
        _log_markdown_asset(
            None,
            "无法确认图片资源补偿租约，跳过对象删除",
            asset_id=asset_id,
            error_type=type(exc).__name__,
        )
        return
    object_deleted = False
    try:
        if object_key:
            object_storage.delete_object(object_key)
        object_deleted = True
    except Exception as exc:
        try:
            repository.mark_asset_cleanup_failed(
                asset_id,
                "图片对象补偿清理失败",
                attempt_token=attempt_token,
            )
        except Exception:
            pass
        _log_markdown_asset(
            None,
            "图片对象补偿清理失败",
            asset_id=asset_id,
            error_type=type(exc).__name__,
        )
    if not object_deleted:
        return
    try:
        repository.delete_unpublished_asset(asset_id, attempt_token)
    except Exception as exc:
        try:
            repository.mark_asset_cleanup_failed(
                asset_id,
                "图片资源记录补偿清理失败",
                attempt_token=attempt_token,
            )
        except Exception:
            pass
        _log_markdown_asset(
            None,
            "图片资源记录补偿清理失败",
            asset_id=asset_id,
            error_type=type(exc).__name__,
        )


def _compensate_unlinked_assets(
    repository: Any,
    object_storage: Any,
    asset_records: dict[str, dict[str, Any]],
    trace_id: str | None,
) -> None:
    """关联事务失败时清理本次准备的无引用对象，避免留下孤儿资源。"""
    for asset_id, asset in asset_records.items():
        if not asset.get("_created_in_attempt"):
            continue
        attempt_token = str(asset.get("_attempt_token") or "").strip()
        if not attempt_token:
            _log_markdown_asset(
                trace_id,
                "图片资源缺少补偿租约，跳过对象删除",
                asset_id=asset_id,
            )
            continue
        try:
            if not repository.prepare_asset_cleanup(asset_id, attempt_token):
                _log_markdown_asset(
                    trace_id,
                    "图片资源已有关联或租约已失效，跳过补偿删除",
                    asset_id=asset_id,
                )
                continue
        except Exception as exc:
            _log_markdown_asset(
                trace_id,
                "无法冻结图片资源补偿租约，跳过对象删除",
                asset_id=asset_id,
                error_type=type(exc).__name__,
            )
            continue
        object_key = str(asset.get("object_key") or "").strip()
        if not object_key:
            try:
                repository.delete_unpublished_asset(asset_id, attempt_token)
            except Exception as exc:
                try:
                    repository.mark_asset_cleanup_failed(
                        asset_id,
                        "图片资源记录补偿清理失败",
                        attempt_token=attempt_token,
                    )
                except Exception:
                    pass
                _log_markdown_asset(
                    trace_id,
                    "图片资源记录补偿清理失败",
                    asset_id=asset_id,
                    error_type=type(exc).__name__,
                )
            continue
        try:
            object_storage.delete_object(object_key)
        except Exception as exc:
            try:
                repository.mark_asset_cleanup_failed(asset_id, attempt_token=attempt_token)
            except Exception:
                pass
            _log_markdown_asset(
                trace_id,
                "图片资源补偿清理失败",
                asset_id=asset_id,
                error_type=type(exc).__name__,
            )
            continue
        try:
            repository.delete_unpublished_asset(asset_id, attempt_token)
        except Exception as exc:
            try:
                repository.mark_asset_cleanup_failed(
                    asset_id,
                    "图片资源记录补偿清理失败",
                    attempt_token=attempt_token,
                )
            except Exception:
                pass
            _log_markdown_asset(
                trace_id,
                "图片资源记录补偿清理失败",
                asset_id=asset_id,
                error_type=type(exc).__name__,
            )



def _decode_markdown(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8-sig", errors="replace")


def _is_supported_attachment_path(relative_path: str) -> bool:
    normalized_path = normalize_markdown_attachment_path(relative_path)
    return bool(
        normalized_path
        and Path(normalized_path).suffix.lower() in SUPPORTED_MARKDOWN_IMAGE_EXTENSIONS
    )


def _resolve_content_type(file_name: str, content_type: str) -> str:
    safe_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if safe_content_type.startswith("image/"):
        return safe_content_type
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def _log_markdown_asset(trace_id: str | None, message: str, **extra: object) -> None:
    parts = [f"[知识库上传][trace={trace_id or '-'}][Markdown附件] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)


def _forward_markdown_body_progress(
    event: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None],
) -> None:
    """转发正文阶段进度，等待附件状态合并后再发送唯一的文件结果事件。"""
    if event.get("event") == "file-result":
        return
    progress_callback(event)


def _upload_result_to_progress_payload(result: RagUploadFileResultDto) -> dict[str, object]:
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
