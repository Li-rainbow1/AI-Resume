-- 将旧的通用归属统一为未分类，并收紧新版本归属约束。
UPDATE rag_documents
SET scope_kind = 'unclassified',
    project_id = NULL
WHERE scope_kind = 'general';

ALTER TABLE rag_documents
    DROP CONSTRAINT IF EXISTS chk_rag_document_scope;

ALTER TABLE rag_documents
    ADD CONSTRAINT chk_rag_document_scope CHECK (
        (scope_kind = 'project' AND project_id IS NOT NULL)
        OR (scope_kind = 'unclassified' AND project_id IS NULL)
    );
