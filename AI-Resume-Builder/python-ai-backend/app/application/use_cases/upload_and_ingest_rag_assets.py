from collections.abc import Callable
import hashlib
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.application.dto.rag_dto import RagUploadAssetDto, RagUploadFileResultDto, RagUploadResponseDto
from app.application.services.rag_upload_support import (
    RagUploadProgressCallback,
    asset_source_id as _asset_source_id,
    assign_global_chunk_indexes as _assign_global_chunk_indexes,
    cleanup_failed_upload as _cleanup_failed_upload,
    default_ingest_source as _default_ingest_source,
    detect_source_type as _detect_source_type,
    emit_embedding_timing_log as _emit_embedding_timing_log,
    emit_file_result as _emit_file_result,
    emit_file_stage as _emit_file_stage,
    emit_progress as _emit_progress,
    embed_chunks_with_semantic_fallback as _embed_chunks_with_semantic_fallback,
    extract_document as _extract_document,
    file_stage_progress_percent as _file_stage_progress_percent,
    log_upload as _log_upload,
    materialize_asset as _materialize_asset,
    normalize_trace_id as _normalize_trace_id,
    overall_file_progress_percent as _overall_file_progress_percent,
    raise_if_upload_cancelled as _raise_if_upload_cancelled,
    require_file_bytes as _require_file_bytes,
    upload_result_to_progress_payload as _upload_result_to_progress_payload,
    validate_asset as _validate_asset,
    validate_assets as _validate_assets,
)
from app.application.services.rag_image_enrichment_queue_service import (
    enqueue_image_enrichment_task,
    image_enrichment_summary_from_document,
)
from app.bootstrap.container import (
    build_document_chunking_service,
    build_embedding_client,
    build_file_parser,
    build_image_markdown_ocr_client,
    build_logical_document_splitter_service,
    build_rag_document_repository,
    build_rag_object_storage,
    build_vector_store,
    resolve_settings,
)
from app.domain.exceptions.rag_exceptions import (
    FileParseError,
    RagDocumentConflictError,
    RagArchivedDocumentConflictError,
    RagDocumentScopeConflictError,
    RagUploadCancelledError,
    RagIngestError,
    public_rag_error_message,
)
from app.domain.models.rag_document import ExtractedDocument, RagChunk
from app.infrastructure.text.markdown_structure_normalizer import MarkdownStructureNormalizer

