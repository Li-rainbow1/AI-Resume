import base64
import json
from time import perf_counter
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from app.domain.exceptions.rag_exceptions import ImageOcrError, safe_rag_log_value
from app.domain.models.rag_image import ImageAnalysisResult, ImageClassification

_IMAGE_CLASSIFICATIONS = {"text", "table", "diagram", "decorative", "empty"}

# 图片视觉解析的传输层重试次数：仅覆盖连接失败、连接超时、限流和 5xx 等瞬时故障，
# 取较小值，在提升单张图片成功率的同时避免上游真实故障时长时间挂起。
_VISION_TRANSPORT_MAX_RETRIES = 2

# 图片视觉解析的内容级总尝试次数（含首次请求，非额外重试次数）：传输层重试只覆盖网络抖动，
# 而视觉模型在输出长文本时，可能返回转义不合规或被截断的 JSON，这类失败发生在请求成功返回
# 之后，SDK 不会重试。这里对「响应已返回但内容不可用」重试，取 3 表示「首次 + 最多 2 次重试」，
# 取小值以避免异常图片把单张图片耗时拖得过长。
# 注意与 _VISION_TRANSPORT_MAX_RETRIES 的区别：后者是 SDK 单次请求内的重试，两者互斥不叠加——
# 内容级失败说明 HTTP 已成功返回；请求级失败直接上抛，不走内容级重试。
_VISION_CONTENT_MAX_ATTEMPTS = 3


class _ImageContentUnusableError(ImageOcrError):
    """视觉服务已返回响应，但内容无法解析为约定 JSON（属内容级失败，可重试）。

    携带稳定原因码供日志记录，避免把模型返回的原始文本写入日志：
    empty_content（正文为空）、invalid_fence（代码块围栏不合法）、
    invalid_shape（首尾不是 JSON 对象，通常是输出被截断）、
    invalid_json（JSON 解码失败，常见于转义不合规）、unknown_classification（分类非法）。
    """

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _guess_media_type(content_type: str | None, file_name: str) -> str:
    normalized = (content_type or "").strip().lower()
    if normalized:
        return normalized
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(suffix, "application/octet-stream")


