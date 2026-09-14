from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import replace
from math import isfinite
from typing import Any, Callable
from urllib.parse import urlsplit

from app.application.ports.system_service_config_repository import SystemServiceConfigRepository
from app.domain.exceptions.auth_exceptions import (
    SystemConfigConflictError,
    SystemConfigUpstreamError,
    SystemConfigValidationError,
)
from app.domain.models.system_service_config import (
    SERVICE_KEYS,
    SERVICE_SECRET_NAMES,
    StoredSystemServiceConfig,
    SystemServiceSnapshot,
    SystemServiceTestResult,
    is_known_service_key,
)
from app.application.ports.vector_store_port import VectorStorePort
from app.infrastructure.config.runtime_settings_provider import embedding_profile_id
from app.infrastructure.config.settings import Settings
from app.infrastructure.security.system_config_cipher import mask_secret, normalize_secret_value
from app.infrastructure.system_config.service_testers import SystemServiceTester
from app.infrastructure.realtime.vocabulary_client import BailianVocabularyClient
from app.shared.constants.rag import DEFAULT_RAG_INTERVIEW_TOP_K, DEFAULT_RAG_TOP_K
from app.domain.policies.bailian_hotword_policy import (
    BailianHotwordValidationError,
    normalize_bailian_hotwords,
)


# 超时是部署运行参数，不允许由管理页面覆盖。该表也用于过滤旧版本已经写入
# 数据库的字段，确保升级后它们不会重新出现在页面或运行时配置中。
_DEPLOYMENT_ONLY_TIMEOUT_FIELDS: dict[str, frozenset[str]] = {
    "embedding": frozenset({"timeoutSeconds"}),
    "chat": frozenset({"timeoutSeconds"}),
    "vision": frozenset({"timeoutSeconds"}),
    "rag": frozenset({"timeoutSeconds"}),
    "smtp": frozenset({"connectionTimeoutSeconds", "timeoutSeconds", "writeTimeoutSeconds"}),
}

_HIDDEN_REALTIME_CONFIG_FIELDS = frozenset({"funVocabularyId"})
_REALTIME_MODELS = frozenset({"qwen-audio-3.0-asr-flash-streaming", "fun-asr-realtime"})
_LOGGER = logging.getLogger("uvicorn.error")


