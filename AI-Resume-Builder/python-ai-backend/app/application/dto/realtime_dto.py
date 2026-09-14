from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RealtimeClientSecretRequestDto:
    model: str | None = None
    language: str | None = None
    context: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class RealtimeClientSecretResponseDto:
    client_secret: str | None
    provider: str
    model: str | None = None
    language: str | None = None
    realtime_api_base_url: str | None = None
    realtime_calls_path: str | None = None
    expires_at: int | None = None
    session_ticket: str | None = None
    websocket_path: str | None = None
    sample_rate: int | None = 16000
