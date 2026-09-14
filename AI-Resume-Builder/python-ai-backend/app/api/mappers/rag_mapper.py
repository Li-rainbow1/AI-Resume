from typing import BinaryIO

from app.api.schemas.rag import (
    RagIngestRequest,
    RagIngestResponse,
    RagQueryRequest,
    RagQueryResponse,
    RagSource,
    RagUploadFileResult,
    RagUploadResponse,
    RagDocumentBatchDeleteItem,
    RagDocumentBatchDeleteRequest,
    RagDocumentBatchDeleteResponse,
    RagDocumentDeleteResponse,
    RagDocumentImageEnrichmentResponse,
    RagDocumentItem,
    RagDocumentListResponse,
)
from app.application.dto.rag_dto import (
    RagDocumentInputDto,
    RagIngestRequestDto,
    RagIngestResponseDto,
    RagQueryRequestDto,
    RagQueryResponseDto,
    RagUploadAssetDto,
    RagMarkdownBundleAssetDto,
    RagMarkdownUploadRequestDto,
    RagUploadResponseDto,
    RagDocumentBatchDeleteRequestDto,
    RagDocumentBatchDeleteResponseDto,
    RagDocumentDeleteResponseDto,
    RagDocumentItemDto,
    RagDocumentListResponseDto,
    RagDocumentImageEnrichmentResponseDto,
)


def rag_query_request_to_dto(request: RagQueryRequest) -> RagQueryRequestDto:
    # API 层 camelCase / 校验模型 转成应用层 snake_case DTO。
    # projectIds 只为旧请求保持可解析，查询范围由问题中的知识库名称自动确定。
    return RagQueryRequestDto(query=request.query, top_k=request.topK, project_ids=None)


def rag_query_response_from_dto(response: RagQueryResponseDto) -> RagQueryResponse:
    return RagQueryResponse(
        projectIds=response.project_ids,
        knowledgeBaseIds=response.knowledge_base_ids or response.project_ids,
        knowledgeBaseNames=response.knowledge_base_names,
        answer=response.answer,
        sources=[
            RagSource(sourceId=item.source_id, content=item.content, metadata=item.metadata)
            for item in response.sources
        ],
    )


def rag_document_image_enrichment_response_from_dto(
    response: RagDocumentImageEnrichmentResponseDto,
) -> RagDocumentImageEnrichmentResponse:
    return RagDocumentImageEnrichmentResponse(
        documentId=response.document_id,
        candidateCount=response.candidate_count,
        analyzedCount=response.analyzed_count,
        indexedCount=response.indexed_count,
        skippedCount=response.skipped_count,
        failedCount=response.failed_count,
        chunkCount=response.chunk_count,
        status=response.status,
        message=response.message,
        retryAvailable=response.retry_available,
        taskId=response.task_id,
        queuedAt=response.queued_at,
        currentPosition=response.current_position,
    )


def rag_ingest_request_to_dto(request: RagIngestRequest) -> RagIngestRequestDto:
    return RagIngestRequestDto(
        documents=[
            RagDocumentInputDto(
                source_id=document.sourceId,
                content=document.content,
                metadata=document.metadata,
            )
            for document in request.documents
        ]
    )


def rag_ingest_response_from_dto(response: RagIngestResponseDto) -> RagIngestResponse:
    return RagIngestResponse(inserted=response.inserted)


def rag_upload_assets_to_dto(files: list[tuple[str, str, BinaryIO]], scope_kind: str = "unclassified", project_id: str | None = None) -> list[RagUploadAssetDto]:
    # Route 层只保留元数据和可读取的文件流，避免在进入 use case 前整批读满内存。
    return [
        RagUploadAssetDto(file_name=file_name, content_type=content_type, file_stream=file_stream, metadata={"scopeKind": scope_kind, "projectId": project_id, "knowledgeBaseId": project_id})
        for file_name, content_type, file_stream in files
    ]


def rag_markdown_bundle_to_dto(
    documents: list[tuple[str, str, BinaryIO, str]],
    attachments: list[tuple[str, str, BinaryIO, str]],
    scope_kind: str = "unclassified", project_id: str | None = None,
) -> RagMarkdownUploadRequestDto:
    """把路由层分离出的正文、附件文件流转换成应用层输入。"""
    return RagMarkdownUploadRequestDto(
        documents=[
            RagMarkdownBundleAssetDto(
                asset=RagUploadAssetDto(file_name=file_name, content_type=content_type, file_stream=file_stream, metadata={"scopeKind": scope_kind, "projectId": project_id, "knowledgeBaseId": project_id}),
                relative_path=relative_path,
            )
            for file_name, content_type, file_stream, relative_path in documents
        ],
        attachments=[
            RagMarkdownBundleAssetDto(
                asset=RagUploadAssetDto(file_name=file_name, content_type=content_type, file_stream=file_stream, metadata={"scopeKind": scope_kind, "projectId": project_id, "knowledgeBaseId": project_id}),
                relative_path=relative_path,
            )
            for file_name, content_type, file_stream, relative_path in attachments
        ],
    )


