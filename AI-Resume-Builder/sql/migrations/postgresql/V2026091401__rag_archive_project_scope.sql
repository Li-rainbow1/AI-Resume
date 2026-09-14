-- 归档与解析状态独立，历史文档保持未归档、未分类。
CREATE TABLE rag_projects (
    project_id TEXT PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    normalized_name VARCHAR(128) NOT NULL UNIQUE,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_rag_project_aliases CHECK (jsonb_typeof(aliases) = 'array')
);
ALTER TABLE rag_documents
    ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN archived_at TIMESTAMPTZ,
    ADD COLUMN scope_kind VARCHAR(16) NOT NULL DEFAULT 'unclassified',
    ADD COLUMN project_id TEXT REFERENCES rag_projects(project_id),
    ADD CONSTRAINT chk_rag_document_scope CHECK (
        (scope_kind = 'project' AND project_id IS NOT NULL)
        OR (scope_kind IN ('general', 'unclassified') AND project_id IS NULL)
    ),
    ADD CONSTRAINT chk_rag_document_archive CHECK (archived = (archived_at IS NOT NULL));
CREATE INDEX idx_rag_documents_scope ON rag_documents (project_id, status) WHERE NOT archived;
