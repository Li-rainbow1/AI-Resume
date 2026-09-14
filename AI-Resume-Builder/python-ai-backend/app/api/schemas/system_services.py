from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SystemServiceTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class SystemServiceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    expectedVersion: int = Field(ge=0)


class SystemServiceSecretState(BaseModel):
    configured: bool
    lastFour: str
    masked: str = ""


class SystemServiceResponse(BaseModel):
    serviceKey: str
    source: str
    version: int
    config: dict[str, Any]
    secrets: dict[str, SystemServiceSecretState]
    status: str
    validationMessage: str
    validatedAt: str | None = None
    embeddingProfile: str | None = None
    requiresRebuild: bool = False
    ollamaBaseUrl: str | None = None


class SystemServiceTestResponse(BaseModel):
    serviceKey: str
    success: bool
    message: str
    elapsedMs: int
