import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.shared.constants.rag import (
    DEFAULT_RAG_CHUNK_OVERLAP,
    DEFAULT_RAG_CHUNK_SIZE,
    DEFAULT_RAG_INTERVIEW_TOP_K,
    DEFAULT_RAG_MAX_FILE_SIZE_MB,
    DEFAULT_RAG_SIMILARITY_THRESHOLD,
    DEFAULT_RAG_TIMEOUT_SECONDS,
    DEFAULT_RAG_TOP_K,
)

_DOTENV_LOADED = False


def _load_local_dotenv() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return

    # python-ai-backend/.env 是手写的轻量加载器，不依赖 python-dotenv。
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        _DOTENV_LOADED = True
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value.strip())

    _DOTENV_LOADED = True


def _get_int(name: str, default: int) -> int:
    # 环境变量常常是字符串，这里统一做安全整数转换。
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    # 超时参数允许用浮点数（例如 2.5 秒）。
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_first_non_empty(*names: str, default: str = "") -> str:
    # 支持“专用变量优先，通用变量兜底”的读取顺序。
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


def _normalize_openai_embedding_base_url(base_url: str) -> str:
    """让官方 OpenAI 的 Embedding 地址包含 API 版本前缀。

    Chat 仍使用根地址并由调用路径补上 `/v1`，Embedding SDK 则会直接
    在 base URL 后拼接 `/embeddings`，因此两者不能共用同一个裸根地址。
    其他 OpenAI-compatible 网关保留管理员明确填写的地址，避免破坏已有部署。
    """
    normalized = (base_url or "").strip().rstrip("/")
    if normalized.lower() == "https://api.openai.com":
        return f"{normalized}/v1"
    return normalized


