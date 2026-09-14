from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SystemServiceDefinition:
    key: str
    label: str
    secret_names: tuple[str, ...] = ()


SERVICE_DEFINITIONS: tuple[SystemServiceDefinition, ...] = (
    SystemServiceDefinition("embedding", "知识库 Embedding", ("apiKey",)),
    SystemServiceDefinition("chat", "聊天模型", ("apiKey",)),
    SystemServiceDefinition("vision", "图片 OCR", ("apiKey",)),
    SystemServiceDefinition("realtime", "实时语音", ("apiKey",)),
    SystemServiceDefinition("rag", "RAG 检索", ()),
    SystemServiceDefinition("smtp", "SMTP 邮件", ("authorizationCode",)),
)
SERVICE_KEYS = tuple(item.key for item in SERVICE_DEFINITIONS)
SERVICE_LABELS = {item.key: item.label for item in SERVICE_DEFINITIONS}
SERVICE_SECRET_NAMES = {item.key: item.secret_names for item in SERVICE_DEFINITIONS}


@dataclass(frozen=True, slots=True)
class StoredSystemServiceConfig:
    service_key: str
    public_config: dict[str, Any]
    secrets: dict[str, str]
    version: int
    last_validation_status: str
    last_validation_message: str
    last_validated_at: datetime | None
    updated_by: str


@dataclass(frozen=True, slots=True)
class SystemServiceSnapshot:
    service_key: str
    source: str
    version: int
    public_config: dict[str, Any]
    secret_states: dict[str, dict[str, Any]]
    status: str
    validation_message: str
    validated_at: datetime | None
    embedding_profile: str | None = None
    requires_rebuild: bool = False
    # 仅供管理员页面在切换到 Ollama 时选择正确的部署地址；不包含任何凭据。
    ollama_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class SystemServiceTestResult:
    service_key: str
    success: bool
    message: str
    elapsed_ms: int


def service_label(service_key: str) -> str:
    return SERVICE_LABELS.get(service_key, service_key)


def is_known_service_key(service_key: str) -> bool:
    return service_key in SERVICE_KEYS
