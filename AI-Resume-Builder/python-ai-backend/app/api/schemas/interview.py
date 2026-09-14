from typing import Any

from pydantic import BaseModel, Field


class InterviewTurnScore(BaseModel):
    score: int = Field(default=0, ge=0, le=100)
    comment: str = ""


class InterviewHistoryItem(BaseModel):
    role: str = "user"
    content: str = ""
    score: InterviewTurnScore | None = None


class InterviewTurnRequest(BaseModel):
    mode: str = "candidate"
    command: str = "continue"
    userInput: str | None = None
    sessionId: str | None = None
    requestId: str | None = None
    memorySummary: str | None = None
    durationMinutes: int | None = 60
    elapsedSeconds: int | None = 0
    history: list[InterviewHistoryItem] = Field(default_factory=list)
    resumeSnapshot: dict[str, Any] = Field(default_factory=dict)
    projectIds: list[str] | None = Field(default=None, max_length=100)
