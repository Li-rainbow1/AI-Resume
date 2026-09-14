from collections.abc import Iterator
import logging
import time
import uuid
from typing import Any

from app.application.dto.interview_dto import InterviewTurnRequestDto
from app.application.ports.interview_session_repository import InterviewSessionRepository
from app.application.services.interview_session_service import persist_turn_result, _new_session_record, _now_iso
from app.application.services.response_stream_service import (
    extract_streaming_assistant_reply,
    to_interview_accepted_event,
    to_interview_chunk_event,
    to_interview_done_event,
    to_interview_error_event,
    to_interview_processing_event,
)
from app.bootstrap.container import build_interview_graph, build_interview_session_repository, resolve_settings

_LOGGER = logging.getLogger("uvicorn.error")


def _build_accepted_status_message(command: str) -> str:
    safe_command = str(command or "").strip().lower()
    if safe_command == "start":
        return "面试已启动，正在同步当前简历上下文"
    if safe_command == "finish":
        return "已收到结束指令"
    return "已收到你的回答"


def _build_processing_status_message(command: str, mode: str) -> str:
    safe_command = str(command or "").strip().lower()
    safe_mode = str(mode or "").strip().lower()
    if safe_command == "start":
        return "AI面试官正在生成第一轮问题..." if safe_mode == "candidate" else "AI候选人正在生成开场回答..."
    if safe_command == "finish":
        return "正在生成评分结果与总结..."
    return "AI面试官正在组织下一轮提问..." if safe_mode == "candidate" else "AI候选人正在组织回答..."


def _prepare_request_payload(
    request: InterviewTurnRequestDto,
    session_repository: InterviewSessionRepository,
) -> dict[str, Any]:
    prepare_payload_started_at = time.monotonic()
    payload = request.model_dump()
    payload['contextVersion'] = 0
    payload['summaryThroughSeq'] = 0
    payload['memorySummary'] = ''
    # 新会话旧客户端可能把当前输入同时放入历史，只去掉末尾这一份重复。
    if payload['history'] and payload['history'][-1].get('role') == 'user' and payload['history'][-1].get('content') == payload.get('userInput'):
        payload['history'] = payload['history'][:-1]
    session_id = str(request.session_id or "").strip()
    if not session_id:
        _LOGGER.warning("[AI面试][流式] payload prepared without sessionId elapsedMs=%s", int((time.monotonic() - prepare_payload_started_at) * 1000))
        return payload

    fetch_session_started_at = time.monotonic()
    stored_session = session_repository.get(session_id, request.user_id)
    fetch_session_elapsed_ms = int((time.monotonic() - fetch_session_started_at) * 1000)
    _LOGGER.warning(
        "[AI面试][流式] session repository get done sessionId=%s elapsedMs=%s found=%s",
        session_id,
        fetch_session_elapsed_ms,
        isinstance(stored_session, dict),
    )
    if not isinstance(stored_session, dict):
        _LOGGER.warning("[AI面试][流式] payload prepared without stored session elapsedMs=%s", int((time.monotonic() - prepare_payload_started_at) * 1000))
        return payload

    stored_messages = stored_session.get("messages")
    safe_stored_messages = stored_messages if isinstance(stored_messages, list) else []
    request_id = str(payload.get('requestId') or '').strip()
    cached_request_id = str(stored_session.get('lastRequestId') or '').strip()
    cached_response = stored_session.get('lastResponse')
    if request_id and request_id == cached_request_id and isinstance(cached_response, dict):
        payload['_cachedResponse'] = cached_response
        return payload
    if request_id and request_id in (stored_session.get('completedRequestIds') or []):
        raise ValueError('该请求已完成且已有后续回合，请刷新会话查看最新记录')

    payload['history'] = safe_stored_messages
    payload['contextVersion'] = int(stored_session.get('contextVersion') or 0)
    payload['summaryThroughSeq'] = int(stored_session.get('summaryThroughSeq') or 0)
    payload['memorySummary'] = stored_session.get('memorySummary') or '' if payload['contextVersion'] else ''
    if stored_session.get('status') == 'finished':
        raise ValueError('当前面试已结束，请新建会话')

    if not payload.get("resumeSnapshot"):
        payload["resumeSnapshot"] = stored_session.get("resumeSnapshot") or {}

    payload['mode'] = stored_session.get('mode') or payload.get('mode')

    _LOGGER.warning(
        "[AI面试][流式] payload prepared from stored session sessionId=%s elapsedMs=%s historySize=%s",
        session_id,
        int((time.monotonic() - prepare_payload_started_at) * 1000),
        len(payload.get("history") or []),
    )
    return payload