@dataclass(frozen=True)
class Settings:
    # 这里只保留当前 Python 后端实际会用到的配置项。
    server_port: int
    app_cors_allowed_origins: str
    auth_token_secret: str
    auth_token_ttl_seconds: int
    auth_email_code_secret: str
    auth_email_code_cooldown_seconds: int
    auth_email_code_expiry_seconds: int
    auth_email_code_max_failed_attempts: int
    mail_host: str
    mail_port: int
    mail_security_mode: str
    mail_username: str
    mail_authorization_code: str
    mail_connection_timeout_seconds: float
    mail_timeout_seconds: float
    mail_write_timeout_seconds: float
    app_rag_top_k: int
    app_interview_rag_top_k: int
    app_interview_rag_similarity_threshold: float
    app_interview_context_soft_chars: int
    app_interview_context_hard_chars: int
    app_interview_summary_chars: int
    app_interview_rag_timeout_seconds: float
    embedding_provider: str
    embedding_model_name: str
    embedding_base_url: str
    embedding_timeout_seconds: float
    openai_base_url: str
    openai_api_key: str
    openai_chat_model: str
    openai_chat_completions_path: str
    openai_chat_timeout_seconds: float
    openai_embedding_base_url: str
    openai_embedding_api_key: str
    openai_embedding_model: str
    openai_embedding_timeout_seconds: float
    ollama_embedding_base_url: str
    ollama_embedding_model: str
    ollama_embedding_timeout_seconds: float
    vision_provider: str
    openai_vision_model: str
    openai_vision_detail: str
    openai_vision_base_url: str
    openai_vision_api_key: str
    openai_vision_timeout_seconds: float
    openai_realtime_base_url: str
    openai_realtime_api_key: str
    openai_realtime_client_secrets_path: str
    openai_realtime_calls_path: str
    openai_realtime_transcription_model: str
    openai_realtime_language: str
    realtime_provider: str
    realtime_proxy_ticket_redis_url: str
    realtime_proxy_ticket_allow_memory: bool
    bailian_asr_region: str
    bailian_asr_workspace_id: str
    bailian_asr_api_key: str
    bailian_asr_model: str
    bailian_asr_hotwords: str
    bailian_asr_vocabulary_id: str
    volcengine_asr_app_key: str
    volcengine_asr_resource_id: str
    volcengine_asr_request_url: str
    mysql_datasource_url: str
    mysql_datasource_username: str
    mysql_datasource_password: str
    pgvector_datasource_url: str
    pgvector_datasource_username: str
    pgvector_datasource_password: str
    pgvector_connect_timeout_seconds: int
    rag_chunk_size: int
    rag_chunk_overlap: int
    rag_max_file_size_mb: int
    rag_object_storage_endpoint: str
    rag_object_storage_bucket: str
    rag_object_storage_access_key: str
    rag_object_storage_secret_key: str
    rag_object_storage_region: str
    rag_image_enrichment_redis_url: str
    rag_image_enrichment_concurrency: int
    autogen_enabled: bool
    system_config_encryption_key: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_local_dotenv()
    # chat 场景优先使用专用 base_url/api_key，没有时退回通用 OPENAI_*。
    openai_base_url = _get_first_non_empty("OPENAI_CHAT_BASE_URL", "OPENAI_BASE_URL", default="https://api.openai.com")
    openai_api_key = _get_first_non_empty("OPENAI_CHAT_API_KEY", "OPENAI_API_KEY")
    # embedding 允许单独走兼容 OpenAI 的第三方路由。
    openai_embedding_base_url = _normalize_openai_embedding_base_url(_get_first_non_empty(
        "OPENAI_EMBEDDING_BASE_URL",
        "OPENAI_CHAT_BASE_URL",
        "OPENAI_BASE_URL",
        default=openai_base_url,
    ))
    openai_embedding_api_key = _get_first_non_empty(
        "OPENAI_EMBEDDING_API_KEY",
        "OPENAI_CHAT_API_KEY",
        "OPENAI_API_KEY",
        default=openai_api_key,
    )
    # embedding 支持按 provider 切换：openai（默认）或 ollama。
    embedding_provider = _normalize_ai_provider(os.getenv("EMBEDDING_PROVIDER", "openai"))
    ollama_embedding_base_url = _get_first_non_empty(
        "OLLAMA_EMBEDDING_BASE_URL",
        "OLLAMA_BASE_URL",
        default="http://127.0.0.1:11434",
    )
    ollama_embedding_model = (os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text") or "").strip() or "nomic-embed-text"
    ollama_embedding_timeout_seconds = max(1.0, _get_float("OLLAMA_EMBEDDING_TIMEOUT_SECONDS", 45.0))
    openai_embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    openai_embedding_timeout_seconds = max(1.0, _get_float("OPENAI_EMBEDDING_TIMEOUT_SECONDS", 10.0))
    vision_provider = _normalize_ai_provider(os.getenv("VISION_PROVIDER", "openai"))
    realtime_provider = _normalize_realtime_provider(
        _get_first_non_empty("REALTIME_PROVIDER", "REALTIME_ASR_PROVIDER", default="openai")
    )
    bailian_asr_api_key = _get_first_non_empty("BAILIAN_ASR_API_KEY", "DASHSCOPE_API_KEY")
    bailian_asr_model = _get_first_non_empty(
        "BAILIAN_ASR_MODEL",
        "DASHSCOPE_REALTIME_MODEL",
        default="qwen-audio-3.0-asr-flash-streaming",
    )
    if bailian_asr_model not in {"qwen-audio-3.0-asr-flash-streaming", "fun-asr-realtime"}:
        bailian_asr_model = "qwen-audio-3.0-asr-flash-streaming"

    # 统一导出当前生效的 embedding 元信息，供容器装配和日志复用。
    if embedding_provider == "ollama":
        embedding_model_name = ollama_embedding_model
        embedding_base_url = ollama_embedding_base_url
        embedding_timeout_seconds = ollama_embedding_timeout_seconds
    else:
        embedding_model_name = openai_embedding_model
        embedding_base_url = openai_embedding_base_url
        embedding_timeout_seconds = openai_embedding_timeout_seconds

    return Settings(
        server_port=_get_int("SERVER_PORT", 8999),
        app_cors_allowed_origins=os.getenv(
            "APP_CORS_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ),
        auth_token_secret=os.getenv("APP_AUTH_TOKEN_SECRET", "resume-builder-local-demo-auth-secret"),
        auth_token_ttl_seconds=max(300, _get_int("APP_AUTH_TOKEN_TTL_SECONDS", 43_200)),
        auth_email_code_secret=os.getenv("APP_AUTH_EMAIL_CODE_SECRET", ""),
        auth_email_code_cooldown_seconds=max(30, _get_int("APP_AUTH_EMAIL_CODE_COOLDOWN_SECONDS", 60)),
        auth_email_code_expiry_seconds=max(30, _get_int("APP_AUTH_EMAIL_CODE_EXPIRY_SECONDS", 600)),
        auth_email_code_max_failed_attempts=max(1, _get_int("APP_AUTH_EMAIL_CODE_MAX_FAILED_ATTEMPTS", 5)),
        mail_host=os.getenv("MAIL_HOST", "smtp.qq.com"),
        mail_port=max(1, _get_int("MAIL_PORT", 465)),
        mail_security_mode=_normalize_smtp_security_mode(
            os.getenv("MAIL_SECURITY_MODE"),
            max(1, _get_int("MAIL_PORT", 465)),
        ),
        mail_username=os.getenv("MAIL_USERNAME", ""),
        mail_authorization_code=os.getenv("MAIL_AUTHORIZATION_CODE", ""),
        mail_connection_timeout_seconds=max(
            1.0,
            _get_float("MAIL_CONNECTION_TIMEOUT_MILLIS", 10_000.0) / 1_000.0,
        ),
        mail_timeout_seconds=max(1.0, _get_float("MAIL_TIMEOUT_MILLIS", 10_000.0) / 1_000.0),
        mail_write_timeout_seconds=max(
            1.0,
            _get_float("MAIL_WRITE_TIMEOUT_MILLIS", 10_000.0) / 1_000.0,
        ),
        app_rag_top_k=min(DEFAULT_RAG_TOP_K, max(1, _get_int("APP_RAG_TOP_K", DEFAULT_RAG_TOP_K))),
        app_interview_rag_top_k=min(
            DEFAULT_RAG_INTERVIEW_TOP_K,
            max(1, _get_int("APP_INTERVIEW_RAG_TOP_K", DEFAULT_RAG_INTERVIEW_TOP_K)),
        ),
        app_interview_rag_similarity_threshold=_normalize_similarity_threshold(
            _get_float("APP_INTERVIEW_RAG_SIMILARITY_THRESHOLD", DEFAULT_RAG_SIMILARITY_THRESHOLD)
        ),
        app_interview_context_soft_chars=_get_int("APP_INTERVIEW_CONTEXT_SOFT_CHARS", 24000),
        app_interview_context_hard_chars=_get_int("APP_INTERVIEW_CONTEXT_HARD_CHARS", 32000),
        app_interview_summary_chars=_get_int("APP_INTERVIEW_SUMMARY_CHARS", 3000),
        app_interview_rag_timeout_seconds=max(
            0.2,
            _get_float("APP_INTERVIEW_RAG_TIMEOUT_SECONDS", DEFAULT_RAG_TIMEOUT_SECONDS),
        ),
        embedding_provider=embedding_provider,
        embedding_model_name=embedding_model_name,
        embedding_base_url=embedding_base_url,
        embedding_timeout_seconds=embedding_timeout_seconds,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        openai_chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4"),
        openai_chat_completions_path=os.getenv("OPENAI_CHAT_COMPLETIONS_PATH", "/v1/chat/completions"),
        openai_chat_timeout_seconds=max(3.0, _get_float("OPENAI_CHAT_TIMEOUT_SECONDS", 25.0)),
        openai_embedding_base_url=openai_embedding_base_url,
        openai_embedding_api_key=openai_embedding_api_key,
        openai_embedding_model=openai_embedding_model,
        openai_embedding_timeout_seconds=openai_embedding_timeout_seconds,
        ollama_embedding_base_url=ollama_embedding_base_url,
        ollama_embedding_model=ollama_embedding_model,
        ollama_embedding_timeout_seconds=ollama_embedding_timeout_seconds,
        vision_provider=vision_provider,
        openai_vision_model=os.getenv("OPENAI_VISION_MODEL", "gpt-4.1"),
        openai_vision_detail=os.getenv("OPENAI_VISION_DETAIL", "high"),
        openai_vision_base_url=_get_first_non_empty("OPENAI_VISION_BASE_URL", "OPENAI_CHAT_BASE_URL", "OPENAI_BASE_URL", default=openai_base_url),
        openai_vision_api_key=_get_first_non_empty("OPENAI_VISION_API_KEY", "OPENAI_CHAT_API_KEY", "OPENAI_API_KEY", default=openai_api_key),
        openai_vision_timeout_seconds=max(3.0, _get_float("OPENAI_VISION_TIMEOUT_SECONDS", 40.0)),
        openai_realtime_base_url=_get_first_non_empty("OPENAI_REALTIME_BASE_URL", "OPENAI_BASE_URL", default=openai_base_url),
        openai_realtime_api_key=_get_first_non_empty("OPENAI_REALTIME_API_KEY", "OPENAI_API_KEY", default=openai_api_key),
        # OpenAI Realtime 内部端点由后端固定，避免部署环境覆盖协议路径。
        openai_realtime_client_secrets_path="/v1/realtime/client_secrets",
        openai_realtime_calls_path="/v1/realtime/calls",
        openai_realtime_transcription_model=os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
        # 不固定语言提示，交给上游自动判断中文为主的中英混合语音。
        # 旧的 OPENAI_REALTIME_LANGUAGE 环境变量不再参与配置，避免遗留的 zh
        # 设置把中英混合语音错误地固定为单一语言。
        openai_realtime_language="",
        realtime_provider=realtime_provider,
        realtime_proxy_ticket_redis_url=(os.getenv("REALTIME_PROXY_TICKET_REDIS_URL", "") or "").strip(),
        realtime_proxy_ticket_allow_memory=(
            os.getenv("REALTIME_PROXY_TICKET_ALLOW_MEMORY", "false") or ""
        ).strip().lower() in {"1", "true", "yes", "on"},
        # 百炼实时语音固定使用华北 2（北京），不再从环境变量读取地域。
        bailian_asr_region="cn-beijing",
        bailian_asr_workspace_id=(os.getenv("BAILIAN_ASR_WORKSPACE_ID", "") or "").strip(),
        bailian_asr_api_key=bailian_asr_api_key,
        bailian_asr_model=bailian_asr_model,
        bailian_asr_hotwords=(os.getenv("BAILIAN_ASR_HOTWORDS", "") or "").strip(),
        bailian_asr_vocabulary_id=(os.getenv("BAILIAN_ASR_VOCABULARY_ID", "") or "").strip(),
        volcengine_asr_app_key=_get_first_non_empty("VOLCENGINE_ASR_APP_KEY", "VOLCENGINE_ASR_API_KEY"),
        volcengine_asr_resource_id=(os.getenv("VOLCENGINE_ASR_RESOURCE_ID", "volc.seedasr.sauc.duration") or "volc.seedasr.sauc.duration").strip(),
        volcengine_asr_request_url=(os.getenv("VOLCENGINE_ASR_REQUEST_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async") or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async").strip(),
        mysql_datasource_url=os.getenv("MYSQL_DATASOURCE_URL", ""),
        mysql_datasource_username=os.getenv("MYSQL_DATASOURCE_USERNAME", ""),
        mysql_datasource_password=os.getenv("MYSQL_DATASOURCE_PASSWORD", ""),
        pgvector_datasource_url=os.getenv("PGVECTOR_DATASOURCE_URL", ""),
        pgvector_datasource_username=os.getenv("PGVECTOR_DATASOURCE_USERNAME", ""),
        pgvector_datasource_password=os.getenv("PGVECTOR_DATASOURCE_PASSWORD", ""),
        pgvector_connect_timeout_seconds=max(1, _get_int("PGVECTOR_CONNECT_TIMEOUT_SECONDS", 8)),
        rag_chunk_size=_get_int("RAG_CHUNK_SIZE", DEFAULT_RAG_CHUNK_SIZE),
        rag_chunk_overlap=_get_int("RAG_CHUNK_OVERLAP", DEFAULT_RAG_CHUNK_OVERLAP),
        rag_max_file_size_mb=_get_int("RAG_MAX_FILE_SIZE_MB", DEFAULT_RAG_MAX_FILE_SIZE_MB),
        rag_object_storage_endpoint=(os.getenv("RAG_OBJECT_STORAGE_ENDPOINT", "http://127.0.0.1:9000") or "").strip().rstrip("/"),
        rag_object_storage_bucket=(os.getenv("RAG_OBJECT_STORAGE_BUCKET", "resume-builder-rag") or "resume-builder-rag").strip(),
        rag_object_storage_access_key=(os.getenv("RAG_OBJECT_STORAGE_ACCESS_KEY", "") or "").strip(),
        rag_object_storage_secret_key=(os.getenv("RAG_OBJECT_STORAGE_SECRET_KEY", "") or "").strip(),
        rag_object_storage_region=(os.getenv("RAG_OBJECT_STORAGE_REGION", "us-east-1") or "us-east-1").strip(),
        rag_image_enrichment_redis_url=(
            os.getenv("RAG_IMAGE_ENRICHMENT_REDIS_URL", "redis://127.0.0.1:6379/0") or ""
        ).strip(),
        rag_image_enrichment_concurrency=min(
            3,
            max(1, _get_int("RAG_IMAGE_ENRICHMENT_CONCURRENCY", 3)),
        ),
        autogen_enabled=os.getenv("AUTOGEN_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        system_config_encryption_key=(os.getenv("APP_SYSTEM_CONFIG_ENCRYPTION_KEY") or "").strip(),
    )


def _normalize_ai_provider(raw_provider: str | None) -> str:
    provider = (raw_provider or "").strip().lower()
    if provider in {"openai", "ollama"}:
        return provider
    return "openai"


def _normalize_realtime_provider(raw_provider: str | None) -> str:
    provider = (raw_provider or "").strip().lower()
    if provider in {"openai", "bailian", "volcengine"}:
        return provider
    # 兼容旧版本曾使用的 dashscope 名称。
    if provider in {"dashscope", "aliyun", "qwen"}:
        return "bailian"
    return "openai"


def _normalize_similarity_threshold(raw_value: float) -> float:
    try:
        threshold = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_RAG_SIMILARITY_THRESHOLD
    if threshold < 0.0:
        return 0.0
    if threshold > 1.0:
        return 1.0
    return threshold


def _normalize_smtp_security_mode(raw_mode: str | None, port: int) -> str:
    """统一 SMTP 加密方式；旧配置缺少字段时按端口保持兼容。"""
    mode = str(raw_mode or "").strip().lower()
    if mode in {"ssl", "starttls", "none"}:
        return mode
    return "ssl" if int(port or 0) == 465 else "starttls"
