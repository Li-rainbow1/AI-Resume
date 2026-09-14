"""知识库相关 SQLAlchemy 表声明和查询表达式。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, MetaData, Numeric, String, Table, Text, case, func, literal
from sqlalchemy.dialects.postgresql import JSONB

from app.domain.exceptions.rag_exceptions import VectorStoreError

RAG_DOCUMENT_FILE_TYPES = frozenset({"all", "pdf", "word", "txt", "md", "image", "other"})


def build_rag_tables() -> tuple[Table, Table]:
    """声明文件主表和 Chunk 表，不在运行时执行建表操作。"""
    try:
        from pgvector.sqlalchemy import Vector
    except ImportError as exc:
        raise VectorStoreError("缺少 pgvector 依赖，无法访问 PostgreSQL + pgvector") from exc

    metadata = MetaData()
    documents = Table(
        "rag_documents",
        metadata,
        Column("document_id", Text, primary_key=True),
        Column("source_id", String(255)),
        Column("original_filename", String(255), nullable=False),
        Column("original_content_type", String(255), nullable=False),
        Column("source_type", String(32), nullable=False),
        Column("ingest_source", String(64), nullable=False),
        Column("file_size_bytes", BigInteger, nullable=False),
        Column("sha256", String(64)),
        Column("object_key", String(512)),
        Column("preview_text", Text, nullable=False),
        Column("status", String(32), nullable=False),
        Column("archived", Boolean, nullable=False),
        Column("archived_at", DateTime(timezone=True)),
        Column("scope_kind", String(16), nullable=False),
        Column("project_id", Text),
        Column("chunk_count", Integer, nullable=False),
        Column("inserted_count", Integer, nullable=False),
        Column("embedding_model", String(128), nullable=False),
        Column("embedding_dimensions", Integer, nullable=False),
        Column("referenced_image_count", Integer, nullable=False),
        Column("matched_image_count", Integer, nullable=False),
        Column("missing_image_count", Integer, nullable=False),
        Column("image_candidate_count", Integer, nullable=False),
        Column("image_analyzed_count", Integer, nullable=False),
        Column("image_indexed_count", Integer, nullable=False),
        Column("image_skipped_count", Integer, nullable=False),
        Column("image_failed_count", Integer, nullable=False),
        Column("image_chunk_count", Integer, nullable=False),
        Column("image_enrichment_status", String(32), nullable=False),
        Column("image_enrichment_message", Text),
        Column("image_enrichment_retryable", Boolean, nullable=False),
        Column("image_enrichment_attempt_token", Text),
        Column("image_enrichment_started_at", DateTime(timezone=True)),
        Column("image_enrichment_task_id", Text),
        Column("image_enrichment_queued_at", DateTime(timezone=True)),
        Column("image_enrichment_current_position", Integer, nullable=False),
        Column("image_enrichment_queue_pending", Boolean, nullable=False),
        Column("image_enrichment_updated_at", DateTime(timezone=True), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    chunks = Table(
        "rag_document_chunks",
        metadata,
        Column("id", Text, primary_key=True, server_default=func.gen_random_uuid().cast(Text)),
        Column("content", Text, nullable=False),
        Column("metadata", JSONB, nullable=False),
        Column("embedding", Vector(), nullable=False),
        Column("source_id", String(255), nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("original_filename", String(255), nullable=False),
        Column("original_content_type", String(255), nullable=False),
        Column("source_type", String(32), nullable=False),
        Column("ingest_source", String(64), nullable=False),
        Column("embedding_model", String(128), nullable=False),
        Column("embedding_dimensions", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("document_id", Text),
        Column("image_extraction_id", Text),
        Column("image_chunk_offset", Integer),
    )
    return documents, chunks


def build_asset_tables() -> tuple[Table, Table]:
    """声明 Markdown 图片资源表。"""
    metadata = MetaData()
    assets = Table(
        "rag_asset_objects",
        metadata,
        Column("asset_id", Text, primary_key=True),
        Column("sha256", String(64), nullable=False),
        Column("object_key", String(512), nullable=False),
        Column("content_type", String(255), nullable=False),
        Column("file_size_bytes", BigInteger, nullable=False),
        Column("status", String(32), nullable=False),
        Column("attempt_token", Text),
        Column("attempt_started_at", DateTime(timezone=True)),
        Column("error_message", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    document_assets = Table(
        "rag_document_assets",
        metadata,
        Column("document_id", Text, nullable=False),
        Column("relative_path", String(1024), nullable=False),
        Column("asset_id", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    return assets, document_assets


def build_image_extraction_table() -> Table:
    """声明图片解析记录表。"""
    metadata = MetaData()
    return Table(
        "rag_document_image_extractions",
        metadata,
        Column("extraction_id", Text, primary_key=True),
        Column("document_id", Text, nullable=False),
        Column("asset_id", Text),
        Column("image_sha256", String(64), nullable=False),
        Column("file_name", String(255), nullable=False),
        Column("content_type", String(255), nullable=False),
        Column("source_kind", String(64), nullable=False),
        Column("source_locator", String(1024), nullable=False),
        Column("page_number", Integer),
        Column("paragraph_index", Integer),
        Column("image_index", Integer, nullable=False),
        Column("classification", String(32)),
        Column("ocr_text", Text, nullable=False),
        Column("description", Text, nullable=False),
        Column("confidence", Numeric(6, 5)),
        Column("status", String(32), nullable=False),
        Column("error_message", Text),
        Column("attempt_token", Text),
        Column("attempt_started_at", DateTime(timezone=True)),
        Column("attempt_finished_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )


def file_type_condition(documents: Table, file_type: str):
    """按扩展名优先、MIME 兜底生成文件类型筛选条件。"""
    safe_file_type = (file_type or "all").strip().lower()
    if safe_file_type not in RAG_DOCUMENT_FILE_TYPES:
        raise VectorStoreError("知识库文件类型筛选参数非法")
    if safe_file_type == "all":
        return None
    filename = func.lower(func.coalesce(documents.c.original_filename, literal("")))
    mime = func.lower(
        func.split_part(
            func.coalesce(documents.c.original_content_type, literal("")),
            literal(";"),
            literal(1),
        )
    )
    expression = case(
        (filename.like("%.pdf"), literal("pdf")),
        (filename.like("%.doc") | filename.like("%.docx"), literal("word")),
        (filename.like("%.txt"), literal("txt")),
        (filename.like("%.md") | filename.like("%.markdown"), literal("md")),
        (
            filename.like("%.png")
            | filename.like("%.jpg")
            | filename.like("%.jpeg")
            | filename.like("%.webp"),
            literal("image"),
        ),
        (mime == literal("application/pdf"), literal("pdf")),
        (
            mime.in_([
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ]),
            literal("word"),
        ),
        (mime == literal("text/plain"), literal("txt")),
        (mime == literal("text/markdown"), literal("md")),
        (mime.in_(["image/png", "image/jpeg", "image/jpg", "image/webp"]), literal("image")),
        else_=literal("other"),
    )
    return expression == literal(safe_file_type)


def document_columns(documents: Table) -> list[Column[Any]]:
    return [
        documents.c.document_id,
        documents.c.source_id,
        documents.c.original_filename,
        documents.c.original_content_type,
        documents.c.source_type,
        documents.c.ingest_source,
        documents.c.file_size_bytes,
        documents.c.sha256,
        documents.c.object_key,
        documents.c.preview_text,
        documents.c.status,
        documents.c.archived,
        documents.c.archived_at,
        documents.c.scope_kind,
        documents.c.project_id,
        documents.c.chunk_count,
        documents.c.inserted_count,
        documents.c.embedding_model,
        documents.c.embedding_dimensions,
        documents.c.referenced_image_count,
        documents.c.matched_image_count,
        documents.c.missing_image_count,
        documents.c.image_candidate_count,
        documents.c.image_analyzed_count,
        documents.c.image_indexed_count,
        documents.c.image_skipped_count,
        documents.c.image_failed_count,
        documents.c.image_chunk_count,
        documents.c.image_enrichment_status,
        documents.c.image_enrichment_message,
        documents.c.image_enrichment_retryable,
        documents.c.image_enrichment_attempt_token,
        documents.c.image_enrichment_started_at,
        documents.c.image_enrichment_task_id,
        documents.c.image_enrichment_queued_at,
        documents.c.image_enrichment_current_position,
        documents.c.image_enrichment_queue_pending,
        documents.c.image_enrichment_updated_at,
        documents.c.created_at,
        documents.c.updated_at,
    ]


def asset_columns(assets: Table) -> list[Column[Any]]:
    return [
        assets.c.asset_id,
        assets.c.sha256,
        assets.c.object_key,
        assets.c.content_type,
        assets.c.file_size_bytes,
        assets.c.status,
        assets.c.attempt_token,
        assets.c.attempt_started_at,
        assets.c.error_message,
        assets.c.created_at,
        assets.c.updated_at,
    ]


def image_extraction_columns(extractions: Table) -> list[Column[Any]]:
    return [
        extractions.c.extraction_id,
        extractions.c.document_id,
        extractions.c.asset_id,
        extractions.c.image_sha256,
        extractions.c.file_name,
        extractions.c.content_type,
        extractions.c.source_kind,
        extractions.c.source_locator,
        extractions.c.page_number,
        extractions.c.paragraph_index,
        extractions.c.image_index,
        extractions.c.classification,
        extractions.c.ocr_text,
        extractions.c.description,
        extractions.c.confidence,
        extractions.c.status,
        extractions.c.error_message,
        extractions.c.attempt_token,
        extractions.c.attempt_started_at,
        extractions.c.attempt_finished_at,
        extractions.c.created_at,
        extractions.c.updated_at,
    ]