class OpenAIImageMarkdownOcrAdapter:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        detail: str,
        provider: str = "openai",
        base_url: str | None = None,
        timeout_seconds: float = 40.0,
    ) -> None:
        self.provider = (provider or "openai").strip().lower()
        self.api_key = "ollama" if self.provider == "ollama" else (api_key or "").strip()
        self.model_name = (model_name or "").strip() or "gpt-4.1"
        self.detail = (detail or "").strip() or "high"
        self.base_url = (base_url or "").strip() or None
        self.timeout_seconds = self._normalize_timeout(timeout_seconds)

    def extract_markdown(
        self,
        image_bytes: bytes,
        file_name: str,
        content_type: str | None = None,
    ) -> str:
        if not image_bytes:
            raise ImageOcrError("Image file cannot be empty")
        if not self.api_key:
            raise ImageOcrError("OPENAI_API_KEY is not configured")

        data_url = self._to_data_url(image_bytes=image_bytes, file_name=file_name, content_type=content_type)
        normalized_base_url = self._normalize_api_base_url(self.base_url)
        prompt = (
            "你是严格的 OCR 文字提取助手。"
            "请只提取图片中清晰可见的文字内容。"
            "不要猜测、补全或编造缺失内容。"
            "只返回纯文本，不要返回 Markdown、代码块、列表标记或任何额外说明。"
        )

        _log_image_ocr(
            "ocr_started",
            base_url=self.base_url or "https://api.openai.com",
            normalized_base_url=normalized_base_url,
            provider=self.provider,
            model=self.model_name,
            api_key=_mask_api_key(self.api_key),
            timeout_seconds=self.timeout_seconds,
            file_name=file_name,
        )

        # Ollama 的 OpenAI 兼容接口接收 image_url 的数据 URL 字符串，且未定义
        # OpenAI 的 detail 字段；兼容服务则保持 OpenAI 的对象形式。
        image_url: str | dict[str, str]
        if self.provider == "ollama":
            image_url = data_url
        else:
            image_url = {"url": data_url, "detail": self.detail}

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": image_url},
                ],
            }
        ]

        started = perf_counter()
        try:
            markdown = self._extract_text_with_openai_sdk(messages=messages, base_url=normalized_base_url)
        except Exception as exc:
            _log_image_ocr(
                "sdk_failed",
                elapsed_ms=f"{(perf_counter() - started) * 1000:.1f}",
                error_type=type(exc).__name__,
            )
            if isinstance(exc, ImageOcrError):
                raise
            raise ImageOcrError("OCR 服务请求失败，请稍后重试") from exc

        if markdown:
            _log_image_ocr("sdk_success", elapsed_ms=f"{(perf_counter() - started) * 1000:.1f}")
            return markdown

        # 关键分支：上游正常返回，但正文里没有任何可提取文字。照片、纯图形 Logo、
        # 没有标注的图表都会走到这里——上游会把说明留在 reasoning 字段而把正文留空。
        # 这表示「这张图没有文字」，不是 OCR 服务故障，因此返回空串交给调用方判断
        # （独立图片上传会据此提示未生成可入库内容），避免把服务可用误报为失败。
        # 服务是否真的不可用，仍由传输层异常与后台「测试连接」的带文字探针共同兜住。
        _log_image_ocr("sdk_empty_content", elapsed_ms=f"{(perf_counter() - started) * 1000:.1f}")
        return ""

    def analyze_image(
        self,
        image_bytes: bytes,
        file_name: str,
        content_type: str | None = None,
    ) -> ImageAnalysisResult:
        """用一次视觉请求完成图片分类、OCR 和图示说明。

        入口意图：知识库图片增强需要「分类 + 提取文本 / 说明图示」的稳定结果，
        单张图片失败会让整篇文档降级为 partial_failed，因此这里要尽量提高单张成功率。
        关键步骤：拼装中文提示词 -> 请求视觉服务 -> 解析为约定 JSON -> 返回分析结果。
        关键分支：把失败分成两类——请求级失败（连接、超时、上游错误，SDK 已按
        `_VISION_TRANSPORT_MAX_RETRIES` 做传输层退避重试）与内容级失败（HTTP 已成功
        返回，但响应不是可解析的约定 JSON）。
        处理策略：内容级失败按总尝试次数重新请求，`_VISION_CONTENT_MAX_ATTEMPTS = 3` 表示
        「首次 + 最多 2 次重试」，因为模型输出长文本时可能转义不合规或被截断，重新请求有机会
        拿到合规结果；请求级失败不在应用层二次重试，避免与 SDK 退避叠加导致单张图片长时间挂起。
        异常与兜底：非 ImageOcrError 的未知异常统一包装为 ImageOcrError 上抛；
        最终失败只记录稳定原因码，不记录模型返回的原始文本。
        输出与副作用：返回单张图片分析结果，并输出 analysis_* 过程日志供排障。
        """
        if not image_bytes:
            raise ImageOcrError("图片文件不能为空")
        if not self.api_key:
            raise ImageOcrError("OPENAI_API_KEY 未配置")

        prompt = (
            "你是知识库图片解析助手。请只返回一个合法 JSON 对象，不要返回 Markdown、代码块或额外说明。"
            "先判断图片的主要内容，再提取文字或说明图示。"
            "classification 只能是 text、table、diagram、decorative、empty 之一。"
            "text 和 table 填写清晰可见的原文到 ocrText；diagram 用 description 描述节点、关系、流程或结构；"
            "decorative 和 empty 的 ocrText、description 返回空字符串。不要猜测不可见内容。"
            'JSON 格式必须为：{"classification":"text","ocrText":"","description":"","confidence":0.0}'
        )
        started = perf_counter()
        content_attempt = 0
        while True:
            content_attempt += 1
            try:
                content = self._request_image_content(
                    image_bytes=image_bytes,
                    file_name=file_name,
                    content_type=content_type,
                    prompt=prompt,
                    log_prefix="analysis",
                )
                result = self._parse_analysis_result(content)
            except _ImageContentUnusableError as exc:
                # 内容级失败：未到尝试上限就重新请求；达到上限才记录原因码并上抛。
                if content_attempt >= _VISION_CONTENT_MAX_ATTEMPTS:
                    _log_image_ocr(
                        "analysis_failed",
                        elapsed_ms=f"{(perf_counter() - started) * 1000:.1f}",
                        error_type=type(exc).__name__,
                        reason=exc.reason,
                        attempts=content_attempt,
                    )
                    raise
                _log_image_ocr(
                    "analysis_content_retry",
                    reason=exc.reason,
                    attempt=content_attempt,
                    max_attempts=_VISION_CONTENT_MAX_ATTEMPTS,
                    file_name=file_name,
                )
                continue
            except Exception as exc:
                # 请求级失败或未知异常：SDK 已在传输层做过退避重试，这里只记录并上抛。
                _log_image_ocr(
                    "analysis_failed",
                    elapsed_ms=f"{(perf_counter() - started) * 1000:.1f}",
                    error_type=type(exc).__name__,
                    reason="request_failed",
                    attempts=content_attempt,
                )
                if isinstance(exc, ImageOcrError):
                    raise
                raise ImageOcrError("图片视觉解析失败，请稍后重试") from exc

            _log_image_ocr(
                "analysis_success",
                elapsed_ms=f"{(perf_counter() - started) * 1000:.1f}",
                classification=result.classification,
                attempts=content_attempt,
            )
            return result

    @staticmethod
    def _to_data_url(image_bytes: bytes, file_name: str, content_type: str | None = None) -> str:
        media_type = _guess_media_type(content_type=content_type, file_name=file_name)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    @staticmethod
    def _extract_text_from_chat_payload(data: object) -> str:
        if hasattr(data, "model_dump"):
            dumped = data.model_dump(mode="python")
            if isinstance(dumped, dict):
                data = dumped

        if not isinstance(data, dict):
            return ""

        choices = data.get("choices") or []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                text = content.strip()
                if text:
                    return text
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("text") or "").strip()
                    if text:
                        return text
        return ""

    def _extract_text_with_openai_sdk(self, messages: list[dict], base_url: str) -> str:
        try:
            from openai import OpenAI
        except Exception as exc:
            raise ImageOcrError("OpenAI OCR SDK 不可用，请稍后重试") from exc

        _log_image_ocr("sdk_client_init_started", normalized_base_url=base_url, model=self.model_name)
        # 关键分支：容器到外部视觉服务之间可能出现瞬时的连接失败或连接超时，
        # 这类传输层抖动不代表图片内容有问题，直接判定失败会让整张图片永久降级为失败态。
        # 这里允许 SDK 做有限次退避重试（仅覆盖连接错误、超时、限流和 5xx），
        # 重试次数保持较小，避免上游真实故障时把单张图片的解析拖成长时间挂起。
        client = OpenAI(
            api_key=self.api_key,
            base_url=base_url,
            timeout=self.timeout_seconds,
            max_retries=_VISION_TRANSPORT_MAX_RETRIES,
        )
        _log_image_ocr("sdk_client_init_finished", normalized_base_url=base_url, model=self.model_name)
        _log_image_ocr("sdk_request_started", normalized_base_url=base_url, model=self.model_name)
        response = client.chat.completions.create(
            model=self.model_name,
            messages=messages,
        )
        _log_image_ocr("sdk_request_finished", normalized_base_url=base_url, model=self.model_name)
        return self._extract_text_from_chat_payload(response)

    def _request_image_content(
        self,
        *,
        image_bytes: bytes,
        file_name: str,
        content_type: str | None,
        prompt: str,
        log_prefix: str,
    ) -> str:
        data_url = self._to_data_url(image_bytes=image_bytes, file_name=file_name, content_type=content_type)
        normalized_base_url = self._normalize_api_base_url(self.base_url)
        image_url: str | dict[str, str]
        if self.provider == "ollama":
            image_url = data_url
        else:
            image_url = {"url": data_url, "detail": self.detail}
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": image_url},
                ],
            }
        ]
        _log_image_ocr(
            f"{log_prefix}_started",
            provider=self.provider,
            model=self.model_name,
            normalized_base_url=normalized_base_url,
            timeout_seconds=self.timeout_seconds,
            file_name=file_name,
        )
        try:
            content = self._extract_text_with_openai_sdk(
                messages=messages,
                base_url=normalized_base_url,
            ).strip()
        except Exception as exc:
            _log_image_ocr(f"{log_prefix}_request_failed", error_type=type(exc).__name__)
            if isinstance(exc, ImageOcrError):
                raise
            raise ImageOcrError("图片视觉服务请求失败，请稍后重试") from exc
        if not content:
            # 内容级失败：HTTP 已成功返回但正文为空，重新请求有机会拿到完整结果。
            raise _ImageContentUnusableError("图片视觉服务没有返回有效内容", reason="empty_content")
        return content

    @staticmethod
    def _parse_analysis_result(content: str) -> ImageAnalysisResult:
        # 关键分支：所有「响应拿到了但用不了」的情况统一抛内容级异常并附稳定原因码，
        # 让上层据此决定是否重试，也让日志能区分转义非法、输出被截断与分类非法三类成因。
        raw = (content or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if len(lines) < 3 or lines[-1].strip() != "```":
                raise _ImageContentUnusableError("图片视觉服务返回的 JSON 格式非法", reason="invalid_fence")
            language = lines[0].strip()[3:].strip().lower()
            if language not in {"", "json"}:
                raise _ImageContentUnusableError("图片视觉服务返回的 JSON 格式非法", reason="invalid_fence")
            raw = "\n".join(lines[1:-1]).strip()
        if not raw.startswith("{") or not raw.endswith("}"):
            # 首尾不是花括号通常意味着模型输出被长度截断，而不是转义问题。
            raise _ImageContentUnusableError("图片视觉服务返回的 JSON 格式非法", reason="invalid_shape")
        try:
            payload: Any = json.loads(raw)
        except (TypeError, ValueError) as exc:
            # 解码失败常见于模型在长文本里写入了不合规转义（例如把单引号写成 \'）。
            raise _ImageContentUnusableError("图片视觉服务返回的 JSON 格式非法", reason="invalid_json") from exc
        if not isinstance(payload, dict):
            raise _ImageContentUnusableError("图片视觉服务返回的 JSON 格式非法", reason="invalid_shape")

        classification = str(
            payload.get("classification") or payload.get("category") or ""
        ).strip().lower()
        if classification not in _IMAGE_CLASSIFICATIONS:
            raise _ImageContentUnusableError("图片视觉服务返回了未知图片分类", reason="unknown_classification")

        def safe_text(value: object) -> str:
            return value.strip() if isinstance(value, str) else ""

        confidence: float | None = None
        raw_confidence = payload.get("confidence")
        if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool):
            confidence = max(0.0, min(1.0, float(raw_confidence)))

        return ImageAnalysisResult(
            classification=cast(ImageClassification, classification),
            ocr_text=safe_text(payload.get("ocrText") or payload.get("ocr_text")),
            description=safe_text(payload.get("description") or payload.get("visualDescription")),
            confidence=confidence,
        )

    @staticmethod
    def _normalize_timeout(raw_timeout: float) -> float:
        try:
            normalized = float(raw_timeout)
        except (TypeError, ValueError):
            return 40.0
        return max(3.0, normalized)

    @staticmethod
    def _normalize_api_base_url(base_url: str | None) -> str:
        raw = (base_url or "").strip() or "https://api.openai.com"
        parsed = urlsplit(raw)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc
        path = parsed.path.rstrip("/")

        if not netloc:
            raise ImageOcrError(f"Invalid OpenAI base_url: {raw}")

        if not path:
            path = "/v1"
        elif not path.endswith("/v1"):
            path = f"{path}/v1"

        return urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment)).rstrip("/")


def _mask_api_key(api_key: str) -> str:
    safe = (api_key or "").strip()
    if not safe:
        return ""
    if len(safe) <= 6:
        return safe[:3] + "***"
    return f"{safe[:3]}***{safe[-4:]}"


def _log_image_ocr(message: str, **extra: object) -> None:
    parts = [f"[knowledge-upload][ImageOCR] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
