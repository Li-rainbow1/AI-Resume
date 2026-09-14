from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, replace
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.domain.models.system_service_config import service_label
from app.infrastructure.config.settings import Settings
from app.infrastructure.llm.openai_chat_client import OpenAIChatClient
from app.infrastructure.llm.ollama_embedding_adapter import OllamaEmbeddingAdapter
from app.infrastructure.llm.openai_embedding_adapter import OpenAIEmbeddingAdapter
from app.infrastructure.llm.openai_image_markdown_ocr_adapter import OpenAIImageMarkdownOcrAdapter
from app.infrastructure.llm.openai_realtime_client import OpenAIRealtimeClient
from app.infrastructure.mail.smtp_connection import smtp_session
from app.infrastructure.realtime.asr_adapters import BailianRealtimeAdapter, VolcengineRealtimeAdapter
from app.shared.constants.rag import DEFAULT_RAG_INTERVIEW_TOP_K, DEFAULT_RAG_TOP_K


@dataclass(frozen=True, slots=True)
class ServiceTestOutcome:
    success: bool
    message: str
    elapsed_ms: int


class SystemServiceTester:
    def test(
        self,
        *,
        service_key: str,
        public_config: dict[str, Any],
        secrets: dict[str, str],
        settings: Settings,
    ) -> ServiceTestOutcome:
        started = time.perf_counter()
        try:
            if service_key == "embedding":
                self._test_embedding(public_config, secrets, settings)
                message = "Embedding 服务连接成功"
            elif service_key == "chat":
                self._test_chat(public_config, secrets, settings)
                message = "聊天模型连接成功"
            elif service_key == "vision":
                self._test_vision(public_config, secrets, settings)
                message = "图片 OCR 服务连接成功"
            elif service_key == "realtime":
                self._test_realtime(public_config, secrets, settings)
                message = "实时语音服务连接成功"
            elif service_key == "smtp":
                self._test_smtp(public_config, secrets, settings)
                message = "SMTP 连接和登录成功"
            elif service_key == "rag":
                self._test_rag(public_config, settings)
                message = "RAG 检索链路连接成功"
            else:
                return self._failure(service_key, started, "不支持的系统服务")
            return ServiceTestOutcome(True, message, _elapsed_ms(started))
        except Exception:
            # 外部供应商的原始错误可能包含 URL、账号或请求头，页面只展示统一脱敏提示。
            return self._failure(service_key, started, f"{service_label(service_key)}连接失败，请检查地址、模型和凭据")

    def _test_embedding(self, config: dict[str, Any], secrets: dict[str, str], settings: Settings) -> None:
        provider = _text(config.get("provider"), "openai").lower()
        model = _required(config.get("model"), "Embedding 模型")
        base_url = _required(config.get("baseUrl"), "Embedding Base URL")
        if provider == "ollama":
            adapter = OllamaEmbeddingAdapter(
                model_name=model,
                base_url=base_url,
                timeout_seconds=settings.ollama_embedding_timeout_seconds,
            )
        else:
            api_key = _required(secrets.get("apiKey"), "Embedding API Key")
            adapter = OpenAIEmbeddingAdapter(
                api_key=api_key,
                model_name=model,
                base_url=base_url,
                timeout_seconds=settings.openai_embedding_timeout_seconds,
            )
        vectors = adapter.embed_texts(["resume builder system service health check"])
        if not vectors or not vectors[0]:
            raise RuntimeError("empty embedding response")

    def _test_chat(self, config: dict[str, Any], secrets: dict[str, str], settings: Settings) -> None:
        provider = _text(config.get("provider"), "openai").lower()
        client = OpenAIChatClient(
            model_name=_required(config.get("model"), "聊天模型"),
            base_url=_required(config.get("baseUrl"), "聊天 Base URL"),
            api_key="ollama" if provider == "ollama" else _required(secrets.get("apiKey"), "聊天 API Key"),
            completions_path=_text(config.get("completionsPath"), "/v1/chat/completions"),
            timeout_seconds=settings.openai_chat_timeout_seconds,
        )
        answer = client.chat("请只回复：连接成功", "你正在执行服务健康检查，只输出简短结果。")
        if not answer.strip():
            raise RuntimeError("empty chat response")

    def _test_vision(self, config: dict[str, Any], secrets: dict[str, str], settings: Settings) -> None:
        provider = _text(config.get("provider"), "openai").lower()
        adapter = OpenAIImageMarkdownOcrAdapter(
            api_key="ollama" if provider == "ollama" else _required(secrets.get("apiKey"), "OCR API Key"),
            model_name=_required(config.get("model"), "OCR 模型"),
            detail=_text(config.get("detail"), "high"),
            provider=provider,
            base_url=_required(config.get("baseUrl"), "OCR Base URL"),
            timeout_seconds=settings.openai_vision_timeout_seconds,
        )
        # 探针图带文字，因此这里可以直接断言「取回了文字」：只验证请求不报错的话，
        # 一个能连通但对任何图片都返回空正文的服务也会被判成通过。
        markdown = adapter.extract_markdown(_ocr_probe_png(), "health-check.png", "image/png")
        if not markdown.strip():
            raise RuntimeError("empty vision ocr response")

    def _test_realtime(self, config: dict[str, Any], secrets: dict[str, str], settings: Settings) -> None:
        provider = _text(config.get("provider"), "openai").lower()
        if provider in {"dashscope", "aliyun", "qwen"}:
            provider = "bailian"
        if provider == "bailian":
            probe_settings = replace(
                settings,
                realtime_provider="bailian",
                bailian_asr_region="cn-beijing",
                bailian_asr_workspace_id=_required(config.get("workspaceId"), "百炼 Workspace ID"),
                bailian_asr_model=_required(config.get("model"), "百炼 ASR 模型"),
                bailian_asr_hotwords=_text(config.get("hotwords"), ""),
                bailian_asr_api_key=_required(secrets.get("apiKey"), "百炼 API Key"),
                # 词表在保存阶段才按当前 Workspace/地域同步；连接测试不应
                # 误用旧 Workspace 下的词表 ID，尤其是在管理员切换地域时。
                bailian_asr_vocabulary_id="",
            )
            asyncio.run(BailianRealtimeAdapter().probe(probe_settings))
            return
        if provider == "volcengine":
            probe_settings = replace(
                settings,
                realtime_provider="volcengine",
                volcengine_asr_app_key=_required(secrets.get("apiKey"), "豆包 App Key"),
            )
            asyncio.run(VolcengineRealtimeAdapter().probe(probe_settings))
            return
        client = OpenAIRealtimeClient(
            base_url=settings.openai_realtime_base_url,
            api_key=_required(secrets.get("apiKey"), "实时语音 API Key"),
            client_secrets_path=settings.openai_realtime_client_secrets_path,
            realtime_calls_path=settings.openai_realtime_calls_path,
            default_model=_required(config.get("transcriptionModel"), "识别模型"),
            default_language="",
        )
        client.create_client_secret()

    def _test_smtp(self, config: dict[str, Any], secrets: dict[str, str], settings: Settings) -> None:
        host = _required(config.get("host"), "SMTP 主机")
        port = _positive_int(config.get("port"), 465)
        security_mode = _smtp_security_mode(config.get("securityMode"), port)
        username = _required(config.get("username"), "SMTP 邮箱账号")
        authorization_code = _required(secrets.get("authorizationCode"), "SMTP 授权码")
        timeout = settings.mail_connection_timeout_seconds
        io_timeout = max(timeout, settings.mail_timeout_seconds, settings.mail_write_timeout_seconds)
        with smtp_session(host=host, port=port, security_mode=security_mode, timeout=timeout) as smtp:
            if smtp.sock is not None:
                smtp.sock.settimeout(io_timeout)
            smtp.login(username, authorization_code)

    @staticmethod
    def _test_rag(config: dict[str, Any], settings: Settings) -> None:
        # 入口意图：仅在显式调用 RAG 测试接口时验证完整的向量链路；配置保存流程不调用这里。
        # 关键步骤：先按当前生效配置校验检索参数，再构建真实 RAG 检索器；检索器会
        # 通过当前 Embedding provider 生成探针向量，并将向量交给 pgvector 做相似度查询。
        # 关键分支：知识库为空时查询可以返回空结果，因为本次只验证 Embedding 和
        # pgvector 的可用性；Embedding 调用、数据库连接或查询失败则返回测试失败。
        # 输出与副作用：查询结果不写入知识库、不改变配置，也不参与 RAG 配置保存。
        top_k = _positive_int(config.get("topK"), settings.app_rag_top_k)
        interview_top_k = _positive_int(config.get("interviewTopK"), settings.app_interview_rag_top_k)
        threshold = float(config.get("similarityThreshold", settings.app_interview_rag_similarity_threshold))
        if (
            top_k > DEFAULT_RAG_TOP_K
            or interview_top_k > DEFAULT_RAG_INTERVIEW_TOP_K
            or not 0.0 <= threshold <= 1.0
        ):
            raise ValueError("invalid rag configuration")

        # 边界职责：测试器只负责触发一次验证，向量库访问和 Embedding 调用仍由
        # RAG 检索器及其基础设施适配器完成，避免在配置服务中重复实现检索逻辑。
        from app.bootstrap.container import build_rag_retriever

        retriever = build_rag_retriever(settings)
        # 只取一个结果即可证明查询链路可用，避免健康校验读取不必要的上下文。
        retriever.query(query="系统服务健康检查", top_k=min(top_k, 1))

    @staticmethod
    def _failure(service_key: str, started: float, message: str) -> ServiceTestOutcome:
        return ServiceTestOutcome(False, message, _elapsed_ms(started))


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    return text


