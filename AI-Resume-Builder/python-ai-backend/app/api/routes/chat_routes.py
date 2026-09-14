from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps.auth import AuthUserContext, require_auth_user_context
from app.api.deps.providers import get_chat_client
from app.api.mappers.chat_mapper import chat_request_to_dto, chat_response_from_dto
from app.api.schemas.chat import ChatRequest, ChatResponse
from app.application.ports.llm_port import ChatClientPort
from app.application.use_cases.run_chat import run_chat as run_chat_use_case
from app.application.use_cases.stream_chat import generate_chat_stream as generate_chat_stream_use_case

router = APIRouter(prefix="/api/ai", tags=["ai-chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    user_context: AuthUserContext = Depends(require_auth_user_context),
    chat_client: ChatClientPort = Depends(get_chat_client),
) -> ChatResponse:
    # 每次请求都通过运行时配置提供者构建聊天客户端，使系统服务配置页保存的
    # 数据库配置立即作用于普通聊天和 AI 简历优化，而不是退回容器环境变量。
    _ = user_context
    return chat_response_from_dto(
        run_chat_use_case(chat_request_to_dto(request), client=chat_client)
    )


@router.post("/chat/stream")
def chat_stream(
    request: ChatRequest,
    user_context: AuthUserContext = Depends(require_auth_user_context),
    chat_client: ChatClientPort = Depends(get_chat_client),
) -> StreamingResponse:
    # 流式请求同样复用数据库运行时配置；用例只负责业务编排和 SSE 转换，
    # 不再自行读取静态环境配置，避免配置测试通过但实际优化仍提示缺少 API Key。
    _ = user_context
    return StreamingResponse(
        generate_chat_stream_use_case(chat_request_to_dto(request), client=chat_client),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
