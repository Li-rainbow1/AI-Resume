from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from threading import RLock

from app.application.ports.system_service_config_repository import SystemServiceConfigRepository
from app.domain.exceptions.auth_exceptions import SystemConfigRepositoryError
from app.domain.models.system_service_config import StoredSystemServiceConfig
from app.infrastructure.config.settings import Settings
from app.shared.constants.rag import DEFAULT_RAG_INTERVIEW_TOP_K, DEFAULT_RAG_TOP_K

_LOGGER = logging.getLogger("uvicorn.error")


class RuntimeSettingsProvider:
    """按全局修订号刷新环境变量与数据库覆盖合并后的运行时配置。"""

    def __init__(self, *, base_settings: Settings, repository: SystemServiceConfigRepository) -> None:
        self._base_settings = base_settings
        self._repository = repository
        self._lock = RLock()
        self._revision: int | None = None
        self._settings = base_settings

    def resolve(self) -> Settings:
        # 没有 MySQL 时保留纯环境变量模式，便于本地只启动 AI 后端做接口联调。
        if not self._base_settings.mysql_datasource_url.strip():
            return self._base_settings

        try:
            revision = self._repository.get_global_revision()
        except SystemConfigRepositoryError as exc:
            # 配置表尚未迁移或数据库短暂不可用时，不阻断原有环境变量链路；
            # 已经加载过的数据库配置继续留在内存中，避免瞬间回退到错误凭据。
            with self._lock:
                if self._revision is not None:
                    _LOGGER.warning("[系统服务配置] 修订号读取失败，继续使用最近一次生效配置：%s", exc)
                    return self._settings
            _LOGGER.warning("[系统服务配置] 配置表暂不可用，使用部署默认配置：%s", exc)
            return self._base_settings

        with self._lock:
            if self._revision == revision:
                return self._settings

        try:
            configs = self._repository.list_configs()
        except SystemConfigRepositoryError as exc:
            # 修订号已经读取成功但配置快照读取失败时，不能让单次数据库故障
            # 阻断所有依赖运行时配置的请求；优先保留最近一次完整生效的快照。
            with self._lock:
                if self._revision is not None:
                    _LOGGER.warning("[系统服务配置] 配置列表读取失败，继续使用最近一次生效配置：%s", exc)
                    return self._settings
            _LOGGER.warning("[系统服务配置] 配置表暂不可用，使用部署默认配置：%s", exc)
            return self._base_settings

        refreshed = apply_stored_overrides(self._base_settings, configs)
        with self._lock:
            # 只有修订号对应的快照完整加载后才替换，避免半套配置进入请求。
            self._settings = refreshed
            self._revision = revision
            return self._settings


def apply_stored_overrides(
    base_settings: Settings,
    configs: list[StoredSystemServiceConfig],
) -> Settings:
    resolved = base_settings
    for config in configs:
        public = config.public_config
        secrets = config.secrets
        if config.service_key == "embedding":
            resolved = _apply_embedding_override(resolved, public, secrets)
        elif config.service_key == "chat":
            resolved = _apply_chat_override(resolved, public, secrets)
        elif config.service_key == "vision":
            resolved = _apply_vision_override(resolved, public, secrets)
        elif config.service_key == "realtime":
            resolved = _apply_realtime_override(resolved, public, secrets)
        elif config.service_key == "rag":
            resolved = _apply_rag_override(resolved, public)
        elif config.service_key == "smtp":
            resolved = _apply_smtp_override(resolved, public, secrets)
    return resolved


def embedding_profile_id(*, provider: str, base_url: str, model: str, base_profile: str = "") -> str:
    normalized_provider = (provider or "openai").strip().lower()
    normalized_base = (base_url or "").strip().rstrip("/").lower()
    normalized_model = (model or "").strip()
    signature = f"{normalized_provider}|{normalized_base}|{normalized_model}"
    base_signature = base_profile.strip()
    if base_signature and signature == base_signature:
        return normalized_model
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    return f"{normalized_model[:96]}@{digest}"


