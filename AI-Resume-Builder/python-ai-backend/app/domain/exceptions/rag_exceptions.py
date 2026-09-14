class RagIngestError(Exception):
    # 统一上传 / 入库链路的基类异常。
    # API 层会根据子类把它们映射到不同 HTTP 状态码。
    """Base error for RAG ingestion failures."""


class UnsupportedFileTypeError(RagIngestError):
    """Raised when upload contains an unsupported file type."""


class FileTooLargeError(RagIngestError):
    """Raised when upload exceeds the configured size limit."""


class FileParseError(RagIngestError):
    """Raised when a file cannot be parsed into text."""


class ImageOcrError(RagIngestError):
    """Raised when OCR provider cannot extract markdown from an image."""


class EmbeddingError(RagIngestError):
    """Raised when embedding provider cannot vectorize extracted chunks."""


class EmbeddingChunkRetryableError(EmbeddingError):
    """单个 Embedding Chunk 遇到可重试的网络异常，并携带其输入序号。"""

    def __init__(self, item_index: int) -> None:
        self.item_index = max(1, int(item_index))
        super().__init__("单个 Embedding Chunk 请求失败，可尝试语义切分后重试")


class VectorStoreError(RagIngestError):
    """Raised when pgvector storage or retrieval fails."""


class ObjectStorageError(RagIngestError):
    """原始知识库文件对象存储不可用。"""


class RagDocumentConflictError(RagIngestError):
    """知识库文件内容已经存在。"""


class RagArchivedDocumentConflictError(RagDocumentConflictError):
    """相同内容文档已归档，需要管理员主动恢复。"""


class RagDocumentScopeConflictError(RagDocumentConflictError):
    """相同内容已存在于另一归属。"""


class RagKnowledgeBaseNotEmptyError(RagDocumentConflictError):
    """知识库仍有关联文档，不能删除。"""


class RagDocumentNotFoundError(RagIngestError):
    """知识库文件记录不存在。"""


class RagUploadCancelledError(RagIngestError):
    """客户端取消上传后停止继续处理。"""


class ImageEnrichmentLeaseLostError(RagIngestError):
    """图片增强任务租约已被其他调用方接管，当前结果必须丢弃。"""


_PUBLIC_RAG_ERROR_MESSAGES: tuple[tuple[type[BaseException], str], ...] = (
    (RagArchivedDocumentConflictError, "相同内容的文档已归档，请在知识库中恢复已有文档"),
    (RagDocumentScopeConflictError, "相同内容已存在于其他归属，请编辑已有文档的归属"),
    (RagKnowledgeBaseNotEmptyError, "知识库仍有关联文档，请先移动或删除文档"),
    (RagDocumentConflictError, "相同内容的知识库文件已经存在，请勿重复上传"),
    (RagUploadCancelledError, "上传已取消"),
    (UnsupportedFileTypeError, "暂不支持该文件类型"),
    (FileTooLargeError, "文件超过大小限制"),
    (FileParseError, "文件解析失败，请检查文件内容或格式"),
    (ImageOcrError, "图片文字识别失败，请稍后重试"),
    (EmbeddingError, "Embedding 服务暂时不可用，请稍后重试"),
    (ObjectStorageError, "文件存储服务暂时不可用，请稍后重试"),
    (VectorStoreError, "向量库服务暂时不可用，请稍后重试"),
    (RagIngestError, "知识库处理失败，请稍后重试"),
)
_REDACTED_RAG_LOG_KEYS = frozenset({"error", "error_message", "exception", "exception_message"})


def public_rag_error_message(error: BaseException, *, fallback: str = "知识库上传失败，请稍后重试") -> str:
    """把 RAG 异常转换为稳定的对外提示，避免泄露供应商或数据库原文。"""
    for error_type, message in _PUBLIC_RAG_ERROR_MESSAGES:
        if isinstance(error, error_type):
            return message
    return fallback


def safe_rag_log_value(key: str, value: object) -> object:
    """阻止日志辅助函数意外打印异常对象或异常文本。"""
    if key in _REDACTED_RAG_LOG_KEYS:
        return "[已脱敏]"
    return value
