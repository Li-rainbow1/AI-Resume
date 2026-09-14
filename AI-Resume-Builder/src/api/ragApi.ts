import { appendRagScope, type RagDocumentScope, type RagListFilters } from './ragScopeApi'
import { API_BASE_PATH } from './apiBase'
import { fetchWithAuth } from '@/services/authService'

const RAG_UPLOAD_TIMEOUT_MS = 300_000

export interface RagUploadFileResult {
  fileName: string
  contentType: string
  sourceType: string
  ingestSource: string
  chunkCount: number
  insertedCount: number
  status: string
  errorMessage?: string | null
  documentId?: string | null
  referencedImageCount?: number
  matchedImageCount?: number
  missingImageCount?: number
  reusedImageCount?: number
  imageCandidateCount?: number
  imageAnalyzedCount?: number
  imageIndexedCount?: number
  imageSkippedCount?: number
  imageFailedCount?: number
  imageChunkCount?: number
  imageEnrichmentStatus?: string
  imageEnrichmentMessage?: string | null
  imageRetryAvailable?: boolean
}

export interface RagUploadResponse {
  totalFiles: number
  succeededFiles: number
  failedFiles: number
  inserted: number
  files: RagUploadFileResult[]
}

export type RagDocumentFileType = 'all' | 'pdf' | 'word' | 'txt' | 'md' | 'image' | 'other'

export interface RagDocumentItem {
  documentId: string
  fileName: string
  contentType: string
  fileType: Exclude<RagDocumentFileType, 'all'>
  sourceType: string
  ingestSource: string
  fileSizeBytes: number
  status: string
  chunkCount: number
  insertedCount: number
  embeddingModel: string
  embeddingDimensions: number
  createdAt: string
  previewType: 'binary' | 'text'
  downloadAvailable: boolean
  archived: boolean
  archivedAt?: string | null
  scopeKind: RagDocumentScope['scopeKind']
  projectId?: string | null
  projectName?: string | null
  knowledgeBaseId?: string | null
  knowledgeBaseName?: string | null
  legacyWarning?: string | null
  referencedImageCount?: number
  matchedImageCount?: number
  missingImageCount?: number
  imageCandidateCount?: number
  imageAnalyzedCount?: number
  imageIndexedCount?: number
  imageSkippedCount?: number
  imageFailedCount?: number
  imageChunkCount?: number
  imageEnrichmentStatus?: string
  imageEnrichmentMessage?: string | null
  imageRetryAvailable?: boolean
}

export interface RagDocumentListResponse {
  items: RagDocumentItem[]
  total: number
  page: number
  pageSize: number
}

export interface RagDocumentDeleteResponse {
  documentId: string
  deletedChunkCount: number
  status: string
}

export interface RagDocumentBatchDeleteItem {
  documentId: string
  deletedChunkCount: number
  status: string
  message?: string | null
  affectedDocumentIds?: string[]
}

export interface RagDocumentBatchDeleteResponse {
  requestedCount: number
  succeededCount: number
  failedCount: number
  deletedChunkCount: number
  items: RagDocumentBatchDeleteItem[]
}

export interface RagDocumentImageEnrichmentResponse {
  documentId: string
  candidateCount: number
  analyzedCount: number
  indexedCount: number
  skippedCount: number
  failedCount: number
  chunkCount: number
  status: string
  message?: string | null
  retryAvailable?: boolean
  taskId?: string | null
  queuedAt?: string | null
  currentPosition?: number
}

export interface RagDocumentBlobResponse {
  blob: Blob
  fileName: string
  contentType: string
}

export interface RagUploadProgressEvent {
  event: string
  traceId?: string
  trace_id?: string
  fileIndex?: number
  file_index?: number
  totalFiles?: number
  total_files?: number
  fileName?: string
  file_name?: string
  stage?: string
  status?: string
  message?: string
  result?: unknown
  summary?: unknown
  files?: unknown
  succeededFiles?: number
  succeeded_files?: number
  failedFiles?: number
  failed_files?: number
  inserted?: number
  progressPercent?: number
  progress_percent?: number
  fileProgressPercent?: number
  file_progress_percent?: number
  imageIndex?: number
  image_index?: number
  imageCount?: number
  image_count?: number
  imageSourceLocator?: string
  image_source_locator?: string
}

export interface RagUploadStreamCallbacks {
  onEvent: (event: RagUploadProgressEvent) => void
}

export function getRagUploadEndpoint(): string {
  return `${API_BASE_PATH}/ai/rag/upload`
}

