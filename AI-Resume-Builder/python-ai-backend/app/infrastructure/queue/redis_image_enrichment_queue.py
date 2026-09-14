"""基于 Redis Stream 的图片增强队列适配器。"""

from __future__ import annotations

import socket
from time import time
from typing import Any
from uuid import uuid4

from app.application.ports.image_enrichment_queue_port import (
    ImageEnrichmentJob,
)
from app.domain.exceptions.rag_exceptions import VectorStoreError, safe_rag_log_value

_STREAM_NAME = "rag:image-enrichment"
_CONSUMER_GROUP = "rag-image-enrichment-workers"
_CLAIM_IDLE_MILLISECONDS = 300_000


class RedisImageEnrichmentQueue:
    """只在 Redis 中保存文档 ID 和任务 ID，不传输图片字节。"""

    def __init__(self, redis_url: str) -> None:
        safe_url = (redis_url or "").strip()
        if not safe_url:
            raise VectorStoreError("RAG_IMAGE_ENRICHMENT_REDIS_URL 未配置")
        try:
            from redis import Redis

            self._client = Redis.from_url(
                safe_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        except Exception as exc:
            raise VectorStoreError("图片增强 Redis 客户端初始化失败") from exc

    def ensure_consumer_group(self) -> None:
        try:
            self._client.xgroup_create(
                name=_STREAM_NAME,
                groupname=_CONSUMER_GROUP,
                id="0-0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc).upper():
                _log_queue("创建 Redis Stream 消费组失败", error_type=type(exc).__name__)
                raise VectorStoreError("图片增强队列初始化失败，请稍后重试") from exc

    def enqueue(self, document_id: str, task_id: str, trace_id: str | None = None) -> str:
        safe_document_id = (document_id or "").strip()
        safe_task_id = (task_id or "").strip()
        if not safe_document_id or not safe_task_id:
            raise VectorStoreError("图片增强队列消息缺少任务标识")
        try:
            message_id = self._client.xadd(
                _STREAM_NAME,
                {
                    "document_id": safe_document_id,
                    "task_id": safe_task_id,
                    "trace_id": (trace_id or "").strip()[:64],
                    "queued_at": str(time()),
                },
                maxlen=10_000,
                approximate=True,
            )
            _log_queue(
                "图片增强任务已入队",
                document_id=safe_document_id,
                task_id=safe_task_id,
                message_id=message_id,
            )
            return str(message_id)
        except Exception as exc:
            _log_queue("图片增强任务入队失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强任务暂时无法入队，请稍后重试") from exc

    def read(self, consumer_name: str, block_millis: int = 1000) -> list[ImageEnrichmentJob]:
        safe_consumer = (consumer_name or "").strip() or f"worker-{uuid4().hex[:8]}"
        try:
            jobs = self._claim_stale(safe_consumer)
            if jobs:
                return jobs
            response = self._client.xreadgroup(
                groupname=_CONSUMER_GROUP,
                consumername=safe_consumer,
                streams={_STREAM_NAME: ">"},
                count=1,
                block=max(0, int(block_millis)),
            )
            return self._parse_messages(response)
        except Exception as exc:
            _log_queue("读取图片增强任务失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强队列读取失败，请稍后重试") from exc

    def acknowledge(self, job: ImageEnrichmentJob) -> None:
        if not job.message_id:
            return
        try:
            self._client.xack(_STREAM_NAME, _CONSUMER_GROUP, job.message_id)
            self._client.xdel(_STREAM_NAME, job.message_id)
        except Exception as exc:
            _log_queue("确认图片增强任务失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强队列确认失败，请稍后重试") from exc

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _claim_stale(self, consumer_name: str) -> list[ImageEnrichmentJob]:
        autoclaim = getattr(self._client, "xautoclaim", None)
        if not callable(autoclaim):
            return []
        response = autoclaim(
            _STREAM_NAME,
            _CONSUMER_GROUP,
            consumer_name,
            min_idle_time=_CLAIM_IDLE_MILLISECONDS,
            start_id="0-0",
            count=1,
        )
        if not isinstance(response, (list, tuple)) or len(response) < 2:
            return []
        return self._parse_messages([(message_id, fields) for message_id, fields in response[1]])

    @staticmethod
    def _parse_messages(response: Any) -> list[ImageEnrichmentJob]:
        jobs: list[ImageEnrichmentJob] = []
        if not isinstance(response, (list, tuple)):
            return jobs
        for stream_item in response:
            if not isinstance(stream_item, (list, tuple)) or len(stream_item) < 2:
                continue
            messages = stream_item[1]
            if isinstance(stream_item[0], str) and isinstance(messages, dict):
                messages = [(stream_item[0], messages)]
            if not isinstance(messages, (list, tuple)):
                continue
            for message in messages:
                if not isinstance(message, (list, tuple)) or len(message) < 2:
                    continue
                message_id, fields = message[0], message[1]
                if not isinstance(fields, dict):
                    continue
                document_id = str(fields.get("document_id") or "").strip()
                task_id = str(fields.get("task_id") or "").strip()
                if not document_id or not task_id:
                    continue
                jobs.append(
                    ImageEnrichmentJob(
                        document_id=document_id,
                        task_id=task_id,
                        trace_id=str(fields.get("trace_id") or "").strip() or None,
                        message_id=str(message_id),
                    )
                )
        return jobs


def create_consumer_name() -> str:
    return f"worker-{socket.gethostname()[:24]}-{uuid4().hex[:8]}"


def _log_queue(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][图片队列] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
