<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Archive,
  ArchiveRestore,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  Eye,
  FileText,
  LoaderCircle,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-vue-next'
import {
  batchDeleteRagDocuments,
  deleteRagDocument,
  downloadRagDocument,
  getRagDocumentImageEnrichment,
  listRagDocuments,
  previewRagDocument,
  retryRagDocumentImageEnrichment,
  type RagDocumentItem,
  type RagDocumentFileType,
  type RagDocumentImageEnrichmentResponse,
} from '@/api/ragApi'
import {
  formatCreatedAt,
  formatFileSize,
  formatFileTypeBadge,
  statusClass,
  statusLabel,
} from '@/services/knowledgeDocumentPresentationService'
import RagDocumentOriginalPreview from './RagDocumentOriginalPreview.vue'

import { listRagKnowledgeBases, setRagDocumentArchived, changeRagDocumentKnowledgeBase, type RagKnowledgeBase } from '@/api/ragScopeApi'
import RagKnowledgeBaseManager from './RagKnowledgeBaseManager.vue'
import RagDocumentScopeSelect from './RagDocumentScopeSelect.vue'

const knowledgeBases = ref<RagKnowledgeBase[]>([])
const archiveFilter = ref<'active' | 'archived' | 'all'>('active')
const scopeFilter = ref('all')
const changingDocumentId = ref('')
const editingDocumentId = ref('')
const editingScope = ref('unclassified')
async function refreshProjects() {
  try {
    knowledgeBases.value = await listRagKnowledgeBases()
    if (scopeFilter.value !== 'all' && scopeFilter.value !== 'unclassified' && !knowledgeBases.value.some((item) => item.knowledgeBaseId === scopeFilter.value)) {
      scopeFilter.value = 'all'
    }
  } catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '知识库加载失败' }
}
async function handleKnowledgeBasesChanged() {
  await refreshProjects()
  await loadDocuments(page.value)
}
async function changeArchive(item: RagDocumentItem) {
  changingDocumentId.value = item.documentId
  try { await setRagDocumentArchived(item.documentId, !item.archived); await loadDocuments(page.value) }
  catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '归档状态修改失败' }
  finally { changingDocumentId.value = '' }
}
function editScope(item: RagDocumentItem) { editingDocumentId.value = item.documentId; editingScope.value = item.knowledgeBaseId || item.projectId || item.scopeKind }
async function saveScope(item: RagDocumentItem) {
  changingDocumentId.value = item.documentId
  const value = editingScope.value
  try {
    await changeRagDocumentKnowledgeBase(item.documentId, value === 'unclassified' ? null : value)
    editingDocumentId.value = ''; await loadDocuments(page.value)
  } catch (cause) { errorMessage.value = cause instanceof Error ? cause.message : '归属修改失败' }
  finally { changingDocumentId.value = '' }
}
watch([archiveFilter, scopeFilter], () => { void loadDocuments() })

const props = defineProps<{ refreshKey: number }>()

const PAGE_SIZE = 20
const documents = ref<RagDocumentItem[]>([])
const page = ref(1)
const total = ref(0)
const isLoading = ref(false)
const errorMessage = ref('')
const deletingDocumentId = ref('')
const selectedDocumentIds = ref<string[]>([])
const isBatchDeleting = ref(false)
const retryingDocumentId = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const previewState = ref<PreviewState | null>(null)
let previewRequestId = 0
let documentListRequestId = 0
let imageEnrichmentPollingTimer: number | null = null
let imageEnrichmentPollingInFlight = false

type PreviewState = {
  documentId: string
  fileName: string
  contentType: string
  blob: Blob
  isLegacy: boolean
  legacyWarning: string | null
}

