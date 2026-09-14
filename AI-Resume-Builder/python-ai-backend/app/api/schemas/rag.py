from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# 查询接口 request schema。
class RagQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    topK: int | None = Field(default=None, ge=1, le=5)
    projectIds: list[str] | None = Field(default=None, max_length=100)


# 查询结果里的来源对象。
class RagSource(BaseModel):
    sourceId: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagQueryResponse(BaseModel):
    answer: str
    sources: list[RagSource]
    projectIds: list[str] = Field(default_factory=list)
    knowledgeBaseIds: list[str] = Field(default_factory=list)
    knowledgeBaseNames: list[str] = Field(default_factory=list)


class RagDocumentInput(BaseModel):
    sourceId: str | None = None
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagIngestRequest(BaseModel):
    documents: list[RagDocumentInput] = Field(min_length=1)


class RagIngestResponse(BaseModel):
    inserted: int


# 统一上传接口里的单文件结果。
class RagUploadFileResult(BaseModel):
    fileName: str
    contentType: str
    sourceType: str
    ingestSource: str
    chunkCount: int
    insertedCount: int
    status: str
    errorMessage: str | None = None
    documentId: str | None = None
    referencedImageCount: int = 0
    matchedImageCount: int = 0
    missingImageCount: int = 0
    reusedImageCount: int = 0
    imageCandidateCount: int = 0
    imageAnalyzedCount: int = 0
    imageIndexedCount: int = 0
    imageSkippedCount: int = 0
    imageFailedCount: int = 0
    imageChunkCount: int = 0
    imageEnrichmentStatus: str = "not_applicable"
    imageEnrichmentMessage: str | None = None
    imageRetryAvailable: bool = False


class RagUploadResponse(BaseModel):
    totalFiles: int
    succeededFiles: int
    failedFiles: int
    inserted: int
    files: list[RagUploadFileResult]


class RagDocumentItem(BaseModel):
    documentId: str
    fileName: str
    contentType: str
    fileType: Literal["pdf", "word", "txt", "md", "image", "other"]
    sourceType: str
    ingestSource: str
    fileSizeBytes: int
    status: str
    chunkCount: int
    insertedCount: int
    embeddingModel: str
    embeddingDimensions: int
    createdAt: str
    previewType: str
    downloadAvailable: bool
    archived: bool = False
    archivedAt: str | None = None
    scopeKind: Literal["project", "unclassified"] = "unclassified"
    projectId: str | None = None
    projectName: str | None = None
    knowledgeBaseId: str | None = None
    knowledgeBaseName: str | None = None
    legacyWarning: str | None = None
    referencedImageCount: int = 0
    matchedImageCount: int = 0
    missingImageCount: int = 0
    imageCandidateCount: int = 0
    imageAnalyzedCount: int = 0
    imageIndexedCount: int = 0
    imageSkippedCount: int = 0
    imageFailedCount: int = 0
    imageChunkCount: int = 0
    imageEnrichmentStatus: str = "not_applicable"
    imageEnrichmentMessage: str | None = None
    imageRetryAvailable: bool = False


class RagDocumentListResponse(BaseModel):
    items: list[RagDocumentItem]
    total: int
    page: int
    pageSize: int


class RagDocumentDeleteResponse(BaseModel):
    documentId: str
    deletedChunkCount: int
    status: str


class RagDocumentBatchDeleteRequest(BaseModel):
    documentIds: list[str] = Field(min_length=1, max_length=100)

    @field_validator("documentIds")
    @classmethod
    def validate_document_ids(cls, value: list[str]) -> list[str]:
        if any(not document_id.strip() for document_id in value):
            raise ValueError("documentIds 不能包含空白值")
        return value


class RagDocumentBatchDeleteItem(BaseModel):
    documentId: str
    deletedChunkCount: int
    status: str
    message: str | None = None
    affectedDocumentIds: list[str] = Field(default_factory=list)


class RagDocumentBatchDeleteResponse(BaseModel):
    requestedCount: int
    succeededCount: int
    failedCount: int
    deletedChunkCount: int
    items: list[RagDocumentBatchDeleteItem]


class RagDocumentImageEnrichmentResponse(BaseModel):
    documentId: str
    candidateCount: int
    analyzedCount: int
    indexedCount: int
    skippedCount: int
    failedCount: int
    chunkCount: int
    status: str
    message: str | None = None
    retryAvailable: bool = False
    taskId: str | None = None
    queuedAt: str | None = None
    currentPosition: int = 0
