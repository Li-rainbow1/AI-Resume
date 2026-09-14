from app.api.schemas.chat import ChatRequest, ChatResponse
from app.application.dto.chat_dto import ChatRequestDto, ChatResponseDto


def chat_request_to_dto(request: ChatRequest) -> ChatRequestDto:
    return ChatRequestDto(
        message=request.message,
        sanitize_output=request.sanitizeOutput,
        optimize_sections=request.optimizeSections,
    )


def chat_response_from_dto(response: ChatResponseDto) -> ChatResponse:
    return ChatResponse(answer=response.answer)
