from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


class RealtimeProxyTicketStoreUnavailable(RuntimeError):
    """实时语音票据存储不可用时对上层暴露的安全异常。"""


def ticket_fingerprint(token: str) -> str:
    """生成用于日志关联的票据指纹，避免日志暴露完整票据。"""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class RealtimeProxyTicket:
    user_id: str
    provider: str
    context: list[dict[str, Any]]
    expires_at: float


class RealtimeProxyTicketStore:
    """一次性实时语音代理票据，Redis 模式不保存上游密钥或完整运行配置。"""

    _CONSUME_SCRIPT = """
    local value = redis.call('GET', KEYS[1])
    if value then
        redis.call('DEL', KEYS[1])
    end
    return value
    """

    def __init__(
        self,
        *,
        redis_url: str = "",
        ttl_seconds: int = 60,
        allow_memory: bool = False,
    ) -> None:
        self._ttl_seconds = max(15, int(ttl_seconds))
        self._max_entries = 10_000
        self._redis_url = str(redis_url or "").strip()
        self._allow_memory = bool(allow_memory)
        self._redis = self._build_redis_client(self._redis_url)
        self._tickets: dict[str, RealtimeProxyTicket] = {}
        self._lock = threading.RLock()
        if self._redis is None and not self._allow_memory:
            # 未显式开启内存模式时拒绝生成不可跨进程消费的票据，避免多进程部署出现隐性故障。
            raise RealtimeProxyTicketStoreUnavailable(
                "实时语音票据必须配置 Redis；仅单进程开发可显式开启内存票据"
            )

    def issue(
        self,
        *,
        user_id: str,
        provider: str,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        ticket = RealtimeProxyTicket(
            user_id=str(user_id),
            provider=str(provider),
            context=_sanitize_context(context or []),
            expires_at=time.time() + self._ttl_seconds,
        )
        if self._redis is not None:
            payload = json.dumps(
                {
                    "userId": ticket.user_id,
                    "provider": ticket.provider,
                    "context": ticket.context,
                    "expiresAt": ticket.expires_at,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            try:
                stored = self._redis.set(
                    self._redis_key(token),
                    payload,
                    ex=self._ttl_seconds,
                    nx=True,
                )
            except Exception as exc:
                raise RealtimeProxyTicketStoreUnavailable("实时语音会话暂时不可用，请稍后重试") from exc
            if not stored:
                raise RealtimeProxyTicketStoreUnavailable("实时语音会话暂时不可用，请稍后重试")
            return token

        now = time.time()
        with self._lock:
            self._purge(now)
            if len(self._tickets) >= self._max_entries:
                # 本地无 Redis 时仍限制内存增长，达到上限直接拒绝新会话。
                raise RealtimeProxyTicketStoreUnavailable("实时语音会话过多，请稍后重试")
            self._tickets[token] = ticket
        return token

    def consume(self, token: str, *, user_id: str | None = None) -> RealtimeProxyTicket | None:
        safe_token = str(token or "").strip()
        if not safe_token or len(safe_token) > 256:
            return None
        if self._redis is not None:
            try:
                raw_payload = self._redis.eval(
                    self._CONSUME_SCRIPT,
                    1,
                    self._redis_key(safe_token),
                )
            except Exception as exc:
                raise RealtimeProxyTicketStoreUnavailable("实时语音会话暂时不可用，请稍后重试") from exc
            return self._decode_ticket(raw_payload, user_id=user_id)

        now = time.time()
        with self._lock:
            self._purge(now)
            ticket = self._tickets.pop(safe_token, None)
        if ticket is None or ticket.expires_at <= now:
            return None
        if user_id is not None and ticket.user_id != str(user_id):
            return None
        return ticket

    @staticmethod
    def _build_redis_client(redis_url: str):
        if not redis_url:
            return None
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - dependency is installed in deployment
            raise RealtimeProxyTicketStoreUnavailable("实时语音票据依赖未安装") from exc
        return redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )

    @staticmethod
    def _redis_key(token: str) -> str:
        return f"resume-builder:realtime-ticket:{token}"

    def _decode_ticket(self, raw_payload: object, *, user_id: str | None) -> RealtimeProxyTicket | None:
        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8", errors="replace")
        if not isinstance(raw_payload, str) or not raw_payload:
            return None
        try:
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                return None
            expires_at = float(payload.get("expiresAt") or 0)
            ticket = RealtimeProxyTicket(
                user_id=str(payload.get("userId") or ""),
                provider=str(payload.get("provider") or ""),
                context=_sanitize_context(payload.get("context") or []),
                expires_at=expires_at,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        now = time.time()
        if not ticket.user_id or not ticket.provider or ticket.expires_at <= now:
            return None
        if user_id is not None and ticket.user_id != str(user_id):
            return None
        return ticket

    def _purge(self, now: float) -> None:
        expired = [token for token, ticket in self._tickets.items() if ticket.expires_at <= now]
        for token in expired:
            self._tickets.pop(token, None)


def _sanitize_context(context: object) -> list[dict[str, Any]]:
    """限制百炼上下文为最近 5 轮用户/助手消息，且不记录除文本外的字段。"""
    if not isinstance(context, list):
        return []
    result: list[dict[str, Any]] = []
    # 前端会多传几条消息以覆盖“AI 开场问题 + 用户回答 + AI 回复”的排列，
    # 后续由百炼适配器按完整轮次取最近 5 轮。这里先限制输入窗口，避免
    # 恶意请求携带超大历史造成无界内存或 Redis 请求体增长。
    for item in context[-20:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if isinstance(content, list):
            texts = [str(part.get("text") or "") for part in content if isinstance(part, dict)]
            content = " ".join(texts)
        text = str(content or "").strip()
        if not text:
            continue
        result.append({"role": role, "content": text[:400]})
    return result