def generate_interview_turn_stream(request: InterviewTurnRequestDto) -> Iterator[str]:
    turn_started_at = time.monotonic()
    requested_session_id = str(request.session_id or "").strip() or "-"
    requested_command = str(request.command or "").strip() or "-"
    requested_mode = str(request.mode or "").strip() or "-"
    _LOGGER.warning(
        "[AI面试][流式] request received mode=%s command=%s sessionId=%s",
        requested_mode,
        requested_command,
        requested_session_id,
    )

    settings_started_at = time.monotonic()
    settings = resolve_settings()
    _LOGGER.warning("[AI面试][流式] settings resolved elapsedMs=%s", int((time.monotonic() - settings_started_at) * 1000))

    build_graph_started_at = time.monotonic()
    graph = build_interview_graph(settings)
    _LOGGER.warning("[AI面试][流式] graph built elapsedMs=%s", int((time.monotonic() - build_graph_started_at) * 1000))

    build_repo_started_at = time.monotonic()
    session_repository = build_interview_session_repository(settings)
    _LOGGER.warning("[AI面试][流式] repository built elapsedMs=%s", int((time.monotonic() - build_repo_started_at) * 1000))

    # 这一段负责把“用户这次回合需要哪些上下文”准备完整，包括历史消息、memorySummary、
    # resumeSnapshot 和 mode。只有这些基础载荷都装配成功，前端才应该收到“accepted”，
    # 否则会把纯本地提交态误判成后端已经真正接住了请求。
    prepare_started_at = time.monotonic()
    try:
        request.session_id = request.session_id or str(uuid.uuid4())
        request.request_id = request.request_id or str(uuid.uuid4())
        request_payload = _prepare_request_payload(request, session_repository)
    except Exception as exc:
        yield to_interview_error_event(f"面试请求准备失败: {exc}")
        return
    request.mode = str(request_payload.get('mode') or request.mode)
    _LOGGER.warning(
        "[AI面试][流式] payload ready command=%s historySize=%s",
        request_payload.get("command"),
        len(request_payload.get("history") or []),
    )
    yield to_interview_accepted_event(_build_accepted_status_message(str(request_payload.get("command") or "")))

    cached_response = request_payload.pop('_cachedResponse', None)
    if isinstance(cached_response, dict):
        yield to_interview_processing_event('正在恢复已完成的面试回合')
        yield to_interview_done_event(cached_response)
        return

    # prepare_turn 的职责是把 HTTP 请求层数据收敛为真正可执行的一轮面试状态。
    # 如果这一层失败，说明还没有进入模型生成阶段，必须立刻回 error，
    # 避免前端一直停留在“准备中”却收不到后续事件。
    try:
        prepared_state = graph.prepare_turn(request_payload)
        # 超阈值时才调用摘要模型；完成后先保存覆盖位置，再开始本轮回复。
        from app.domain.services.interview_context_service import model_message
        if len(graph._build_system_prompt(prepared_state)) + len(model_message(prepared_state)) > graph.context_budget.soft:
            yield to_interview_processing_event('正在整理较早的面试记录')
        compacted = graph.compact_context(prepared_state)
        if compacted['summaryThroughSeq'] != prepared_state['summaryThroughSeq']:
            session = session_repository.get(request.session_id, request.user_id)
            if session is None:
                session = _new_session_record(request.session_id, request, _now_iso())
                session['messages'] = request_payload['history']
            session.update(memorySummary=compacted['memorySummary'], summaryThroughSeq=compacted['summaryThroughSeq'],
                           contextVersion=prepared_state['contextVersion'], updatedAt=_now_iso())
            session_repository.save(request.session_id, request.user_id, session)
            compacted['contextVersion'] = session['contextVersion']
        prepared_state = compacted
    except Exception as exc:
        _LOGGER.warning("[AI面试][流式] prepare exception command=%s error=%s", request_payload.get("command"), exc)
        yield to_interview_error_event(f"面试回合准备失败: {exc}")
        return

    prepare_elapsed_ms = int((time.monotonic() - prepare_started_at) * 1000)
    _LOGGER.warning(
        "[AI面试][流式] prepare done mode=%s command=%s sessionId=%s elapsedMs=%s",
        prepared_state.get("mode"),
        prepared_state.get("command"),
        prepared_state.get("sessionId"),
        prepare_elapsed_ms,
    )
    yield to_interview_processing_event(
        _build_processing_status_message(
            str(prepared_state.get("command") or ""),
            str(prepared_state.get("mode") or ""),
        )
    )

    raw_buffer = ""
    last_emitted_reply = ""
    llm_error: str | None = None
    first_token_emitted = False
    stream_chunk_count = 0
    stream_started_at = time.monotonic()

    try:
        # 这里开始真正进入模型流式输出。我们持续累积原始 token，
        # 但只在解析出 assistantReply 可展示片段后才向前端发 chunk，
        # 这样前端收到的第一段正文就是用户可见内容，而不是半截 JSON。
        _LOGGER.warning("[AI面试][流式] stream start sessionId=%s", prepared_state.get("sessionId"))
        for token in graph.stream_turn_reply(prepared_state):
            if not token:
                continue
            stream_chunk_count += 1
            raw_buffer += token
            assistant_reply = extract_streaming_assistant_reply(raw_buffer)
            if assistant_reply and assistant_reply != last_emitted_reply:
                last_emitted_reply = assistant_reply
                if not first_token_emitted:
                    first_token_emitted = True
                    first_token_elapsed_ms = int((time.monotonic() - stream_started_at) * 1000)
                    _LOGGER.warning(
                        "[AI面试][流式] first token parsed sessionId=%s elapsedMs=%s",
                        prepared_state.get("sessionId"),
                        first_token_elapsed_ms,
                    )
                yield to_interview_chunk_event(assistant_reply)
    except Exception as exc:
        llm_error = '模型流式生成未完成，请重试'
        _LOGGER.warning(
            "[AI面试][流式] stream exception sessionId=%s chunkCount=%s error=%s",
            prepared_state.get("sessionId"),
            stream_chunk_count,
            llm_error,
        )

    stream_elapsed_ms = int((time.monotonic() - stream_started_at) * 1000)
    _LOGGER.warning(
        "[AI面试][流式] stream end sessionId=%s chunkCount=%s rawChars=%s elapsedMs=%s",
        prepared_state.get("sessionId"),
        stream_chunk_count,
        len(raw_buffer),
        stream_elapsed_ms,
    )

    if not raw_buffer.strip():
        _LOGGER.warning(
            "[AI面试][流式] stream ended without payload sessionId=%s error=%s",
            prepared_state.get("sessionId"),
            llm_error or "unknown",
        )
        yield to_interview_error_event(llm_error or "模型未返回任何内容")
        return

    try:
        # finalize 阶段负责把流式原始输出收敛为最终业务结果，再持久化到会话仓储。
        # done 事件必须只在这两个动作都完成后发出，避免前端认为本轮已经结束，
        # 但历史会话、评分或 memorySummary 还没有真正落库。
        finalize_started_at = time.monotonic()
        graph_output = graph.finalize_stream_turn(prepared_state, raw_buffer, llm_error=llm_error)
        finalize_elapsed_ms = int((time.monotonic() - finalize_started_at) * 1000)

        persist_started_at = time.monotonic()
        done_payload = persist_turn_result(request, graph_output, session_repository)
        persist_elapsed_ms = int((time.monotonic() - persist_started_at) * 1000)
        final_reply = str(done_payload.get("assistantReply") or "")

        if final_reply and final_reply != last_emitted_reply:
            yield to_interview_chunk_event(final_reply)

        total_elapsed_ms = int((time.monotonic() - turn_started_at) * 1000)
        _LOGGER.warning(
            "[AI面试][流式] turn done sessionId=%s finalizeMs=%s persistMs=%s totalMs=%s",
            prepared_state.get("sessionId"),
            finalize_elapsed_ms,
            persist_elapsed_ms,
            total_elapsed_ms,
        )
        yield to_interview_done_event(done_payload)
    except Exception as exc:  # pragma: no cover
        _LOGGER.warning("[AI面试][流式] finalize exception sessionId=%s error=%s", prepared_state.get("sessionId"), exc)
        yield to_interview_error_event(str(exc))
