from __future__ import annotations

import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.bootstrap.container import build_realtime_proxy_ticket_store, resolve_settings
from app.infrastructure.realtime.asr_adapters import bridge_realtime_session
from app.infrastructure.realtime.proxy_tickets import RealtimeProxyTicketStoreUnavailable, ticket_fingerprint

_LOGGER = logging.getLogger("uvicorn.error")
proxy_router = APIRouter(tags=["ai-realtime"])


@proxy_router.websocket("/ws/ai/realtime-asr")
async def realtime_asr_websocket(websocket: WebSocket) -> None:
    ticket_hash = "-"
    await websocket.accept()
    _LOGGER.info("[实时语音] WebSocket 已接受 pid=%s", os.getpid())
    try:
        first = await websocket.receive_json()
        if not isinstance(first, dict) or first.get("type") != "session.start":
            _LOGGER.warning("[实时语音] 会话启动消息无效 pid=%s", os.getpid())
            await websocket.send_json({"type": "error", "message": "实时语音会话无效"})
            await websocket.close(code=1008)
            return
        raw_ticket = str(first.get("ticket") or "")
        ticket_hash = ticket_fingerprint(raw_ticket) if raw_ticket else "-"
        _LOGGER.info(
            "[实时语音] WebSocket 请求消费票据 pid=%s ticket_fingerprint=%s",
            os.getpid(),
            ticket_hash,
        )
        try:
            ticket = build_realtime_proxy_ticket_store().consume(raw_ticket)
        except RealtimeProxyTicketStoreUnavailable:
            _LOGGER.warning(
                "[实时语音] 票据消费失败 pid=%s ticket_fingerprint=%s 原因=票据存储不可用",
                os.getpid(),
                ticket_hash,
            )
            await websocket.send_json({"type": "error", "message": "实时语音服务暂时不可用，请稍后重试"})
            await websocket.close(code=1013)
            return
        if ticket is None:
            _LOGGER.warning(
                "[实时语音] 票据消费失败 pid=%s ticket_fingerprint=%s 原因=票据不存在或已过期",
                os.getpid(),
                ticket_hash,
            )
            await websocket.send_json({"type": "error", "message": "实时语音会话已过期，请重试"})
            await websocket.close(code=1008)
            return
        # 票据中的上下文由已鉴权的 HTTP 请求创建；运行时密钥重新从当前配置读取，
        # 避免把完整 Settings（包含上游密钥）写入 Redis，也避免配置切换后继续使用旧凭据。
        settings = resolve_settings()
        if settings.realtime_provider != ticket.provider:
            _LOGGER.warning(
                "[实时语音] 票据消费失败 pid=%s ticket_fingerprint=%s 原因=语音配置已更新",
                os.getpid(),
                ticket_hash,
            )
            await websocket.send_json({"type": "error", "message": "实时语音配置已更新，请重试"})
            await websocket.close(code=4091)
            return
        _LOGGER.info(
            "[实时语音] 票据消费成功 pid=%s ticket_fingerprint=%s provider=%s",
            os.getpid(),
            ticket_hash,
            ticket.provider,
        )
        await bridge_realtime_session(websocket, settings, ticket.context)
    except WebSocketDisconnect:
        _LOGGER.info(
            "[实时语音] WebSocket 已断开 pid=%s ticket_fingerprint=%s",
            os.getpid(),
            ticket_hash,
        )
        return
    except Exception:
        # 上游异常统一脱敏，日志不打印请求头、音频和配置密钥。
        _LOGGER.warning(
            "[实时语音] 代理会话异常 pid=%s ticket_fingerprint=%s",
            os.getpid(),
            ticket_hash,
        )
        try:
            await websocket.send_json({"type": "error", "message": "实时语音服务暂时不可用"})
        except Exception:
            pass
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