def upload_and_ingest_rag_assets(
    assets: list[RagUploadAssetDto],
    trace_id: str | None = None,
    progress_callback: RagUploadProgressCallback | None = None,
    raise_conflict: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    emit_batch_complete: bool = True,
) -> RagUploadResponseDto:
    safe_trace_id = _normalize_trace_id(trace_id)
    _log_upload(safe_trace_id, "请求", "开始处理上传请求", file_count=len(assets))
    _emit_progress(
        progress_callback,
        "batch-start",
        trace_id=safe_trace_id,
        total_files=len(assets),
        status="uploading",
        stage="开始",
        progress_percent=0,
        message="开始处理批量上传",
    )

    settings = resolve_settings()
    _raise_if_upload_cancelled(cancel_check)
    _emit_progress(
        progress_callback,
        "batch-stage",
        trace_id=safe_trace_id,
        stage="校验",
        status="uploading",
        progress_percent=1 if assets else 0,
        message="开始批量基础校验",
    )
    _log_upload(
        safe_trace_id,
        "校验",
        "开始批量基础校验",
        max_file_size_mb=settings.rag_max_file_size_mb,
    )
    _validate_assets(assets=assets)
    _log_upload(safe_trace_id, "校验", "批量基础校验通过")
    _emit_progress(
        progress_callback,
        "batch-stage",
        trace_id=safe_trace_id,
        stage="初始化",
        status="uploading",
        progress_percent=2 if assets else 0,
        message="校验通过，正在初始化处理依赖",
    )

    # 业务意图：
    # 1) application 层统一编排文件解析、图片 OCR、切块、向量化和 pgvector 入库。
    # 2) 逐文件执行校验与处理，保证单个坏文件不会把整批上传直接打成 4xx/5xx。
    # 3) 响应必须按输入顺序返回每个文件的结果，方便前端逐项展示失败原因。
    file_parser = build_file_parser(settings)
    image_ocr = build_image_markdown_ocr_client(settings)
    embedding_client = build_embedding_client(settings)
    vector_store = build_vector_store(settings)
    document_repository = build_rag_document_repository(settings)
    object_storage = build_rag_object_storage(settings)
    chunking_service = build_document_chunking_service(settings)
    logical_document_splitter = build_logical_document_splitter_service()
    normalizer = MarkdownStructureNormalizer()
    _log_upload(safe_trace_id, "初始化", "依赖构建完成，进入逐文件处理")
    _emit_progress(
        progress_callback,
        "batch-stage",
        trace_id=safe_trace_id,
        stage="处理",
        status="uploading",
        progress_percent=3 if assets else 0,
        message="依赖初始化完成，进入逐文件处理",
    )

    results: list[RagUploadFileResultDto] = []
    inserted = 0

    for index, asset in enumerate(assets, start=1):
        _raise_if_upload_cancelled(cancel_check)
        source_type = _detect_source_type(asset.file_name)
        fallback_ingest_source = _default_ingest_source(source_type)
        _log_upload(
            safe_trace_id,
            "文件",
            "开始处理文件",
            index=index,
            file_name=asset.file_name,
            source_type=source_type,
        )
        _emit_progress(
            progress_callback,
            "file-start",
            trace_id=safe_trace_id,
            file_index=index,
            total_files=len(assets),
            file_name=asset.file_name,
            source_type=source_type,
            ingest_source=fallback_ingest_source,
            stage="读取",
            status="uploading",
            file_progress_percent=_file_stage_progress_percent("读取"),
            progress_percent=_overall_file_progress_percent(index, len(assets), "读取"),
            message="开始处理文件",
        )

        stage = "读取"
        document_id: str | None = None
        object_key: str | None = None
        body_published = False
        try:
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在读取文件内容",
            )
            materialized_asset = _materialize_asset(
                asset=asset,
                max_file_size_mb=settings.rag_max_file_size_mb,
            )
            file_size_bytes = len(_require_file_bytes(materialized_asset))
            _raise_if_upload_cancelled(cancel_check)
            _log_upload(
                safe_trace_id,
                "读取",
                "文件流读取完成",
                file_name=asset.file_name,
                size_bytes=file_size_bytes,
            )

            stage = "校验"
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在校验文件格式和大小",
            )
            _validate_asset(asset=materialized_asset, max_file_size_mb=settings.rag_max_file_size_mb)
            _log_upload(safe_trace_id, "校验", "单文件校验通过", file_name=asset.file_name)

            file_bytes = _require_file_bytes(materialized_asset)
            file_sha256 = hashlib.sha256(file_bytes).hexdigest()
            scope_kind = str((asset.metadata or {}).get("scopeKind") or "unclassified")
            if scope_kind == "general":
                scope_kind = "unclassified"
            project_id = (asset.metadata or {}).get("knowledgeBaseId") or (asset.metadata or {}).get("projectId")
            if scope_kind == "knowledge_base":
                scope_kind = "project"
            document_repository.validate_document_scope(scope_kind, project_id)
            duplicate_document = document_repository.find_by_sha256(file_sha256)
            if duplicate_document is not None:
                if duplicate_document.get("archived"):
                    raise RagArchivedDocumentConflictError()
                if duplicate_document.get("scope_kind", "unclassified") != scope_kind or duplicate_document.get("project_id") != project_id:
                    raise RagDocumentScopeConflictError()
                raise RagDocumentConflictError("相同内容的知识库文件已经存在")

            document_id = uuid4().hex
            object_key = f"rag-documents/{document_id}/original"
            document_repository.create_processing_document(
                {
                    "document_id": document_id,
                    "scope_kind": scope_kind, "project_id": project_id,
                    "source_id": _asset_source_id(asset),
                    "original_filename": asset.file_name,
                    "original_content_type": materialized_asset.content_type,
                    "source_type": source_type,
                    "ingest_source": fallback_ingest_source,
                    "file_size_bytes": len(file_bytes),
                    "sha256": file_sha256,
                    "object_key": object_key,
                }
            )
            # 即使 SDK 在写入过程中抛错，也尝试按同一对象键补偿删除，避免留下孤儿对象。
            object_storage.put_object(object_key, file_bytes, materialized_asset.content_type)
            _raise_if_upload_cancelled(cancel_check)
            _log_upload(
                safe_trace_id,
                "文件",
                "原始文件已写入对象存储",
                file_name=asset.file_name,
                object_storage="minio",
            )

            stage = "解析"
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在解析文件内容",
            )
            extracted = _extract_document(
                asset=materialized_asset,
                file_parser=file_parser,
                image_ocr=image_ocr,
            )
            _log_upload(
                safe_trace_id,
                "解析",
                "文件内容解析完成",
                file_name=asset.file_name,
                ingest_source=extracted.ingest_source,
                content_chars=len(extracted.content),
            )

            stage = "规范化"
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在规范化文本结构",
            )
            normalized_content = normalizer.normalize(extracted.content)
            can_publish_without_text = Path(asset.file_name).suffix.lower() in {".pdf", ".docx"}
            if not normalized_content and not can_publish_without_text:
                raise FileParseError(f"文件 {asset.file_name} 未生成可入库内容")
            extracted = ExtractedDocument(
                source_id=extracted.source_id,
                original_filename=extracted.original_filename,
                original_content_type=extracted.original_content_type,
                source_type=extracted.source_type,
                ingest_source=extracted.ingest_source,
                content=normalized_content,
                metadata=extracted.metadata,
            )
            _log_upload(
                safe_trace_id,
                "规范化",
                "文本规范化完成",
                file_name=asset.file_name,
                normalized_chars=len(normalized_content),
            )

            stage = "逻辑文档拆分"
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在拆分逻辑文档",
            )
            logical_documents = (
                logical_document_splitter.split_document(extracted) if normalized_content else []
            )
            if not logical_documents and not can_publish_without_text:
                raise FileParseError(f"文件 {asset.file_name} 未拆分出有效 logical document")
            _log_upload(
                safe_trace_id,
                "逻辑文档拆分",
                "逻辑文档拆分完成",
                file_name=asset.file_name,
                logical_document_count=len(logical_documents),
            )

            stage = "切块"
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在切分知识库 chunk",
            )
            chunks: list[RagChunk] = []
            for logical_document in logical_documents:
                chunks.extend(chunking_service.chunk_document(logical_document))
            if not chunks and not can_publish_without_text:
                raise FileParseError(f"文件 {asset.file_name} 未切分出有效 chunk")
            _raise_if_upload_cancelled(cancel_check)
            _assign_global_chunk_indexes(chunks)
            chunk_texts = [chunk.content for chunk in chunks]
            total_chars = sum(len(text) for text in chunk_texts)
            _log_upload(
                safe_trace_id,
                "切块",
                "文档切块完成",
                file_name=asset.file_name,
                logical_document_count=len(logical_documents),
                chunk_count=len(chunks),
                total_chunk_chars=total_chars,
            )

            stage = "Embedding"
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在生成 Embedding",
            )
            embedding_started_at = perf_counter()
            _emit_embedding_timing_log(
                trace_id=safe_trace_id,
                event="start",
                file_name=asset.file_name,
                source_type=extracted.source_type,
                ingest_source=extracted.ingest_source,
                chunk_count=len(chunk_texts),
                total_chars=total_chars,
                file_size_bytes=file_size_bytes,
                embedding_model=getattr(embedding_client, "model_name", None),
                embedding_base_url=getattr(embedding_client, "base_url", None),
            )
            embeddings: list[list[float]] = []
            if chunks:
                try:
                    chunks, embeddings = _embed_chunks_with_semantic_fallback(
                        chunks=chunks,
                        embedding_client=embedding_client,
                        chunking_service=chunking_service,
                        trace_id=safe_trace_id,
                        file_name=asset.file_name,
                        cancel_check=cancel_check,
                    )
                    chunk_texts = [chunk.content for chunk in chunks]
                    total_chars = sum(len(text) for text in chunk_texts)
                except Exception as exc:
                    _emit_embedding_timing_log(
                        trace_id=safe_trace_id,
                        event="failed",
                        file_name=asset.file_name,
                        source_type=extracted.source_type,
                        ingest_source=extracted.ingest_source,
                        chunk_count=len(chunk_texts),
                        total_chars=total_chars,
                        file_size_bytes=file_size_bytes,
                        elapsed_ms=(perf_counter() - embedding_started_at) * 1000,
                        embedding_model=getattr(embedding_client, "model_name", None),
                        embedding_base_url=getattr(embedding_client, "base_url", None),
                        error_type=type(exc).__name__,
                    )
                    raise
                _emit_embedding_timing_log(
                    trace_id=safe_trace_id,
                    event="finish",
                    file_name=asset.file_name,
                    source_type=extracted.source_type,
                    ingest_source=extracted.ingest_source,
                    chunk_count=len(chunk_texts),
                    total_chars=total_chars,
                    file_size_bytes=file_size_bytes,
                    elapsed_ms=(perf_counter() - embedding_started_at) * 1000,
                    embedding_model=getattr(embedding_client, "model_name", None),
                    embedding_base_url=getattr(embedding_client, "base_url", None),
                    vector_count=len(embeddings),
                )
            else:
                _log_upload(
                    safe_trace_id,
                    "Embedding",
                    "正文没有文字 Chunk，等待图片增强处理",
                    file_name=asset.file_name,
                )

            stage = "入库"
            _raise_if_upload_cancelled(cancel_check)
            _emit_file_stage(
                progress_callback,
                safe_trace_id,
                index,
                len(assets),
                asset.file_name,
                stage,
                "正在写入 pgvector",
            )
            _log_upload(
                safe_trace_id,
                "入库",
                "开始写入向量库",
                file_name=asset.file_name,
                vector_count=len(embeddings),
            )
            inserted_count = document_repository.complete_document_ingest(
                document_id=document_id,
                documents=[
                    {
                        "source_id": chunk.source_id,
                        "content": chunk.content,
                        "metadata": chunk.metadata,
                    }
                    for chunk in chunks
                ],
                embeddings=embeddings,
                preview_text=normalized_content,
                embedding_model=getattr(vector_store, "embedding_model_name", settings.embedding_model_name),
            )
            body_published = True
            image_summary = None
            if Path(asset.file_name).suffix.lower() in {".pdf", ".docx"}:
                try:
                    image_summary = enqueue_image_enrichment_task(
                        document_id,
                        repository=document_repository,
                        trace_id=safe_trace_id,
                        force=True,
                    )
                except Exception as exc:
                    # 正文已经发布，队列编排异常只能记录日志，不能回滚正文。
                    _log_upload(
                        safe_trace_id,
                        "图片增强",
                        "图片增强异常，正文保持可用",
                        file_name=asset.file_name,
                        error_type=type(exc).__name__,
                    )
                    image_summary = image_enrichment_summary_from_document(
                        document_repository.get_document(document_id) or {
                            "image_enrichment_status": "failed",
                            "image_enrichment_message": "图片增强任务暂时不可用，可稍后重试图片解析",
                            "image_enrichment_retryable": True,
                        }
                    )
                    if image_summary.status == "not_applicable":
                        image_summary.status = "failed"
                        image_summary.message = "图片增强任务暂时不可用，可稍后重试图片解析"
                        image_summary.retryable = True
            image_chunk_count = image_summary.chunk_count if image_summary is not None else 0
            total_chunk_count = len(chunks) + image_chunk_count
            total_inserted_count = inserted_count + image_chunk_count
            _raise_if_upload_cancelled(cancel_check)
            inserted += total_inserted_count
            _log_upload(
                safe_trace_id,
                "入库",
                "向量库写入完成",
                file_name=asset.file_name,
                inserted_count=total_inserted_count,
                image_indexed_count=image_summary.indexed_count if image_summary else 0,
                image_skipped_count=image_summary.skipped_count if image_summary else 0,
                image_failed_count=image_summary.failed_count if image_summary else 0,
            )

            result = RagUploadFileResultDto(
                file_name=asset.file_name,
                content_type=materialized_asset.content_type,
                source_type=extracted.source_type,
                ingest_source=extracted.ingest_source,
                chunk_count=total_chunk_count,
                inserted_count=total_inserted_count,
                status="success",
                document_id=document_id,
                image_candidate_count=image_summary.candidate_count if image_summary else 0,
                image_analyzed_count=image_summary.analyzed_count if image_summary else 0,
                image_indexed_count=image_summary.indexed_count if image_summary else 0,
                image_skipped_count=image_summary.skipped_count if image_summary else 0,
                image_failed_count=image_summary.failed_count if image_summary else 0,
                image_chunk_count=image_chunk_count,
                image_enrichment_status=image_summary.status if image_summary else "not_applicable",
                image_enrichment_message=image_summary.message if image_summary else None,
                image_retry_available=image_summary.retryable if image_summary else False,
            )
            results.append(result)
            _emit_file_result(progress_callback, safe_trace_id, index, len(assets), result)
            _log_upload(safe_trace_id, "文件", "文件处理成功", file_name=asset.file_name)
        except RagIngestError as exc:
            # 异常与兜底策略：
            # 1) 只吞掉明确的 RAG 业务异常，保持逐文件失败契约。
            # 2) 未知异常继续上抛，由统一异常处理器返回通用错误，避免静默掩盖程序错误。
            safe_error_message = public_rag_error_message(exc, fallback="文件处理失败，请稍后重试")
            if not body_published:
                _cleanup_failed_upload(
                    document_repository=document_repository,
                    object_storage=object_storage,
                    document_id=document_id,
                    object_key=object_key,
                    trace_id=safe_trace_id,
                    file_name=asset.file_name,
                )
            if isinstance(exc, RagUploadCancelledError):
                raise
            if body_published:
                raise
            if isinstance(exc, RagDocumentConflictError) and raise_conflict and len(assets) == 1:
                raise
            _log_upload(
                safe_trace_id,
                "异常",
                "文件处理失败",
                file_name=asset.file_name,
                stage=stage,
                error_type=type(exc).__name__,
            )
            result = RagUploadFileResultDto(
                file_name=asset.file_name,
                content_type=asset.content_type,
                source_type=source_type,
                ingest_source=fallback_ingest_source,
                chunk_count=0,
                inserted_count=0,
                status="failed",
                error_message=safe_error_message,
            )
            results.append(result)
            _emit_file_result(progress_callback, safe_trace_id, index, len(assets), result, stage=stage)
        except Exception:
            if not body_published:
                _cleanup_failed_upload(
                    document_repository=document_repository,
                    object_storage=object_storage,
                    document_id=document_id,
                    object_key=object_key,
                    trace_id=safe_trace_id,
                    file_name=asset.file_name,
                )
            raise

    response = RagUploadResponseDto(
        total_files=len(assets),
        succeeded_files=sum(1 for item in results if item.status == "success"),
        failed_files=sum(1 for item in results if item.status == "failed"),
        inserted=inserted,
        files=results,
    )
    if emit_batch_complete:
        _log_upload(
            safe_trace_id,
            "完成",
            "上传请求处理结束",
            total_files=response.total_files,
            succeeded_files=response.succeeded_files,
            failed_files=response.failed_files,
            inserted=response.inserted,
        )
        _emit_progress(
            progress_callback,
            "batch-complete",
            trace_id=safe_trace_id,
            total_files=response.total_files,
            succeeded_files=response.succeeded_files,
            failed_files=response.failed_files,
            inserted=response.inserted,
            stage="完成",
            status="success" if response.failed_files == 0 else "failed",
            progress_percent=100,
            files=[_upload_result_to_progress_payload(item) for item in response.files],
            message="上传请求处理结束",
        )
    return response
