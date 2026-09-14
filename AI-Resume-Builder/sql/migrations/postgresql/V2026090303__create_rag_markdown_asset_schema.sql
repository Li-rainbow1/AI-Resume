-- 为 Markdown 原文件建立可复用的图片资源和文档关联关系。

CREATE TABLE IF NOT EXISTS rag_asset_objects (
    asset_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    sha256 VARCHAR(64) NOT NULL,
    object_key VARCHAR(512) NOT NULL,
    content_type VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ready',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_rag_asset_objects_status
        CHECK (status IN ('ready', 'cleanup_failed'))
);

CREATE TABLE IF NOT EXISTS rag_document_assets (
    document_id TEXT NOT NULL,
    relative_path VARCHAR(1024) NOT NULL,
    asset_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_rag_document_assets PRIMARY KEY (document_id, relative_path),
    CONSTRAINT fk_rag_document_assets_document
        FOREIGN KEY (document_id) REFERENCES rag_documents(document_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_rag_document_assets_object
        FOREIGN KEY (asset_id) REFERENCES rag_asset_objects(asset_id)
        ON DELETE RESTRICT
);

ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS referenced_image_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS matched_image_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS missing_image_count INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS uq_rag_asset_objects_sha256
    ON rag_asset_objects (sha256);

CREATE INDEX IF NOT EXISTS idx_rag_asset_objects_status
    ON rag_asset_objects (status);

CREATE INDEX IF NOT EXISTS idx_rag_document_assets_asset_id
    ON rag_document_assets (asset_id);

CREATE INDEX IF NOT EXISTS idx_rag_document_assets_document_id
    ON rag_document_assets (document_id);
