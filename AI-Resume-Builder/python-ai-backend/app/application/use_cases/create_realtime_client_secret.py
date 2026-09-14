import logging
import os

from app.application.dto.realtime_dto import RealtimeClientSecretRequestDto, RealtimeClientSecretResponseDto
from app.bootstrap.container import (
    build_realtime_client,
    build_realtime_proxy_ticket_store,
    resolve_settings,
)
from app.domain.exceptions.auth_exceptions import AuthServiceUnavailableError
from app.infrastructure.realtime.proxy_tickets import RealtimeProxyTicketStoreUnavailable, ticket_fingerprint

_LOGGER = logging.getLogger("uvicorn.error")


def create_realtime_client_secret(
    request: RealtimeClientSecretRequestDto,
    *,
    user_id: str,
) -> RealtimeClientSecretResponseDto:
    settings = resolve_settings()
    provider = settings.realtime_provider
    if provider == "openai":
        client = build_realtime_client(settings)
        # 普通用户只能使用管理员启用的全局模型；不传固定 language，
        # 让 OpenAI 的转写服务自动处理中文为主的中英混合语音。
        try:
            response_payload = client.create_client_secret(
                model=settings.openai_realtime_transcription_model,
                language=None,
            )
        except Exception as exc:
            # 上游响应可能包含请求地址或鉴权细节，不能原样返回给浏览器。
            raise AuthServiceUnavailableError("实时语音服务暂时不可用，请检查配置") from exc
        return RealtimeClientSecretResponseDto(
            client_secret=str(response_payload.get("clientSecret") or ""),
            provider="openai",
            expires_at=response_payload.get("expiresAt"),
            model=str(response_payload.get("model") or settings.openai_realtime_transcription_model),
            language=None,
            realtime_api_base_url=str(response_payload.get("realtimeApiBaseUrl") or ""),
            realtime_calls_path=str(response_payload.get("realtimeCallsPath") or ""),
            sample_rate=None,
        )

    try:
        ticket = build_realtime_proxy_ticket_store().issue(
            user_id=user_id,
            provider=provider,
            context=request.context or [],
        )
    except RealtimeProxyTicketStoreUnavailable as exc:
        raise AuthServiceUnavailableError(str(exc)) from exc
    # 只记录进程号和不可逆指纹，用于验证多 Worker 下签发与消费是否跨进程，
    # 避免把完整票据、用户上下文或上游鉴权信息写入日志。
    _LOGGER.info(
        "[实时语音] 代理票据已签发 pid=%s ticket_fingerprint=%s 存储=%s",
        os.getpid(),
        ticket_fingerprint(ticket),
        "Redis" if settings.realtime_proxy_ticket_redis_url else "内存",
    )
    return RealtimeClientSecretResponseDto(
        client_secret=None,
        provider=provider,
        # 代理票据响应不回显管理员选择的模型或任何其他全局配置。
        model=None,
        language=None,
        session_ticket=ticket,
        websocket_path="/ws/ai/realtime-asr",
        sample_rate=16000,
    )
