-- 为图片增强增加 Redis 队列状态、任务标识和补偿入队标记。

ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS image_enrichment_task_id TEXT,
    ADD COLUMN IF NOT EXISTS image_enrichment_queued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS image_enrichment_current_position INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_enrichment_queue_pending BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE rag_documents
    DROP CONSTRAINT IF EXISTS chk_rag_documents_image_enrichment_status;

ALTER TABLE rag_documents
    ADD CONSTRAINT chk_rag_documents_image_enrichment_status
        CHECK (image_enrichment_status IN ('not_applicable', 'queued', 'processing', 'completed', 'partial_failed', 'failed'));

CREATE INDEX IF NOT EXISTS idx_rag_documents_image_enrichment_queue
    ON rag_documents (image_enrichment_status, image_enrichment_queue_pending, image_enrichment_queued_at);