def _apply_embedding_override(settings: Settings, public: dict[str, object], secrets: dict[str, str]) -> Settings:
    # Embedding 统一使用兼容 OpenAI 的 Base URL、模型和密钥；早期页面中的
    # region/workspaceId 并不会影响供应商请求，因此不进入运行时配置。
    provider = _text(public.get("provider"), settings.embedding_provider).lower()
    model = _text(public.get("model"), settings.embedding_model_name)
    base_url = _text(public.get("baseUrl"), settings.embedding_base_url).rstrip("/")
    api_key = _text(secrets.get("apiKey"), settings.openai_embedding_api_key)
    base_signature = f"{settings.embedding_provider}|{settings.embedding_base_url.rstrip('/').lower()}|{settings.embedding_model_name}"
    profile = embedding_profile_id(
        provider=provider,
        base_url=base_url,
        model=model,
        base_profile=base_signature,
    )

    if provider == "ollama":
        return replace(
            settings,
            embedding_provider="ollama",
            embedding_model_name=profile,
            embedding_base_url=base_url,
            embedding_timeout_seconds=settings.ollama_embedding_timeout_seconds,
            ollama_embedding_base_url=base_url,
            ollama_embedding_model=model,
        )

    return replace(
        settings,
        embedding_provider="openai",
        embedding_model_name=profile,
        embedding_base_url=base_url,
        embedding_timeout_seconds=settings.openai_embedding_timeout_seconds,
        openai_embedding_base_url=base_url,
        openai_embedding_api_key=api_key,
        openai_embedding_model=model,
    )


def _apply_chat_override(settings: Settings, public: dict[str, object], secrets: dict[str, str]) -> Settings:
    base_url = _text(public.get("baseUrl"), settings.openai_base_url).rstrip("/")
    provider = _text(public.get("provider"), "openai").lower()
    # Ollama 的 OpenAI 兼容接口要求调用方传入 api_key 参数，但本地服务会忽略其值。
    api_key = "ollama" if provider == "ollama" else _text(secrets.get("apiKey"), settings.openai_api_key)
    return replace(
        settings,
        openai_base_url=base_url,
        openai_api_key=api_key,
        openai_chat_model=_text(public.get("model"), settings.openai_chat_model),
        openai_chat_completions_path=_path(public.get("completionsPath"), settings.openai_chat_completions_path),
    )


def _apply_vision_override(settings: Settings, public: dict[str, object], secrets: dict[str, str]) -> Settings:
    provider = _text(public.get("provider"), settings.vision_provider).lower()
    return replace(
        settings,
        vision_provider=provider if provider in {"openai", "ollama"} else "openai",
        openai_vision_base_url=_text(public.get("baseUrl"), settings.openai_vision_base_url).rstrip("/"),
        # OpenAI SDK 要求传入 api_key；Ollama 本地 OpenAI 兼容接口会忽略占位值。
        openai_vision_api_key="ollama" if provider == "ollama" else _text(secrets.get("apiKey"), settings.openai_vision_api_key),
        openai_vision_model=_text(public.get("model"), settings.openai_vision_model),
        openai_vision_detail=_text(public.get("detail"), settings.openai_vision_detail) if provider != "ollama" else "",
    )


