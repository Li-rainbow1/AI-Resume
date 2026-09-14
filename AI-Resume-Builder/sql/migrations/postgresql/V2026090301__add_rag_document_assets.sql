-- 为知识库增加原始文件资产主表，并保留旧向量的 legacy 兼容展示。

CREATE TABLE IF NOT EXISTS rag_documents (
    document_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    source_id VARCHAR(255),
    original_filename VARCHAR(255) NOT NULL DEFAULT '',
    original_content_type VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
    source_type VARCHAR(32) NOT NULL DEFAULT 'document',
    ingest_source VARCHAR(64) NOT NULL DEFAULT 'text_document',
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 VARCHAR(64),
    object_key VARCHAR(512),
    preview_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'processing',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    embedding_model VARCHAR(128) NOT NULL DEFAULT '',
    embedding_dimensions INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_rag_documents_status
        CHECK (status IN ('processing', 'ready', 'legacy', 'deleting', 'cleanup_failed'))
);

ALTER TABLE rag_document_chunks
    ADD COLUMN IF NOT EXISTS document_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_rag_document_chunks_document'
    ) THEN
        ALTER TABLE rag_document_chunks
            ADD CONSTRAINT fk_rag_document_chunks_document
            FOREIGN KEY (document_id) REFERENCES rag_documents(document_id)
            ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_rag_documents_created_at
    ON rag_documents (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rag_documents_status
    ON rag_documents (status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_documents_sha256
    ON rag_documents (sha256)
    WHERE sha256 IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rag_document_chunks_document_id
    ON rag_document_chunks (document_id, chunk_index);

INSERT INTO rag_documents (
    document_id,
    source_id,
    original_filename,
    original_content_type,
    source_type,
    ingest_source,
    file_size_bytes,
    preview_text,
    status,
    chunk_count,
    inserted_count,
    embedding_model,
    embedding_dimensions,
    created_at,
    updated_at
)
SELECT
    gen_random_uuid()::text,
    grouped.source_id,
    grouped.original_filename,
    grouped.original_content_type,
    grouped.source_type,
    grouped.ingest_source,
    0,
    LEFT(grouped.preview_text, 50000),
    'legacy',
    grouped.chunk_count,
    grouped.chunk_count,
    grouped.embedding_model,
    grouped.embedding_dimensions,
    grouped.created_at,
    CURRENT_TIMESTAMP
FROM (
    SELECT
        source_id,
        original_filename,
        MAX(original_content_type) AS original_content_type,
        MAX(source_type) AS source_type,
        MAX(ingest_source) AS ingest_source,
        COUNT(*)::integer AS chunk_count,
        MAX(embedding_model) AS embedding_model,
        MAX(embedding_dimensions)::integer AS embedding_dimensions,
        MIN(created_at) AS created_at,
        string_agg(content, E'\n\n' ORDER BY chunk_index, created_at) AS preview_text
    FROM rag_document_chunks
    WHERE document_id IS NULL
    GROUP BY source_id, original_filename
) AS grouped
WHERE NOT EXISTS (
    SELECT 1
    FROM rag_documents existing
    WHERE existing.status = 'legacy'
      AND existing.source_id IS NOT DISTINCT FROM grouped.source_id
      AND existing.original_filename = grouped.original_filename
);