export function getRagUploadStreamEndpoint(): string {
  return `${API_BASE_PATH}/ai/rag/upload/stream`
}

export function getRagDocumentsEndpoint(
  page = 1,
  pageSize = 20,
  fileType: RagDocumentFileType = 'all',
  filters: RagListFilters = {}
): string {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
  if (fileType !== 'all') params.set('fileType', fileType)
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
  return `${API_BASE_PATH}/ai/rag/documents?${params.toString()}`
}

export function getRagDocumentPreviewEndpoint(documentId: string): string {
  return `${API_BASE_PATH}/ai/rag/documents/${encodeURIComponent(documentId)}/preview`
}

export function getRagDocumentDownloadEndpoint(documentId: string): string {
  return `${API_BASE_PATH}/ai/rag/documents/${encodeURIComponent(documentId)}/download`
}

export function getRagDocumentAssetEndpoint(documentId: string, relativePath: string): string {
  const params = new URLSearchParams({ path: relativePath })
  return `${API_BASE_PATH}/ai/rag/documents/${encodeURIComponent(documentId)}/asset?${params.toString()}`
}

export function getRagDocumentDeleteEndpoint(documentId: string): string {
  return `${API_BASE_PATH}/ai/rag/documents/${encodeURIComponent(documentId)}`
}

export function getRagDocumentBatchDeleteEndpoint(): string {
  return `${API_BASE_PATH}/ai/rag/documents`
}

export function getRagDocumentImageEnrichmentRetryEndpoint(documentId: string): string {
  return `${API_BASE_PATH}/ai/rag/documents/${encodeURIComponent(documentId)}/image-enrichment/retry`
}

export function getRagDocumentImageEnrichmentEndpoint(documentId: string): string {
  return `${API_BASE_PATH}/ai/rag/documents/${encodeURIComponent(documentId)}/image-enrichment`
}

export async function listRagDocuments(
  page = 1,
  pageSize = 20,
  fileType: RagDocumentFileType = 'all',
  filters: RagListFilters = {}
): Promise<RagDocumentListResponse> {
  const response = await fetchWithAuth(getRagDocumentsEndpoint(page, pageSize, fileType, filters), {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await readRagErrorMessage(response, '知识库文件列表加载失败'))
  const payload = (await response.json().catch(() => null)) as RagDocumentListResponse | null
  if (!payload || !Array.isArray(payload.items)) throw new Error('知识库文件列表返回了无效响应')
  return payload
}

export async function previewRagDocument(documentId: string): Promise<RagDocumentBlobResponse> {
  return fetchRagDocumentBlob(getRagDocumentPreviewEndpoint(documentId), 'inline')
}

export async function downloadRagDocument(documentId: string): Promise<RagDocumentBlobResponse> {
  return fetchRagDocumentBlob(getRagDocumentDownloadEndpoint(documentId), 'attachment')
}

export async function fetchRagDocumentAsset(
  documentId: string,
  relativePath: string
): Promise<RagDocumentBlobResponse> {
  return fetchRagDocumentBlob(getRagDocumentAssetEndpoint(documentId, relativePath), 'inline')
}

export async function deleteRagDocument(documentId: string): Promise<RagDocumentDeleteResponse> {
  const response = await fetchWithAuth(getRagDocumentDeleteEndpoint(documentId), {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await readRagErrorMessage(response, '知识库文件删除失败'))
  const payload = (await response.json().catch(() => null)) as RagDocumentDeleteResponse | null
  if (!payload || !payload.documentId) throw new Error('知识库文件删除返回了无效响应')
  return payload
}

export async function batchDeleteRagDocuments(documentIds: string[]): Promise<RagDocumentBatchDeleteResponse> {
  const response = await fetchWithAuth(getRagDocumentBatchDeleteEndpoint(), {
    method: 'DELETE',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ documentIds }),
  })
  if (!response.ok) throw new Error(await readRagErrorMessage(response, '知识库文件批量删除失败'))
  const payload = (await response.json().catch(() => null)) as RagDocumentBatchDeleteResponse | null
  if (!payload || !Array.isArray(payload.items)) throw new Error('知识库文件批量删除返回了无效响应')
  return payload
}