const documentTypeFilters: Array<{ value: RagDocumentFileType; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'pdf', label: 'PDF' },
  { value: 'word', label: 'DOC/DOCX' },
  { value: 'txt', label: 'TXT' },
  { value: 'md', label: 'MD' },
  { value: 'image', label: '图片' },
  { value: 'other', label: '其他' },
]
const activeFileType = ref<RagDocumentFileType>('all')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const hasPreviousPage = computed(() => page.value > 1)
const hasNextPage = computed(() => page.value < pageCount.value)
const selectedDocumentCount = computed(() => selectedDocumentIds.value.length)
const allDocumentsSelected = computed(
  () => documents.value.length > 0 && documents.value.every((item) => selectedDocumentIds.value.includes(item.documentId))
)
const hasPartialSelection = computed(() => selectedDocumentCount.value > 0 && !allDocumentsSelected.value)
const libraryBusy = computed(
  () => Boolean(changingDocumentId.value) || isLoading.value || isBatchDeleting.value || Boolean(deletingDocumentId.value) || Boolean(retryingDocumentId.value)
)
const activeFileTypeLabel = computed(
  () => documentTypeFilters.find((filter) => filter.value === activeFileType.value)?.label || '当前类型'
)

onMounted(() => {
  void loadDocuments()
  void refreshProjects()
})

watch(
  () => props.refreshKey,
  (current, previous) => {
    if (current !== previous) void loadDocuments(page.value)
  }
)

onUnmounted(() => {
  previewRequestId += 1
  documentListRequestId += 1
  stopImageEnrichmentPolling()
})

async function loadDocuments(targetPage = 1, targetFileType = activeFileType.value) {
  const requestId = ++documentListRequestId
  selectedDocumentIds.value = []
  isLoading.value = true
  errorMessage.value = ''
  try {
    const response = await listRagDocuments(targetPage, PAGE_SIZE, targetFileType, {
      archive: archiveFilter.value,
      scopeKind: scopeFilter.value === 'unclassified' ? scopeFilter.value : undefined,
      knowledgeBaseId: scopeFilter.value !== 'all' && scopeFilter.value !== 'unclassified' ? scopeFilter.value : undefined,
    })
    if (requestId !== documentListRequestId) return
    if (targetPage > 1 && !response.items.length && response.total > 0) { await loadDocuments(Math.max(1, Math.ceil(response.total / PAGE_SIZE)), targetFileType); return }
    documents.value = response.items
    total.value = response.total
    page.value = response.page
    syncImageEnrichmentPolling()
  } catch (error) {
    if (requestId !== documentListRequestId) return
    errorMessage.value = error instanceof Error ? error.message : '知识库文件列表加载失败'
  } finally {
    if (requestId === documentListRequestId) isLoading.value = false
  }
}

function syncImageEnrichmentPolling() {
  const hasActiveImageTasks = documents.value.some(isImageEnrichmentActive)
  if (hasActiveImageTasks) {
    startImageEnrichmentPolling()
  } else {
    stopImageEnrichmentPolling()
  }
}

function isImageEnrichmentActive(item: RagDocumentItem): boolean {
  return item.status === 'ready' && ['queued', 'processing'].includes(item.imageEnrichmentStatus || '')
}

function startImageEnrichmentPolling() {
  if (imageEnrichmentPollingTimer !== null) return
  imageEnrichmentPollingTimer = window.setInterval(() => {
    void pollImageEnrichmentStatus()
  }, 3000)
}

function stopImageEnrichmentPolling() {
  if (imageEnrichmentPollingTimer === null) return
  window.clearInterval(imageEnrichmentPollingTimer)
  imageEnrichmentPollingTimer = null
}

async function pollImageEnrichmentStatus() {
  if (imageEnrichmentPollingInFlight || libraryBusy.value) return
  const activeItems = documents.value.filter(isImageEnrichmentActive)
  if (!activeItems.length) {
    stopImageEnrichmentPolling()
    return
  }
  imageEnrichmentPollingInFlight = true
  try {
    const results = await Promise.allSettled(
      activeItems.map((item) => getRagDocumentImageEnrichment(item.documentId))
    )
    let shouldReload = false
    results.forEach((result, index) => {
      const activeItem = activeItems[index]
      if (!activeItem || result.status === 'rejected') return
      const item = documents.value.find((current) => current.documentId === activeItem.documentId)
      if (!item) return
      applyImageEnrichmentStatus(item, result.value)
      if (!['queued', 'processing'].includes(result.value.status)) shouldReload = true
    })
    if (shouldReload) {
      await loadDocuments(page.value)
    } else {
      syncImageEnrichmentPolling()
    }
  } finally {
    imageEnrichmentPollingInFlight = false
  }
}

