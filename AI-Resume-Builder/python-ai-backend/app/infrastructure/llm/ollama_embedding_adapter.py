import json
from typing import Any
from urllib.parse import urlparse

import httpx

from app.domain.exceptions.rag_exceptions import EmbeddingError, safe_rag_log_value


class OllamaEmbeddingAdapter:
    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.model_name = (model_name or "").strip() or "nomic-embed-text"
        self.base_url = (base_url or "").strip() or "http://127.0.0.1:11434"
        self.timeout_seconds = self._normalize_timeout(timeout_seconds)
        self.provider_name = "ollama"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # 基础设施层职责：
        # 1) 对外保持 EmbeddingPort 行为一致。
        # 2) 先确认 Ollama 服务和模型可用，再发送 /api/embed 请求。
        # 3) 仅把网络/协议异常映射为 EmbeddingError，不承载业务分支判断。
        safe_texts = [text.strip() for text in texts if (text or "").strip()]
        if not safe_texts:
            _log_embedding("skip_empty_input")
            return []

        self._ensure_ollama_ready()

        embed_endpoint = self._build_embed_endpoint()
        payload = {
            "model": self.model_name,
            "input": safe_texts,
        }

        try:
            response_json = self._post_json(embed_endpoint, payload)
        except Exception as exc:
            raise EmbeddingError("Ollama Embedding 调用失败，请稍后重试") from exc

        vectors = self._extract_vectors(response_json)
        if len(vectors) != len(safe_texts):
            raise EmbeddingError("Embedding 返回数量与输入数量不一致")
        return [self._normalize_vector(vector) for vector in vectors]

    def _ensure_ollama_ready(self) -> None:
        tags_endpoint = self._build_tags_endpoint()
        data = self._get_json(tags_endpoint)

        raw_models = data.get("models") if isinstance(data, dict) else None
        model_names: set[str] = set()
        if isinstance(raw_models, list):
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_model_name(item.get("name"))
                if normalized:
                    model_names.add(normalized)

        requested_model = self._normalize_model_name(self.model_name)
        if requested_model not in model_names:
            raise EmbeddingError(
                f"Ollama 未发现 embedding 模型 `{self.model_name}`。请先执行 `ollama pull {self.model_name}` 后重试。"
            )

    def _get_json(self, url: str) -> dict[str, Any]:
        timeout = self._build_timeout()
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise EmbeddingError("Ollama 请求超时，请稍后重试") from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError("Ollama 请求失败，请稍后重试") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise EmbeddingError("Ollama 返回格式无效，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("无法连接 Ollama 服务，请稍后重试") from exc

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = self._build_timeout()
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise EmbeddingError("Ollama 请求超时，请稍后重试") from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError("Ollama 请求失败，请稍后重试") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise EmbeddingError("Ollama 返回格式无效，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("无法连接 Ollama 服务，请稍后重试") from exc

    @staticmethod
    def _extract_vectors(response_json: dict[str, Any]) -> list[list[float]]:
        if not isinstance(response_json, dict):
            raise EmbeddingError("Ollama /api/embed 返回结构非法")

        embeddings = response_json.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return embeddings

        single_embedding = response_json.get("embedding")
        if isinstance(single_embedding, list) and single_embedding:
            return [single_embedding]

        raise EmbeddingError("Ollama /api/embed 未返回 embeddings")

    def _build_tags_endpoint(self) -> str:
        return f"{self._normalized_base_url()}/api/tags"

    def _build_embed_endpoint(self) -> str:
        return f"{self._normalized_base_url()}/api/embed"

    def _normalized_base_url(self) -> str:
        parsed = urlparse(self.base_url)
        if parsed.scheme and parsed.netloc:
            return self.base_url.rstrip("/")
        return "http://127.0.0.1:11434"

    def _build_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            timeout=self.timeout_seconds,
            connect=self.timeout_seconds,
            read=self.timeout_seconds,
            write=self.timeout_seconds,
            pool=self.timeout_seconds,
        )

    @staticmethod
    def _normalize_timeout(raw_timeout: float) -> float:
        try:
            normalized = float(raw_timeout)
        except (TypeError, ValueError):
            return 45.0
        return max(1.0, normalized)

    @staticmethod
    def _normalize_vector(raw_vector: list[float]) -> list[float]:
        if not isinstance(raw_vector, list) or not raw_vector:
            raise EmbeddingError("Embedding 向量为空或格式非法")
        try:
            return [float(value) for value in raw_vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding 向量包含无法转换为 float 的值") from exc

    @staticmethod
    def _normalize_model_name(raw_name: Any) -> str:
        safe_name = str(raw_name or "").strip()
        if not safe_name:
            return ""
        return safe_name[:-7] if safe_name.endswith(":latest") else safe_name


def _log_embedding(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][EmbeddingAdapter] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