export async function retryRagDocumentImageEnrichment(
  documentId: string
): Promise<RagDocumentImageEnrichmentResponse> {
  const response = await fetchWithAuth(getRagDocumentImageEnrichmentRetryEndpoint(documentId), {
    method: 'POST',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await readRagErrorMessage(response, '图片解析重试失败'))
  const payload = (await response.json().catch(() => null)) as RagDocumentImageEnrichmentResponse | null
  if (!payload || !payload.documentId) throw new Error('图片解析重试返回了无效响应')
  return payload
}

export async function getRagDocumentImageEnrichment(
  documentId: string
): Promise<RagDocumentImageEnrichmentResponse> {
  const response = await fetchWithAuth(getRagDocumentImageEnrichmentEndpoint(documentId), {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await readRagErrorMessage(response, '图片解析状态加载失败'))
  const payload = (await response.json().catch(() => null)) as RagDocumentImageEnrichmentResponse | null
  if (!payload || !payload.documentId) throw new Error('图片解析状态返回了无效响应')
  return payload
}

export async function uploadKnowledgeAssets(
  files: File[],
  signal?: AbortSignal
): Promise<RagUploadResponse> {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }

  const requestController = new AbortController()
  let didTimeout = false
  const handleExternalAbort = () => requestController.abort(signal?.reason)
  if (signal) {
    if (signal.aborted) {
      requestController.abort(signal.reason)
    } else {
      signal.addEventListener('abort', handleExternalAbort, { once: true })
    }
  }
  const timeoutId = setTimeout(() => {
    didTimeout = true
    requestController.abort()
  }, RAG_UPLOAD_TIMEOUT_MS)

  let response: Response
  try {
    response = await fetchWithAuth(getRagUploadEndpoint(), {
      method: 'POST',
      headers: {
        Accept: 'application/json',
      },
      body: formData,
      signal: requestController.signal,
    })
  } catch (error) {
    if (didTimeout) {
      throw new Error(
        `知识库上传超时（${Math.floor(RAG_UPLOAD_TIMEOUT_MS / 1000)} 秒），请检查 Embedding 服务与 pgvector 连接`
      )
    }
    if (signal?.aborted) {
      throw new Error('已取消上传')
    }
    if (error instanceof Error && error.message) {
      throw new Error(`知识库上传失败：${error.message}`)
    }
    throw new Error('知识库上传失败')
  } finally {
    clearTimeout(timeoutId)
    if (signal) {
      signal.removeEventListener('abort', handleExternalAbort)
    }
  }

  if (!response.ok) {
    throw new Error(await readRagErrorMessage(response, '知识库上传失败'))
  }

  const payload = (await response.json().catch(() => null)) as RagUploadResponse | null

  if (!payload || !('files' in payload)) {
    throw new Error('知识库上传返回了无效响应')
  }

  return payload
}

export async function uploadKnowledgeAssetsStream(
  files: File[],
  callbacks: RagUploadStreamCallbacks,
  signal?: AbortSignal,
  scope?: RagDocumentScope
): Promise<void> {
  const formData = new FormData()
  appendRagScope(formData, scope)
  for (const file of files) {
    formData.append('files', file)
  }
  await consumeRagUploadStream(getRagUploadStreamEndpoint(), formData, callbacks, signal)
}

export type RagMarkdownUploadManifest = {
  documents: Array<{ index: number; relativePath: string }>
  attachments: Array<{ index: number; relativePath: string }>
}

export async function uploadMarkdownBundleStream(
  documents: File[],
  attachments: File[],
  manifest: RagMarkdownUploadManifest,
  callbacks: RagUploadStreamCallbacks,
  signal?: AbortSignal,
  scope?: RagDocumentScope
): Promise<void> {
  const formData = new FormData()
  appendRagScope(formData, scope)
  for (const file of [...documents, ...attachments]) {
    formData.append('files', file)
  }
  formData.append('manifest', JSON.stringify(manifest))

  await consumeRagUploadStream(
    getRagMarkdownUploadStreamEndpoint(),
    formData,
    callbacks,
    signal
  )
}

export function getRagMarkdownUploadStreamEndpoint(): string {
  return `${API_BASE_PATH}/ai/rag/markdown-upload/stream`
}

async function consumeRagUploadStream(
  endpoint: string,
  formData: FormData,
  callbacks: RagUploadStreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  const requestController = new AbortController()
  let didTimeout = false
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  const handleExternalAbort = () => requestController.abort(signal?.reason)
  if (signal) {
    if (signal.aborted) {
      requestController.abort(signal.reason)
    } else {
      signal.addEventListener('abort', handleExternalAbort, { once: true })
    }
  }
  const timeoutId = setTimeout(() => {
    didTimeout = true
    requestController.abort()
  }, RAG_UPLOAD_TIMEOUT_MS)

  try {
    const response = await fetchWithAuth(endpoint, {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      body: formData,
      signal: requestController.signal,
    })
    if (!response.ok) {
      throw new Error(await readRagErrorMessage(response, '知识库上传失败'))
    }

    reader = response.body?.getReader()
    if (!reader) throw new Error('知识库上传未返回可读取的进度流')

    const decoder = new TextDecoder()
    let buffer = ''
    let eventName = 'message'
    let dataLines: string[] = []
    let receivedBatchComplete = false

    const dispatchEvent = () => {
      if (dataLines.length === 0) {
        eventName = 'message'
        return
      }
      const data = dataLines.join('\n')
      dataLines = []
      const parsed = parseProgressEventData(data)
      const event = { ...parsed, event: parsed.event || eventName }
      callbacks.onEvent(event)
      if (event.event === 'batch-complete') receivedBatchComplete = true
      if (event.event === 'error') throw new Error(event.message || '知识库上传失败')
      eventName = 'message'
    }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          dispatchEvent()
          continue
        }
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim() || 'message'
          continue
        }
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      }
    }

    buffer += decoder.decode()
    if (buffer.trim()) {
      for (const rawLine of buffer.split('\n')) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          dispatchEvent()
          continue
        }
        if (line.startsWith('event:')) eventName = line.slice(6).trim() || 'message'
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      }
    }
    if (dataLines.length > 0) dispatchEvent()
    if (!receivedBatchComplete) throw new Error('知识库上传进度流意外中断，请刷新列表后重试')
  } catch (error) {
    if (didTimeout) {
      throw new Error(
        `知识库上传超时（${Math.floor(RAG_UPLOAD_TIMEOUT_MS / 1000)} 秒），请检查 Embedding 服务与 pgvector 连接`
      )
    }
    if (signal?.aborted) throw new Error('已取消上传')
    if (error instanceof Error && error.message) throw error
    throw new Error('知识库上传失败')
  } finally {
    clearTimeout(timeoutId)
    if (signal) signal.removeEventListener('abort', handleExternalAbort)
    await reader?.cancel().catch(() => undefined)
  }
}