def _text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _positive_int(value: object, fallback: int) -> int:
    try:
        return max(1, int(value)) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _smtp_security_mode(value: object, port: int) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"ssl", "starttls", "none"}:
        return mode
    return "ssl" if port == 465 else "starttls"


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _ocr_probe_png() -> bytes:
    """生成视觉服务健康检查用的带文字探针图。

    早期版本用 1×1 全透明 PNG 探测，但上游对「图里没有可提取文字」的输入会把说明留在
    reasoning 字段、把正文留空，于是健康检查把「连接正常」误判成失败；空白图也无法区分
    「服务取不到文字」与「这张图本来就没有文字」。这里改为白底黑字的短文本：既验证连通性
    与凭据，也验证 OCR 真的能取回文字。字形来自 Pillow 内置字体，不依赖系统字体，
    也不携带任何用户数据。
    """
    text = "OCR HEALTH CHECK"
    font = ImageFont.load_default()
    probe = Image.new("RGB", (10, 10), "white")
    bounds = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    padding = 4
    canvas = Image.new(
        "RGB",
        (bounds[2] - bounds[0] + padding * 2, bounds[3] - bounds[1] + padding * 2),
        "white",
    )
    ImageDraw.Draw(canvas).text(
        (padding - bounds[0], padding - bounds[1]),
        text,
        fill="black",
        font=font,
    )
    # 内置字体默认约 11px，直接送检偏小；按整数倍最近邻放大可保持字形锐利，
    # 且不必依赖 load_default(size=...)（该参数要求 Pillow 10.1 以上）。
    scale = 6
    canvas = canvas.resize((canvas.width * scale, canvas.height * scale), Image.NEAREST)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()
