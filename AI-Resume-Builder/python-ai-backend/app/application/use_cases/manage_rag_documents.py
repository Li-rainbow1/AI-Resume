from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.application.dto.rag_dto import (
    RagDocumentAssetDto,
    RagDocumentBatchDeleteItemDto,
    RagDocumentBatchDeleteRequestDto,
    RagDocumentBatchDeleteResponseDto,
    RagDocumentDeleteResponseDto,
    RagDocumentItemDto,
    RagDocumentListResponseDto,
)
from app.bootstrap.container import (
    build_rag_document_repository,
    build_rag_object_storage,
)
from app.domain.exceptions.rag_exceptions import (
    ObjectStorageError,
    RagDocumentConflictError,
    RagDocumentNotFoundError,
    VectorStoreError,
)
from app.domain.services.markdown_attachment_service import normalize_markdown_attachment_path

_PREVIEW_MAX_CHARS = 50_000
_BINARY_PREVIEW_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_TEXT_PREVIEW_EXTENSIONS = {".txt", ".md"}
_RAG_DOCUMENT_FILE_TYPES = frozenset({"all", "pdf", "word", "txt", "md", "image", "other"})
_FILE_TYPE_BY_EXTENSION = {
    ".pdf": "pdf",
    ".doc": "word",
    ".docx": "word",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}
_FILE_TYPE_BY_MIME = {
    "application/pdf": "pdf",
    "application/msword": "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "text/plain": "txt",
    "text/markdown": "md",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/webp": "image",
}
_CONTENT_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@dataclass(slots=True)
class _RagDocumentDeleteOperation:
    """一组需要一次性处理的逻辑文件及其请求方文档 ID。"""

    requested_document_ids: list[str] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ready"


def list_rag_documents(
    page: int = 1,
    page_size: int = 20,
    file_type: str = "all",
    archive: str = "active",
    scope_kind: str | None = None,
    project_id: str | None = None,
) -> RagDocumentListResponseDto:
    # 这里只读取文件资产列表，不改变文件状态、对象存储或向量数据。
    safe_page = max(1, int(page))
    safe_page_size = min(100, max(1, int(page_size)))
    safe_file_type = _normalize_file_type(file_type)
    repository = build_rag_document_repository()
    # 类型条件和分页交给仓储统一执行，保证 total 与 items 使用同一筛选范围。
    if archive not in {"active", "archived", "all"}:
        raise ValueError("归档筛选参数无效")
    if scope_kind == "general":
        scope_kind = "unclassified"
    if scope_kind and scope_kind not in {"project", "unclassified"}:
        raise ValueError("归属筛选参数无效")
    rows, total = repository.list_documents(safe_page, safe_page_size, safe_file_type, archive, scope_kind, project_id)
    names = {item["project_id"]: item["name"] for item in repository.list_projects()}
    for row in rows:
        row["project_name"] = names.get(row.get("project_id"))
    return RagDocumentListResponseDto(
        items=[_document_item_from_record(row) for row in rows],
        total=total,
        page=safe_page,
        page_size=safe_page_size,
    )


def preview_rag_document(document_id: str) -> RagDocumentAssetDto:
    # 预览只负责协调文件状态、历史兼容分支和对象存储读取，不把 MinIO 地址暴露给 API 层。
    # 新上传文件必须从私有对象存储读取原始字节；只有没有原始对象的 legacy 记录才返回已入库文本。
    # 处理中或删除中的记录立即拒绝，原始对象缺失和对象存储异常统一交给存储异常处理器。
    repository = build_rag_document_repository()
    document = _get_document(repository, document_id)
    status = str(document.get("status") or "")
    if status in {"processing", "deleting", "cleanup_failed"}:
        raise RagDocumentConflictError("文件仍在处理中或清理中，暂时不能预览")

    file_name = str(document.get("original_filename") or "document")
    if status == "legacy":
        preview_text = repository.get_preview_text(document, _PREVIEW_MAX_CHARS)
        return RagDocumentAssetDto(
            document_id=str(document.get("document_id") or document_id),
            file_name=file_name,
            content_type="text/plain; charset=utf-8",
            content=preview_text.encode("utf-8"),
            is_text=True,
        )

    object_key = str(document.get("object_key") or "").strip()
    if not object_key:
        raise ObjectStorageError("知识库原始文件对象不存在")
    stored = build_rag_object_storage().get_object(object_key)
    extension = Path(file_name).suffix.lower()
    return RagDocumentAssetDto(
        document_id=str(document.get("document_id") or document_id),
        file_name=file_name,
        content_type=_resolve_content_type(document, stored.content_type),
        content=stored.content,
        is_text=extension in _TEXT_PREVIEW_EXTENSIONS,
    )


