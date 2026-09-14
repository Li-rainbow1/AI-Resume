"""复合文档图片解析状态、租约和图片 Chunk 的 SQLAlchemy 持久化职责。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError

from app.domain.exceptions.rag_exceptions import (
    ImageEnrichmentLeaseLostError,
    VectorStoreError,
    safe_rag_log_value,
)
from app.infrastructure.persistence.pgvector.sqlalchemy_rag_tables import image_extraction_columns

_IMAGE_ENRICHMENT_STATUSES = {
    "not_applicable",
    "queued",
    "processing",
    "completed",
    "partial_failed",
    "failed",
}
_IMAGE_EXTRACTION_STATUSES = {"processing", "indexed", "skipped", "failed"}


class RagImageStoreMixin:
    """提供图片增强任务的状态、租约和结果写入操作。"""

    def begin_image_enrichment(
        self,
        document_id: str,
        task_id: str | None = None,
        lease_seconds: int = 300,
    ) -> str | None:
        """原子领取文档级图片任务，只有对应队列任务才能提交结果。"""
        documents, _ = self._get_tables()
        safe_document_id = (document_id or "").strip()
        if not safe_document_id:
            raise VectorStoreError("图片增强缺少文档 ID")
        safe_task_id = (task_id or "").strip()
        lease = max(1, int(lease_seconds))
        attempt_token = uuid4().hex
        # PostgreSQL 的 make_interval 使用位置参数，最后一个参数表示秒数。
        stale_before = func.current_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, lease)
        conditions = [
            documents.c.document_id == safe_document_id,
            documents.c.status == "ready",
            or_(
                documents.c.image_enrichment_status != "processing",
                documents.c.image_enrichment_started_at.is_(None),
                documents.c.image_enrichment_started_at < stale_before,
            ),
        ]
        if safe_task_id:
            conditions.append(documents.c.image_enrichment_task_id == safe_task_id)
        else:
            # 兼容旧的同步调用方，但不能抢走已经排队的任务。
            conditions.append(documents.c.image_enrichment_status != "queued")
        statement = (
            update(documents)
            .where(and_(*conditions))
            .values(
                image_enrichment_status="processing",
                image_enrichment_message=None,
                image_enrichment_retryable=False,
                image_enrichment_attempt_token=attempt_token,
                image_enrichment_started_at=func.current_timestamp(),
                image_enrichment_queue_pending=False,
                image_enrichment_updated_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                claimed_document_id = connection.execute(statement).scalar_one_or_none()
                return attempt_token if claimed_document_id is not None else None
        except SQLAlchemyError as exc:
            _log_image_store("进入图片增强状态失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强状态更新失败，请稍后重试") from exc

    def queue_image_enrichment(
        self,
        document_id: str,
        task_id: str,
        force: bool = False,
    ) -> bool:
        """在数据库中先登记排队任务，Redis 入队失败时可由扫描器补偿。"""
        documents, _ = self._get_tables()
        safe_document_id = (document_id or "").strip()
        safe_task_id = (task_id or "").strip()
        if not safe_document_id or not safe_task_id:
            raise VectorStoreError("图片增强排队缺少文档或任务 ID")
        allowed_statuses = ["not_applicable", "completed", "partial_failed", "failed"]
        conditions = [
            documents.c.document_id == safe_document_id,
            documents.c.status == "ready",
            documents.c.image_enrichment_status.in_(allowed_statuses),
        ]
        if not force:
            conditions.append(documents.c.image_enrichment_retryable.is_(True))
        statement = (
            update(documents)
            .where(and_(*conditions))
            .values(
                image_enrichment_status="queued",
                image_enrichment_message="图片增强排队中",
                image_enrichment_retryable=False,
                image_enrichment_attempt_token=None,
                image_enrichment_started_at=None,
                image_enrichment_task_id=safe_task_id,
                image_enrichment_queued_at=func.current_timestamp(),
                image_enrichment_current_position=0,
                image_enrichment_queue_pending=True,
                image_enrichment_updated_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_image_store("登记图片增强队列任务失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强任务排队失败，请稍后重试") from exc

    def mark_image_enrichment_enqueued(self, document_id: str, task_id: str) -> bool:
        """Redis 已接受任务后清除补偿标记，状态仍保持 queued。"""
        documents, _ = self._get_tables()
        statement = (
            update(documents)
            .where(
                and_(
                    documents.c.document_id == (document_id or "").strip(),
                    documents.c.status == "ready",
                    documents.c.image_enrichment_status.in_(["queued", "failed"]),
                    documents.c.image_enrichment_task_id == (task_id or "").strip(),
                )
            )
            .values(
                image_enrichment_status="queued",
                image_enrichment_queue_pending=False,
                image_enrichment_message="图片增强排队中",
                image_enrichment_updated_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_image_store("确认图片增强入队失败", error_type=type(exc).__name__)
            return False

    def mark_image_enrichment_queue_failed(
        self,
        document_id: str,
        task_id: str,
        message: str,
    ) -> bool:
        """Redis 不可用时保留任务补偿标记，不影响正文检索。"""
        documents, _ = self._get_tables()
        statement = (
            update(documents)
            .where(
                and_(
                    documents.c.document_id == (document_id or "").strip(),
                    documents.c.status == "ready",
                    documents.c.image_enrichment_status == "queued",
                    documents.c.image_enrichment_task_id == (task_id or "").strip(),
                )
            )
            .values(
                image_enrichment_status="failed",
                image_enrichment_message=(message or "图片增强任务暂时无法入队").strip()[:500],
                image_enrichment_retryable=True,
                image_enrichment_queue_pending=True,
                image_enrichment_updated_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_image_store("记录图片增强入队失败状态失败", error_type=type(exc).__name__)
            return False

    def mark_image_enrichment_task_failed(
        self,
        document_id: str,
        task_id: str,
        message: str,
    ) -> bool:
        """Worker 出现未预期异常时保留可重试任务。"""
        documents, _ = self._get_tables()
        statement = (
            update(documents)
            .where(
                and_(
                    documents.c.document_id == (document_id or "").strip(),
                    documents.c.status == "ready",
                    documents.c.image_enrichment_task_id == (task_id or "").strip(),
                    documents.c.image_enrichment_status.in_(["queued", "processing"]),
                )
            )
            .values(
                image_enrichment_status="failed",
                image_enrichment_message=(message or "图片增强任务异常，可重试图片解析").strip()[:500],
                image_enrichment_retryable=True,
                image_enrichment_queue_pending=True,
                image_enrichment_updated_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_image_store("记录 Worker 图片增强失败状态失败", error_type=type(exc).__name__)
            return False

    def set_image_enrichment_terminal(
        self,
        document_id: str,
        status: str,
        message: str | None = None,
        retryable: bool = False,
    ) -> bool:
        """图片无需排队或附件不可补齐时写入明确的终态。"""
        safe_status = (status or "").strip().lower()
        if safe_status not in _IMAGE_ENRICHMENT_STATUSES or safe_status in {"queued", "processing"}:
            raise VectorStoreError("图片增强终态非法")
        documents, _ = self._get_tables()
        statement = (
            update(documents)
            .where(
                and_(
                    documents.c.document_id == (document_id or "").strip(),
                    documents.c.status == "ready",
                    documents.c.image_enrichment_status != "processing",
                )
            )
            .values(
                image_enrichment_status=safe_status,
                image_enrichment_message=(message or "").strip()[:500] or None,
                image_enrichment_retryable=bool(retryable),
                image_enrichment_queue_pending=False,
                image_enrichment_updated_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_image_store("保存图片增强终态失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强状态保存失败，请稍后重试") from exc

    def list_queued_image_enrichment_documents(self, limit: int = 100) -> list[dict[str, Any]]:
        """查询 Redis 入队中断或尚未确认的文档，供 Worker 补偿。"""
        documents, _ = self._get_tables()
        safe_limit = min(100, max(1, int(limit)))
        statement = (
            select(
                documents.c.document_id,
                documents.c.image_enrichment_task_id,
                documents.c.image_enrichment_status,
                documents.c.image_enrichment_queued_at,
                documents.c.image_enrichment_queue_pending,
            )
            .where(
                and_(
                    documents.c.status == "ready",
                    documents.c.image_enrichment_queue_pending.is_(True),
                    documents.c.image_enrichment_task_id.is_not(None),
                )
            )
            .order_by(documents.c.image_enrichment_queued_at.asc(), documents.c.updated_at.asc())
            .limit(safe_limit)
        )
        try:
            with self._get_engine().connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_image_store("查询待补偿图片增强任务失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强队列状态查询失败，请稍后重试") from exc
        return [dict(row) for row in rows]

    def finish_image_enrichment(
        self,
        document_id: str,
        status: str,
        message: str | None = None,
        retryable: bool = False,
        attempt_token: str | None = None,
        current_position: int | None = None,
    ) -> None:
        safe_status = (status or "").strip().lower()
        if safe_status not in _IMAGE_ENRICHMENT_STATUSES or safe_status == "processing":
            raise VectorStoreError("图片增强完成状态非法")
        safe_token = (attempt_token or "").strip()
        if not safe_token:
            raise VectorStoreError("图片增强完成状态缺少任务租约")
        documents, _ = self._get_tables()
        values: dict[str, Any] = {
            "image_enrichment_status": safe_status,
            "image_enrichment_message": (message or "").strip()[:500] or None,
            "image_enrichment_retryable": bool(retryable),
            "image_enrichment_queue_pending": False,
            "image_enrichment_updated_at": func.current_timestamp(),
            "updated_at": func.current_timestamp(),
        }
        if current_position is not None:
            values["image_enrichment_current_position"] = max(0, int(current_position))
        statement = (
            update(documents)
            .where(
                and_(
                    documents.c.document_id == (document_id or "").strip(),
                    documents.c.status == "ready",
                    documents.c.image_enrichment_status == "processing",
                    documents.c.image_enrichment_attempt_token == safe_token,
                )
            )
            .values(**values)
            .returning(documents.c.document_id)
        )
        try:
            with self._get_engine().begin() as connection:
                if connection.execute(statement).scalar_one_or_none() is None:
                    raise ImageEnrichmentLeaseLostError("图片增强任务租约已失效")
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_image_store("保存图片增强完成状态失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片增强状态保存失败，请稍后重试") from exc

    def list_document_image_extractions(self, document_id: str) -> list[dict[str, Any]]:
        safe_document_id = (document_id or "").strip()
        if not safe_document_id:
            return []
        extractions = self._get_image_extraction_table()
        statement = (
            select(*image_extraction_columns(extractions))
            .where(extractions.c.document_id == safe_document_id)
            .order_by(extractions.c.image_index.asc(), extractions.c.extraction_id.asc())
        )
        try:
            with self._get_engine().connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_image_store("查询文档图片解析状态失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片解析状态查询失败，请稍后重试") from exc
        return [dict(row) for row in rows]

    def claim_image_extraction(
        self,
        document_id: str,
        extraction: dict[str, Any],
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """按文档、来源位置原子领取一张图片，返回空值表示已有任务正在处理。"""
        extractions = self._get_image_extraction_table()
        documents, _ = self._get_tables()
        safe_document_id = (document_id or "").strip()
        source_kind = str(extraction.get("source_kind") or "").strip()
        source_locator = str(extraction.get("source_locator") or "").strip()
        if not safe_document_id or not source_kind or not source_locator:
            raise VectorStoreError("图片解析缺少文档或来源位置")
        extraction_id = str(extraction.get("extraction_id") or uuid4().hex).strip()
        token = uuid4().hex
        lease = max(1, int(lease_seconds))
        # PostgreSQL 的 make_interval 使用位置参数，最后一个参数表示秒数。
        stale_before = func.current_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, lease)
        values = {
            "extraction_id": extraction_id,
            "document_id": safe_document_id,
            "asset_id": str(extraction.get("asset_id") or "").strip() or None,
            "image_sha256": str(extraction.get("image_sha256") or "").strip().lower(),
            "file_name": str(extraction.get("file_name") or "image").strip()[:255],
            "content_type": str(extraction.get("content_type") or "application/octet-stream").strip(),
            "source_kind": source_kind,
            "source_locator": source_locator,
            "page_number": self._optional_int(extraction.get("page_number")),
            "paragraph_index": self._optional_int(extraction.get("paragraph_index")),
            "image_index": self._to_int(extraction.get("image_index"), 0),
            "classification": None,
            "ocr_text": "",
            "description": "",
            "confidence": None,
            "status": "processing",
            "error_message": None,
            "attempt_token": token,
            "attempt_started_at": func.current_timestamp(),
            "attempt_finished_at": None,
            "created_at": func.current_timestamp(),
            "updated_at": func.current_timestamp(),
        }
        try:
            with self._get_engine().begin() as connection:
                document_row = connection.execute(
                    select(documents.c.status)
                    .where(documents.c.document_id == safe_document_id)
                    .with_for_update()
                ).first()
                if document_row is None or str(document_row.status or "") != "ready":
                    return None
                inserted = connection.execute(
                    postgres_insert(extractions)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[
                            extractions.c.document_id,
                            extractions.c.source_kind,
                            extractions.c.source_locator,
                        ]
                    )
                    .returning(extractions.c.extraction_id)
                )
                inserted_extraction_id = inserted.scalar_one_or_none()
                row = connection.execute(
                    select(*image_extraction_columns(extractions))
                    .where(
                        and_(
                            extractions.c.document_id == safe_document_id,
                            extractions.c.source_kind == source_kind,
                            extractions.c.source_locator == source_locator,
                        )
                    )
                    .with_for_update()
                    .limit(1)
                ).mappings().first()
                if row is None:
                    raise VectorStoreError("图片解析任务领取失败")
                if str(inserted_extraction_id or "") == extraction_id:
                    return {**dict(row), "claimed": True, "attempt_token": token}
                status = str(row.get("status") or "")
                if status == "indexed" or status == "skipped":
                    return None
                result = connection.execute(
                    update(extractions)
                    .where(
                        and_(
                            extractions.c.extraction_id == row["extraction_id"],
                            extractions.c.status.in_(["processing", "failed"]),
                            or_(
                                extractions.c.status == "failed",
                                extractions.c.attempt_started_at.is_(None),
                                extractions.c.attempt_started_at < stale_before,
                            ),
                        )
                    )
                    .values(
                        asset_id=values["asset_id"],
                        image_sha256=values["image_sha256"],
                        file_name=values["file_name"],
                        content_type=values["content_type"],
                        page_number=values["page_number"],
                        paragraph_index=values["paragraph_index"],
                        image_index=values["image_index"],
                        status="processing",
                        error_message=None,
                        attempt_token=token,
                        attempt_started_at=func.current_timestamp(),
                        attempt_finished_at=None,
                        updated_at=func.current_timestamp(),
                    )
                    .returning(extractions.c.extraction_id)
                )
                if result.scalar_one_or_none() is None:
                    return None
                updated = connection.execute(
                    select(*image_extraction_columns(extractions))
                    .where(extractions.c.extraction_id == row["extraction_id"])
                ).mappings().first()
                return {**dict(updated or row), "claimed": True, "attempt_token": token}
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_image_store("领取图片解析任务失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片解析任务领取失败，请稍后重试") from exc

    def save_image_extraction_result(
        self,
        document_id: str,
        extraction: dict[str, Any],
        image_chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        embedding_model: str,
        attempt_token: str,
    ) -> int:
        """只允许租约持有者在一个事务中发布解析结果和图片 Chunk。"""
        safe_document_id = (document_id or "").strip()
        safe_token = (attempt_token or "").strip()
        safe_status = str(extraction.get("status") or "failed").strip().lower()
        if not safe_document_id or not safe_token:
            raise VectorStoreError("图片解析结果缺少文档或任务租约")
        if safe_status not in _IMAGE_EXTRACTION_STATUSES:
            raise VectorStoreError("图片解析状态非法")
        if len(image_chunks) != len(embeddings):
            raise VectorStoreError("图片 Chunk 与 Embedding 数量不一致")

        extractions = self._get_image_extraction_table()
        documents_table, chunks_table = self._get_tables()
        extraction_id = str(extraction.get("extraction_id") or "").strip()
        if not extraction_id:
            raise VectorStoreError("图片解析结果缺少解析 ID")
        normalized_chunks = self._normalize_image_chunks(
            image_chunks=image_chunks,
            document_id=safe_document_id,
            extraction_id=extraction_id,
        )
        normalized_embeddings = [self._normalize_embedding(item) for item in embeddings]
        if len(normalized_chunks) != len(normalized_embeddings):
            raise VectorStoreError("图片 Chunk 与 Embedding 数量不一致")
        update_values = {
            "asset_id": str(extraction.get("asset_id") or "").strip() or None,
            "image_sha256": str(extraction.get("image_sha256") or "").strip().lower(),
            "file_name": str(extraction.get("file_name") or "image").strip()[:255],
            "content_type": str(extraction.get("content_type") or "application/octet-stream").strip(),
            "page_number": self._optional_int(extraction.get("page_number")),
            "paragraph_index": self._optional_int(extraction.get("paragraph_index")),
            "image_index": self._to_int(extraction.get("image_index"), 0),
            "classification": str(extraction.get("classification") or "").strip().lower() or None,
            "ocr_text": str(extraction.get("ocr_text") or ""),
            "description": str(extraction.get("description") or ""),
            "confidence": self._optional_float(extraction.get("confidence")),
            "status": safe_status,
            "error_message": str(extraction.get("error_message") or "").strip()[:500] or None,
            "attempt_finished_at": func.current_timestamp(),
            "updated_at": func.current_timestamp(),
        }
        inserted_count = 0
        try:
            with self._get_engine().begin() as connection:
                document_row = connection.execute(
                    select(documents_table.c.status)
                    .where(documents_table.c.document_id == safe_document_id)
                    .with_for_update()
                ).first()
                if document_row is None or str(document_row.status or "") != "ready":
                    raise ImageEnrichmentLeaseLostError("文档已不再允许写入图片解析结果")
                owned = connection.execute(
                    update(extractions)
                    .where(
                        and_(
                            extractions.c.extraction_id == extraction_id,
                            extractions.c.document_id == safe_document_id,
                            extractions.c.status == "processing",
                            extractions.c.attempt_token == safe_token,
                        )
                    )
                    .values(**update_values)
                    .returning(extractions.c.extraction_id)
                )
                if owned.scalar_one_or_none() is None:
                    raise ImageEnrichmentLeaseLostError("图片解析任务租约已失效")

                if safe_status == "indexed" and normalized_chunks:
                    for offset, item in enumerate(normalized_chunks):
                        item["image_extraction_id"] = extraction_id
                        item["image_chunk_offset"] = offset
                        item["metadata"]["imageExtractionId"] = extraction_id
                        item["metadata"]["imageChunkOffset"] = offset
                    parameters = self._build_insert_parameters(
                        normalized_chunks,
                        normalized_embeddings,
                        embedding_model=embedding_model,
                    )
                    if parameters:
                        statement = postgres_insert(chunks_table).values(parameters)
                        statement = statement.on_conflict_do_nothing(
                            index_elements=[
                                chunks_table.c.image_extraction_id,
                                chunks_table.c.image_chunk_offset,
                            ],
                            index_where=and_(
                                chunks_table.c.image_extraction_id.is_not(None),
                                chunks_table.c.image_chunk_offset.is_not(None),
                            ),
                        )
                        # 不使用驱动的 rowcount；部分 PostgreSQL 驱动对带 RETURNING 的批量
                        # INSERT 会返回 0 或 -1，直接读取返回的主键才能准确区分新增和重复。
                        inserted_ids = connection.execute(
                            statement.returning(chunks_table.c.id)
                        ).scalars().all()
                        inserted_count = len(inserted_ids)
                        if inserted_count:
                            connection.execute(
                                update(documents_table)
                                .where(documents_table.c.document_id == safe_document_id)
                                .values(
                                    embedding_model=(embedding_model or self.embedding_model_name).strip(),
                                    embedding_dimensions=len(normalized_embeddings[0]),
                                    updated_at=func.current_timestamp(),
                                )
                            )

                self._refresh_image_statistics(
                    connection=connection,
                    documents_table=documents_table,
                    chunks_table=chunks_table,
                    extractions=extractions,
                    document_id=safe_document_id,
                )
        except (VectorStoreError, ImageEnrichmentLeaseLostError):
            raise
        except SQLAlchemyError as exc:
            _log_image_store("写入图片解析结果失败", error_type=type(exc).__name__)
            raise VectorStoreError("图片解析结果写入失败，请稍后重试") from exc
        return inserted_count

    def mark_image_extraction_failed(
        self,
        document_id: str,
        extraction_id: str,
        attempt_token: str,
        message: str,
    ) -> bool:
        """仅由当前图片租约持有者把异常中的任务落为 failed。"""
        extractions = self._get_image_extraction_table()
        statement = (
            update(extractions)
            .where(
                and_(
                    extractions.c.document_id == (document_id or "").strip(),
                    extractions.c.extraction_id == (extraction_id or "").strip(),
                    extractions.c.status == "processing",
                    extractions.c.attempt_token == (attempt_token or "").strip(),
                )
            )
            .values(
                status="failed",
                error_message=(message or "图片解析失败").strip()[:500] or "图片解析失败",
                attempt_finished_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(extractions.c.extraction_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_image_store("标记图片解析失败状态失败", error_type=type(exc).__name__)
            return False

    @staticmethod
    def _normalize_image_chunks(
        image_chunks: list[dict[str, Any]],
        document_id: str,
        extraction_id: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(image_chunks):
            if not isinstance(item, dict):
                continue
            current = dict(item)
            current["document_id"] = document_id
            current["image_extraction_id"] = extraction_id
            metadata = current.get("metadata")
            safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            current["metadata"] = safe_metadata
            current["original_filename"] = str(safe_metadata.get("originalFilename") or "document")
            current["original_content_type"] = str(
                safe_metadata.get("originalContentType") or "text/plain"
            )
            current["source_type"] = str(safe_metadata.get("sourceType") or "image")
            current["ingest_source"] = str(safe_metadata.get("ingestSource") or "image_vision")
            try:
                current["chunk_index"] = int(safe_metadata.get("chunkIndex", index))
            except (TypeError, ValueError):
                current["chunk_index"] = index
            normalized.append(current)
        return normalized

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _refresh_image_statistics(
        *,
        connection: Any,
        documents_table: Any,
        chunks_table: Any,
        extractions: Any,
        document_id: str,
    ) -> None:
        status_rows = connection.execute(
            select(extractions.c.status, func.count().label("count"))
            .where(extractions.c.document_id == document_id)
            .group_by(extractions.c.status)
        ).all()
        status_counts = {str(row.status): int(row.count or 0) for row in status_rows}
        image_chunk_count = int(
            connection.scalar(
                select(func.count())
                .select_from(chunks_table)
                .where(
                    and_(
                        chunks_table.c.document_id == document_id,
                        chunks_table.c.ingest_source == "image_vision",
                    )
                )
            )
            or 0
        )
        base_chunk_count = int(
            connection.scalar(
                select(func.count())
                .select_from(chunks_table)
                .where(
                    and_(
                        chunks_table.c.document_id == document_id,
                        chunks_table.c.ingest_source != "image_vision",
                    )
                )
            )
            or 0
        )
        result = connection.execute(
            update(documents_table)
            .where(documents_table.c.document_id == document_id)
            .values(
                image_candidate_count=sum(status_counts.values()),
                image_analyzed_count=status_counts.get("indexed", 0) + status_counts.get("skipped", 0),
                image_indexed_count=status_counts.get("indexed", 0),
                image_skipped_count=status_counts.get("skipped", 0),
                image_failed_count=status_counts.get("failed", 0),
                image_chunk_count=image_chunk_count,
                image_enrichment_current_position=sum(status_counts.values()),
                chunk_count=base_chunk_count + image_chunk_count,
                inserted_count=base_chunk_count + image_chunk_count,
                updated_at=func.current_timestamp(),
            )
            .returning(documents_table.c.document_id)
        )
        if result.scalar_one_or_none() is None:
            raise VectorStoreError("图片统计更新失败")


def _log_image_store(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][PgVector][图片增强] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
