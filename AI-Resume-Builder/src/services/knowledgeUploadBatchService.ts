import type {
  RagUploadFileResult,
  RagUploadProgressEvent,
  RagUploadResponse,
} from '@/api/ragApi'
import {
  normalizeNumber,
  normalizeString,
  normalizeUploadFileResult,
} from '@/services/knowledgeUploadResultService'
import { getMarkdownBundleRelativePath } from '@/services/knowledgeMarkdownAttachmentService'

// 本地上传状态比后端多出 pending（未开始）语义，其余与后端保持同名。
export type LocalUploadStatus = 'pending' | 'queued' | 'uploading' | 'success' | 'failed' | 'cancelled'
export type LocalUploadItem = Omit<RagUploadFileResult, 'status'> & {
  status: LocalUploadStatus
  fileSize: number
  order: number
  stage?: string
  stageProgress: number
}
export type UploadPhase = 'idle' | 'ready' | 'uploading' | 'completed' | 'error'

// 阶段关键字按顺序匹配，靠后的高进度关键字放在前面以避免被子串提前命中。
export const UPLOAD_STAGE_PROGRESS: Array<{ keyword: string; progress: number }> = [
  { keyword: '排队', progress: 0 },
  { keyword: '开始', progress: 0.08 },
  { keyword: '读取', progress: 0.16 },
  { keyword: '校验', progress: 0.25 },
  { keyword: '解析', progress: 0.38 },
  { keyword: '图片提取', progress: 0.43 },
  { keyword: '图片解析', progress: 0.48 },
  { keyword: '图片 Embedding', progress: 0.52 },
  { keyword: '规范化', progress: 0.5 },
  { keyword: '逻辑文档拆分', progress: 0.6 },
  { keyword: '拆分', progress: 0.6 },
  { keyword: '切块', progress: 0.72 },
  { keyword: 'Embedding', progress: 0.84 },
  { keyword: '入库', progress: 0.94 },
  { keyword: '完成', progress: 1 },
]

/** 把任意数值收敛到 0-100 的整数进度。 */
export function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, Math.round(value)))
}

export function normalizeOptionalPercent(value: unknown): number | null {
  const numberValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numberValue)) return null
  return clampProgress(numberValue)
}

export function normalizeUploadStatus(status: string): LocalUploadStatus {
  if (status === 'success' || status === 'failed') return status
  if (status === 'uploading' || status === 'queued' || status === 'cancelled') return status
  return 'pending'
}

/** 按阶段关键字推导进度权重，未知阶段给 0.35 兜底，空阶段按刚开始处理。 */
export function resolveStageProgress(stage?: string): number {
  const normalizedStage = normalizeString(stage)
  if (!normalizedStage) return 0.08
  const matchedStage = UPLOAD_STAGE_PROGRESS.find((item) => normalizedStage.includes(item.keyword))
  return matchedStage?.progress ?? 0.35
}

export function resolveResultProgress(status: string, stage?: string): number {
  if (status === 'success' || status === 'failed') return 1
  return resolveStageProgress(stage)
}

export function resolveEventFileProgress(event: RagUploadProgressEvent, stage?: string): number {
  const fileProgressPercent = normalizeOptionalPercent(event.fileProgressPercent ?? event.file_progress_percent)
  if (fileProgressPercent !== null) return fileProgressPercent / 100
  return resolveStageProgress(stage)
}

/** 终态（成功/失败）直接记满，处理中至少保留 8% 以避免进度条不动。 */
export function resolveItemProgress(item: LocalUploadItem): number {
  if (item.status === 'success' || item.status === 'failed') return 1
  if (item.status === 'uploading') return Math.max(0.08, item.stageProgress)
  return item.stageProgress
}

