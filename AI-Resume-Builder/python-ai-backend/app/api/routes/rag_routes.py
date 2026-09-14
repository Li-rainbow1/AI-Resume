import json
from collections.abc import AsyncIterator
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from tempfile import TemporaryFile
from typing import BinaryIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse

from app.api.deps.auth import AuthUserContext, require_admin_user_context, require_auth_user_context
from app.api.mappers.rag_mapper import (
    rag_ingest_request_to_dto,
    rag_ingest_response_from_dto,
    rag_document_batch_delete_request_to_dto,
    rag_document_batch_delete_response_from_dto,
    rag_query_request_to_dto,
    rag_query_response_from_dto,
    rag_upload_assets_to_dto,
    rag_markdown_bundle_to_dto,
    rag_upload_response_from_dto,
    rag_document_delete_response_from_dto,
    rag_document_list_response_from_dto,
    rag_document_image_enrichment_response_from_dto,
)
from app.api.schemas.rag import (
    RagIngestRequest,
    RagIngestResponse,
    RagQueryRequest,
    RagQueryResponse,
    RagUploadResponse,
    RagDocumentBatchDeleteRequest,
    RagDocumentBatchDeleteResponse,
    RagDocumentDeleteResponse,
    RagDocumentListResponse,
    RagDocumentImageEnrichmentResponse,
)
from app.application.use_cases.ingest_rag_documents import ingest_rag_documents as ingest_rag_documents_use_case
from app.application.use_cases.manage_rag_scope import normalize_scope_input, validate_upload_scope
from app.application.use_cases.run_rag_query import run_rag_query as run_rag_query_use_case
from app.application.use_cases.manage_rag_documents import (
    batch_delete_rag_documents,
    delete_rag_document,
    download_rag_document,
    list_rag_documents,
    preview_rag_document,
    preview_rag_document_asset as preview_rag_document_asset_use_case,
)
from app.application.use_cases.upload_and_ingest_rag_assets import (
    upload_and_ingest_rag_assets as upload_and_ingest_rag_assets_use_case,
)
from app.application.use_cases.upload_markdown_bundle import (
    upload_markdown_bundle as upload_markdown_bundle_use_case,
)
from app.application.use_cases.enrich_rag_document_images import (
    retry_rag_document_image_enrichment,
)
from app.application.services.rag_image_enrichment_queue_service import (
    get_rag_document_image_enrichment,
)
from app.application.dto.rag_dto import RagDocumentImageEnrichmentResponseDto
from app.domain.exceptions.rag_exceptions import (
    RagIngestError,
    RagUploadCancelledError,
    public_rag_error_message,
    safe_rag_log_value,
)