def preview_rag_document_asset(document_id: str, relative_path: str) -> RagDocumentAssetDto:
    """读取 Markdown 已关联的图片，保证浏览器不会直接访问相对路径。"""
    normalized_path = normalize_markdown_attachment_path(relative_path)
    if normalized_path is None:
        raise RagDocumentNotFoundError("图片附件路径无效")
    repository = build_rag_document_repository()
    document = _get_document(repository, document_id)
    if str(document.get("status") or "") != "ready":
        raise RagDocumentConflictError("文件当前不可读取图片附件")
    asset = repository.get_document_asset(document_id, normalized_path)
    if asset is None:
        raise RagDocumentNotFoundError("图片附件未上传")
    object_key = str(asset.get("object_key") or "").strip()
    if not object_key:
        raise ObjectStorageError("图片附件对象不存在")
    stored = build_rag_object_storage().get_object(object_key)
    return RagDocumentAssetDto(
        document_id=str(document.get("document_id") or document_id),
        file_name=Path(normalized_path).name or "attachment",
        content_type=str(asset.get("content_type") or stored.content_type or "application/octet-stream"),
        content=stored.content,
        is_text=False,
    )


def download_rag_document(document_id: str) -> RagDocumentAssetDto:
    repository = build_rag_document_repository()
    document = _get_document(repository, document_id)
    status = str(document.get("status") or "")
    if status in {"processing", "deleting"}:
        raise RagDocumentConflictError("文件仍在处理中或删除中，暂时不能下载")
    object_key = str(document.get("object_key") or "").strip()
    if status in {"legacy", "cleanup_failed"} or not object_key:
        raise RagDocumentConflictError("历史文件没有可下载的原始文件")
    stored = build_rag_object_storage().get_object(object_key)
    return RagDocumentAssetDto(
        document_id=str(document.get("document_id") or document_id),
        file_name=str(document.get("original_filename") or "document"),
        content_type=_resolve_content_type(document, stored.content_type),
        content=stored.content,
        is_text=False,
    )


def delete_rag_document(document_id: str) -> RagDocumentDeleteResponseDto:
    repository = build_rag_document_repository()
    object_storage = build_rag_object_storage()
    document = _get_document(repository, document_id)
    operation = _build_delete_operation(repository, document, [document_id])
    deleted_count = _execute_delete_operation(repository, object_storage, operation)

    return RagDocumentDeleteResponseDto(
        document_id=document_id,
        deleted_chunk_count=deleted_count,
        status="deleted",
    )


