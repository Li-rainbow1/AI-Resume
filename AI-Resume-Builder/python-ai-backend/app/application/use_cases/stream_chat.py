from collections.abc import Iterator

from app.application.dto.chat_dto import ChatRequestDto
from app.application.ports.llm_port import ChatClientPort
from app.domain.policies.sanitize_policy import sanitize_resume_markdown
from app.domain.services.resume_section_clean_service import contains_only_section_headings
from app.shared.streaming.sse import to_sse


def generate_chat_stream(request: ChatRequestDto, client: ChatClientPort) -> Iterator[str]:
    """生成聊天 SSE 流。

    API 层注入的客户端已使用运行时系统服务配置；本用例按请求模式执行原始流式输出
    或安全清理后的稳定片段，并把上游异常转换成 SSE error 事件。这样既保留现有
    流式协议，也不会因图片、简历或优化请求进入另一条静态配置路径。
    """

    try:
        if not request.sanitize_output:
            for chunk in client.stream_chat(message=request.message):
                if not chunk:
                    continue
                yield to_sse("chunk", chunk)
            return

        raw_buffer = ""
        emitted_length = 0

        for chunk in client.stream_chat(message=request.message):
            if not chunk:
                continue
            raw_buffer += chunk
            sanitized = sanitize_resume_markdown(raw_buffer)
            stable_end = sanitized.rfind("\n")
            if stable_end < 0:
                continue
            emit_until = stable_end + 1
            if emit_until > emitted_length:
                delta = sanitized[emitted_length:emit_until]
                if contains_only_section_headings(delta):
                    continue
                yield to_sse("chunk", delta)
                emitted_length = emit_until

        sanitized = sanitize_resume_markdown(raw_buffer)
        if len(sanitized) > emitted_length:
            tail = sanitized[emitted_length:]
            if not contains_only_section_headings(tail):
                yield to_sse("chunk", tail)
    except Exception as exc:  # pragma: no cover
        yield to_sse("error", str(exc))