router = APIRouter(prefix="/api/ai", tags=["ai-rag"])
_PRIVATE_FILE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/rag/documents", response_model=RagDocumentListResponse)
def rag_list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    file_type: str = Query(default="all", alias="fileType"),
    archive: str = Query(default="active"),
    scope_kind: str | None = Query(default=None, alias="scopeKind"),
    project_id: str | None = Query(default=None, alias="projectId"),
    knowledge_base_id: str | None = Query(default=None, alias="knowledgeBaseId"),
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagDocumentListResponse:
    _ = user_context
    try:
        if project_id and knowledge_base_id and project_id != knowledge_base_id:
            raise ValueError("knowledgeBaseId 与 projectId 不能同时指向不同知识库")
        response = list_rag_documents(
            page=page,
            page_size=page_size,
            file_type=file_type,
            archive=archive,
            scope_kind=scope_kind,
            project_id=knowledge_base_id or project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return rag_document_list_response_from_dto(response)


@router.get("/rag/documents/{document_id}/preview")
def rag_preview_document(
    document_id: str,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> Response:
    _ = user_context
    asset = preview_rag_document(document_id)
    headers = {
        **_PRIVATE_FILE_HEADERS,
        "Content-Disposition": _content_disposition(asset.file_name, "inline"),
    }
    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers=headers,
    )


@router.get("/rag/documents/{document_id}/asset")
def rag_preview_document_asset(
    document_id: str,
    path: str = Query(..., min_length=1),
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> Response:
    _ = user_context
    asset = preview_rag_document_asset_use_case(document_id, path)
    headers = {
        **_PRIVATE_FILE_HEADERS,
        "Content-Disposition": _content_disposition(asset.file_name, "inline"),
    }
    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers=headers,
    )


@router.get("/rag/documents/{document_id}/download")
def rag_download_document(
    document_id: str,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> Response:
    _ = user_context
    asset = download_rag_document(document_id)
    headers = {
        **_PRIVATE_FILE_HEADERS,
        "Content-Disposition": _content_disposition(asset.file_name, "attachment"),
    }
    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers=headers,
    )


@router.delete("/rag/documents", response_model=RagDocumentBatchDeleteResponse)
def rag_batch_delete_documents(
    request: RagDocumentBatchDeleteRequest,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagDocumentBatchDeleteResponse:
    _ = user_context
    return rag_document_batch_delete_response_from_dto(
        batch_delete_rag_documents(rag_document_batch_delete_request_to_dto(request))
    )


@router.delete("/rag/documents/{document_id}", response_model=RagDocumentDeleteResponse)
def rag_delete_document(
    document_id: str,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagDocumentDeleteResponse:
    _ = user_context
    return rag_document_delete_response_from_dto(delete_rag_document(document_id))


@router.post(
    "/rag/documents/{document_id}/image-enrichment/retry",
    response_model=RagDocumentImageEnrichmentResponse,
)
def rag_retry_document_image_enrichment(
    document_id: str,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagDocumentImageEnrichmentResponse:
    """管理员重试指定复合文档中失败或尚未入库的图片解析。"""
    _ = user_context
    summary = retry_rag_document_image_enrichment(document_id)
    return rag_document_image_enrichment_response_from_dto(
        RagDocumentImageEnrichmentResponseDto(
            document_id=document_id,
            candidate_count=summary.candidate_count,
            analyzed_count=summary.analyzed_count,
            indexed_count=summary.indexed_count,
            skipped_count=summary.skipped_count,
            failed_count=summary.failed_count,
            chunk_count=summary.chunk_count,
            status=summary.status,
            message=summary.message,
            retry_available=summary.retryable,
            task_id=summary.task_id,
            queued_at=summary.queued_at,
            current_position=summary.current_position,
        )
    )


@router.get(
    "/rag/documents/{document_id}/image-enrichment",
    response_model=RagDocumentImageEnrichmentResponse,
)
def rag_get_document_image_enrichment(
    document_id: str,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagDocumentImageEnrichmentResponse:
    """管理员读取图片增强队列和处理进度。"""
    _ = user_context
    summary = get_rag_document_image_enrichment(document_id)
    return rag_document_image_enrichment_response_from_dto(
        RagDocumentImageEnrichmentResponseDto(
            document_id=document_id,
            candidate_count=summary.candidate_count,
            analyzed_count=summary.analyzed_count,
            indexed_count=summary.indexed_count,
            skipped_count=summary.skipped_count,
            failed_count=summary.failed_count,
            chunk_count=summary.chunk_count,
            status=summary.status,
            message=summary.message,
            retry_available=summary.retryable,
            task_id=summary.task_id,
            queued_at=summary.queued_at,
            current_position=summary.current_position,
        )
    )


@router.post("/rag/query", response_model=RagQueryResponse)
def rag_query(
    request: RagQueryRequest,
    user_context: AuthUserContext = Depends(require_auth_user_context),
) -> RagQueryResponse:
    _ = user_context
    return rag_query_response_from_dto(run_rag_query_use_case(rag_query_request_to_dto(request)))


@router.post("/rag/documents", response_model=RagIngestResponse)
def rag_ingest_documents(
    request: RagIngestRequest,
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagIngestResponse:
    _ = user_context
    return rag_ingest_response_from_dto(
        ingest_rag_documents_use_case(rag_ingest_request_to_dto(request))
    )


@router.post("/rag/upload", response_model=RagUploadResponse)
async def rag_upload_assets(
    files: list[UploadFile] = File(...),
    scope_kind: str = Form(default="unclassified", alias="scopeKind"),
    project_id: str | None = Form(default=None, alias="projectId"),
    knowledge_base_id: str | None = Form(default=None, alias="knowledgeBaseId"),
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> RagUploadResponse:
    _ = user_context
    try:
        scope_kind, project_id = normalize_scope_input(scope_kind, project_id, knowledge_base_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await run_in_threadpool(validate_upload_scope, scope_kind, project_id)
    trace_id = uuid4().hex[:8]
    _log_route(trace_id, "收到上传请求", file_count=len(files))

    # 路由层只负责 HTTP 入参接收和日志观察。
    # 文件读取、OCR、Embedding、pgvector 写入都是阻塞链路，必须整体移出事件循环，
    # 否则一次上传就会卡住同进程内其他 FastAPI 请求。
    try:
        return await run_in_threadpool(_handle_rag_upload_request, files, trace_id, scope_kind, project_id)
    except Exception as exc:
        _log_route(trace_id, "上传请求异常", error_type=type(exc).__name__)
        if isinstance(exc, RagIngestError):
            raise
        raise HTTPException(status_code=500, detail=public_rag_error_message(exc)) from None


def _handle_rag_upload_request(files: list[UploadFile], trace_id: str, scope_kind: str = "unclassified", project_id: str | None = None) -> RagUploadResponse:
    # 同步上传流水线的职责：
    # 1) 只登记文件名、content-type 和可读取的 file handle，不在路由层预读整批字节。
    # 2) 让 application use case 逐文件执行限流读取、解析、OCR、切块、向量化和入库。
    # 3) 保持现有前端响应契约，并记录成功/失败汇总日志。
    assets: list[tuple[str, str, BinaryIO]] = []
    for index, item in enumerate(files, start=1):
        file_name = item.filename or "upload.bin"
        content_type = item.content_type or "application/octet-stream"
        _log_route(trace_id, "登记上传文件", index=index, file_name=file_name, content_type=content_type)
        item.file.seek(0)
        assets.append((file_name, content_type, item.file))

    response_dto = upload_and_ingest_rag_assets_use_case(
        rag_upload_assets_to_dto(assets, scope_kind, project_id),
        trace_id=trace_id,
        raise_conflict=True,
    )
    _log_route(
        trace_id,
        "上传请求处理完成",
        total_files=response_dto.total_files,
        succeeded_files=response_dto.succeeded_files,
        failed_files=response_dto.failed_files,
        inserted=response_dto.inserted,
    )
    return rag_upload_response_from_dto(response_dto)


@router.post("/rag/upload/stream")
async def rag_upload_assets_stream(
    request: Request,
    files: list[UploadFile] = File(...),
    scope_kind: str = Form(default="unclassified", alias="scopeKind"),
    project_id: str | None = Form(default=None, alias="projectId"),
    knowledge_base_id: str | None = Form(default=None, alias="knowledgeBaseId"),
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> StreamingResponse:
    _ = user_context
    try:
        scope_kind, project_id = normalize_scope_input(scope_kind, project_id, knowledge_base_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await run_in_threadpool(validate_upload_scope, scope_kind, project_id)
    trace_id = uuid4().hex[:8]
    _log_route(trace_id, "收到流式上传请求", file_count=len(files))
    assets = await run_in_threadpool(_detach_rag_upload_assets, files, trace_id)
    return StreamingResponse(
        _iterate_rag_upload_progress_stream(assets, trace_id, request, scope_kind, project_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/rag/markdown-upload/stream")
async def rag_markdown_upload_assets_stream(
    request: Request,
    files: list[UploadFile] = File(...),
    scope_kind: str = Form(default="unclassified", alias="scopeKind"),
    project_id: str | None = Form(default=None, alias="projectId"),
    knowledge_base_id: str | None = Form(default=None, alias="knowledgeBaseId"),
    manifest: str = Form(...),
    user_context: AuthUserContext = Depends(require_admin_user_context),
) -> StreamingResponse:
    """接收 Markdown 正文和附件目录，并把长耗时处理交给应用层。"""
    _ = user_context
    try:
        scope_kind, project_id = normalize_scope_input(scope_kind, project_id, knowledge_base_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await run_in_threadpool(validate_upload_scope, scope_kind, project_id)
    trace_id = uuid4().hex[:8]
    _log_route(trace_id, "收到 Markdown 附件上传请求", file_count=len(files))
    try:
        bundle = await run_in_threadpool(
            _detach_rag_markdown_bundle_assets,
            files,
            manifest,
            trace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StreamingResponse(
        _iterate_markdown_upload_progress_stream(bundle, trace_id, request, scope_kind, project_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _detach_rag_markdown_bundle_assets(
    files: list[UploadFile],
    manifest: str,
    trace_id: str,
) -> tuple[
    list[tuple[str, str, BinaryIO, str]],
    list[tuple[str, str, BinaryIO, str]],
]:
    """校验 manifest 后复制正文与附件到请求外仍可读取的临时文件。"""
    document_specs, attachment_specs = _parse_markdown_manifest(manifest, len(files))
    detached: list[tuple[str, str, BinaryIO, str]] = []
    try:
        for index, item in enumerate(files):
            spec = document_specs.get(index) or attachment_specs.get(index)
            if spec is None:
                raise ValueError("manifest 未覆盖全部上传文件")
            relative_path = spec
            file_name = item.filename or relative_path.rsplit("/", 1)[-1] or "upload.bin"
            content_type = item.content_type or "application/octet-stream"
            _log_route(
                trace_id,
                "准备 Markdown 文件",
                index=index,
                file_name=file_name,
                relative_path=relative_path,
                role="document" if index in document_specs else "attachment",
            )
            item.file.seek(0)
            detached_file = TemporaryFile(mode="w+b")
            while True:
                chunk = item.file.read(1024 * 1024)
                if not chunk:
                    break
                detached_file.write(chunk)
            detached_file.seek(0)
            detached.append((file_name, content_type, detached_file, relative_path))
        documents = [item for index, item in enumerate(detached) if index in document_specs]
        attachments = [item for index, item in enumerate(detached) if index in attachment_specs]
        return documents, attachments
    except Exception:
        for _, _, stream, _ in detached:
            try:
                stream.close()
            except Exception:
                pass
        raise


def _parse_markdown_manifest(
    manifest: str,
    file_count: int,
) -> tuple[dict[int, str], dict[int, str]]:
    try:
        payload = json.loads(manifest)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest 不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("manifest 格式无效")

    def parse_entries(key: str) -> dict[int, str]:
        raw_entries = payload.get(key)
        if not isinstance(raw_entries, list):
            raise ValueError(f"manifest.{key} 必须是数组")
        entries: dict[int, str] = {}
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                raise ValueError(f"manifest.{key} 包含无效项")
            index = raw_entry.get("index")
            relative_path = raw_entry.get("relativePath")
            if isinstance(index, bool) or not isinstance(index, int) or not isinstance(relative_path, str):
                raise ValueError(f"manifest.{key} 的 index 或 relativePath 无效")
            if index < 0 or index >= file_count or index in entries:
                raise ValueError(f"manifest.{key} 的 index 重复或越界")
            safe_path = relative_path.strip()
            if not safe_path:
                raise ValueError(f"manifest.{key} 的 relativePath 不能为空")
            entries[index] = safe_path
        return entries

    document_specs = parse_entries("documents")
    attachment_specs = parse_entries("attachments")
    if not document_specs:
        raise ValueError("manifest.documents 不能为空")
    all_indexes = set(document_specs) | set(attachment_specs)
    if len(all_indexes) != file_count or all_indexes != set(range(file_count)):
        raise ValueError("manifest 必须准确描述每一个上传文件")
    if set(document_specs) & set(attachment_specs):
        raise ValueError("同一个文件不能同时作为正文和附件")
    return document_specs, attachment_specs


async def _iterate_markdown_upload_progress_stream(
    bundle: tuple[
        list[tuple[str, str, BinaryIO, str]],
        list[tuple[str, str, BinaryIO, str]],
    ],
    trace_id: str,
    request: Request,
    scope_kind: str = "unclassified", project_id: str | None = None,
) -> AsyncIterator[str]:
    """把 Markdown 组合上传用例的进度转换为现有 SSE 契约。"""
    document_files, attachment_files = bundle
    application_bundle = rag_markdown_bundle_to_dto(document_files, attachment_files, scope_kind, project_id)
    event_queue: Queue[dict[str, object] | None] = Queue()
    cancel_event = Event()

    def emit_progress(event: dict[str, object]) -> None:
        event_queue.put(event)

    def run_upload() -> None:
        try:
            upload_markdown_bundle_use_case(
                application_bundle,
                trace_id=trace_id,
                progress_callback=emit_progress,
                cancel_check=cancel_event.is_set,
            )
        except Exception as exc:
            _log_route(trace_id, "Markdown 附件上传请求异常", error_type=type(exc).__name__)
            if not isinstance(exc, RagUploadCancelledError) and not cancel_event.is_set():
                event_queue.put(
                    {
                        "event": "error",
                        "trace_id": trace_id,
                        "status": "failed",
                        "stage": "异常",
                        "progress_percent": 0,
                        "message": public_rag_error_message(exc),
                    }
                )
        finally:
            for _, _, file_stream, _ in [*document_files, *attachment_files]:
                try:
                    file_stream.close()
                except Exception:
                    pass
            event_queue.put(None)

    Thread(target=run_upload, daemon=True).start()
    last_keepalive = monotonic()
    try:
        while True:
            if await request.is_disconnected():
                cancel_event.set()
                break
            try:
                event = await run_in_threadpool(event_queue.get, True, 0.5)
            except Empty:
                if monotonic() - last_keepalive >= 15:
                    yield ": keep-alive\n\n"
                    last_keepalive = monotonic()
                continue
            if event is None:
                break
            last_keepalive = monotonic()
            yield _format_sse_event(event)
    finally:
        cancel_event.set()


def _detach_rag_upload_assets(files: list[UploadFile], trace_id: str) -> list[tuple[str, str, BinaryIO]]:
    """复制到由后台线程自行管理的临时文件，避免依赖请求生命周期内的 UploadFile。"""
    assets: list[tuple[str, str, BinaryIO]] = []
    detached_file: BinaryIO | None = None
    try:
        for index, item in enumerate(files, start=1):
            file_name = item.filename or "upload.bin"
            content_type = item.content_type or "application/octet-stream"
            _log_route(trace_id, "准备流式上传文件", index=index, file_name=file_name, content_type=content_type)
            item.file.seek(0)
            detached_file = TemporaryFile(mode="w+b")
            while True:
                chunk = item.file.read(1024 * 1024)
                if not chunk:
                    break
                detached_file.write(chunk)
            detached_file.seek(0)
            assets.append((file_name, content_type, detached_file))
            detached_file = None
    except Exception:
        if detached_file is not None:
            try:
                detached_file.close()
            except Exception:
                pass
        for _, _, asset_stream in assets:
            try:
                asset_stream.close()
            except Exception:
                pass
        raise
    return assets


async def _iterate_rag_upload_progress_stream(
    assets: list[tuple[str, str, BinaryIO]],
    trace_id: str,
    request: Request,
    scope_kind: str = "unclassified", project_id: str | None = None,
) -> AsyncIterator[str]:
    # 流式路由的职责边界：
    # 1) HTTP 层只负责把进度字典转成 SSE。
    # 2) RAG 解析、OCR、Embedding、pgvector 写入仍由 application use case 负责。
    # 3) 阻塞处理放入后台线程，避免 StreamingResponse 等整批完成后才 flush。
    event_queue: Queue[dict[str, object] | None] = Queue()
    cancel_event = Event()

    def emit_progress(event: dict[str, object]) -> None:
        event_queue.put(event)

    def run_upload() -> None:
        try:
            upload_and_ingest_rag_assets_use_case(
                rag_upload_assets_to_dto(assets, scope_kind, project_id),
                trace_id=trace_id,
                progress_callback=emit_progress,
                cancel_check=cancel_event.is_set,
            )
        except Exception as exc:
            _log_route(trace_id, "流式上传请求异常", error_type=type(exc).__name__)
            if not isinstance(exc, RagUploadCancelledError) and not cancel_event.is_set():
                event_queue.put(
                    {
                        "event": "error",
                        "trace_id": trace_id,
                        "status": "failed",
                        "stage": "异常",
                        "progress_percent": 0,
                        "message": public_rag_error_message(exc),
                    }
                )
        finally:
            for _, _, file_stream in assets:
                try:
                    file_stream.close()
                except Exception:
                    pass
            event_queue.put(None)

    Thread(target=run_upload, daemon=True).start()

    last_keepalive = monotonic()
    try:
        while True:
            if await request.is_disconnected():
                cancel_event.set()
                break
            try:
                event = await run_in_threadpool(event_queue.get, True, 0.5)
            except Empty:
                if monotonic() - last_keepalive >= 15:
                    yield ": keep-alive\n\n"
                    last_keepalive = monotonic()
                continue
            if event is None:
                break
            last_keepalive = monotonic()
            yield _format_sse_event(event)
    finally:
        # 正常结束、客户端断开和响应取消都要通知后台任务停止后续阶段。
        cancel_event.set()


def _format_sse_event(event: dict[str, object]) -> str:
    event_name = str(event.get("event") or "message")
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n"


def _log_route(trace_id: str, message: str, **extra: object) -> None:
    parts = [f"[知识库上传][trace={trace_id}][路由] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)


def _content_disposition(file_name: str, disposition: str) -> str:
    from urllib.parse import quote

    safe_name = (file_name or "document").replace("\r", "").replace("\n", "")
    return f"{disposition}; filename*=UTF-8''{quote(safe_name, safe='')}"