def batch_delete_rag_documents(
    request: RagDocumentBatchDeleteRequestDto,
) -> RagDocumentBatchDeleteResponseDto:
    """按逻辑文件批量删除资产，并返回每个请求 ID 的可展示结果。

    入口意图：管理员需要一次清理当前页的多个知识库文件，同时保证文件主记录、
    pgvector Chunk 和 MinIO 原文件不会因为某一个文件失败而被错误地显示为全部成功。
    关键步骤：先规范化并去重 ID，再读取文档；新版文档按 document_id 建立操作，
    legacy 文档按 source_id + original_filename 合并；每个操作先标记 deleting，
    再删除向量和对象，最后删除主记录。
    关键分支：同一 legacy 分组只执行一次 Chunk 删除；未知 ID 返回 not_found；
    任意外部清理失败只影响当前逻辑操作，并把已标记记录置为 cleanup_failed。
    输出与副作用：返回成功/失败汇总和逐项结果；成功项从数据库及对象存储移除，
    失败项被排除在 RAG 检索之外，后续可通过单删或再次批量删除重试。
    """
    repository = build_rag_document_repository()
    object_storage = build_rag_object_storage()
    document_ids = _normalize_batch_document_ids(request.document_ids)
    if not document_ids:
        raise RagDocumentConflictError("请至少选择一个知识库文件")
    if len(document_ids) > 100:
        raise RagDocumentConflictError("批量删除一次最多支持 100 个文件")

    results_by_id: dict[str, RagDocumentBatchDeleteItemDto] = {}
    operations_by_key: dict[tuple[object, ...], _RagDocumentDeleteOperation] = {}
    operations: list[_RagDocumentDeleteOperation] = []

    # API 层只负责参数校验；这里负责把请求 ID 组织成应用层的逻辑删除操作。
    # legacy 记录的向量按来源和文件名共用，因此必须在进入删除阶段前完成分组去重。
    for document_id in document_ids:
        document = repository.get_document(document_id)
        if document is None:
            results_by_id[document_id] = _batch_delete_item(
                document_id=document_id,
                status="not_found",
                message="知识库文件不存在",
            )
            continue

        status = str(document.get("status") or "")
        if _is_legacy_document(document):
            group_key = (
                "legacy",
                document.get("source_id"),
                str(document.get("original_filename") or ""),
            )
            operation = operations_by_key.get(group_key)
            if operation is None:
                group_documents = repository.list_legacy_document_group(
                    document.get("source_id"),
                    str(document.get("original_filename") or ""),
                )
                operation = _RagDocumentDeleteOperation(
                    documents=group_documents or [document],
                    status="legacy",
                )
                operations_by_key[group_key] = operation
                operations.append(operation)
        else:
            group_key = ("document", document_id)
            operation = operations_by_key.get(group_key)
            if operation is None:
                operation = _RagDocumentDeleteOperation(documents=[document], status=status)
                operations_by_key[group_key] = operation
                operations.append(operation)
        operation.requested_document_ids.append(document_id)

    deleted_chunk_count = 0
    for operation in operations:
        try:
            operation_deleted_count = _execute_delete_operation(repository, object_storage, operation)
            deleted_chunk_count += operation_deleted_count
            affected_document_ids = _document_ids(operation.documents)
            for index, document_id in enumerate(operation.requested_document_ids):
                results_by_id[document_id] = _batch_delete_item(
                    document_id=document_id,
                    status="deleted",
                    deleted_chunk_count=operation_deleted_count if index == 0 else 0,
                    message=None if index == 0 else "同源历史分组已一并删除",
                    affected_document_ids=affected_document_ids,
                )
        except Exception as error:
            status, message = _batch_delete_failure(error)
            affected_document_ids = _document_ids(operation.documents)
            for document_id in operation.requested_document_ids:
                results_by_id[document_id] = _batch_delete_item(
                    document_id=document_id,
                    status=status,
                    message=message,
                    affected_document_ids=affected_document_ids,
                )

    items = [
        results_by_id.get(
            document_id,
            _batch_delete_item(
                document_id=document_id,
                status="cleanup_failed",
                message="文件清理失败，可稍后重试",
            ),
        )
        for document_id in document_ids
    ]
    succeeded_count = sum(1 for item in items if item.status == "deleted")
    return RagDocumentBatchDeleteResponseDto(
        requested_count=len(document_ids),
        succeeded_count=succeeded_count,
        failed_count=len(document_ids) - succeeded_count,
        deleted_chunk_count=deleted_chunk_count,
        items=items,
    )


