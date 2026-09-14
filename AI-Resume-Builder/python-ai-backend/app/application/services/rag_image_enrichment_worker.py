"""图片增强 Redis Stream Worker 主循环。"""

from __future__ import annotations

import time
from typing import Any

from app.application.services.rag_image_enrichment_processor import (
    process_rag_document_image_enrichment,
)
from app.bootstrap.container import (
    build_image_enrichment_queue,
    build_rag_document_repository,
    resolve_settings,
)
from app.domain.exceptions.rag_exceptions import (
    RagDocumentConflictError,
    RagDocumentNotFoundError,
    VectorStoreError,
    safe_rag_log_value,
)
from app.infrastructure.queue.redis_image_enrichment_queue import create_consumer_name

_PENDING_SCAN_INTERVAL_SECONDS = 15.0
_RECONNECT_DELAY_SECONDS = 5.0


def run_rag_image_enrichment_worker() -> None:
    """持续消费图片任务；未确认消息由新 Worker 通过租约接管。"""
    settings = resolve_settings()
    repository = build_rag_document_repository(settings)
    image_queue = None
    consumer_name = create_consumer_name()
    next_scan_at = 0.0
    _log_worker("图片增强 Worker 启动", consumer=consumer_name)
    try:
        while True:
            try:
                if image_queue is None:
                    image_queue = build_image_enrichment_queue(settings)
                    image_queue.ensure_consumer_group()
                    _log_worker("Redis Stream 消费组已就绪", consumer=consumer_name)

                now = time.monotonic()
                if now >= next_scan_at:
                    _requeue_pending_documents(repository, image_queue)
                    next_scan_at = now + _PENDING_SCAN_INTERVAL_SECONDS

                jobs = image_queue.read(consumer_name, block_millis=1000)
                for job in jobs:
                    _process_job(repository, image_queue, job)
            except VectorStoreError as exc:
                _log_worker("队列或数据库暂时不可用，稍后重连", error_type=type(exc).__name__)
                if image_queue is not None:
                    image_queue.close()
                    image_queue = None
                time.sleep(_RECONNECT_DELAY_SECONDS)
            except Exception as exc:
                _log_worker("Worker 主循环异常，稍后继续", error_type=type(exc).__name__)
                time.sleep(_RECONNECT_DELAY_SECONDS)
    except KeyboardInterrupt:
        _log_worker("收到停止信号，Worker 退出")
    finally:
        if image_queue is not None:
            image_queue.close()


def _process_job(repository: Any, image_queue: Any, job: Any) -> None:
    try:
        process_rag_document_image_enrichment(
            document_id=job.document_id,
            task_id=job.task_id,
            trace_id=job.trace_id,
        )
    except RagDocumentNotFoundError:
        # 文档被管理员删除后，旧消息只需确认，不允许重新创建图片 Chunk。
        _log_worker("文档已不存在，丢弃图片任务", document_id=job.document_id, task_id=job.task_id)
    except RagDocumentConflictError:
        _log_worker("文档已删除或不可处理，丢弃图片任务", document_id=job.document_id, task_id=job.task_id)
    except Exception as exc:
        marked = repository.mark_image_enrichment_task_failed(
            job.document_id,
            job.task_id,
            "图片 Worker 异常，可重试图片解析",
        )
        _log_worker(
            "图片任务处理异常",
            document_id=job.document_id,
            task_id=job.task_id,
            state_saved=marked,
            error_type=type(exc).__name__,
        )
        if not marked:
            # 数据库也不可用时不确认消息，交给 Redis 的 pending 接管机制重试。
            raise VectorStoreError("图片任务失败状态暂时无法保存") from exc
    image_queue.acknowledge(job)


def _requeue_pending_documents(repository: Any, image_queue: Any) -> None:
    for document in repository.list_queued_image_enrichment_documents(limit=50):
        document_id = str(document.get("document_id") or "").strip()
        task_id = str(document.get("image_enrichment_task_id") or "").strip()
        if not document_id or not task_id:
            continue
        try:
            image_queue.enqueue(document_id, task_id)
            repository.mark_image_enrichment_enqueued(document_id, task_id)
            _log_worker("补偿图片任务入队", document_id=document_id, task_id=task_id)
        except Exception as exc:
            _log_worker(
                "补偿图片任务入队失败",
                document_id=document_id,
                task_id=task_id,
                error_type=type(exc).__name__,
            )


def _log_worker(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][图片Worker] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