class SystemServiceConfigService:
    def __init__(
        self,
        *,
        base_settings: Settings,
        settings_provider: Callable[[], Settings],
        repository: SystemServiceConfigRepository,
        tester: SystemServiceTester | None = None,
        vector_store_factory: Callable[[Settings], VectorStorePort] | None = None,
    ) -> None:
        self._base_settings = base_settings
        self._settings_provider = settings_provider
        self._repository = repository
        self._tester = tester or SystemServiceTester()
        self._vector_store_factory = vector_store_factory

    def list_snapshots(self) -> list[SystemServiceSnapshot]:
        overrides = {item.service_key: item for item in self._repository.list_configs()}
        runtime_settings = self._settings_provider()
        return [self._snapshot_for(key, overrides.get(key), runtime_settings) for key in SERVICE_KEYS]

    def test_service(
        self,
        *,
        service_key: str,
        public_config: dict[str, Any] | None,
        secrets: dict[str, str] | None,
    ) -> SystemServiceTestResult:
        self._ensure_known_service(service_key)
        stored = self._repository.get_config(service_key)
        draft_config, draft_secrets = self._normalize_draft(
            service_key,
            public_config or {},
            secrets or {},
            stored=stored,
        )
        outcome = self._tester.test(
            service_key=service_key,
            public_config=draft_config,
            secrets=draft_secrets,
            # RAG 测试需要使用当前已生效的 Embedding/pgvector 配置；其他服务
            # 也沿用同一份运行时快照，避免数据库覆盖存在时测试到旧部署默认值。
            settings=self._settings_provider(),
        )
        return SystemServiceTestResult(
            service_key=service_key,
            success=outcome.success,
            message=outcome.message,
            elapsed_ms=outcome.elapsed_ms,
        )

    def save_service(
        self,
        *,
        service_key: str,
        public_config: dict[str, Any] | None,
        secrets: dict[str, str] | None,
        expected_version: int,
        actor_user_id: str,
    ) -> SystemServiceSnapshot:
        self._ensure_known_service(service_key)
        if expected_version < 0:
            raise SystemConfigValidationError("系统服务配置版本无效")
        stored = self._repository.get_config(service_key)
        current_version = stored.version if stored is not None else 0
        if current_version != expected_version:
            raise SystemConfigConflictError("系统服务配置已被其他管理员更新，请刷新后重试")

        normalized_config, normalized_secrets = self._normalize_draft(
            service_key,
            public_config or {},
            secrets or {},
            stored=stored,
        )
        if service_key == "rag":
            # RAG 保存只依赖草稿字段校验；页面没有独立测试按钮，保存也不触发
            # Embedding 请求或 pgvector 查询，避免把外部服务探测混入配置落库流程。
            validation_message = "RAG 配置字段校验通过"
            validation_elapsed_ms = 0
        else:
            outcome = self._tester.test(
                service_key=service_key,
                public_config=normalized_config,
                secrets=normalized_secrets,
                settings=self._settings_provider(),
            )
            if not outcome.success:
                raise SystemConfigUpstreamError(outcome.message)
            validation_message = outcome.message
            validation_elapsed_ms = outcome.elapsed_ms

        old_vocabulary_id = ""
        new_vocabulary_id = ""
        old_vocabulary_config: dict[str, Any] = {}
        old_vocabulary_secrets: dict[str, str] = {}
        if service_key == "realtime":
            if stored is not None:
                old_vocabulary_config = dict(stored.public_config)
                old_vocabulary_secrets = dict(stored.secrets)
            normalized_config, old_vocabulary_id, new_vocabulary_id = self._prepare_fun_vocabulary(
                normalized_config,
                normalized_secrets,
                stored,
            )

        try:
            saved = self._repository.save_config(
                service_key=service_key,
                public_config=normalized_config,
                secrets=normalized_secrets,
                expected_version=expected_version,
                validation_status="success",
                validation_message=validation_message,
                validated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                updated_by=actor_user_id,
                audit_detail=(
                    f"{service_key} 配置字段校验通过并启用"
                    if service_key == "rag"
                    else f"{service_key} 配置测试通过并启用，耗时 {validation_elapsed_ms}ms"
                ),
            )
        except Exception:
            if new_vocabulary_id:
                self._delete_fun_vocabulary(normalized_config, normalized_secrets, new_vocabulary_id)
            raise
        if old_vocabulary_id and old_vocabulary_id != new_vocabulary_id:
            # 旧词表属于旧 Workspace/API Key，删除时使用旧快照，避免管理员
            # 同时更换凭据后无法清理旧资源。
            self._delete_fun_vocabulary(
                old_vocabulary_config or normalized_config,
                old_vocabulary_secrets or normalized_secrets,
                old_vocabulary_id,
            )
        runtime_settings = self._settings_provider()
        return self._snapshot_for(service_key, saved, runtime_settings)

    def _prepare_fun_vocabulary(
        self,
        config: dict[str, Any],
        secrets: dict[str, str],
        stored: StoredSystemServiceConfig | None,
    ) -> tuple[dict[str, Any], str, str]:
        old_id = str((stored.public_config if stored else {}).get("funVocabularyId") or "").strip()
        if str(config.get("provider") or "") != "bailian":
            # 切换到 OpenAI/豆包后仍清理之前由 Fun-ASR 创建的词表。
            return config, old_id, ""
        model = str(config.get("model") or "")
        hotwords = str(config.get("hotwords") or "")
        if model != "fun-asr-realtime":
            config.pop("funVocabularyId", None)
            return config, old_id, ""
        old_hotwords = str((stored.public_config if stored else {}).get("hotwords") or "")
        old_region = str((stored.public_config if stored else {}).get("region") or "").strip().lower()
        old_workspace = str((stored.public_config if stored else {}).get("workspaceId") or "").strip()
        current_region = str(config.get("region") or self._base_settings.bailian_asr_region).strip().lower()
        current_workspace = str(config.get("workspaceId") or self._base_settings.bailian_asr_workspace_id).strip()
        if old_id and old_hotwords == hotwords and old_region == current_region and old_workspace == current_workspace:
            config["funVocabularyId"] = old_id
            return config, old_id, old_id
        if not hotwords.strip():
            config.pop("funVocabularyId", None)
            return config, old_id, ""
        candidate_settings = replace(
            self._base_settings,
            bailian_asr_region=str(config.get("region") or self._base_settings.bailian_asr_region),
            bailian_asr_workspace_id=str(config.get("workspaceId") or self._base_settings.bailian_asr_workspace_id),
            bailian_asr_api_key=str(secrets.get("apiKey") or self._base_settings.bailian_asr_api_key),
        )
        try:
            new_id = BailianVocabularyClient(
                settings=candidate_settings,
                api_key=candidate_settings.bailian_asr_api_key,
            ).create(hotwords, target_model=model)
        except BailianHotwordValidationError as exc:
            raise SystemConfigValidationError(str(exc)) from None
        except Exception as exc:
            raise SystemConfigUpstreamError("百炼热词表同步失败，当前配置未改变") from exc
        config["funVocabularyId"] = new_id
        return config, old_id, new_id

    def _delete_fun_vocabulary(self, config: dict[str, Any], secrets: dict[str, str], vocabulary_id: str) -> None:
        try:
            # 删除失败不影响已保存配置；不记录词表 ID，避免其出现在日志或审计中。
            settings = replace(
                self._base_settings,
                bailian_asr_region=str(config.get("region") or self._base_settings.bailian_asr_region),
                bailian_asr_workspace_id=str(config.get("workspaceId") or self._base_settings.bailian_asr_workspace_id),
                bailian_asr_api_key=str(secrets.get("apiKey") or ""),
            )
            BailianVocabularyClient(settings=settings, api_key=settings.bailian_asr_api_key).delete(vocabulary_id)
        except Exception:
            return

    def restore_service(
        self,
        *,
        service_key: str,
        expected_version: int,
        actor_user_id: str,
    ) -> SystemServiceSnapshot:
        self._ensure_known_service(service_key)
        if expected_version < 0:
            raise SystemConfigValidationError("系统服务配置版本无效")
        stored = self._repository.get_config(service_key)
        self._repository.delete_config(
            service_key=service_key,
            expected_version=expected_version,
            updated_by=actor_user_id,
            audit_detail=f"{service_key} 已恢复部署默认配置",
        )
        if service_key == "realtime" and stored is not None:
            old_id = str(stored.public_config.get("funVocabularyId") or "").strip()
            if old_id:
                self._delete_fun_vocabulary(stored.public_config, stored.secrets, old_id)
        runtime_settings = self._settings_provider()
        return self._snapshot_for(service_key, None, runtime_settings)

    def _snapshot_for(
        self,
        service_key: str,
        stored: StoredSystemServiceConfig | None,
        runtime_settings: Settings,
    ) -> SystemServiceSnapshot:
        source = "database" if stored is not None else "deployment"
        public_config = self._merge_public_config(service_key, stored.public_config if stored else {}, self._base_settings)
        # 兼容早期误把凭据放进 public_config 的数据，禁止它们随查询接口返回。
        public_secret_names = set(SERVICE_SECRET_NAMES.get(service_key, ())) | {
            "api_key",
            "appKey",
            "app_key",
            "authorization_code",
        }
        public_config = {
            key: value for key, value in public_config.items() if key not in public_secret_names
        }
        if service_key == "realtime":
            # Fun 的词表 ID 只供服务端运行时使用，永远不返回管理员页面。
            public_config = {key: value for key, value in public_config.items() if key not in _HIDDEN_REALTIME_CONFIG_FIELDS}
        secrets = stored.secrets if stored is not None else self._environment_secrets(service_key, self._base_settings)
        secret_states = {name: mask_secret(secrets.get(name)) for name in SERVICE_SECRET_NAMES.get(service_key, ())}
        status = stored.last_validation_status if stored is not None else "unknown"
        message = stored.last_validation_message if stored is not None else "使用部署环境变量"
        validated_at = stored.last_validated_at if stored is not None else None
        profile = None
        requires_rebuild = False
        if service_key == "embedding":
            provider = str(public_config.get("provider") or "openai")
            model = str(public_config.get("model") or runtime_settings.embedding_model_name)
            base_url = str(public_config.get("baseUrl") or runtime_settings.embedding_base_url)
            base_signature = f"{self._base_settings.embedding_provider}|{self._base_settings.embedding_base_url.rstrip('/').lower()}|{self._base_settings.embedding_model_name}"
            profile = embedding_profile_id(
                provider=provider,
                base_url=base_url,
                model=model,
                base_profile=base_signature,
            )
            # 数据库配置与部署默认值不同，不代表已有向量一定不兼容；是否需要
            # 重建必须以 pgvector 中实际保存的模型和维度元数据为准。
            if self._vector_store_factory is not None and str(runtime_settings.pgvector_datasource_url or "").strip():
                try:
                    vector_store = self._vector_store_factory(runtime_settings)
                    inspector = getattr(vector_store, "has_incompatible_embedding_profiles", None)
                    if callable(inspector):
                        requires_rebuild = bool(inspector())
                except Exception as exc:
                    # 兼容性提示不能让管理员配置接口变成 500；真实检索时
                    # 仍会返回脱敏的 pgvector 错误。日志只记录异常类型。
                    _LOGGER.warning(
                        "[系统服务配置] Embedding 向量兼容性检查失败：%s",
                        type(exc).__name__,
                    )

        # Ollama 的宿主地址随部署方式变化：裸 Python 使用环境中的本机地址，
        # Docker Compose 则由 OLLAMA_EMBEDDING_BASE_URL 注入 host.docker.internal。
        # 将这个非敏感的部署默认值随快照返回，前端切换供应商时不再猜测运行环境。
        ollama_base_url = (
            self._base_settings.ollama_embedding_base_url
            if service_key in {"embedding", "chat", "vision"}
            else None
        )

        return SystemServiceSnapshot(
            service_key=service_key,
            source=source,
            version=stored.version if stored is not None else 0,
            public_config=public_config,
            secret_states=secret_states,
            status=status,
            validation_message=message,
            validated_at=validated_at,
            embedding_profile=profile,
            requires_rebuild=requires_rebuild,
            ollama_base_url=ollama_base_url,
        )

    def _normalize_draft(
        self,
        service_key: str,
        incoming_config: dict[str, Any],
        incoming_secrets: dict[str, str],
        *,
        stored: StoredSystemServiceConfig | None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        if not isinstance(incoming_config, dict) or not isinstance(incoming_secrets, dict):
            raise SystemConfigValidationError("系统服务配置格式不正确")
        allowed_config = set(self._default_public_config(service_key, self._base_settings))
        if service_key == "realtime":
            # 不同实时供应商的配置字段互斥，但允许一次请求切换供应商。
            allowed_config.update({"provider", "transcriptionModel", "region", "workspaceId", "model", "hotwords"})
            # 词表 ID 由服务端创建和维护，禁止浏览器提交或覆盖。
            allowed_config -= _HIDDEN_REALTIME_CONFIG_FIELDS
        if service_key == "vision":
            # detail 仅在 OpenAI 兼容模式下使用；即使当前默认是 Ollama，也要允许切回
            # OpenAI 后一并提交该字段。
            allowed_config.add("detail")
        if service_key == "rag":
            # 这三个字段与既有向量数据和 Nginx 限制绑定，只读展示，不接受网页修改。
            allowed_config -= {"chunkSize", "chunkOverlap", "maxFileSizeMb"}
        unknown_config = set(incoming_config) - allowed_config
        if unknown_config:
            raise SystemConfigValidationError("请求包含不支持的系统服务配置项")
        allowed_secrets = set(SERVICE_SECRET_NAMES.get(service_key, ()))
        unknown_secrets = set(incoming_secrets) - allowed_secrets
        if unknown_secrets:
            raise SystemConfigValidationError("请求包含不支持的系统服务密钥项")

        current_public = self._merge_public_config(
            service_key,
            stored.public_config if stored else {},
            self._base_settings,
        )
        raw_current_secrets = stored.secrets if stored is not None else self._environment_secrets(service_key, self._base_settings)
        # 旧版本或旧环境文件可能保存过模板凭据；在合并草稿时也按未配置处理，
        # 这样管理员留空保存不会继续沿用无效示例值。
        current_secrets: dict[str, str] = {}
        for name, value in raw_current_secrets.items():
            normalized_secret = normalize_secret_value(value)
            if normalized_secret:
                current_secrets[name] = normalized_secret
        merged_public = {**current_public, **incoming_config}
        merged_secrets = dict(current_secrets)
        for name, value in incoming_secrets.items():
            safe_value = str(value or "").strip()
            if safe_value:
                merged_secrets[name] = safe_value

        normalized_public = self._validate_public_config(service_key, merged_public)
        if service_key == "realtime":
            previous_provider = str(current_public.get("provider") or "openai").strip().lower()
            current_provider = str(normalized_public.get("provider") or "openai").strip().lower()
            if previous_provider != current_provider and not str(incoming_secrets.get("apiKey") or "").strip():
                # 切换供应商时不能把旧 OpenAI/百炼密钥误当成新供应商凭据。
                merged_secrets.pop("apiKey", None)
        uses_local_ollama = service_key in {"embedding", "chat", "vision"} and normalized_public.get("provider") == "ollama"
        requires_api_key = not uses_local_ollama
        if uses_local_ollama:
            # 切到本地 Ollama 后不再保留此前 OpenAI 兼容服务的无关密钥。
            merged_secrets.pop("apiKey", None)
        for secret_name in allowed_secrets:
            if requires_api_key and not str(merged_secrets.get(secret_name) or "").strip():
                raise SystemConfigValidationError(f"{service_key} 服务密钥未配置")
        return normalized_public, merged_secrets

    def _validate_public_config(self, service_key: str, config: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(config)
        if service_key == "embedding":
            provider = str(normalized.get("provider") or "openai").strip().lower()
            if provider in {"qwen", "dashscope", "qwen-dashscope"}:
                # 兼容早期 API 调用；千问遵循 OpenAI 兼容协议，不再作为独立接入方式。
                provider = "openai"
            if provider not in {"openai", "ollama"}:
                raise SystemConfigValidationError("Embedding 供应商不受支持")
            normalized["provider"] = provider
            normalized["model"] = _required_text(normalized.get("model"), "Embedding 模型")
            normalized["baseUrl"] = _validate_url(normalized.get("baseUrl"), "Embedding Base URL")
            return normalized
        if service_key == "chat":
            provider = str(normalized.get("provider") or "openai").strip().lower()
            if provider not in {"openai", "ollama"}:
                raise SystemConfigValidationError("聊天模型接入方式不受支持")
            normalized["provider"] = provider
            normalized["baseUrl"] = _validate_url(normalized.get("baseUrl"), "聊天 Base URL")
            normalized["model"] = _required_text(normalized.get("model"), "聊天模型")
            normalized["completionsPath"] = _path(normalized.get("completionsPath"), "/v1/chat/completions")
            return normalized
        if service_key == "vision":
            provider = str(normalized.get("provider") or "openai").strip().lower()
            if provider not in {"openai", "ollama"}:
                raise SystemConfigValidationError("图片 OCR 接入方式不受支持")
            normalized["provider"] = provider
            normalized["baseUrl"] = _validate_url(normalized.get("baseUrl"), "OCR Base URL")
            normalized["model"] = _required_text(normalized.get("model"), "OCR 模型")
            if provider == "ollama":
                # Ollama 的图片输入接口没有 OpenAI 的 detail 参数，不能传给本地模型。
                normalized.pop("detail", None)
                return normalized
            detail = str(normalized.get("detail") or "high").strip().lower()
            if detail not in {"low", "auto", "high"}:
                raise SystemConfigValidationError("OCR detail 只能是 low、auto 或 high")
            normalized["detail"] = detail
            return normalized
        if service_key == "realtime":
            provider = str(normalized.get("provider") or "openai").strip().lower()
            if provider in {"dashscope", "aliyun", "qwen"}:
                provider = "bailian"
            if provider not in {"openai", "bailian", "volcengine"}:
                raise SystemConfigValidationError("实时语音接入方式不受支持")
            normalized["provider"] = provider
            # 旧版本页面曾保存过内部路径和 language；升级后不再接受或展示这些字段。
            for obsolete in ("clientSecretsPath", "callsPath", "language"):
                normalized.pop(obsolete, None)
            if provider == "openai":
                normalized["transcriptionModel"] = _required_text(normalized.get("transcriptionModel"), "识别模型")
                return {key: value for key, value in normalized.items() if key in {"provider", "transcriptionModel"}}
            if provider == "bailian":
                region = str(normalized.get("region") or "cn-beijing").strip().lower()
                if region != "cn-beijing":
                    raise SystemConfigValidationError("百炼地域固定为华北 2（北京）")
                normalized["region"] = "cn-beijing"
                normalized["workspaceId"] = _required_text(normalized.get("workspaceId"), "百炼 Workspace ID")
                model = _required_text(normalized.get("model"), "百炼 ASR 模型")
                if model not in _REALTIME_MODELS:
                    raise SystemConfigValidationError("百炼 ASR 模型不受支持")
                normalized["model"] = model
                normalized["hotwords"] = _normalize_hotwords(
                    normalized.get("hotwords"),
                    model=model,
                )
                # 词表 ID 只能由服务端根据当前 Workspace、地域和热词同步生成，
                # 不接受浏览器提交的隐藏字段，避免伪造或复用其他业务空间的词表。
                normalized.pop("funVocabularyId", None)
                return {key: value for key, value in normalized.items() if key in {"provider", "region", "workspaceId", "model", "hotwords"}}
            return {"provider": "volcengine"}
        if service_key == "rag":
            normalized["topK"] = _integer(
                normalized.get("topK"),
                "默认 TopK",
                minimum=1,
                maximum=DEFAULT_RAG_TOP_K,
            )
            normalized["interviewTopK"] = _integer(
                normalized.get("interviewTopK"),
                "面试 TopK",
                minimum=1,
                maximum=DEFAULT_RAG_INTERVIEW_TOP_K,
            )
            normalized["similarityThreshold"] = _number(normalized.get("similarityThreshold"), "相似度阈值", minimum=0.0, maximum=1.0)
            return normalized
        if service_key == "smtp":
            normalized["host"] = _required_text(normalized.get("host"), "SMTP 主机")
            normalized["port"] = _integer(normalized.get("port"), "SMTP 端口", minimum=1, maximum=65535)
            normalized["securityMode"] = _normalize_smtp_security_mode(
                normalized.get("securityMode"),
                normalized["port"],
            )
            normalized["username"] = _required_text(normalized.get("username"), "SMTP 邮箱账号")
            return normalized
        raise SystemConfigValidationError("不支持的系统服务")

    @staticmethod
    def _ensure_known_service(service_key: str) -> None:
        if not is_known_service_key(service_key):
            raise SystemConfigValidationError("不支持的系统服务")

    @staticmethod
    def _environment_secrets(service_key: str, settings: Settings) -> dict[str, str]:
        if service_key in {"embedding", "chat", "vision", "realtime"}:
            if service_key == "embedding" and settings.embedding_provider == "ollama":
                return {}
            if service_key == "vision" and settings.vision_provider == "ollama":
                return {}
            key = {
                "embedding": settings.openai_embedding_api_key,
                "chat": settings.openai_api_key,
                "vision": settings.openai_vision_api_key,
                "realtime": (
                    settings.bailian_asr_api_key
                    if settings.realtime_provider == "bailian"
                    else settings.volcengine_asr_app_key
                    if settings.realtime_provider == "volcengine"
                    else settings.openai_realtime_api_key
                ),
            }[service_key]
            key = normalize_secret_value(key)
            return {"apiKey": key} if key else {}
        if service_key == "smtp":
            code = normalize_secret_value(settings.mail_authorization_code)
            return {"authorizationCode": code} if code else {}
        return {}

    @classmethod
    def _default_public_config(cls, service_key: str, settings: Settings) -> dict[str, Any]:
        if service_key == "embedding":
            provider = settings.embedding_provider if settings.embedding_provider in {"openai", "ollama"} else "openai"
            if provider == "ollama":
                return {
                    "provider": "ollama",
                    "model": settings.ollama_embedding_model,
                    "baseUrl": settings.ollama_embedding_base_url,
                }
            return {
                "provider": "openai",
                "model": settings.openai_embedding_model,
                "baseUrl": settings.openai_embedding_base_url,
            }
        if service_key == "chat":
            return {
                "provider": "openai",
                "baseUrl": settings.openai_base_url,
                "model": settings.openai_chat_model,
                "completionsPath": settings.openai_chat_completions_path,
            }
        if service_key == "vision":
            config = {
                "provider": settings.vision_provider if settings.vision_provider in {"openai", "ollama"} else "openai",
                "baseUrl": settings.openai_vision_base_url,
                "model": settings.openai_vision_model,
            }
            if config["provider"] != "ollama":
                config["detail"] = settings.openai_vision_detail
            return config
        if service_key == "realtime":
            provider = settings.realtime_provider
            if provider == "bailian":
                return {
                    "provider": "bailian",
                    "region": "cn-beijing",
                    "workspaceId": settings.bailian_asr_workspace_id,
                    "model": settings.bailian_asr_model,
                    "hotwords": settings.bailian_asr_hotwords,
                }
            if provider == "volcengine":
                return {"provider": "volcengine"}
            return {
                "provider": "openai",
                "transcriptionModel": settings.openai_realtime_transcription_model,
            }
        if service_key == "rag":
            return {
                "topK": settings.app_rag_top_k,
                "interviewTopK": settings.app_interview_rag_top_k,
                "similarityThreshold": settings.app_interview_rag_similarity_threshold,
                "chunkSize": settings.rag_chunk_size,
                "chunkOverlap": settings.rag_chunk_overlap,
                "maxFileSizeMb": settings.rag_max_file_size_mb,
            }
        if service_key == "smtp":
            return {
                "host": settings.mail_host,
                "port": settings.mail_port,
                "securityMode": settings.mail_security_mode,
                "username": settings.mail_username,
            }
        return {}

    @classmethod
    def _merge_public_config(cls, service_key: str, stored: dict[str, Any], settings: Settings) -> dict[str, Any]:
        merged = cls._default_public_config(service_key, settings)
        deployment_only_fields = _DEPLOYMENT_ONLY_TIMEOUT_FIELDS.get(service_key, frozenset())
        for key, value in stored.items():
            if value is not None and key not in deployment_only_fields:
                merged[key] = value
        if service_key == "rag":
            # 兼容旧数据库中可能保存的 100 或其他较大值；读取时先收敛到当前
            # 业务上限，避免旧配置继续绕过页面和运行时边界。
            merged["topK"] = _clamp_integer(
                merged.get("topK"),
                settings.app_rag_top_k,
                minimum=1,
                maximum=DEFAULT_RAG_TOP_K,
            )
            merged["interviewTopK"] = _clamp_integer(
                merged.get("interviewTopK"),
                settings.app_interview_rag_top_k,
                minimum=1,
                maximum=DEFAULT_RAG_INTERVIEW_TOP_K,
            )
        if service_key == "embedding":
            # 兼容早期开发版本曾保存的未生效字段，避免继续出现在管理页面或后续请求中。
            merged.pop("region", None)
            merged.pop("workspaceId", None)
            if str(merged.get("provider") or "").strip().lower() in {"qwen", "dashscope", "qwen-dashscope"}:
                merged["provider"] = "openai"
        if service_key == "realtime":
            provider = str(merged.get("provider") or settings.realtime_provider or "openai").strip().lower()
            if provider in {"dashscope", "aliyun", "qwen"}:
                provider = "bailian"
            if provider == "bailian":
                stored_region = str(stored.get("region") or "cn-beijing").strip().lower()
                vocabulary_id = merged.get("funVocabularyId") if stored_region == "cn-beijing" else ""
                return {
                    "provider": "bailian",
                    "region": "cn-beijing",
                    "workspaceId": str(merged.get("workspaceId") or settings.bailian_asr_workspace_id),
                    "model": str(merged.get("model") or settings.bailian_asr_model),
                    "hotwords": str(merged.get("hotwords") or settings.bailian_asr_hotwords),
                    **({"funVocabularyId": vocabulary_id} if vocabulary_id else {}),
                }
            if provider == "volcengine":
                return {"provider": "volcengine"}
            return {
                "provider": "openai",
                "transcriptionModel": str(merged.get("transcriptionModel") or settings.openai_realtime_transcription_model),
            }
        if service_key == "smtp":
            # 旧数据库配置没有 securityMode 时，按合并后的端口推导，避免
            # 部署默认的 465/SSL 覆盖旧配置的 587/STARTTLS 语义。
            stored_mode = str(stored.get("securityMode") or "").strip().lower()
            if stored_mode not in {"ssl", "starttls", "none"}:
                try:
                    port = int(merged.get("port") or 465)
                except (TypeError, ValueError):
                    port = 465
                merged["securityMode"] = "ssl" if port == 465 else "starttls"
        if service_key == "vision" and str(merged.get("provider") or "openai").strip().lower() == "ollama":
            # 兼容以前保存的 OpenAI OCR 草稿；本地 Ollama 不读取也不展示 detail。
            merged.pop("detail", None)
        return merged


def _normalize_hotwords(value: object, *, model: str) -> str:
    try:
        return normalize_bailian_hotwords(value, model=model)
    except BailianHotwordValidationError as exc:
        raise SystemConfigValidationError(str(exc)) from None


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SystemConfigValidationError(f"{label}不能为空")
    return text[:512]


def _validate_url(value: object, label: str) -> str:
    raw = _required_text(value, label).rstrip("/")
    try:
        parsed = urlsplit(raw)
        # 访问地址只用于定位服务端点，凭据必须走独立的加密 secrets 字段，
        # 避免用户名、密码或查询参数中的密钥进入数据库、代理和请求日志。
        has_sensitive_url_parts = (
            parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        )
        valid_scheme = parsed.scheme.lower() in {"http", "https"}
        valid_host = bool(parsed.hostname)
        # 主动读取 port，让非法端口也按配置错误处理，而不是在实际请求时才抛出异常。
        parsed_port = parsed.port
        valid_port = parsed_port is None or 0 <= parsed_port <= 65535
    except ValueError as exc:
        raise SystemConfigValidationError(f"{label}必须是有效的 http 或 https 地址") from exc
    if not valid_scheme or not valid_host or not valid_port:
        raise SystemConfigValidationError(f"{label}必须是 http 或 https 地址")
    if has_sensitive_url_parts:
        raise SystemConfigValidationError(f"{label}不能包含用户名、密码、查询参数或片段")
    return raw


def _path(value: object, fallback: str) -> str:
    raw = str(value or fallback).strip() or fallback
    return raw if raw.startswith("/") else f"/{raw}"


def _number(value: object, label: str, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SystemConfigValidationError(f"{label}格式不正确") from exc
    if not isfinite(number) or number < minimum or number > maximum:
        raise SystemConfigValidationError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return round(number, 3)


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemConfigValidationError(f"{label}格式不正确") from exc
    if number < minimum or number > maximum:
        raise SystemConfigValidationError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return number


def _clamp_integer(value: object, fallback: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(fallback)
    return min(maximum, max(minimum, number))


def _normalize_smtp_security_mode(value: object, port: int) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"ssl", "starttls", "none"}:
        return mode
    if mode:
        raise SystemConfigValidationError("SMTP 加密方式只能是 ssl、starttls 或 none")
    # 兼容历史配置：没有加密字段时沿用原先的端口约定。
    return "ssl" if int(port or 0) == 465 else "starttls"
