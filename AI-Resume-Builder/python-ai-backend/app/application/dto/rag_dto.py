from dataclasses import dataclass, field
from typing import Any, BinaryIO


# RAG 查询入参 DTO。
@dataclass(slots=True)
class RagQueryRequestDto:
    query: str
    top_k: int | None = None
    project_ids: list[str] | None = None


# RAG 查询结果里的单个来源。
@dataclass(slots=True)
class RagSourceDto:
    source_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


# RAG 查询响应 DTO。
@dataclass(slots=True)
class RagQueryResponseDto:
    answer: str
    sources: list[RagSourceDto] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)
    knowledge_base_ids: list[str] = field(default_factory=list)
    knowledge_base_names: list[str] = field(default_factory=list)


# 旧的纯文档入库 DTO。
@dataclass(slots=True)
class RagDocumentInputDto:
    content: str
    source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RagIngestRequestDto:
    documents: list[RagDocumentInputDto] = field(default_factory=list)


@dataclass(slots=True)
class RagIngestResponseDto:
    inserted: int


# 统一上传接口的单个文件输入。
@dataclass(slots=True)
class RagUploadAssetDto:
    file_name: str
    content_type: str
    file_bytes: bytes | None = None
    file_stream: BinaryIO | None = None
    source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# 统一上传接口的单个文件处理结果。
@dataclass(slots=True)
class RagUploadFileResultDto:
    file_name: str
    content_type: str
    source_type: str
    ingest_source: str
    chunk_count: int
    inserted_count: int
    status: str
    error_message: str | None = None
    document_id: str | None = None
    referenced_image_count: int = 0
    matched_image_count: int = 0
    missing_image_count: int = 0
    reused_image_count: int = 0
    image_candidate_count: int = 0
    image_analyzed_count: int = 0
    image_indexed_count: int = 0
    image_skipped_count: int = 0
    image_failed_count: int = 0
    image_chunk_count: int = 0
    image_enrichment_status: str = "not_applicable"
    image_enrichment_message: str | None = None
    image_retry_available: bool = False


@dataclass(slots=True)
class RagUploadResponseDto:
    total_files: int
    succeeded_files: int
    failed_files: int
    inserted: int
    files: list[RagUploadFileResultDto] = field(default_factory=list)


@dataclass(slots=True)
class RagMarkdownBundleAssetDto:
    """Markdown 正文或附件文件及其浏览器提供的相对路径。"""

    asset: RagUploadAssetDto
    relative_path: str


@dataclass(slots=True)
class RagMarkdownUploadRequestDto:
    """Markdown 附件上传用例的内部输入，不把 multipart 细节带入 application。"""

    documents: list[RagMarkdownBundleAssetDto] = field(default_factory=list)
    attachments: list[RagMarkdownBundleAssetDto] = field(default_factory=list)


@dataclass(slots=True)
class RagDocumentItemDto:
    document_id: str
    file_name: str
    content_type: str
    file_type: str
    source_type: str
    ingest_source: str
    file_size_bytes: int
    status: str
    chunk_count: int
    inserted_count: int
    embedding_model: str
    embedding_dimensions: int
    created_at: str
    preview_type: str
    download_available: bool
    archived: bool = False
    archived_at: str | None = None
    scope_kind: str = "unclassified"
    project_id: str | None = None
    project_name: str | None = None
    knowledge_base_id: str | None = None
    knowledge_base_name: str | None = None
    legacy_warning: str | None = None
    referenced_image_count: int = 0
    matched_image_count: int = 0
    missing_image_count: int = 0
    image_candidate_count: int = 0
    image_analyzed_count: int = 0
    image_indexed_count: int = 0
    image_skipped_count: int = 0
    image_failed_count: int = 0
    image_chunk_count: int = 0
    image_enrichment_status: str = "not_applicable"
    image_enrichment_message: str | None = None
    image_retry_available: bool = False


@dataclass(slots=True)
class RagDocumentListResponseDto:
    items: list[RagDocumentItemDto]
    total: int
    page: int
    page_size: int


@dataclass(slots=True)
class RagDocumentAssetDto:
    document_id: str
    file_name: str
    content_type: str
    content: bytes
    is_text: bool


@dataclass(slots=True)
class RagDocumentDeleteResponseDto:
    document_id: str
    deleted_chunk_count: int
    status: str


@dataclass(slots=True)
class RagDocumentBatchDeleteRequestDto:
    document_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RagDocumentBatchDeleteItemDto:
    document_id: str
    deleted_chunk_count: int
    status: str
    message: str | None = None
    affected_document_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RagDocumentBatchDeleteResponseDto:
    requested_count: int
    succeeded_count: int
    failed_count: int
    deleted_chunk_count: int
    items: list[RagDocumentBatchDeleteItemDto] = field(default_factory=list)


@dataclass(slots=True)
class RagDocumentImageEnrichmentResponseDto:
    document_id: str
    candidate_count: int
    analyzed_count: int
    indexed_count: int
    skipped_count: int
    failed_count: int
    chunk_count: int
    status: str
    message: str | None = None
    retry_available: bool = False
    task_id: str | None = None
    queued_at: str | None = None
    current_position: int = 0
