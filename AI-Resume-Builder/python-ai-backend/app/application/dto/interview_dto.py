from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InterviewTurnScoreDto:
    score: int = 0
    comment: str = ""

    def model_dump(self) -> dict[str, Any]:
        return {"score": self.score, "comment": self.comment}


@dataclass(slots=True)
class InterviewHistoryItemDto:
    role: str = "user"
    content: str = ""
    score: InterviewTurnScoreDto | None = None


@dataclass(slots=True)
class InterviewTurnRequestDto:
    user_id: str = ""
    mode: str = "candidate"
    command: str = "continue"
    user_input: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    memory_summary: str | None = None
    duration_minutes: int | None = 60
    elapsed_seconds: int | None = 0
    history: list[InterviewHistoryItemDto] = field(default_factory=list)
    resume_snapshot: dict[str, Any] = field(default_factory=dict)
    # 旧请求字段仅为反序列化兼容，面试范围不再由客户端指定。
    project_ids: list[str] | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "mode": self.mode,
            "command": self.command,
            "userInput": self.user_input,
            "sessionId": self.session_id,
            "requestId": self.request_id,
            "memorySummary": self.memory_summary,
            "durationMinutes": self.duration_minutes,
            "elapsedSeconds": self.elapsed_seconds,
            "history": [
                {
                    "role": item.role,
                    "content": item.content,
                    "score": item.score.model_dump() if item.score else None,
                }
                for item in self.history
            ],
            "resumeSnapshot": self.resume_snapshot,
        }
