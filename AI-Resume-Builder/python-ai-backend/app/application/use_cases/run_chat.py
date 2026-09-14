from app.application.dto.chat_dto import ChatRequestDto, ChatResponseDto
from app.application.ports.llm_port import ChatClientPort
from app.domain.policies.sanitize_policy import safe_content, sanitize_resume_markdown


def run_chat(request: ChatRequestDto, client: ChatClientPort) -> ChatResponseDto:
    """执行普通聊天请求。

    聊天客户端由 API 层通过依赖注入提供，客户端已经读取数据库中的系统服务配置。
    本用例只执行消息调用、输出清理和 DTO 封装；上游调用或清理异常继续交给现有
    API 异常处理链路，避免再次回退到只读取容器环境变量的静态工厂。
    """
    output = client.chat(message=request.message)
    safe_output = safe_content(output)
    if request.sanitize_output:
        safe_output = sanitize_resume_markdown(safe_output)
    return ChatResponseDto(answer=safe_output)