function applyImageEnrichmentStatus(
  item: RagDocumentItem,
  response: RagDocumentImageEnrichmentResponse
) {
  item.imageCandidateCount = response.candidateCount
  item.imageAnalyzedCount = response.analyzedCount
  item.imageIndexedCount = response.indexedCount
  item.imageSkippedCount = response.skippedCount
  item.imageFailedCount = response.failedCount
  item.imageChunkCount = response.chunkCount
  item.imageEnrichmentStatus = response.status
  item.imageEnrichmentMessage = response.message
  item.imageRetryAvailable = response.retryAvailable
}

async function handlePreview(document: RagDocumentItem) {
  if (libraryBusy.value) return
  closePreview()
  const requestId = previewRequestId
  previewLoading.value = true
  previewError.value = ''
  try {
    const asset = await previewRagDocument(document.documentId)
    if (requestId !== previewRequestId) return
    previewState.value = {
      documentId: document.documentId,
      fileName: asset.fileName || document.fileName,
      contentType: asset.contentType,
      blob: asset.blob,
      isLegacy: document.status === 'legacy',
      legacyWarning: document.legacyWarning || null,
    }
  } catch (error) {
    if (requestId === previewRequestId) {
      previewError.value = error instanceof Error ? error.message : '文件预览失败'
    }
  } finally {
    if (requestId === previewRequestId) previewLoading.value = false
  }
}