def _apply_realtime_override(settings: Settings, public: dict[str, object], secrets: dict[str, str]) -> Settings:
    provider = _text(public.get("provider"), settings.realtime_provider).lower()
    if provider not in {"openai", "bailian", "volcengine"}:
        provider = "openai"

    # OpenAI 仍由浏览器 WebRTC 直连；Base URL、内部路径和语言由部署/后端固定，
    # 不接受网页传入的覆盖值。
    openai_model = _text(public.get("transcriptionModel"), settings.openai_realtime_transcription_model)
    if provider == "bailian":
        bailian_model = _text(public.get("model"), settings.bailian_asr_model)
        if bailian_model not in {"qwen-audio-3.0-asr-flash-streaming", "fun-asr-realtime"}:
            bailian_model = settings.bailian_asr_model
        stored_region = _text(public.get("region"), "cn-beijing").lower()
        return replace(
            settings,
            realtime_provider="bailian",
            bailian_asr_region="cn-beijing",
            bailian_asr_workspace_id=_text(public.get("workspaceId"), settings.bailian_asr_workspace_id),
            bailian_asr_api_key=_text(secrets.get("apiKey"), settings.bailian_asr_api_key),
            bailian_asr_model=bailian_model,
            bailian_asr_hotwords=_text(public.get("hotwords"), settings.bailian_asr_hotwords),
            # 数据库配置一旦存在就完全接管词表；字段缺失表示当前没有词表，
            # 不能回退到部署环境中的旧 ID，避免 Qwen/空热词继续误用旧词表。
            # 旧的新加坡配置生成的词表 ID 不能在北京地域复用，避免把跨地域资源当作当前词表。
            bailian_asr_vocabulary_id=_text(public.get("funVocabularyId"), "") if stored_region == "cn-beijing" else "",
        )
    if provider == "volcengine":
        return replace(
            settings,
            realtime_provider="volcengine",
            volcengine_asr_app_key=_text(secrets.get("apiKey"), settings.volcengine_asr_app_key),
        )

    return replace(
        settings,
        realtime_provider="openai",
        openai_realtime_base_url=settings.openai_realtime_base_url,
        openai_realtime_api_key=_text(secrets.get("apiKey"), settings.openai_realtime_api_key),
        openai_realtime_transcription_model=openai_model,
        # 页面不开放语言配置；留空让 OpenAI 自动识别中英混合语音。
        openai_realtime_language="",
    )


def _apply_rag_override(settings: Settings, public: dict[str, object]) -> Settings:
    return replace(
        settings,
        app_rag_top_k=_bounded_top_k(public.get("topK"), settings.app_rag_top_k, DEFAULT_RAG_TOP_K),
        app_interview_rag_top_k=_bounded_top_k(
            public.get("interviewTopK"),
            settings.app_interview_rag_top_k,
            DEFAULT_RAG_INTERVIEW_TOP_K,
        ),
        app_interview_rag_similarity_threshold=_bounded_float(
            public.get("similarityThreshold"), settings.app_interview_rag_similarity_threshold
        ),
    )


def _apply_smtp_override(settings: Settings, public: dict[str, object], secrets: dict[str, str]) -> Settings:
    port = _positive_int(public.get("port"), settings.mail_port)
    security_mode = _smtp_security_mode(public.get("securityMode"), port, settings.mail_security_mode)
    return replace(
        settings,
        mail_host=_text(public.get("host"), settings.mail_host),
        mail_port=port,
        mail_security_mode=security_mode,
        mail_username=_text(public.get("username"), settings.mail_username),
        mail_authorization_code=_text(secrets.get("authorizationCode"), settings.mail_authorization_code),
    )


def _text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or str(fallback or "").strip()


def _path(value: object, fallback: str) -> str:
    path = _text(value, fallback)
    return path if path.startswith("/") else f"/{path}"


def _positive_int(value: object, fallback: int) -> int:
    try:
        return max(1, int(value)) if value is not None else max(1, int(fallback))
    except (TypeError, ValueError):
        return max(1, int(fallback))


def _bounded_top_k(value: object, fallback: int, maximum: int) -> int:
    try:
        candidate = int(value) if value is not None else int(fallback)
    except (TypeError, ValueError):
        candidate = int(fallback)
    return min(maximum, max(1, candidate))


def _positive_float(value: object, fallback: float, *, minimum: float) -> float:
    try:
        return max(minimum, float(value)) if value is not None else max(minimum, float(fallback))
    except (TypeError, ValueError):
        return max(minimum, float(fallback))


def _bounded_float(value: object, fallback: float) -> float:
    return min(1.0, max(0.0, _positive_float(value, fallback, minimum=0.0)))


def _smtp_security_mode(value: object, port: int, fallback: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"ssl", "starttls", "none"}:
        return mode
    if mode:
        return fallback
    return "ssl" if port == 465 else "starttls"