function parseProgressEventData(data: string): RagUploadProgressEvent {
  try {
    const parsed = JSON.parse(data) as RagUploadProgressEvent
    return parsed && typeof parsed === 'object' ? parsed : { event: 'message', message: data }
  } catch {
    return { event: 'message', message: data }
  }
}

async function fetchRagDocumentBlob(endpoint: string, disposition: string): Promise<RagDocumentBlobResponse> {
  const response = await fetchWithAuth(endpoint, {
    method: 'GET',
    headers: { Accept: '*/*' },
  })
  const fallbackMessage = disposition === 'inline' ? '知识库文件预览失败' : '知识库文件下载失败'
  if (!response.ok) throw new Error(await readRagErrorMessage(response, fallbackMessage))
  const blob = await response.blob()
  return {
    blob,
    fileName: resolveContentDispositionFileName(response.headers.get('content-disposition')),
    contentType: response.headers.get('content-type') || blob.type || (disposition === 'inline' ? 'text/plain' : 'application/octet-stream'),
  }
}

function resolveContentDispositionFileName(value: string | null): string {
  if (!value) return 'knowledge-document'
  const encodedName = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encodedName) {
    try {
      return decodeURIComponent(encodedName)
    } catch {
      return encodedName
    }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] || 'knowledge-document'
}

async function readRagErrorMessage(response: Response, fallbackMessage: string): Promise<string> {
  const contentType = response.headers.get('content-type')?.toLowerCase() ?? ''
  const responseText = await response.text().catch(() => '')

  if (contentType.includes('application/json')) {
    try {
      const payload = JSON.parse(responseText) as { detail?: unknown; message?: unknown }
      const detail = typeof payload.detail === 'string' ? payload.detail.trim() : ''
      const message = typeof payload.message === 'string' ? payload.message.trim() : ''
      if (detail || message) return detail || message
    } catch {
      // 响应不是合法 JSON 时继续按 HTTP 状态码给出稳定提示。
    }
  }

  if (response.status === 413) {
    return `${fallbackMessage}：请求内容过大，请将单个文件控制在 10 MB 以内，并减少本批次文件数量后重试。`
  }

  return `${fallbackMessage}（HTTP ${response.status}）`
}
