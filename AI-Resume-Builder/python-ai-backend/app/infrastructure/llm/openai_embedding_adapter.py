import re
import unicodedata

from app.domain.exceptions.rag_exceptions import (
    EmbeddingChunkRetryableError,
    EmbeddingError,
    safe_rag_log_value,
)

_LLAMA_INDEX_SUPPORTED_OPENAI_EMBEDDING_MODELS = frozenset(
    {
        "davinci",
        "curie",
        "babbage",
        "ada",
        "text-embedding-ada-002",
        "text-embedding-3-large",
        "text-embedding-3-small",
    }
)
_LLAMA_INDEX_FALLBACK_EMBEDDING_MODEL = "text-embedding-3-large"
# Embedding 请求的传输层重试次数：只覆盖连接失败、连接超时、限流与 5xx 等瞬时故障，
# 取较小值，在缓解上游网络抖动的同时避免上游真实故障时长时间挂起。
_EMBEDDING_TRANSPORT_MAX_RETRIES = 2
# 兼容百炼当前 20 行上限；正常请求保持 10 条，仅在上游拒绝或中断时才降级拆分。
_EMBEDDING_REQUEST_BATCH_SIZE = 10
# 上游以 4xx 明确拒绝请求时的状态码；这类错误可通过拆分定位并绕过被拒片段。
_EMBEDDING_UPSTREAM_REJECTION_STATUS_CODES = frozenset({400, 413, 422})
# 状态码之外，用错误码、错误类型与异常文案中的关键词兜底识别「上游拒绝」，
# 既覆盖长度超限，也覆盖 embedding 模型的敏感词 / 内容审核拦截。
_EMBEDDING_UPSTREAM_REJECTION_KEYWORDS = (
    "too large",
    "too many tokens",
    "maximum context length",
    "context_length_exceeded",
    "input too long",
    "token limit",
    "length limit",
    "payload",
    "batch size",
    "sensitive",
    "content filter",
    "content_filter",
    "moderation",
    "unsafe",
    "prohibited",
    "敏感",
    "审查",
    "违规",
)
# 请求发出前就失败的连接类异常：TCP 握手未完成，请求体根本没送达上游，
# 内容层面（含敏感词）不可能导致失败，拆分只会白耗请求，应直接快速失败。
_EMBEDDING_PRE_SEND_TRANSPORT_TYPES = frozenset(
    {
        "ConnectError",
        "ConnectTimeout",
        "PoolTimeout",
        "ConnectionRefusedError",
    }
)
_CJK_WHITESPACE_RE = re.compile(
    r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])\s+"
    r"(?=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])"
)