def _build_delete_operation(
    repository: Any,
    document: dict[str, Any],
    requested_document_ids: list[str],
) -> _RagDocumentDeleteOperation:
    status = str(document.get("status") or "")
    if not _is_legacy_document(document):
        return _RagDocumentDeleteOperation(
            requested_document_ids=list(requested_document_ids),
            documents=[document],
            status=status,
        )
    group_documents = repository.list_legacy_document_group(
        document.get("source_id"),
        str(document.get("original_filename") or ""),
    )
    return _RagDocumentDeleteOperation(
        requested_document_ids=list(requested_document_ids),
        documents=group_documents or [document],
        status="legacy",
    )


def _execute_delete_operation(
    repository: Any,
    object_storage: Any,
    operation: _RagDocumentDeleteOperation,
) -> int:
    """执行一个新版文件或一个 legacy 同源分组的完整清理。"""
    marked_documents: list[dict[str, Any]] = []
    try:
        # 先把所有目标置为 deleting，使清理期间的 Chunk 立即不再参与 RAG。
        for document in operation.documents:
            document_id = str(document.get("document_id") or "").strip()
            deleting = repository.mark_deleting(document_id)
            if deleting is None:
                raise RagDocumentNotFoundError("知识库文件不存在或状态不允许删除")
            # mark_deleting 返回新状态 deleting；恢复旧业务状态，供 legacy 分支按同源条件删向量。
            deleting["status"] = operation.status
            marked_documents.append(deleting)

        # 在文档仍保留关联时记录图片资源，后面据此判断对象是否被其他 Markdown 共用。
        # 如果对象删除失败，关联不会提前移除，下一次重试仍能找到同一资源完成补偿清理。
        marked_document_ids = _document_ids(marked_documents)
        document_assets = repository.list_document_assets(marked_document_ids)
        asset_records_by_id = {
            str(asset.get("asset_id") or "").strip(): asset
            for asset in document_assets
            if str(asset.get("asset_id") or "").strip()
        }
        # 先在数据库事务内冻结无其他引用的资源，阻止并发关联；目标关联仍保留到
        # MinIO 删除成功后再移除，保证清理失败时下次重试还能定位对象键。
        orphan_asset_records = repository.prepare_document_assets_for_cleanup(marked_document_ids)

        # 仓储层在一个 PostgreSQL 事务中删除 Chunk，并按整个逻辑分组处理计数。
        # legacy 可能有多个历史主记录，必须把整组 ID 一起传入，保证失败重试仍然幂等。
        expected_chunk_count = sum(
            max(0, int(document.get("chunk_count") or 0)) for document in marked_documents
        )
        delete_target = {**marked_documents[0], "chunk_count": expected_chunk_count}
        if operation.status == "legacy":
            delete_target["legacy_document_ids"] = _document_ids(marked_documents)
        deleted_count = repository.delete_document_chunks(delete_target)

        # MinIO 不参加 PostgreSQL 事务，因此对象清理失败时保留 cleanup_failed 记录进行补偿。
        object_keys = {
            str(document.get("object_key") or "").strip()
            for document in marked_documents
            if str(document.get("object_key") or "").strip()
        }
        for object_key in object_keys:
            object_storage.delete_object(object_key)

        # 共享图片只删除当前文档的关联，不触碰仍被其他 Markdown 使用的 MinIO 对象。
        # 非共享图片删除失败会保留关联和 cleanup_failed 资源状态，等待管理员重试。
        for asset in orphan_asset_records:
            asset_id = str(asset.get("asset_id") or "").strip()
            asset_key = str(asset.get("object_key") or "").strip()
            if not asset_id:
                raise ObjectStorageError("图片资源记录缺少资源 ID")
            if not asset_key:
                raise ObjectStorageError("图片资源记录缺少对象键")
            try:
                object_storage.delete_object(asset_key)
            except Exception:
                try:
                    repository.mark_asset_cleanup_failed(asset_id)
                except Exception:
                    pass
                raise

        pending_asset_records = repository.finalize_document_assets(
            marked_document_ids,
            list(asset_records_by_id),
        )
        # 共享资源可能在终结检查前刚好失去最后一个引用；此时先冻结并返回，
        # 删除对象后再次终结，避免把对象存储清理遗漏成孤儿对象。
        if pending_asset_records:
            for asset in pending_asset_records:
                asset_id = str(asset.get("asset_id") or "").strip()
                asset_key = str(asset.get("object_key") or "").strip()
                if not asset_id:
                    raise ObjectStorageError("图片资源记录缺少资源 ID")
                if not asset_key:
                    raise ObjectStorageError("图片资源记录缺少对象键")
                object_storage.delete_object(asset_key)
            repository.finalize_document_assets(
                marked_document_ids,
                [str(asset.get("asset_id") or "").strip() for asset in pending_asset_records],
            )

        # 只有向量和对象都清理成功后，才删除文件主记录；legacy 分组逐条清理历史主记录。
        for document in marked_documents:
            repository.finalize_document_deletion(str(document.get("document_id") or ""))
        return deleted_count
    except Exception:
        for document in marked_documents:
            repository.mark_cleanup_failed(str(document.get("document_id") or ""))
        raise


