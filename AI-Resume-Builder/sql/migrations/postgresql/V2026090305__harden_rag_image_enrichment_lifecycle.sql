-- 固化复合文档图片增强的租约、结果幂等和资源清理状态。

ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS image_enrichment_status VARCHAR(32) NOT NULL DEFAULT 'not_applicable',
    ADD COLUMN IF NOT EXISTS image_enrichment_message TEXT,
    ADD COLUMN IF NOT EXISTS image_enrichment_retryable BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS image_enrichment_attempt_token TEXT,
    ADD COLUMN IF NOT EXISTS image_enrichment_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS image_enrichment_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE rag_documents
    DROP CONSTRAINT IF EXISTS chk_rag_documents_image_enrichment_status;

ALTER TABLE rag_documents
    ADD CONSTRAINT chk_rag_documents_image_enrichment_status
        CHECK (image_enrichment_status IN ('not_applicable', 'processing', 'completed', 'partial_failed', 'failed'));

ALTER TABLE rag_document_image_extractions
    ADD COLUMN IF NOT EXISTS attempt_token TEXT,
    ADD COLUMN IF NOT EXISTS attempt_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempt_finished_at TIMESTAMPTZ;

ALTER TABLE rag_document_chunks
    ADD COLUMN IF NOT EXISTS image_extraction_id TEXT,
    ADD COLUMN IF NOT EXISTS image_chunk_offset INTEGER;

ALTER TABLE rag_document_chunks
    DROP CONSTRAINT IF EXISTS fk_rag_document_chunks_image_extraction;

ALTER TABLE rag_document_chunks
    ADD CONSTRAINT fk_rag_document_chunks_image_extraction
        FOREIGN KEY (image_extraction_id)
        REFERENCES rag_document_image_extractions(extraction_id)
        ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_document_chunks_image_offset
    ON rag_document_chunks (image_extraction_id, image_chunk_offset)
    WHERE image_extraction_id IS NOT NULL AND image_chunk_offset IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rag_document_chunks_image_extraction
    ON rag_document_chunks (image_extraction_id);

ALTER TABLE rag_asset_objects
    ADD COLUMN IF NOT EXISTS attempt_token TEXT,
    ADD COLUMN IF NOT EXISTS attempt_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE rag_asset_objects
    DROP CONSTRAINT IF EXISTS chk_rag_asset_objects_status;

ALTER TABLE rag_asset_objects
    ADD CONSTRAINT chk_rag_asset_objects_status
        CHECK (status IN ('processing', 'ready', 'cleanup_failed'));

CREATE INDEX IF NOT EXISTS idx_rag_asset_objects_processing_lease
    ON rag_asset_objects (status, attempt_started_at);

UPDATE rag_documents
SET image_enrichment_status = CASE
        WHEN image_candidate_count = 0 THEN 'not_applicable'
        WHEN image_failed_count > 0 AND image_indexed_count + image_skipped_count > 0 THEN 'partial_failed'
        WHEN image_failed_count > 0 THEN 'failed'
        ELSE 'completed'
    END,
    image_enrichment_retryable = image_failed_count > 0,
    image_enrichment_updated_at = COALESCE(image_enrichment_updated_at, updated_at, CURRENT_TIMESTAMP)
WHERE image_enrichment_status = 'not_applicable'
  AND image_candidate_count > 0;