export function createPendingItem(file: File, status: LocalUploadStatus, order: number): LocalUploadItem {
  const sourceType = guessSourceType(file.name)
  return {
    fileName: file.name,
    contentType: file.type || 'application/octet-stream',
    sourceType,
    ingestSource: sourceType === 'image' ? 'image_ocr_text' : 'text_document',
    chunkCount: 0,
    insertedCount: 0,
    status,
    errorMessage: null,
    fileSize: file.size,
    order,
    stage: status === 'queued' ? '排队' : undefined,
    stageProgress: status === 'queued' ? 0 : resolveStageProgress(),
    referencedImageCount: 0,
    matchedImageCount: 0,
    missingImageCount: 0,
    reusedImageCount: 0,
    imageCandidateCount: 0,
    imageAnalyzedCount: 0,
    imageIndexedCount: 0,
    imageSkippedCount: 0,
    imageFailedCount: 0,
    imageChunkCount: 0,
    imageEnrichmentStatus: 'not_applicable',
    imageEnrichmentMessage: null,
    imageRetryAvailable: false,
  }
}

export function createResultItem(
  result: RagUploadFileResult,
  file: File,
  order: number,
  stage?: string,
  stageProgress?: number
): LocalUploadItem {
  return {
    ...result,
    status: normalizeUploadStatus(result.status),
    fileSize: file.size,
    order,
    stage,
    stageProgress: stageProgress ?? resolveResultProgress(result.status, stage),
  }
}

export function createFailedItem(file: File, order: number, message: string): LocalUploadItem {
  return {
    ...createPendingItem(file, 'failed', order),
    errorMessage: message,
  }
}

export function replaceUploadItem(
  items: LocalUploadItem[],
  index: number,
  item: LocalUploadItem
): LocalUploadItem[] {
  return items.map((currentItem, currentIndex) => (currentIndex === index ? item : currentItem))
}

export function patchUploadItem(
  items: LocalUploadItem[],
  index: number,
  patch: Partial<LocalUploadItem>
): LocalUploadItem[] {
  return items.map((currentItem, currentIndex) =>
    currentIndex === index ? { ...currentItem, ...patch } : currentItem
  )
}

/** 取消时只影响尚未进入终态的条目，已成功的保留结果。 */
export function markCancelledItems(items: LocalUploadItem[], startIndex: number): LocalUploadItem[] {
  return items.map((item, index) => {
    if (index < startIndex || item.status === 'success' || item.status === 'failed') return item
    return {
      ...item,
      status: 'cancelled',
      errorMessage: '已取消上传',
      stageProgress: item.stageProgress,
    }
  })
}

/** 请求异常中断：正在处理的标记失败，未开始的标记取消。 */
export function markInterruptedItems(items: LocalUploadItem[], message: string): LocalUploadItem[] {
  return items.map((item) => {
    if (item.status === 'success' || item.status === 'failed' || item.status === 'cancelled') return item
    if (item.status === 'uploading') {
      return {
        ...item,
        status: 'failed',
        errorMessage: message,
        stageProgress: Math.max(item.stageProgress, 1),
      }
    }
    return {
      ...item,
      status: 'cancelled',
      errorMessage: '请求中断，未进入处理',
      stageProgress: item.stageProgress,
    }
  })
}

export function toRagUploadFileResult(item: LocalUploadItem): RagUploadFileResult {
  return {
    fileName: item.fileName,
    contentType: item.contentType,
    sourceType: item.sourceType,
    ingestSource: item.ingestSource,
    chunkCount: item.chunkCount,
    insertedCount: item.insertedCount,
    status: item.status,
    errorMessage: item.errorMessage,
    documentId: item.documentId,
    referencedImageCount: item.referencedImageCount,
    matchedImageCount: item.matchedImageCount,
    missingImageCount: item.missingImageCount,
    reusedImageCount: item.reusedImageCount,
    imageCandidateCount: item.imageCandidateCount,
    imageAnalyzedCount: item.imageAnalyzedCount,
    imageIndexedCount: item.imageIndexedCount,
    imageSkippedCount: item.imageSkippedCount,
    imageFailedCount: item.imageFailedCount,
    imageChunkCount: item.imageChunkCount,
    imageEnrichmentStatus: item.imageEnrichmentStatus,
    imageEnrichmentMessage: item.imageEnrichmentMessage,
    imageRetryAvailable: item.imageRetryAvailable,
  }
}