def _normalize_batch_document_ids(document_ids: list[str]) -> list[str]:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for document_id in document_ids:
        safe_document_id = str(document_id or "").strip()
        if safe_document_id and safe_document_id not in seen:
            normalized_ids.append(safe_document_id)
            seen.add(safe_document_id)
    return normalized_ids


def _document_ids(documents: list[dict[str, Any]]) -> list[str]:
    return [
        document_id
        for document in documents
        if (document_id := str(document.get("document_id") or "").strip())
    ]


def _batch_delete_item(
    *,
    document_id: str,
    status: str,
    deleted_chunk_count: int = 0,
    message: str | None = None,
    affected_document_ids: list[str] | None = None,
) -> RagDocumentBatchDeleteItemDto:
    return RagDocumentBatchDeleteItemDto(
        document_id=document_id,
        deleted_chunk_count=max(0, int(deleted_chunk_count)),
        status=status,
        message=message,
        affected_document_ids=list(affected_document_ids or []),
    )


def _batch_delete_failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, RagDocumentNotFoundError):
        return "not_found", "知识库文件不存在"
    if isinstance(error, ObjectStorageError):
        return "cleanup_failed", "原文件清理失败，可稍后重试"
    if isinstance(error, VectorStoreError):
        return "cleanup_failed", "向量清理失败，可稍后重试"
    return "cleanup_failed", "文件清理失败，可稍后重试"


def _get_document(repository, document_id: str) -> dict:
    safe_document_id = (document_id or "").strip()
    document = repository.get_document(safe_document_id)
    if document is None:
        raise RagDocumentNotFoundError("知识库文件不存在")
    return document