def rag_upload_response_from_dto(response: RagUploadResponseDto) -> RagUploadResponse:
    # 应用层统一使用 snake_case，返回给前端前再映射回现有前端契约。
    return RagUploadResponse(
        totalFiles=response.total_files,
        succeededFiles=response.succeeded_files,
        failedFiles=response.failed_files,
        inserted=response.inserted,
        files=[
            RagUploadFileResult(
                fileName=item.file_name,
                contentType=item.content_type,
                sourceType=item.source_type,
                ingestSource=item.ingest_source,
                chunkCount=item.chunk_count,
                insertedCount=item.inserted_count,
                status=item.status,
                errorMessage=item.error_message,
                documentId=item.document_id,
                referencedImageCount=item.referenced_image_count,
                matchedImageCount=item.matched_image_count,
                missingImageCount=item.missing_image_count,
                reusedImageCount=item.reused_image_count,
                imageCandidateCount=item.image_candidate_count,
                imageAnalyzedCount=item.image_analyzed_count,
                imageIndexedCount=item.image_indexed_count,
                imageSkippedCount=item.image_skipped_count,
                imageFailedCount=item.image_failed_count,
                imageChunkCount=item.image_chunk_count,
                imageEnrichmentStatus=item.image_enrichment_status,
                imageEnrichmentMessage=item.image_enrichment_message,
                imageRetryAvailable=item.image_retry_available,
            )
            for item in response.files
        ],
    )


def rag_document_list_response_from_dto(response: RagDocumentListResponseDto) -> RagDocumentListResponse:
    return RagDocumentListResponse(
        items=[rag_document_item_from_dto(item) for item in response.items],
        total=response.total,
        page=response.page,
        pageSize=response.page_size,
    )


def rag_document_item_from_dto(item: RagDocumentItemDto) -> RagDocumentItem:
    return RagDocumentItem(
        archived=item.archived, archivedAt=item.archived_at,
        scopeKind=item.scope_kind, projectId=item.project_id, projectName=item.project_name,
        knowledgeBaseId=item.knowledge_base_id or item.project_id,
        knowledgeBaseName=item.knowledge_base_name or item.project_name,
        documentId=item.document_id,
        fileName=item.file_name,
        contentType=item.content_type,
        fileType=item.file_type,
        sourceType=item.source_type,
        ingestSource=item.ingest_source,
        fileSizeBytes=item.file_size_bytes,
        status=item.status,
        chunkCount=item.chunk_count,
        insertedCount=item.inserted_count,
        embeddingModel=item.embedding_model,
        embeddingDimensions=item.embedding_dimensions,
        createdAt=item.created_at,
        previewType=item.preview_type,
        downloadAvailable=item.download_available,
        legacyWarning=item.legacy_warning,
        referencedImageCount=item.referenced_image_count,
        matchedImageCount=item.matched_image_count,
        missingImageCount=item.missing_image_count,
        imageCandidateCount=item.image_candidate_count,
        imageAnalyzedCount=item.image_analyzed_count,
        imageIndexedCount=item.image_indexed_count,
        imageSkippedCount=item.image_skipped_count,
        imageFailedCount=item.image_failed_count,
        imageChunkCount=item.image_chunk_count,
        imageEnrichmentStatus=item.image_enrichment_status,
        imageEnrichmentMessage=item.image_enrichment_message,
        imageRetryAvailable=item.image_retry_available,
    )


def rag_document_delete_response_from_dto(response: RagDocumentDeleteResponseDto) -> RagDocumentDeleteResponse:
    return RagDocumentDeleteResponse(
        documentId=response.document_id,
        deletedChunkCount=response.deleted_chunk_count,
        status=response.status,
    )


def rag_document_batch_delete_request_to_dto(
    request: RagDocumentBatchDeleteRequest,
) -> RagDocumentBatchDeleteRequestDto:
    return RagDocumentBatchDeleteRequestDto(document_ids=list(request.documentIds))


def rag_document_batch_delete_response_from_dto(
    response: RagDocumentBatchDeleteResponseDto,
) -> RagDocumentBatchDeleteResponse:
    return RagDocumentBatchDeleteResponse(
        requestedCount=response.requested_count,
        succeededCount=response.succeeded_count,
        failedCount=response.failed_count,
        deletedChunkCount=response.deleted_chunk_count,
        items=[
            RagDocumentBatchDeleteItem(
                documentId=item.document_id,
                deletedChunkCount=item.deleted_chunk_count,
                status=item.status,
                message=item.message,
                affectedDocumentIds=item.affected_document_ids,
            )
            for item in response.items
        ],
    )