async function handleDownload(item: RagDocumentItem) {
  if (!item.downloadAvailable || libraryBusy.value) return
  try {
    const asset = await downloadRagDocument(item.documentId)
    const objectUrl = URL.createObjectURL(asset.blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = asset.fileName || item.fileName
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '文件下载失败'
  }
}

async function handleDelete(item: RagDocumentItem) {
  if (libraryBusy.value) return
  const confirmed = window.confirm(`确定永久删除“${item.fileName}”吗？对应的 Chunk 和向量也会删除。`)
  if (!confirmed) return

  deletingDocumentId.value = item.documentId
  errorMessage.value = ''
  try {
    await deleteRagDocument(item.documentId)
    await loadDocuments(documents.value.length === 1 && page.value > 1 ? page.value - 1 : page.value)
  } catch (error) {
    const deleteError = error instanceof Error ? error.message : '文件删除失败，可稍后重试清理'
    await loadDocuments(page.value)
    errorMessage.value = errorMessage.value ? `${deleteError}；${errorMessage.value}` : deleteError
  } finally {
    deletingDocumentId.value = ''
  }
}

async function handleBatchDelete() {
  const documentIds = [...selectedDocumentIds.value]
  if (isBatchDeleting.value || !documentIds.length || deletingDocumentId.value) return

  const confirmed = window.confirm(
    `确定永久删除选中的 ${documentIds.length} 个文件吗？对应的原文件、Chunk 和向量也会删除。`
  )
  if (!confirmed) return

  isBatchDeleting.value = true
  errorMessage.value = ''
  try {
    const response = await batchDeleteRagDocuments(documentIds)
    const nextPage = resolvePageAfterBatchDelete(response)
    await loadDocuments(nextPage)
    if (response.failedCount > 0) {
      errorMessage.value = `已完成 ${response.succeededCount} 个文件，${response.failedCount} 个文件清理失败，请稍后重试。`
    }
  } catch (error) {
    const batchDeleteError = error instanceof Error ? error.message : '文件批量删除失败，可稍后重试清理'
    await loadDocuments(page.value)
    errorMessage.value = errorMessage.value ? `${batchDeleteError}；${errorMessage.value}` : batchDeleteError
  } finally {
    isBatchDeleting.value = false
  }
}

async function handleRetryImageEnrichment(item: RagDocumentItem) {
  if (libraryBusy.value || item.status !== 'ready' || !item.imageRetryAvailable) return
  retryingDocumentId.value = item.documentId
  errorMessage.value = ''
  try {
    const response = await retryRagDocumentImageEnrichment(item.documentId)
    await loadDocuments(page.value)
    if (response.status === 'failed' && response.retryAvailable) {
      errorMessage.value = response.message || '图片任务暂时无法入队，系统会自动重试。'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '图片解析重试失败，可稍后重试'
    await loadDocuments(page.value)
  } finally {
    retryingDocumentId.value = ''
  }
}

function handleSelectCurrentPage(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedDocumentIds.value = checked ? documents.value.map((item) => item.documentId) : []
}

function handleDocumentSelection(documentId: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const nextIds = new Set(selectedDocumentIds.value)
  if (checked) {
    nextIds.add(documentId)
  } else {
    nextIds.delete(documentId)
  }
  selectedDocumentIds.value = [...nextIds]
}

function resolvePageAfterBatchDelete(response: { items: Array<{ status: string; documentId: string; affectedDocumentIds?: string[] }> }): number {
  const affectedDocumentIds = new Set<string>()
  for (const item of response.items) {
    if (item.status !== 'deleted') continue
    const affectedIds = item.affectedDocumentIds?.length ? item.affectedDocumentIds : [item.documentId]
    for (const documentId of affectedIds) affectedDocumentIds.add(documentId)
  }
  const remainingTotal = Math.max(0, total.value - affectedDocumentIds.size)
  return page.value > 1 && remainingTotal <= (page.value - 1) * PAGE_SIZE ? page.value - 1 : page.value
}

function closePreview() {
  previewRequestId += 1
  previewState.value = null
  previewError.value = ''
  previewLoading.value = false
}

function movePage(nextPage: number) {
  if (libraryBusy.value || nextPage < 1 || nextPage > pageCount.value) return
  void loadDocuments(nextPage)
}

function handleFileTypeChange(fileType: RagDocumentFileType) {
  if (libraryBusy.value || activeFileType.value === fileType) return
  activeFileType.value = fileType
  void loadDocuments(1, fileType)
}

</script>

<template>
  <section class="document-library">
    <div class="library-header">
      <div>
        <p class="library-kicker">DOCUMENT LIBRARY</p>
        <h2>已入库文件</h2>
        <p class="library-description">历史文件会持久保留在私有对象存储中，可按权限预览、下载或永久删除。</p>
      </div>
      <div class="library-header-actions">
        <span class="library-count">共 {{ total }} 项</span>
        <button class="icon-action" type="button" title="刷新文件列表" aria-label="刷新文件列表" :disabled="libraryBusy" @click="loadDocuments(page)">
          <RefreshCw :size="16" :class="{ spinning: isLoading }" aria-hidden="true" />
        </button>
      </div>
    </div>

    <RagKnowledgeBaseManager :knowledge-bases="knowledgeBases" @changed="handleKnowledgeBasesChanged" />
    <div class="scope-filters">
      <label>状态 <select aria-label="归档状态筛选" v-model="archiveFilter" :disabled="libraryBusy"><option value="active">未归档</option><option value="archived">已归档</option><option value="all">全部</option></select></label>
      <label>所属知识库 <select aria-label="所属知识库筛选" v-model="scopeFilter" :disabled="libraryBusy"><option value="all">全部</option><option value="unclassified">未分类</option><option v-for="knowledgeBase in knowledgeBases" :key="knowledgeBase.knowledgeBaseId" :value="knowledgeBase.knowledgeBaseId">{{ knowledgeBase.name }}</option></select></label>
    </div>
    <div class="file-type-filter" role="group" aria-label="按文件类型筛选">
      <span class="file-type-filter-label">类型</span>
      <div class="file-type-filter-options">
        <button
          v-for="filter in documentTypeFilters"
          :key="filter.value"
          class="file-type-filter-option"
          :class="{ 'is-active': activeFileType === filter.value }"
          type="button"
          :aria-pressed="activeFileType === filter.value"
          :disabled="libraryBusy"
          @click="handleFileTypeChange(filter.value)"
        >
          {{ filter.label }}
        </button>
      </div>
    </div>

    <p v-if="errorMessage" class="library-error">{{ errorMessage }}</p>

    <div v-if="documents.length" class="library-toolbar">
      <label class="select-page">
        <input
          type="checkbox"
          :checked="allDocumentsSelected"
          :indeterminate="hasPartialSelection"
          :disabled="libraryBusy"
          aria-label="全选当前页文件"
          @change="handleSelectCurrentPage"
        />
        <span>全选当前页</span>
      </label>
      <div class="batch-actions">
        <span v-if="selectedDocumentCount" class="selected-count">已选 {{ selectedDocumentCount }} 项</span>
        <button class="text-action danger" type="button" :disabled="libraryBusy || !selectedDocumentCount" @click="handleBatchDelete">
          <Trash2 :size="15" aria-hidden="true" />
          {{ isBatchDeleting ? '删除中…' : '批量删除' }}
        </button>
      </div>
    </div>

    <div v-if="isLoading && documents.length === 0" class="library-empty">
      <LoaderCircle class="spinning" :size="24" aria-hidden="true" />
      <span>正在加载已入库文件…</span>
    </div>
    <div v-else-if="!documents.length" class="library-empty">
      <FileText :size="25" aria-hidden="true" />
      <strong v-if="activeFileType === 'all'">当前筛选范围没有文档</strong>
      <strong v-else>暂无 {{ activeFileTypeLabel }} 文件</strong>
      <span v-if="activeFileType === 'all'">可切换归档状态或所属知识库查看。</span>
      <span v-else>切换类型或上传文件后再查看。</span>
    </div>
    <div v-else class="document-grid" role="list">
      <article
        v-for="item in documents"
        :key="item.documentId"
        class="document-card"
        :class="{ 'is-selected': selectedDocumentIds.includes(item.documentId) }"
        role="listitem"
      >
        <div
          class="document-card-top"
          :class="{ 'has-image-retry': item.status === 'ready' && item.imageRetryAvailable }"
        >
          <label class="document-select">
            <input
              type="checkbox"
              :checked="selectedDocumentIds.includes(item.documentId)"
              :disabled="libraryBusy"
              :aria-label="`选择 ${item.fileName}`"
              @change="handleDocumentSelection(item.documentId, $event)"
            />
          </label>
          <span class="document-type-badge">{{ formatFileTypeBadge(item) }}</span>
          <button
            v-if="item.status === 'ready' && item.imageRetryAvailable"
            class="text-action warning document-retry-action"
            type="button"
            :disabled="libraryBusy"
            @click="handleRetryImageEnrichment(item)"
          >
            <RefreshCw :size="14" :class="{ spinning: retryingDocumentId === item.documentId }" aria-hidden="true" />
            {{ retryingDocumentId === item.documentId ? '重试中' : '重试图片' }}
          </button>
          <span class="document-status" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span>
        </div>
        <div class="document-main">
          <p class="document-name" :title="item.fileName">{{ item.fileName }}</p>
          <p class="document-meta">
            {{ formatFileSize(item.fileSizeBytes) }} · {{ formatCreatedAt(item.createdAt) }}
          </p>
          <p class="document-submeta">{{ item.archived ? '已归档' : '未归档' }} · {{ item.knowledgeBaseName || item.projectName || '未分类' }}</p>
          <div v-if="editingDocumentId === item.documentId" class="scope-editor">
            <RagDocumentScopeSelect v-model="editingScope" :knowledge-bases="knowledgeBases" :disabled="libraryBusy" />
            <button class="text-action" :disabled="libraryBusy" @click="saveScope(item)">保存</button><button class="text-action" :disabled="libraryBusy" @click="editingDocumentId = ''">取消</button>
          </div>
          <p class="document-submeta">
            Chunk {{ item.chunkCount }} · 入库 {{ item.insertedCount }}<span v-if="item.embeddingDimensions"> · {{ item.embeddingDimensions }} 维</span>
          </p>
          <p v-if="item.imageEnrichmentStatus === 'queued'" class="document-image-meta">
            图片解析排队中
          </p>
          <p v-else-if="item.imageEnrichmentStatus === 'processing'" class="document-image-meta">
            图片解析中…
          </p>
          <p v-else-if="item.imageCandidateCount" class="document-image-meta">
            图片解析 · 入库 {{ item.imageIndexedCount || 0 }} · 跳过 {{ item.imageSkippedCount || 0 }} · 失败
            {{ item.imageFailedCount || 0 }}
          </p>
          <p
            v-if="item.legacyWarning"
            class="document-warning"
            :title="item.legacyWarning"
            :aria-label="item.legacyWarning"
          >
            历史数据 · 原文件不可下载
          </p>
          <p
            v-if="item.imageEnrichmentMessage && !item.legacyWarning && !['queued', 'processing'].includes(item.imageEnrichmentStatus || '')"
            class="document-image-warning"
            :title="item.imageEnrichmentMessage"
          >
            {{ item.imageEnrichmentMessage }}
          </p>
        </div>
        <div class="document-actions">
          <button class="text-action" type="button" :disabled="libraryBusy || ['deleting', 'cleanup_failed'].includes(item.status)" @click="changeArchive(item)"><component :is="item.archived ? ArchiveRestore : Archive" :size="15" aria-hidden="true" />{{ item.archived ? '恢复' : '归档' }}</button>
          <button class="text-action" type="button" :disabled="libraryBusy || ['deleting', 'cleanup_failed'].includes(item.status)" @click="editScope(item)">
            <Database :size="15" aria-hidden="true" />
            归属
          </button>
          <button class="text-action" type="button" :disabled="libraryBusy || item.status === 'processing' || item.status === 'deleting' || item.status === 'cleanup_failed'" @click="handlePreview(item)">
            <Eye :size="15" aria-hidden="true" />
            预览
          </button>
          <button class="text-action" type="button" :disabled="!item.downloadAvailable || libraryBusy" @click="handleDownload(item)">
            <Download :size="15" aria-hidden="true" />
            下载
          </button>
          <button class="text-action danger" type="button" :disabled="libraryBusy" @click="handleDelete(item)">
            <Trash2 :size="15" aria-hidden="true" />
            {{ deletingDocumentId === item.documentId ? '删除中' : '删除' }}
          </button>
        </div>
      </article>
    </div>

    <div v-if="total > 0" class="library-footer">
      <span>第 {{ page }} / {{ pageCount }} 页</span>
      <div class="pagination-actions">
        <button class="icon-action" type="button" aria-label="上一页" :disabled="!hasPreviousPage || libraryBusy" @click="movePage(page - 1)">
          <ChevronLeft :size="16" aria-hidden="true" />
        </button>
        <button class="icon-action" type="button" aria-label="下一页" :disabled="!hasNextPage || libraryBusy" @click="movePage(page + 1)">
          <ChevronRight :size="16" aria-hidden="true" />
        </button>
      </div>
    </div>
  </section>

  <Teleport to="body">
    <div v-if="previewLoading || previewState || previewError" class="preview-overlay" @click.self="closePreview">
      <section class="preview-dialog" role="dialog" aria-modal="true" aria-label="知识库文件预览">
        <header class="preview-header">
          <div>
            <p class="library-kicker">PREVIEW</p>
            <h2>{{ previewState?.fileName || '文件预览' }}</h2>
          </div>
          <button class="icon-action" type="button" aria-label="关闭预览" @click="closePreview">
            <X :size="18" aria-hidden="true" />
          </button>
        </header>
        <div v-if="previewLoading" class="preview-loading">
          <LoaderCircle class="spinning" :size="26" aria-hidden="true" />
          <span>正在读取文件…</span>
        </div>
        <p v-else-if="previewError" class="library-error">{{ previewError }}</p>
        <RagDocumentOriginalPreview
          v-else-if="previewState"
          :file-name="previewState.fileName"
          :document-id="previewState.documentId"
          :content-type="previewState.contentType"
          :blob="previewState.blob"
          :legacy="previewState.isLegacy"
          :legacy-warning="previewState.legacyWarning"
        />
      </section>
    </div>
  </Teleport>
</template>

<style scoped src="./KnowledgeDocumentLibrary.css"></style>