def _document_item_from_record(document: dict) -> RagDocumentItemDto:
    file_name = str(document.get("original_filename") or "document")
    status = str(document.get("status") or "processing")
    image_enrichment_status = str(document.get("image_enrichment_status") or "not_applicable")
    image_retry_available = (
        bool(document.get("image_enrichment_retryable"))
        and status == "ready"
        and not _is_legacy_document(document)
        and image_enrichment_status in {"failed", "partial_failed"}
    )
    return RagDocumentItemDto(
        archived=bool(document.get("archived")),
        archived_at=_format_created_at(document["archived_at"]) if document.get("archived_at") else None,
        scope_kind=("unclassified" if str(document.get("scope_kind") or "unclassified") == "general" else str(document.get("scope_kind") or "unclassified")),
        project_id=document.get("project_id"), project_name=document.get("project_name"),
        knowledge_base_id=document.get("project_id"), knowledge_base_name=document.get("project_name"),
        document_id=str(document.get("document_id") or ""),
        file_name=file_name,
        content_type=_resolve_content_type(document, "application/octet-stream"),
        file_type=_document_file_type(document),
        source_type=str(document.get("source_type") or "document"),
        ingest_source=str(document.get("ingest_source") or "text_document"),
        file_size_bytes=max(0, int(document.get("file_size_bytes") or 0)),
        status=status,
        chunk_count=max(0, int(document.get("chunk_count") or 0)),
        inserted_count=max(0, int(document.get("inserted_count") or 0)),
        embedding_model=str(document.get("embedding_model") or ""),
        embedding_dimensions=max(0, int(document.get("embedding_dimensions") or 0)),
        created_at=_format_created_at(document.get("created_at")),
        preview_type=_preview_type(document),
        download_available=status not in {"legacy", "processing", "deleting", "cleanup_failed"}
        and bool(str(document.get("object_key") or "").strip()),
        legacy_warning=(
            "历史向量按来源文件名分组，删除会影响同源旧 Chunk；原始文件不可下载。"
            if _is_legacy_document(document)
            else None
        ),
        referenced_image_count=max(0, int(document.get("referenced_image_count") or 0)),
        matched_image_count=max(0, int(document.get("matched_image_count") or 0)),
        missing_image_count=max(0, int(document.get("missing_image_count") or 0)),
        image_candidate_count=max(0, int(document.get("image_candidate_count") or 0)),
        image_analyzed_count=max(0, int(document.get("image_analyzed_count") or 0)),
        image_indexed_count=max(0, int(document.get("image_indexed_count") or 0)),
        image_skipped_count=max(0, int(document.get("image_skipped_count") or 0)),
        image_failed_count=max(0, int(document.get("image_failed_count") or 0)),
        image_chunk_count=max(0, int(document.get("image_chunk_count") or 0)),
        image_enrichment_status=image_enrichment_status,
        image_enrichment_message=(
            str(document.get("image_enrichment_message"))
            if document.get("image_enrichment_message") is not None
            else None
        ),
        image_retry_available=image_retry_available,
    )


def _normalize_file_type(value: str | None) -> str:
    safe_value = (value or "all").strip().lower()
    if safe_value not in _RAG_DOCUMENT_FILE_TYPES:
        raise ValueError("不支持的文件类型筛选")
    return safe_value


def _is_legacy_document(document: dict[str, Any]) -> bool:
    """识别 legacy 记录及其清理失败后的可重试状态。"""
    status = str(document.get("status") or "")
    if status == "legacy":
        return True
    if status != "cleanup_failed":
        return False
    return not str(document.get("object_key") or "").strip() and not str(document.get("sha256") or "").strip()


def _document_file_type(document: dict) -> str:
    extension = Path(str(document.get("original_filename") or "")).suffix.lower()
    if extension in _FILE_TYPE_BY_EXTENSION:
        return _FILE_TYPE_BY_EXTENSION[extension]
    content_type = str(document.get("original_content_type") or "").split(";", 1)[0].strip().lower()
    return _FILE_TYPE_BY_MIME.get(content_type, "other")


def _preview_type(document: dict) -> str:
    if str(document.get("status") or "") == "legacy":
        return "text"
    content_type = str(document.get("original_content_type") or "").lower()
    extension = Path(str(document.get("original_filename") or "")).suffix.lower()
    if content_type == "application/pdf" or content_type.startswith("image/") or extension in _BINARY_PREVIEW_EXTENSIONS:
        return "binary"
    return "text"


def _resolve_content_type(document: dict, stored_content_type: str) -> str:
    file_name = str(document.get("original_filename") or "")
    extension_content_type = _CONTENT_TYPE_BY_EXTENSION.get(Path(file_name).suffix.lower())
    if extension_content_type:
        return extension_content_type
    content_type = str(document.get("original_content_type") or "").split(";", 1)[0].strip().lower()
    if content_type in _FILE_TYPE_BY_MIME:
        return content_type
    stored_type = str(stored_content_type or "").split(";", 1)[0].strip().lower()
    if stored_type in _FILE_TYPE_BY_MIME:
        return stored_type
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def _format_created_at(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")
