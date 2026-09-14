import json
import urllib.error
import urllib.request
from typing import Any


def _normalize_base_url(base_url: str) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    return cleaned or "https://api.openai.com"


def _normalize_path(path: str, fallback: str) -> str:
    cleaned = (path or "").strip()
    target = cleaned or fallback
    return target if target.startswith("/") else f"/{target}"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


class OpenAIRealtimeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client_secrets_path: str,
        realtime_calls_path: str,
        default_model: str,
        default_language: str,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.api_key = _safe_text(api_key)
        self.client_secrets_path = _normalize_path(client_secrets_path, "/v1/realtime/client_secrets")
        self.realtime_calls_path = _normalize_path(realtime_calls_path, "/v1/realtime/calls")
        self.default_model = _safe_text(default_model) or "gpt-4o-transcribe"
        # language 是可选提示；留空时由 OpenAI 自动判断中英文混合语音。
        self.default_language = _safe_text(default_language)

    def create_client_secret(self, model: str | None = None, language: str | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Realtime API key is not configured")

        resolved_model = _safe_text(model) or self.default_model
        if not resolved_model:
            raise RuntimeError("Realtime transcription model is not configured")
        resolved_language = _safe_text(language) or self.default_language

        transcription: dict[str, Any] = {"model": resolved_model}
        if resolved_language:
            transcription["language"] = resolved_language

        payload = {
            "session": {
                "type": "transcription",
                "audio": {"input": {"transcription": transcription}},
            }
        }

        request = urllib.request.Request(
            url=f"{self.base_url}{self.client_secrets_path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Realtime API HTTP {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Realtime API connection failed: {exc.reason}") from exc

        return self._parse_response(raw=raw, fallback_model=resolved_model)

    def _parse_response(self, raw: str, fallback_model: str) -> dict[str, Any]:
        try:
            payload = json.loads(_safe_text(raw) or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to parse realtime session response") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Failed to parse realtime session response")

        client_secret = ""
        client_secret_node = payload.get("client_secret")
        if isinstance(client_secret_node, dict):
            client_secret = _safe_text(client_secret_node.get("value"))
        if not client_secret:
            client_secret = _safe_text(payload.get("value"))
        if not client_secret:
            raise RuntimeError("Realtime provider did not return a client secret")

        expires_at: int | None = None
        if isinstance(client_secret_node, dict):
            raw_expires_at = client_secret_node.get("expires_at")
            if isinstance(raw_expires_at, (int, float)):
                expires_at = int(raw_expires_at)
        if expires_at is None:
            raw_expires_at = payload.get("expires_at")
            if isinstance(raw_expires_at, (int, float)):
                expires_at = int(raw_expires_at)

        model = ""
        session = payload.get("session")
        if isinstance(session, dict):
            audio = session.get("audio")
            if isinstance(audio, dict):
                input_node = audio.get("input")
                if isinstance(input_node, dict):
                    transcription = input_node.get("transcription")
                    if isinstance(transcription, dict):
                        model = _safe_text(transcription.get("model"))
            if not model:
                model = _safe_text(session.get("model"))
        if not model:
            model = fallback_model

        return {
            "clientSecret": client_secret,
            "expiresAt": expires_at,
            "model": model,
            "realtimeApiBaseUrl": self.base_url,
            "realtimeCallsPath": self.realtime_calls_path,
        }
