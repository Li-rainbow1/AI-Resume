"""基于 SQLAlchemy 的 PostgreSQL + pgvector 适配器。

该模块只负责把应用端口转换为 SQLAlchemy 表达式和事务操作，不在运行代码中
拼接或执行手写 SQL。表结构仍由 PostgreSQL Flyway 迁移负责创建。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, delete, exists, func, insert, literal, or_, select, update
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.application.ports.embedding_port import EmbeddingPort
from app.domain.exceptions.rag_exceptions import (
    EmbeddingError,
    RagDocumentConflictError,
    VectorStoreError,
    safe_rag_log_value,
)

from app.infrastructure.persistence.pgvector.rag_scope_conditions import retrieval_document_condition
from app.infrastructure.persistence.pgvector.sqlalchemy_rag_scope_store import RagScopeStoreMixin
from app.infrastructure.persistence.pgvector.sqlalchemy_rag_asset_store import RagAssetStoreMixin
from app.infrastructure.persistence.pgvector.sqlalchemy_rag_image_store import RagImageStoreMixin
from app.infrastructure.persistence.pgvector.sqlalchemy_rag_tables import (
    RAG_DOCUMENT_FILE_TYPES,
    build_asset_tables,
    build_image_extraction_table,
    build_rag_tables,
    document_columns,
    file_type_condition,
)

_RAG_DOCUMENT_FILE_TYPES = RAG_DOCUMENT_FILE_TYPES
_KNOWN_EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class PgVectorStoreAdapter(RagScopeStoreMixin, RagAssetStoreMixin, RagImageStoreMixin):
    """同时实现向量库端口和知识库文件仓储端口的 PostgreSQL 适配器。"""

    def __init__(
        self,
        connection_url: str,
        embedding_client: EmbeddingPort | None = None,
        embedding_model_name: str = "",
        connect_timeout_seconds: int = 8,
    ) -> None:
        self.connection_url = (connection_url or "").strip()
        self.embedding_client = embedding_client
        self.embedding_model_name = (embedding_model_name or "").strip()
        self.connect_timeout_seconds = self._normalize_connect_timeout(connect_timeout_seconds)
        self._engine: Engine | None = None
        self._tables: tuple[Any, Any] | None = None
        self._asset_tables: tuple[Any, Any] | None = None
        self._image_extraction_table: Any | None = None

    def add_documents(self, documents: list[dict[str, Any]]) -> int:
        normalized_documents = self._normalize_documents(documents)
        if not normalized_documents:
            _log_pgvector("add_documents 无有效文档，直接返回 0")
            return 0
        embeddings = self._embed_texts([item["content"] for item in normalized_documents])
        return self._insert_documents(normalized_documents, embeddings)

    def add_documents_with_embeddings(
        self,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> int:
        normalized_documents = self._normalize_documents(documents)
        if not normalized_documents:
            _log_pgvector("add_documents_with_embeddings 无有效文档，直接返回 0")
            return 0
        if len(normalized_documents) != len(embeddings):
            raise VectorStoreError("documents 与 embeddings 数量不一致，无法写入 pgvector")
        normalized_embeddings = [self._normalize_embedding(item) for item in embeddings]
        return self._insert_documents(normalized_documents, normalized_embeddings)

    def find_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        """按文件摘要查找已有资产，供上传前的重复内容校验使用。"""
        safe_sha256 = (sha256 or "").strip().lower()
        if not safe_sha256:
            return None
        documents, _ = self._get_tables()
        statement = (
            select(documents.c.document_id, documents.c.original_filename, documents.c.status, documents.c.archived, documents.c.scope_kind, documents.c.project_id)
            .where(documents.c.sha256 == safe_sha256)
            .order_by(documents.c.created_at.desc())
            .limit(1)
        )
        try:
            with self._get_engine().connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            _log_pgvector("查找重复知识库文件失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件元数据查询失败，请稍后重试") from exc
        return dict(row) if row else None

    def create_processing_document(self, document: dict[str, Any]) -> str:
        documents, _ = self._get_tables()
        document_id = str(document.get("document_id") or "").strip()
        if not document_id:
            raise VectorStoreError("知识库文件缺少文档 ID")
        statement = insert(documents).values(
            document_id=document_id,
            scope_kind=document.get("scope_kind", "unclassified"),
            project_id=document.get("project_id"),
            source_id=document.get("source_id"),
            original_filename=document.get("original_filename") or "",
            original_content_type=document.get("original_content_type") or "application/octet-stream",
            source_type=document.get("source_type") or "document",
            ingest_source=document.get("ingest_source") or "text_document",
            file_size_bytes=int(document.get("file_size_bytes") or 0),
            sha256=str(document.get("sha256") or "").strip().lower() or None,
            object_key=document.get("object_key"),
            preview_text="",
            status="processing",
            chunk_count=0,
            inserted_count=0,
            embedding_model="",
            embedding_dimensions=0,
            referenced_image_count=0,
            matched_image_count=0,
            missing_image_count=0,
            image_candidate_count=0,
            image_analyzed_count=0,
            image_indexed_count=0,
            image_skipped_count=0,
            image_failed_count=0,
            image_chunk_count=0,
            image_enrichment_status="not_applicable",
            image_enrichment_message=None,
            image_enrichment_retryable=False,
            image_enrichment_attempt_token=None,
            image_enrichment_started_at=None,
            image_enrichment_task_id=None,
            image_enrichment_queued_at=None,
            image_enrichment_current_position=0,
            image_enrichment_queue_pending=False,
            image_enrichment_updated_at=func.current_timestamp(),
        )
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
        except IntegrityError as exc:
            raise RagDocumentConflictError("相同内容的知识库文件已经存在") from exc
        except SQLAlchemyError as exc:
            _log_pgvector("创建知识库文件处理记录失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件记录创建失败，请稍后重试") from exc
        return document_id

    def complete_document_ingest(
        self,
        document_id: str,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]],
        preview_text: str,
        embedding_model: str,
    ) -> int:
        """在同一个 PostgreSQL 事务内写入全部 Chunk 并发布 ready 状态。"""
        normalized_documents = self._normalize_documents(documents, document_id=document_id)
        if len(normalized_documents) != len(embeddings):
            raise VectorStoreError("documents 与 embeddings 数量不一致，无法写入 pgvector")
        normalized_embeddings = [self._normalize_embedding(item) for item in embeddings]
        documents_table, chunks = self._get_tables()
        parameters = self._build_insert_parameters(normalized_documents, normalized_embeddings)
        dimensions = len(normalized_embeddings[0]) if normalized_embeddings else 0
        insert_statement = insert(chunks)
        update_statement = (
            update(documents_table)
            .where(
                and_(
                    documents_table.c.document_id == document_id,
                    documents_table.c.status == "processing",
                )
            )
            .values(
                preview_text=(preview_text or "")[:50_000],
                status="ready",
                chunk_count=len(normalized_documents),
                inserted_count=len(normalized_documents),
                embedding_model=(embedding_model or self.embedding_model_name).strip(),
                embedding_dimensions=dimensions,
                updated_at=func.current_timestamp(),
            )
        )
        try:
            with self._get_engine().begin() as connection:
                if parameters:
                    connection.execute(insert_statement, parameters)
                result = connection.execute(update_statement)
                if result.rowcount != 1:
                    raise VectorStoreError("知识库文件状态发布失败")
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_pgvector("知识库文件与向量事务写入失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件入库失败，请稍后重试") from exc
        return len(normalized_documents)

    def list_documents(
        self,
        page: int,
        page_size: int,
        file_type: str = "all",
        archive: str = "active",
        scope_kind: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        documents, _ = self._get_tables()
        safe_page = max(1, int(page))
        safe_page_size = min(100, max(1, int(page_size)))
        offset = (safe_page - 1) * safe_page_size
        conditions = [file_type_condition(documents, file_type)]
        if archive != "all":
            conditions.append(documents.c.archived.is_(archive == "archived"))
        if scope_kind:
            conditions.append(documents.c.scope_kind == scope_kind)
        if project_id:
            conditions.append(documents.c.project_id == project_id)
        condition = and_(*[item for item in conditions if item is not None])
        count_statement = select(func.count()).select_from(documents)
        list_statement = (
            select(*document_columns(documents))
            .select_from(documents)
            .order_by(documents.c.created_at.desc(), documents.c.document_id.desc())
            .limit(safe_page_size)
            .offset(offset)
        )
        if condition is not None:
            count_statement = count_statement.where(condition)
            list_statement = list_statement.where(condition)
        try:
            with self._get_engine().connect() as connection:
                total = int(connection.scalar(count_statement) or 0)
                rows = connection.execute(list_statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_pgvector("知识库文件列表查询失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件列表查询失败，请稍后重试") from exc
        return [dict(row) for row in rows], total

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        safe_document_id = (document_id or "").strip()
        if not safe_document_id:
            return None
        documents, _ = self._get_tables()
        statement = select(*document_columns(documents)).where(
            documents.c.document_id == safe_document_id
        )
        try:
            with self._get_engine().connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            _log_pgvector("知识库文件详情查询失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件详情查询失败，请稍后重试") from exc
        return dict(row) if row else None

    def get_preview_text(self, document: dict[str, Any], max_chars: int) -> str:
        safe_limit = min(50_000, max(1, int(max_chars)))
        return str(document.get("preview_text") or "")[:safe_limit]

    def mark_deleting(self, document_id: str) -> dict[str, Any] | None:
        documents, _ = self._get_tables()
        statement = (
            update(documents)
            .where(
                and_(
                    documents.c.document_id == document_id,
                    documents.c.status.in_(
                        ["processing", "ready", "legacy", "cleanup_failed", "deleting"]
                    ),
                )
            )
            .values(status="deleting", updated_at=func.current_timestamp())
            .returning(
                documents.c.document_id,
                documents.c.source_id,
                documents.c.original_filename,
                documents.c.object_key,
                documents.c.status,
                documents.c.chunk_count,
                documents.c.inserted_count,
            )
        )
        try:
            with self._get_engine().begin() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            _log_pgvector("标记知识库文件删除中失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件删除准备失败，请稍后重试") from exc
        return dict(row) if row else None

    def list_legacy_document_group(
        self,
        source_id: str | None,
        original_filename: str,
    ) -> list[dict[str, Any]]:
        """查询同一来源文件名下的历史主记录，供批量删除合并处理。"""
        documents, _ = self._get_tables()
        statement = (
            select(
                documents.c.document_id,
                documents.c.source_id,
                documents.c.original_filename,
                documents.c.object_key,
                documents.c.sha256,
                documents.c.status,
                documents.c.chunk_count,
                documents.c.inserted_count,
            )
            .where(
                and_(
                    documents.c.status.in_(["legacy", "cleanup_failed"]),
                    documents.c.object_key.is_(None),
                    documents.c.sha256.is_(None),
                    documents.c.source_id.is_not_distinct_from(source_id),
                    documents.c.original_filename.is_not_distinct_from(original_filename),
                )
            )
            .order_by(documents.c.created_at.asc(), documents.c.document_id.asc())
        )
        try:
            with self._get_engine().connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_pgvector("查询知识库历史文件分组失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库历史文件分组查询失败，请稍后重试") from exc
        return [dict(row) for row in rows]

    def delete_document_chunks(self, document: dict[str, Any]) -> int:
        documents, chunks = self._get_tables()
        status = str(document.get("status") or "")
        if status == "legacy":
            delete_statement = delete(chunks).where(
                and_(
                    chunks.c.document_id.is_(None),
                    chunks.c.source_id.is_not_distinct_from(document.get("source_id")),
                    chunks.c.original_filename.is_not_distinct_from(
                        document.get("original_filename")
                    ),
                )
            )
        else:
            delete_statement = delete(chunks).where(
                chunks.c.document_id == document.get("document_id")
            )
        expected_count = max(0, int(document.get("chunk_count") or 0))
        raw_document_ids = document.get("legacy_document_ids") if status == "legacy" else None
        if not isinstance(raw_document_ids, list) or not raw_document_ids:
            raw_document_ids = [document.get("document_id")]
        document_ids = [str(item).strip() for item in raw_document_ids if str(item or "").strip()]
        try:
            with self._get_engine().begin() as connection:
                result = connection.execute(delete_statement)
                deleted_count = max(0, int(result.rowcount or 0))
                if status != "legacy" and deleted_count < expected_count:
                    raise VectorStoreError("知识库文件向量数量不一致，已停止删除主记录，请重试清理")
                if document_ids:
                    connection.execute(
                        update(documents)
                        .where(documents.c.document_id.in_(document_ids))
                        .values(
                            chunk_count=0,
                            inserted_count=0,
                            updated_at=func.current_timestamp(),
                        )
                    )
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_pgvector("删除知识库文件向量失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件向量删除失败，请稍后重试") from exc
        return deleted_count

    def finalize_document_deletion(self, document_id: str) -> None:
        documents, _ = self._get_tables()
        statement = delete(documents).where(documents.c.document_id == document_id)
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as exc:
            _log_pgvector("删除知识库文件主记录失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件主记录删除失败，请稍后重试") from exc

    def mark_cleanup_failed(self, document_id: str) -> None:
        documents, _ = self._get_tables()
        statement = (
            update(documents)
            .where(documents.c.document_id == document_id)
            .values(status="cleanup_failed", updated_at=func.current_timestamp())
        )
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as exc:
            _log_pgvector("标记知识库文件清理失败状态失败", error_type=type(exc).__name__)

    def delete_unpublished_document(self, document_id: str) -> None:
        documents, _ = self._get_tables()
        statement = delete(documents).where(
            and_(documents.c.document_id == document_id, documents.c.status == "processing")
        )
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as exc:
            _log_pgvector("删除未发布知识库记录失败", error_type=type(exc).__name__)
            raise VectorStoreError("知识库文件失败记录清理失败，请稍后重试") from exc

    def similarity_search(self, query: str, top_k: int, project_ids: list[str] | None = None) -> list[dict[str, Any]]:
        safe_query = (query or "").strip()
        if not safe_query:
            return []
        query_embeddings = self._embed_texts([safe_query])
        if not query_embeddings:
            return []

        query_embedding = query_embeddings[0]
        query_dimensions = len(query_embedding)
        safe_top_k = max(1, int(top_k))
        documents, chunks = self._get_tables()
        joined_documents = documents.alias("documents")
        distance = chunks.c.embedding.cosine_distance(query_embedding)
        availability = retrieval_document_condition(documents, chunks, joined_documents, project_ids)
        statement = (
            select(
                chunks.c.source_id,
                chunks.c.content,
                chunks.c["metadata"],
                chunks.c.embedding_model,
                chunks.c.embedding_dimensions,
                chunks.c.document_id,
                (literal(1.0) - distance).label("similarity"),
            )
            .select_from(
                chunks.outerjoin(
                    joined_documents,
                    joined_documents.c.document_id == chunks.c.document_id,
                )
            )
            .where(
                and_(
                    chunks.c.embedding_dimensions == query_dimensions,
                    availability,
                )
            )
            .order_by(distance)
            .limit(safe_top_k)
        )
        if self.embedding_model_name:
            statement = statement.where(chunks.c.embedding_model == self.embedding_model_name)

        _log_pgvector(
            "开始执行相似度检索",
            top_k=safe_top_k,
            query_dimensions=query_dimensions,
            connect_timeout_seconds=self.connect_timeout_seconds,
        )
        try:
            with self._get_engine().connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_pgvector("相似度检索失败", error_type=type(exc).__name__)
            raise VectorStoreError("pgvector 检索失败，请稍后重试") from exc

        _log_pgvector("相似度检索完成", row_count=len(rows))
        results: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.get("metadata")
            safe_metadata = metadata if isinstance(metadata, dict) else {}
            results.append(
                {
                    "source_id": str(row.get("source_id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": {
                        **safe_metadata,
                        "topK": safe_top_k,
                        "engine": "pgvector",
                        "pgvectorAvailable": True,
                        "embeddingModel": str(row.get("embedding_model") or ""),
                        "embeddingDimensions": int(row.get("embedding_dimensions") or 0),
                        "similarity": float(row.get("similarity") or 0.0),
                    },
                }
            )
            if row.get("document_id"):
                results[-1]["metadata"]["documentId"] = str(row["document_id"])
        return results

    def has_incompatible_embedding_profiles(self) -> bool:
        """检查已有向量是否能被当前 Embedding 配置检索。"""
        expected_model = self.embedding_model_name.strip()
        if not expected_model:
            return False
        _, chunks = self._get_tables()
        statement = select(chunks.c.embedding_model, chunks.c.embedding_dimensions).group_by(
            chunks.c.embedding_model, chunks.c.embedding_dimensions
        )
        _log_pgvector(
            "检查已有向量的 Embedding 兼容性",
            expected_model=expected_model,
            connect_timeout_seconds=self.connect_timeout_seconds,
        )
        try:
            with self._get_engine().connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_pgvector(
                "检查已有向量的 Embedding 兼容性失败",
                error_type=type(exc).__name__,
            )
            raise VectorStoreError("pgvector 兼容性检查失败，请稍后重试") from exc

        profiles = {
            (
                str(row.get("embedding_model") or "").strip(),
                self._to_int(row.get("embedding_dimensions"), 0),
            )
            for row in rows
        }
        if not profiles:
            return False
        expected_dimensions = self._expected_embedding_dimensions(expected_model)
        expected_profile_dimensions = {
            dimensions for model, dimensions in profiles if model == expected_model
        }
        for model, dimensions in profiles:
            if model != expected_model or dimensions <= 0:
                return True
            if expected_dimensions is not None and dimensions != expected_dimensions:
                return True
        return len(expected_profile_dimensions) > 1

    def _insert_documents(
        self,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> int:
        if len(documents) != len(embeddings):
            raise VectorStoreError("documents 与 embeddings 数量不一致，无法写入 pgvector")
        _, chunks = self._get_tables()
        parameters = self._build_insert_parameters(documents, embeddings)
        _log_pgvector(
            "开始写入 pgvector",
            document_count=len(parameters),
            connect_timeout_seconds=self.connect_timeout_seconds,
        )
        try:
            with self._get_engine().begin() as connection:
                if parameters:
                    connection.execute(insert(chunks), parameters)
        except SQLAlchemyError as exc:
            _log_pgvector("写入 pgvector 失败", error_type=type(exc).__name__)
            raise VectorStoreError("pgvector 写入失败，请稍后重试") from exc
        _log_pgvector("写入 pgvector 完成", inserted_count=len(parameters))
        return len(parameters)

    def _build_insert_parameters(
        self,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]],
        embedding_model: str | None = None,
    ) -> list[dict[str, Any]]:
        parameters: list[dict[str, Any]] = []
        for document, embedding in zip(documents, embeddings, strict=True):
            parameters.append(
                {
                    "document_id": document.get("document_id"),
                    "source_id": document["source_id"],
                    "chunk_index": document["chunk_index"],
                    "original_filename": document["original_filename"],
                    "original_content_type": document["original_content_type"],
                    "source_type": document["source_type"],
                    "ingest_source": document["ingest_source"],
                    "content": document["content"],
                    "metadata": document["metadata"],
                    "embedding": embedding,
                    "embedding_model": (embedding_model or self.embedding_model_name).strip(),
                    "embedding_dimensions": len(embedding),
                    "image_extraction_id": document.get("image_extraction_id"),
                    "image_chunk_offset": document.get("image_chunk_offset"),
                }
            )
        return parameters

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.embedding_client is None:
            raise EmbeddingError("未配置 Embedding 客户端，无法执行向量化")
        return [self._normalize_embedding(item) for item in self.embedding_client.embed_texts(texts)]

    def _normalize_documents(
        self,
        documents: list[dict[str, Any]],
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(documents):
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()
            if not content:
                continue
            source_id = (
                str(raw.get("source_id") or raw.get("sourceId") or "").strip()
                or f"doc-{index + 1}"
            )
            metadata = raw.get("metadata")
            safe_metadata = metadata if isinstance(metadata, dict) else {}
            chunk_index = self._to_int(safe_metadata.get("chunkIndex"), index)
            original_filename = str(safe_metadata.get("originalFilename") or source_id)
            original_content_type = str(
                safe_metadata.get("originalContentType") or "text/plain"
            )
            source_type = str(safe_metadata.get("sourceType") or "document")
            ingest_source = str(safe_metadata.get("ingestSource") or "text_document")
            normalized.append(
                {
                    "document_id": str(raw.get("document_id") or document_id or "").strip() or None,
                    "source_id": source_id,
                    "chunk_index": chunk_index,
                    "original_filename": original_filename,
                    "original_content_type": original_content_type,
                    "source_type": source_type,
                    "ingest_source": ingest_source,
                    "content": content,
                    "metadata": {
                        **safe_metadata,
                        "originalFilename": original_filename,
                        "originalContentType": original_content_type,
                        "sourceType": source_type,
                        "ingestSource": ingest_source,
                        "chunkIndex": chunk_index,
                    },
                }
            )
        return normalized

    @staticmethod
    def _normalize_embedding(raw_embedding: Any) -> list[float]:
        if not isinstance(raw_embedding, (list, tuple)) or not raw_embedding:
            raise VectorStoreError("Embedding 数据为空或格式非法")
        try:
            return [float(value) for value in raw_embedding]
        except (TypeError, ValueError) as exc:
            raise VectorStoreError("Embedding 数据格式非法") from exc

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_connect_timeout(raw_timeout: int) -> int:
        try:
            normalized = int(raw_timeout)
        except (TypeError, ValueError):
            return 8
        return max(1, normalized)

    @staticmethod
    def _expected_embedding_dimensions(model_name: str) -> int | None:
        base_model = model_name.split("@", 1)[0].strip()
        return _KNOWN_EMBEDDING_DIMENSIONS.get(base_model)

    def _get_engine(self) -> Engine:
        if self._engine is not None:
            return self._engine
        if not self.connection_url:
            raise VectorStoreError("PGVECTOR_DATASOURCE_URL 未配置，无法连接 pgvector")
        try:
            sqlalchemy_url = self._to_sqlalchemy_url(self.connection_url)
            self._engine = create_engine(
                sqlalchemy_url,
                connect_args={
                    "connect_timeout": self.connect_timeout_seconds,
                    "options": f"-c statement_timeout={self.connect_timeout_seconds * 1000}",
                },
                pool_pre_ping=True,
            )
        except (ImportError, SQLAlchemyError, ValueError) as exc:
            _log_pgvector("初始化 PostgreSQL 连接失败", error_type=type(exc).__name__)
            raise VectorStoreError("pgvector 连接配置不可用，请稍后重试") from exc
        return self._engine

    def _get_tables(self) -> tuple[Any, Any]:
        if self._tables is None:
            self._tables = build_rag_tables()
        return self._tables

    def _get_asset_tables(self) -> tuple[Any, Any]:
        if self._asset_tables is None:
            self._asset_tables = build_asset_tables()
        return self._asset_tables

    def _get_image_extraction_table(self) -> Any:
        if self._image_extraction_table is None:
            self._image_extraction_table = build_image_extraction_table()
        return self._image_extraction_table

    @staticmethod
    def _to_sqlalchemy_url(connection_url: str) -> str:
        safe_url = connection_url.strip()
        if safe_url.startswith("jdbc:"):
            safe_url = safe_url[5:]
        if safe_url.startswith("postgres://"):
            safe_url = "postgresql://" + safe_url[len("postgres://") :]
        parsed = urlsplit(safe_url)
        if parsed.scheme == "postgresql+psycopg":
            return safe_url
        if parsed.scheme != "postgresql":
            raise ValueError("PGVECTOR_DATASOURCE_URL 必须使用 PostgreSQL URL")
        return urlunsplit(
            (
                "postgresql+psycopg",
                parsed.netloc,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )


def _log_pgvector(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][PgVector] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