/** 用条目现状拼出与后端同构的批次汇总，供流中断时兜底。 */
export function buildUploadSummary(items: LocalUploadItem[]): RagUploadResponse {
  return {
    totalFiles: items.length,
    succeededFiles: items.filter((item) => item.status === 'success').length,
    failedFiles: items.filter((item) => item.status === 'failed').length,
    inserted: items.reduce((sum, item) => sum + item.insertedCount, 0),
    files: items.map(toRagUploadFileResult),
  }
}

export function countUploadItems(items: LocalUploadItem[], statuses: LocalUploadStatus[]): number {
  const statusSet = new Set<LocalUploadStatus>(statuses)
  return items.filter((item) => statusSet.has(item.status)).length
}

/** 后端批次汇总可能缺字段，缺失时退回本地条目推断。 */
export function normalizeUploadSummary(
  value: unknown,
  fallbackFiles: RagUploadFileResult[]
): RagUploadResponse | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const files = Array.isArray(raw.files)
    ? raw.files.map(normalizeUploadFileResult).filter((item): item is RagUploadFileResult => item !== null)
    : fallbackFiles
  return {
    totalFiles: normalizeNumber(raw.totalFiles ?? raw.total_files) || files.length,
    succeededFiles: normalizeNumber(raw.succeededFiles ?? raw.succeeded_files),
    failedFiles: normalizeNumber(raw.failedFiles ?? raw.failed_files),
    inserted: normalizeNumber(raw.inserted),
    files,
  }
}

/** 优先用 fileIndex，其次退回文件名匹配。 */
export function resolveEventFileIndex(event: RagUploadProgressEvent, files: File[]): number {
  const rawIndex = event.fileIndex ?? event.file_index
  if (typeof rawIndex === 'number' && rawIndex > 0) return rawIndex - 1

  const fileName = normalizeString(event.fileName ?? event.file_name)
  if (!fileName) return -1
  return files.findIndex((file) => file.name === fileName)
}

export function normalizeEventStage(event: RagUploadProgressEvent): string | undefined {
  const stage = normalizeString(event.stage)
  if (stage) return stage
  return normalizeString(event.message) || undefined
}

export function dedupeFiles(files: File[]): File[] {
  const map = new Map<string, File>()
  for (const file of files) {
    map.set(`${file.name}:${file.size}:${file.lastModified}`, file)
  }
  return Array.from(map.values())
}

export function dedupeAttachmentFiles(files: File[]): File[] {
  const map = new Map<string, File>()
  for (const file of files) {
    map.set(`${getMarkdownBundleRelativePath(file)}:${file.size}:${file.lastModified}`, file)
  }
  return Array.from(map.values())
}

export function isMarkdownFile(file: File): boolean {
  return file.name.toLowerCase().endsWith('.md')
}

export function resolveAttachmentFolderName(files: File[]): string {
  const firstPath = files[0] ? getMarkdownBundleRelativePath(files[0]) : ''
  return firstPath.split('/')[0] || '附件文件夹'
}

export function guessSourceType(fileName: string): string {
  const extension = fileName.split('.').pop()?.toLowerCase() ?? ''
  return ['png', 'jpg', 'jpeg', 'webp'].includes(extension) ? 'image' : 'document'
}

export function formatFileSize(size: number): string {
  if (size <= 0) return '0 B'
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

export function sourceTypeLabel(sourceType: string): string {
  return sourceType === 'image' ? '图片' : '文档'
}

export function ingestSourceLabel(ingestSource: string): string {
  if (ingestSource === 'image_ocr_text') return 'OCR 入库'
  if (ingestSource === 'text_document') return '文本入库'
  return ingestSource
}

export function statusLabel(status: string): string {
  if (status === 'success') return '成功'
  if (status === 'failed') return '失败'
  if (status === 'cancelled') return '已取消'
  if (status === 'queued') return '排队中'
  if (status === 'uploading') return '处理中'
  return '待上传'
}
