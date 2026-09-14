-- 重新按最多 50,000 个字符生成 legacy 预览，避免历史数据预览文本无界增长。

WITH ordered_chunks AS (
    SELECT
        source_id,
        original_filename,
        chunk_index,
        created_at,
        content,
        COALESCE(
            SUM(char_length(content)::bigint + 2) OVER (
                PARTITION BY source_id, original_filename
                ORDER BY chunk_index, created_at
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ),
            0
        ) AS chars_before
    FROM rag_document_chunks
    WHERE document_id IS NULL
), bounded_chunks AS (
    SELECT
        source_id,
        original_filename,
        chunk_index,
        created_at,
        LEFT(content, GREATEST(0, 50000 - chars_before)::integer) AS content
    FROM ordered_chunks
    WHERE chars_before < 50000
), bounded_previews AS (
    SELECT
        source_id,
        original_filename,
        LEFT(
            string_agg(content, E'\n\n' ORDER BY chunk_index, created_at),
            50000
        ) AS preview_text
    FROM bounded_chunks
    GROUP BY source_id, original_filename
)
UPDATE rag_documents AS documents
SET preview_text = previews.preview_text,
    updated_at = CURRENT_TIMESTAMP
FROM bounded_previews AS previews
WHERE documents.status = 'legacy'
  AND documents.source_id IS NOT DISTINCT FROM previews.source_id
  AND documents.original_filename IS NOT DISTINCT FROM previews.original_filename;
