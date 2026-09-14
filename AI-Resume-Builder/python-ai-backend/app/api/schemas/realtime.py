from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RealtimeClientSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    language: str | None = None
    context: list[dict[str, Any]] = Field(default_factory=list)


class RealtimeClientSecretResponse(BaseModel):
    clientSecret: str | None = None
    expiresAt: int | None = None
    provider: str
    model: str | None = None
    language: str | None = None
    realtimeApiBaseUrl: str | None = None
    realtimeCallsPath: str | None = None
    sessionTicket: str | None = None
    websocketPath: str | None = None
    sampleRate: int | None = 16000