class OpenAIEmbeddingAdapter:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model_name = (model_name or "").strip() or "text-embedding-3-large"
        self.base_url = (base_url or "").strip() or None
        self.timeout_seconds = self._normalize_timeout(timeout_seconds)
        self.provider_name = "openai"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # 业务入口意图：把上游切好的 chunk 文本批量向量化，供知识库入库和检索链路复用。
        raw_texts = []
        safe_texts = []
        for text in texts:
            raw_text = (text or "").strip()
            normalized_text = self._normalize_embedding_text(raw_text)
            if normalized_text:
                raw_texts.append(raw_text)
                safe_texts.append(normalized_text)
        if not safe_texts:
            _log_embedding("跳过调用，输入为空")
            return []
        if not self.api_key:
            raise EmbeddingError("OPENAI_API_KEY 未配置，无法生成 Embedding")

        # 关键步骤：统一通过 llama_index 的 OpenAIEmbedding 适配器发起请求，
        # 让 application/domain 不直接绑定具体 SDK，同时在这里兜住第三方兼容网关差异。
        client = self._create_client()
        all_vectors: list[list[float]] = []
        total_batches = (len(safe_texts) + _EMBEDDING_REQUEST_BATCH_SIZE - 1) // _EMBEDDING_REQUEST_BATCH_SIZE
        for batch_index, start in enumerate(
            range(0, len(safe_texts), _EMBEDDING_REQUEST_BATCH_SIZE),
            start=1,
        ):
            batch_texts = safe_texts[start : start + _EMBEDDING_REQUEST_BATCH_SIZE]
            batch_raw_texts = raw_texts[start : start + _EMBEDDING_REQUEST_BATCH_SIZE]
            try:
                vectors = self._embed_batch_with_timeout_fallback(
                    client=client,
                    batch_texts=batch_texts,
                    raw_batch_texts=batch_raw_texts,
                    batch_index=str(batch_index),
                    item_start=start + 1,
                    total_batches=total_batches,
                )
            except Exception as exc:
                raise EmbeddingError("OpenAI Embedding 调用失败，请稍后重试") from exc

            # 关键分支：每批返回数量必须与该批输入数量一致，否则不能安全合并入库。
            if len(vectors) != len(batch_texts):
                raise EmbeddingError("Embedding 返回数量与输入数量不一致")
            all_vectors.extend(self._normalize_vector(vector) for vector in vectors)

        return all_vectors

    def _embed_batch_with_timeout_fallback(
        self,
        client,
        batch_texts: list[str],
        raw_batch_texts: list[str] | None,
        batch_index: str,
        item_start: int,
        total_batches: int,
    ) -> list[list[float]]:
        # 先按正常批次请求；若兼容网关对某一批无响应，再递归拆小，避免整份文档直接失败。
        _log_embedding(
            "提交 Embedding 批次",
            batch_index=batch_index,
            batch_size=len(batch_texts),
            item_start=item_start,
            total_batches=total_batches,
        )
        try:
            return client.get_text_embedding_batch(batch_texts)
        except Exception as exc:
            # 关键分支：只有「上游收到请求后拒绝或中断」才值得拆分。
            # 敏感词被 embedding 模型拦截时，上游通常表现为连接被断开或读取超时，
            # 因此这类传输层中断必须保留拆分，才能定位并绕过被拒片段；
            # 但连接从未建立（TCP 未握手、请求体未送达）与内容无关，拆分无效。
            should_split = self._should_split_batch(exc)
            if not should_split or len(batch_texts) <= 1:
                if len(batch_texts) == 1:
                    raw_text_length = len((raw_batch_texts or batch_texts)[0])
                    _log_embedding(
                        "最终失败 Chunk（仅记录元信息）",
                        item_start=item_start,
                        raw_chars=raw_text_length,
                        normalized_chars=len(batch_texts[0]),
                    )
                _log_embedding(
                    "Embedding 批次请求失败",
                    batch_index=batch_index,
                    batch_size=len(batch_texts),
                    item_start=item_start,
                    should_split=should_split,
                    upstream_rejection=self._is_upstream_rejection_exception(exc),
                    transport_error=self._is_transport_exception(exc),
                    pre_send_transport=self._is_pre_send_transport_failure(exc),
                    exception_type=type(exc).__name__,
                    cause_type=self._exception_cause_type(exc),
                    upstream_status=self._exception_status_code(exc),
                    upstream_code=self._exception_error_field(exc, "code"),
                    upstream_type=self._exception_error_field(exc, "type"),
                    )
                if should_split and len(batch_texts) == 1:
                    # 单条文本被上游拒绝（敏感词 / 长度超限）或提交后被中断时，
                    # 回传位置交由上层做语义二次切分，缩小到能被上游接受为止。
                    raise EmbeddingChunkRetryableError(item_start) from exc
                raise

            split_at = max(1, len(batch_texts) // 2)
            _log_embedding(
                "Embedding 批次被上游拒绝或中断，降级拆分",
                batch_index=batch_index,
                batch_size=len(batch_texts),
                item_start=item_start,
                split_sizes=f"{split_at}+{len(batch_texts) - split_at}",
                exception_type=type(exc).__name__,
                cause_type=self._exception_cause_type(exc),
            )
            left_vectors = self._embed_batch_with_timeout_fallback(
                client=client,
                batch_texts=batch_texts[:split_at],
                raw_batch_texts=(raw_batch_texts or batch_texts)[:split_at],
                batch_index=f"{batch_index}.1",
                item_start=item_start,
                total_batches=total_batches,
            )
            right_vectors = self._embed_batch_with_timeout_fallback(
                client=client,
                batch_texts=batch_texts[split_at:],
                raw_batch_texts=(raw_batch_texts or batch_texts)[split_at:],
                batch_index=f"{batch_index}.2",
                item_start=item_start + split_at,
                total_batches=total_batches,
            )
            return [*left_vectors, *right_vectors]

    def _create_client(self):
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
        except ImportError as exc:
            raise EmbeddingError("缺少 llama_index.embeddings.openai 依赖，无法生成 Embedding") from exc

        # 边界职责：适配器层负责把仓库配置映射为三方库参数，
        # 并在真正创建客户端前处理 llama_index 对模型枚举的限制。
        kwargs = self._build_client_kwargs()
        _log_embedding(
            "创建 OpenAIEmbedding 客户端",
            provider=self.provider_name,
            model=self.model_name,
            base_url=self.base_url or "default",
            timeout_seconds=self.timeout_seconds,
            reuse_client=False,
        )
        return OpenAIEmbedding(**kwargs)

    def _build_client_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "model": self.model_name,
            "api_key": self.api_key,
            "timeout": self.timeout_seconds,
            # 关键分支：兼容网关在高并发下会间歇性丢弃连接，默认不重试会把一次网络抖动
            # 直接放大成整份文档入库失败；这里允许 SDK 做有限次退避重试。
            "max_retries": _EMBEDDING_TRANSPORT_MAX_RETRIES,
            # 兼容网关连续复用同一长连接时，后续批次可能出现无响应；每次请求隔离客户端连接状态。
            "reuse_client": False,
        }
        if self.base_url:
            kwargs["api_base"] = self.base_url

        # 关键分支：阿里百炼等 OpenAI 兼容网关经常使用自定义 embedding model id。
        # llama_index 会先校验 `model` 是否属于内置枚举，这会让请求在真正发到上游前就失败。
        # 这里先传一个合法占位模型，再用 `model_name` 覆盖最终请求的模型名，以兼容自定义 id。
        if self.model_name not in _LLAMA_INDEX_SUPPORTED_OPENAI_EMBEDDING_MODELS:
            kwargs["model"] = _LLAMA_INDEX_FALLBACK_EMBEDDING_MODEL
            kwargs["model_name"] = self.model_name
            _log_embedding(
                "检测到自定义 OpenAI 兼容 embedding 模型，启用 llama_index 兼容模式",
                requested_model=self.model_name,
                fallback_model=_LLAMA_INDEX_FALLBACK_EMBEDDING_MODEL,
            )

        return kwargs

    @staticmethod
    def _normalize_timeout(raw_timeout: float) -> float:
        try:
            normalized = float(raw_timeout)
        except (TypeError, ValueError):
            return 10.0
        return max(1.0, normalized)

    @staticmethod
    def _normalize_embedding_text(text: str) -> str:
        # PDF/DOCX 解析可能把连续汉字拆成“字 空格 字”，百炼 Embedding 对这类噪声请求可能长时间无响应。
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        # 孤立代理字符会在 HTTP 编码阶段触发 UnicodeEncodeError；只清理这一类，不改控制字符和私有区字符。
        normalized = "".join(
            " " if unicodedata.category(char) == "Cs" else char
            for char in normalized
        )
        normalized = _CJK_WHITESPACE_RE.sub("", normalized)
        return normalized.strip()

    @classmethod
    def _is_transport_exception(cls, exc: BaseException) -> bool:
        """判断异常是否属于传输层故障（连接失败、连接或读取超时、连接池耗尽等）。

        传输层故障交给 SDK 的 `max_retries` 做有限次退避重试；
        其中「请求发出后才中断」的一类还可能由上游内容审核触发，
        由 `_should_split_batch` 进一步区分，不能一概当作与内容无关。
        """
        transport_types = frozenset(
            {
                "APITimeoutError",
                "APIConnectionError",
                "ConnectTimeout",
                "ConnectError",
                "PoolTimeout",
                "ReadTimeout",
                "ReadError",
                "RemoteProtocolError",
                "TimeoutException",
                "WriteTimeout",
                "WriteError",
                "NetworkError",
            }
        )
        return cls._exception_chain_has_type(exc, transport_types)

    @classmethod
    def _is_pre_send_transport_failure(cls, exc: BaseException) -> bool:
        """判断是否属于「请求发出前」就失败的连接异常（TCP 未握手、连接池拿不到连接）。

        这类失败时请求体根本没有送达上游，内容层面（含敏感词）不可能是原因，
        因此不能拿它当作「文本被拒」的信号去拆分。
        """
        return cls._exception_chain_has_type(exc, _EMBEDDING_PRE_SEND_TRANSPORT_TYPES)

    @classmethod
    def _is_upstream_rejection_exception(cls, exc: Exception) -> bool:
        """判断上游是否以 4xx 明确拒绝了请求（长度超限、敏感词 / 内容审核等）。

        与传输层中断不同，这类拒绝带有明确报文，可直接作为「内容被拒」的判据。
        """
        if cls._is_transport_exception(exc):
            return False
        status_code = cls._exception_status_code(exc)
        code = cls._exception_error_field(exc, "code") or ""
        error_type = cls._exception_error_field(exc, "type") or ""
        detail = f"{code} {error_type} {exc}".lower()
        if any(keyword in detail for keyword in _EMBEDDING_UPSTREAM_REJECTION_KEYWORDS):
            return True
        # 兜底：上游以 400/413/422 拒绝但未给出可识别细节时，仍按「内容被拒」处理，
        # 让拆分流程再试一次；其余异常（鉴权失败、模型不存在等）保持快速失败。
        return status_code in _EMBEDDING_UPSTREAM_REJECTION_STATUS_CODES

    @classmethod
    def _should_split_batch(cls, exc: Exception) -> bool:
        """判断本次失败是否值得通过拆分批次来规避。

        拆分只在「上游收到请求后才拒绝或中断」时才有意义：
        1. 上游以 4xx 明确拒绝（内容审核 / 敏感词、长度超限）：拆小后重新提交，可定位并绕过被拒片段；
        2. 请求已发出后的连接中断或读取超时：敏感词被拦截时通常就是这种形态，需要保留拆分；
        3. 连接从未建立（TCP 未握手）：请求体未送达，内容不可能导致失败，只能快速失败。
        """
        if cls._is_pre_send_transport_failure(exc):
            return False
        if cls._is_transport_exception(exc):
            return True
        return cls._is_upstream_rejection_exception(exc)

    @staticmethod
    def _exception_chain_has_type(exc: BaseException, type_names: frozenset[str]) -> bool:
        """沿异常因果链查找是否存在指定类名的异常。"""
        current: BaseException | None = exc
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if type(current).__name__ in type_names:
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _exception_cause_type(exc: BaseException) -> str | None:
        cause = exc.__cause__ or exc.__context__
        return type(cause).__name__ if cause is not None else None

    @staticmethod
    def _normalize_vector(raw_vector: list[float]) -> list[float]:
        if not isinstance(raw_vector, list) or not raw_vector:
            raise EmbeddingError("Embedding 向量为空或格式非法")
        try:
            return [float(value) for value in raw_vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding 向量包含无法转换为 float 的值") from exc

    @staticmethod
    def _exception_status_code(exc: Exception) -> int | None:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    @staticmethod
    def _exception_error_field(exc: Exception, field: str) -> str | None:
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            return None
        error = body.get("error")
        if not isinstance(error, dict):
            return None
        value = error.get(field)
        return value if isinstance(value, str) and len(value) <= 80 else None


def _log_embedding(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][EmbeddingAdapter] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
