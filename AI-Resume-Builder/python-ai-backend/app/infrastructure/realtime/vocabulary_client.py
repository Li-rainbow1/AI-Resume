from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any

from app.domain.policies.bailian_hotword_policy import normalize_bailian_hotwords
from app.infrastructure.config.settings import Settings


class BailianVocabularyClient:
    """百炼 Fun-ASR 预编译热词表 HTTP API 的最小适配器。"""

    def __init__(self, *, settings: Settings, api_key: str) -> None:
        self.settings = settings
        self.api_key = str(api_key or "").strip()

    def create(self, hotwords: str, *, target_model: str) -> str:
        normalized_hotwords = normalize_bailian_hotwords(hotwords, model=target_model)
        terms = normalized_hotwords.splitlines()
        if not terms:
            return ""
        payload = {
            "model": "speech-biasing",
            "input": {
                "action": "create_vocabulary",
                "target_model": target_model,
                "prefix": f"resumebl{uuid.uuid4().hex[:2]}",
                # 不写死词条语言，避免 Java、MySQL 等英文术语被误标为中文。
                "vocabulary": [{"text": term, "weight": 4} for term in terms],
            },
        }
        response = self._request(payload)
        output = response.get("output") if isinstance(response.get("output"), dict) else response
        vocabulary_id = str((output or {}).get("vocabulary_id") or "").strip()
        if not vocabulary_id:
            raise RuntimeError("百炼词表创建未返回 ID")
        return vocabulary_id

    def delete(self, vocabulary_id: str) -> None:
        safe_id = str(vocabulary_id or "").strip()
        if not safe_id:
            return
        self._request({
            "model": "speech-biasing",
            "input": {"action": "delete_vocabulary", "vocabulary_id": safe_id},
        })

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = (self.settings.bailian_asr_workspace_id or "").strip()
        # 百炼热词表固定走华北 2（北京），与实时语音请求保持同一地域。
        suffix = "cn-beijing"
        if not workspace or not self.api_key:
            raise RuntimeError("百炼词表服务凭据未配置")
        url = f"https://{workspace}.{suffix}.maas.aliyuncs.com/api/v1/services/audio/asr/customization"
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("百炼热词表同步失败") from exc
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("百炼热词表响应无效") from exc
        if not isinstance(value, dict):
            raise RuntimeError("百炼热词表响应无效")
        code = str(value.get("code") or value.get("error_code") or "")
        if code and code not in {"200", "0", "SUCCESS"}:
            raise RuntimeError("百炼热词表同步失败")
        return value
