import type { RagUploadFileResult } from '@/api/ragApi'

export function normalizeUploadFileResult(value: unknown): RagUploadFileResult | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const fileName = normalizeString(raw.fileName ?? raw.file_name)
  if (!fileName) return null
  return {
    fileName,
    contentType: normalizeString(raw.contentType ?? raw.content_type) || 'application/octet-stream',
    sourceType: normalizeString(raw.sourceType ?? raw.source_type) || 'document',
    ingestSource: normalizeString(raw.ingestSource ?? raw.ingest_source) || 'text_document',
    chunkCount: normalizeNumber(raw.chunkCount ?? raw.chunk_count),
    insertedCount: normalizeNumber(raw.insertedCount ?? raw.inserted_count),
    status: normalizeString(raw.status) || 'failed',
    errorMessage: normalizeNullableString(raw.errorMessage ?? raw.error_message),
    documentId: normalizeNullableString(raw.documentId ?? raw.document_id),
    referencedImageCount: normalizeNumber(raw.referencedImageCount ?? raw.referenced_image_count),
    matchedImageCount: normalizeNumber(raw.matchedImageCount ?? raw.matched_image_count),
    missingImageCount: normalizeNumber(raw.missingImageCount ?? raw.missing_image_count),
    reusedImageCount: normalizeNumber(raw.reusedImageCount ?? raw.reused_image_count),
    imageCandidateCount: normalizeNumber(raw.imageCandidateCount ?? raw.image_candidate_count),
    imageAnalyzedCount: normalizeNumber(raw.imageAnalyzedCount ?? raw.image_analyzed_count),
    imageIndexedCount: normalizeNumber(raw.imageIndexedCount ?? raw.image_indexed_count),
    imageSkippedCount: normalizeNumber(raw.imageSkippedCount ?? raw.image_skipped_count),
    imageFailedCount: normalizeNumber(raw.imageFailedCount ?? raw.image_failed_count),
    imageChunkCount: normalizeNumber(raw.imageChunkCount ?? raw.image_chunk_count),
    imageEnrichmentStatus: normalizeString(raw.imageEnrichmentStatus ?? raw.image_enrichment_status) || 'not_applicable',
    imageEnrichmentMessage: normalizeNullableString(raw.imageEnrichmentMessage ?? raw.image_enrichment_message),
    imageRetryAvailable: normalizeBoolean(raw.imageRetryAvailable ?? raw.image_retry_available),
  }
}

export function normalizeNumber(value: unknown): number {
  const numberValue = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numberValue) ? numberValue : 0
}

export function normalizeString(value: unknown): string {
  return String(value ?? '').trim()
}

export function normalizeNullableString(value: unknown): string | null {
  const text = normalizeString(value)
  return text || null
}

export function normalizeBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') return value.trim().toLowerCase() === 'true'
  return Boolean(value)
}
