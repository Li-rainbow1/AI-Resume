-- 为 PDF、DOCX 和 Markdown 图片增强增加解析记录及图片统计。

ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS image_candidate_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_analyzed_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_indexed_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_skipped_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_failed_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_chunk_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS rag_document_image_extractions (
    extraction_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    document_id TEXT NOT NULL,
    asset_id TEXT,
    image_sha256 VARCHAR(64) NOT NULL DEFAULT '',
    file_name VARCHAR(255) NOT NULL DEFAULT '',
    content_type VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
    source_kind VARCHAR(64) NOT NULL,
    source_locator VARCHAR(1024) NOT NULL,
    page_number INTEGER,
    paragraph_index INTEGER,
    image_index INTEGER NOT NULL DEFAULT 0,
    classification VARCHAR(32),
    ocr_text TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    confidence NUMERIC(6,5),
    status VARCHAR(32) NOT NULL DEFAULT 'processing',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_document_image_extractions_document
        FOREIGN KEY (document_id) REFERENCES rag_documents(document_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_rag_document_image_extractions_asset
        FOREIGN KEY (asset_id) REFERENCES rag_asset_objects(asset_id)
        ON DELETE SET NULL,
    CONSTRAINT chk_rag_document_image_extractions_classification
        CHECK (classification IS NULL OR classification IN ('text', 'table', 'diagram', 'decorative', 'empty')),
    CONSTRAINT chk_rag_document_image_extractions_status
        CHECK (status IN ('processing', 'indexed', 'skipped', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_document_image_extractions_source
    ON rag_document_image_extractions (document_id, source_kind, source_locator);

CREATE INDEX IF NOT EXISTS idx_rag_document_image_extractions_document
    ON rag_document_image_extractions (document_id, status);

CREATE INDEX IF NOT EXISTS idx_rag_document_image_extractions_asset
    ON rag_document_image_extractions (asset_id);
