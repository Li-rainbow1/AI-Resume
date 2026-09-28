from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any


class QwenRerankClient:
    """调用百炼文本重排接口，并按上游返回的重排顺序返回来源。"""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "qwen3.7-text-rerank",
        endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model_name = (model_name or "qwen3.7-text-rerank").strip()
        self.endpoint = (endpoint or "").strip()
        self.timeout_seconds = max(3.0, float(timeout_seconds or 120.0))

    def rerank(self, query: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        safe_query = str(query or "").strip()
        if not safe_query or not isinstance(sources, list):
            return []
        candidates = [item for item in sources if isinstance(item, dict) and str(item.get("content") or "").strip()]
        if not candidates:
            return []
        if not self.api_key:
            raise RuntimeError("Rerank API Key 未配置")
        if not self.endpoint:
            raise RuntimeError("Rerank endpoint 未配置")

        payload = {
            "model": self.model_name,
            "input": {
                "query": safe_query,
                "documents": [str(item.get("content") or "") for item in candidates],
            },
            "parameters": {"return_documents": False, "top_n": len(candidates)},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Rerank API HTTP {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Rerank API connection failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Rerank API 返回了非法 JSON") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("Rerank API 返回结构非法")
        error = parsed.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"Rerank API 返回错误: {str(message)[:300]}")

        output = parsed.get("output") if isinstance(parsed.get("output"), dict) else {}
        ranked = output.get("results") or output.get("data") or []
        if not isinstance(ranked, list):
            raise RuntimeError("Rerank API 未返回 results")

        scored: list[tuple[float, int]] = []
        for row in ranked:
            if not isinstance(row, dict):
                continue
            raw_index = row.get("index", row.get("document_index"))
            raw_score = row.get("relevance_score", row.get("score"))
            try:
                index = int(raw_index)
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates) and math.isfinite(score):
                scored.append((score, index))
        if not scored:
            raise RuntimeError("Rerank API 未返回有效排序结果")

        # 只按重排分排序；向量相似度不参与排序或过滤。
        scored.sort(key=lambda pair: pair[0], reverse=True)
        reranked: list[dict[str, Any]] = []
        for rank, (score, index) in enumerate(scored, start=1):
            item = dict(candidates[index])
            item["rerank_score"] = score
            item["rerank_rank"] = rank
            reranked.append(item)
        return reranked
