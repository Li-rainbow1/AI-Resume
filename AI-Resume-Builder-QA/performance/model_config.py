# author: jf
"""为性能场景组装模型连接配置，并生成不含密钥的运行摘要。"""

from typing import Mapping
from urllib.parse import urlparse


def _required(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name, "")).strip()
    if not value:
        raise RuntimeError(f"真实模型模式缺少配置：{name}")
    return value


def build_model_environment(environment: Mapping[str, str], mode: str) -> dict[str, str]:
    normalized = mode.strip().lower()
    if normalized == "mock":
        mock_key = str(environment.get("QA_MOCK_API_KEY", "qa-mock-key"))
        return {
            "PERF_OPENAI_BASE_URL": "http://mock-ai:8000",
            "PERF_OPENAI_API_KEY": mock_key,
            "PERF_OPENAI_CHAT_BASE_URL": "http://mock-ai:8000",
            "PERF_OPENAI_CHAT_API_KEY": mock_key,
            "PERF_OPENAI_CHAT_COMPLETIONS_PATH": "/v1/chat/completions",
            "PERF_OPENAI_CHAT_MODEL": "mock-chat",
            "PERF_EMBEDDING_PROVIDER": "openai",
            "PERF_OPENAI_EMBEDDING_BASE_URL": "http://mock-ai:8000/v1",
            "PERF_OPENAI_EMBEDDING_API_KEY": mock_key,
            "PERF_OPENAI_EMBEDDING_MODEL": "text-embedding-3-large",
            "PERF_VISION_PROVIDER": "openai",
            "PERF_OPENAI_VISION_BASE_URL": "http://mock-ai:8000",
            "PERF_OPENAI_VISION_API_KEY": mock_key,
            "PERF_OPENAI_VISION_MODEL": "mock-vision",
        }
    if normalized != "real":
        raise RuntimeError(f"不支持的模型模式：{mode}，可选 mock 或 real")

    embedding_provider = str(environment.get("PERF_REAL_EMBEDDING_PROVIDER", "openai")).strip() or "openai"
    vision_provider = str(environment.get("PERF_REAL_VISION_PROVIDER", "openai")).strip() or "openai"
    chat_key = _required(environment, "PERF_REAL_CHAT_API_KEY")
    embedding_key = (
        _required(environment, "PERF_REAL_EMBEDDING_API_KEY")
        if embedding_provider != "ollama"
        else ""
    )
    vision_key = _required(environment, "PERF_REAL_VISION_API_KEY") if vision_provider != "ollama" else ""
    return {
        "PERF_OPENAI_BASE_URL": _required(environment, "PERF_REAL_CHAT_BASE_URL"),
        "PERF_OPENAI_API_KEY": chat_key,
        "PERF_OPENAI_CHAT_BASE_URL": _required(environment, "PERF_REAL_CHAT_BASE_URL"),
        "PERF_OPENAI_CHAT_API_KEY": chat_key,
        "PERF_OPENAI_CHAT_COMPLETIONS_PATH": str(environment.get("PERF_REAL_CHAT_COMPLETIONS_PATH", "/v1/chat/completions")),
        "PERF_OPENAI_CHAT_MODEL": _required(environment, "PERF_REAL_CHAT_MODEL"),
        "PERF_EMBEDDING_PROVIDER": embedding_provider,
        "PERF_OPENAI_EMBEDDING_BASE_URL": _required(environment, "PERF_REAL_EMBEDDING_BASE_URL"),
        "PERF_OPENAI_EMBEDDING_API_KEY": embedding_key,
        "PERF_OPENAI_EMBEDDING_MODEL": _required(environment, "PERF_REAL_EMBEDDING_MODEL"),
        "PERF_VISION_PROVIDER": vision_provider,
        "PERF_OPENAI_VISION_BASE_URL": _required(environment, "PERF_REAL_VISION_BASE_URL"),
        "PERF_OPENAI_VISION_API_KEY": vision_key,
        "PERF_OPENAI_VISION_MODEL": _required(environment, "PERF_REAL_VISION_MODEL"),
    }


def _host(value: str) -> str | None:
    parsed = urlparse(value)
    return parsed.hostname or None


def model_summary(model_environment: Mapping[str, str], mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "chat": {
            "model": model_environment.get("PERF_OPENAI_CHAT_MODEL"),
            "baseUrlHost": _host(str(model_environment.get("PERF_OPENAI_CHAT_BASE_URL", ""))),
            "completionsPath": model_environment.get("PERF_OPENAI_CHAT_COMPLETIONS_PATH"),
        },
        "embedding": {
            "provider": model_environment.get("PERF_EMBEDDING_PROVIDER"),
            "model": model_environment.get("PERF_OPENAI_EMBEDDING_MODEL"),
            "baseUrlHost": _host(str(model_environment.get("PERF_OPENAI_EMBEDDING_BASE_URL", ""))),
        },
        "vision": {
            "provider": model_environment.get("PERF_VISION_PROVIDER"),
            "model": model_environment.get("PERF_OPENAI_VISION_MODEL"),
            "baseUrlHost": _host(str(model_environment.get("PERF_OPENAI_VISION_BASE_URL", ""))),
        },
    }


def selected_mode(environment: Mapping[str, str], argument: str | None) -> str:
    return (argument or environment.get("PERF_MODEL_MODE", "mock")).strip().lower()
