from app.api.schemas.interview import InterviewHistoryItem, InterviewTurnRequest
from app.application.dto.interview_dto import InterviewHistoryItemDto, InterviewTurnRequestDto, InterviewTurnScoreDto


def _history_item_to_dto(item: InterviewHistoryItem) -> InterviewHistoryItemDto:
    return InterviewHistoryItemDto(
        role=item.role,
        content=item.content,
        score=InterviewTurnScoreDto(score=item.score.score, comment=item.score.comment) if item.score else None,
    )


def interview_turn_request_to_dto(request: InterviewTurnRequest, user_id: str) -> InterviewTurnRequestDto:
    return InterviewTurnRequestDto(
        user_id=user_id,
        mode=request.mode,
        command=request.command,
        user_input=request.userInput,
        session_id=request.sessionId,
        request_id=request.requestId,
        memory_summary=request.memorySummary,
        duration_minutes=request.durationMinutes,
        elapsed_seconds=request.elapsedSeconds,
        history=[_history_item_to_dto(item) for item in request.history],
        resume_snapshot=request.resumeSnapshot,
        # 旧 projectIds 字段继续接受，但不写入面试请求状态。
        project_ids=None,
    )
