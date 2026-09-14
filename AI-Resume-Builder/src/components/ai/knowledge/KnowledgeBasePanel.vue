<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Blocks,
  CircleX,
  CloudUpload,
  DatabaseZap,
  FileText,
  FileUp,
  FolderOpen,
  Image as ImageIcon,
  ListChecks,
  Play,
  ScanText,
  Server,
  ShieldCheck,
  Trash2,
} from 'lucide-vue-next'
import {
  uploadMarkdownBundleStream,
  uploadKnowledgeAssetsStream,
  type RagDocumentImageEnrichmentResponse,
  type RagUploadProgressEvent,
  type RagUploadResponse,
} from '@/api/ragApi'
import {
  normalizeUploadFileResult,
} from '@/services/knowledgeUploadResultService'
import {
  buildMarkdownUploadManifest,
  inspectMarkdownAttachments,
  isSupportedMarkdownAttachment,
} from '@/services/knowledgeMarkdownAttachmentService'
import {
  buildUploadSummary,
  clampProgress,
  countUploadItems,
  createFailedItem,
  createPendingItem,
  createResultItem,
  dedupeAttachmentFiles,
  dedupeFiles,
  formatFileSize,
  ingestSourceLabel,
  isMarkdownFile,
  markCancelledItems,
  markInterruptedItems,
  normalizeEventStage,
  normalizeOptionalPercent,
  normalizeUploadSummary,
  patchUploadItem,
  replaceUploadItem,
  resolveAttachmentFolderName,
  resolveEventFileIndex,
  resolveEventFileProgress,
  resolveItemProgress,
  sourceTypeLabel,
  statusLabel,
  toRagUploadFileResult,
  type LocalUploadItem,
  type UploadPhase,
} from '@/services/knowledgeUploadBatchService'
import { createKnowledgeUploadImageEnrichmentPoller } from '@/services/knowledgeUploadImageEnrichmentPolling'
import KnowledgeDocumentLibrary from './KnowledgeDocumentLibrary.vue'
import RagKnowledgeBasePicker from './RagKnowledgeBasePicker.vue'
import { listRagKnowledgeBases, type RagKnowledgeBase, type RagDocumentScope } from '@/api/ragScopeApi'

type CompactMetric = {
  icon: 'files' | 'checks' | 'chunks'
  label: string
  value: string
}
type GuidanceFact = {
  icon: 'format' | 'limit' | 'target' | 'type'
  label: string
  value: string
}

const ACCEPT = '.pdf,.txt,.md,.docx,.png,.jpg,.jpeg,.webp'
const ATTACHMENT_ACCEPT = '.png,.jpg,.jpeg,.webp'

const uploadScope = ref('unclassified')
const uploadKnowledgeBases = ref<RagKnowledgeBase[]>([])
const selectedFiles = ref<File[]>([])
const selectedAttachmentFiles = ref<File[]>([])
const attachmentSummary = ref<Awaited<ReturnType<typeof inspectMarkdownAttachments>> | null>(null)
const attachmentFolderName = ref('')
const uploadItems = ref<LocalUploadItem[]>([])
const uploadSummary = ref<RagUploadResponse | null>(null)
const errorMessage = ref('')
const isUploading = ref(false)
const isKnowledgeBasePickerOpen = ref(false)
const isDragOver = ref(false)
const libraryRefreshKey = ref(0)
const displayedUploadProgressPercent = ref(0)
const backendUploadProgressPercent = ref<number | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const attachmentFolderInputRef = ref<HTMLInputElement | null>(null)
let abortController: AbortController | null = null
let progressAnimationTimer: number | null = null
let attachmentInspectVersion = 0

const documentScope = computed<RagDocumentScope>(() => uploadScope.value === 'unclassified'
  ? { scopeKind: 'unclassified' }
  : { scopeKind: 'knowledge_base', knowledgeBaseId: uploadScope.value })

const uploadImageEnrichmentPoller = createKnowledgeUploadImageEnrichmentPoller(
  () => uploadItems.value,
  updateUploadImageEnrichment,
  () => {
    libraryRefreshKey.value += 1
  }
)

const hasFiles = computed(() => selectedFiles.value.length > 0)
const hasMarkdownFiles = computed(() => selectedFiles.value.some(isMarkdownFile))
const hasAttachmentFiles = computed(() => selectedAttachmentFiles.value.length > 0)
const hasUploadItems = computed(() => uploadItems.value.length > 0)
const totalFiles = computed(() => uploadSummary.value?.totalFiles ?? selectedFiles.value.length)
const succeededFiles = computed(
  () => uploadSummary.value?.succeededFiles ?? countUploadItems(uploadItems.value, ['success'])
)
const failedFiles = computed(
  () => uploadSummary.value?.failedFiles ?? countUploadItems(uploadItems.value, ['failed'])
)
const cancelledFiles = computed(() => countUploadItems(uploadItems.value, ['cancelled']))
const insertedChunks = computed(
  () => uploadSummary.value?.inserted ?? uploadItems.value.reduce((sum, item) => sum + item.insertedCount, 0)
)
const totalSelectedSize = computed(() => selectedFiles.value.reduce((sum, file) => sum + file.size, 0))
const totalSelectedSizeLabel = computed(() => formatFileSize(totalSelectedSize.value))
const processedFiles = computed(() => countUploadItems(uploadItems.value, ['success', 'failed']))
const activeUploadIndex = computed(() => uploadItems.value.findIndex((item) => item.status === 'uploading'))
const activeUploadItem = computed(() => {
  if (activeUploadIndex.value < 0) return null
  return uploadItems.value[activeUploadIndex.value] ?? null
})
const uploadPhase = computed<UploadPhase>(() => {
  if (isUploading.value) return 'uploading'
  if (errorMessage.value) return 'error'
  if (uploadSummary.value) return 'completed'
  if (selectedFiles.value.length > 0) return 'ready'
  return 'idle'
})
const uploadProgressPercent = computed(() => {
  const total = totalFiles.value
  if (total <= 0) return 0
  if (uploadPhase.value === 'completed' && cancelledFiles.value === 0) return 100
  if (uploadPhase.value === 'ready' || uploadPhase.value === 'idle') return 0
  if (backendUploadProgressPercent.value !== null) {
    const percent = backendUploadProgressPercent.value
    if (isUploading.value) return Math.min(99, Math.max(4, percent))
    return percent
  }
  const weightedProgress = uploadItems.value.reduce((sum, item) => sum + resolveItemProgress(item), 0)
  const percent = Math.round((weightedProgress / total) * 100)
  if (isUploading.value) return Math.min(99, Math.max(4, percent))
  return percent
})
const uploadProgressText = computed(() => {
  if (isUploading.value && activeUploadItem.value) {
    const stageText = activeUploadItem.value.stage ? `（${activeUploadItem.value.stage}）` : ''
    return `正在处理第 ${activeUploadItem.value.order}/${totalFiles.value} 个：${activeUploadItem.value.fileName}${stageText}`
  }
  if (isUploading.value) {
    return `正在处理文件，请稍等，已完成 ${processedFiles.value}/${totalFiles.value} 个`
  }
  if (uploadPhase.value === 'completed') {
    return failedFiles.value > 0
      ? `已处理 ${processedFiles.value}/${totalFiles.value} 个文件，${failedFiles.value} 个失败`
      : `已完成 ${totalFiles.value} 个文件`
  }
  if (uploadPhase.value === 'error') {
    if (cancelledFiles.value > 0) return `已取消，已处理 ${processedFiles.value}/${totalFiles.value} 个文件`
    return `上传中断，已处理 ${processedFiles.value}/${totalFiles.value} 个文件`
  }
  if (uploadPhase.value === 'ready') return `等待开始，共 ${totalFiles.value} 个文件`
  return '等待选择文件'
})
const progressAssistText = computed(() => {
  const cancelledText = cancelledFiles.value > 0 ? `，取消 ${cancelledFiles.value}` : ''
  return `成功 ${succeededFiles.value}，失败 ${failedFiles.value}${cancelledText}，Chunk ${insertedChunks.value}`
})
const compactSummary = computed(() => {
  switch (uploadPhase.value) {
    case 'ready':
      return `已选 ${totalFiles.value} 个文件，共 ${totalSelectedSizeLabel.value}`
    case 'uploading':
      return uploadProgressText.value
    case 'completed':
      return `本批次已完成，成功 ${succeededFiles.value}，失败 ${failedFiles.value}`
    case 'error':
      return errorMessage.value || '上传未完成，请查看当前批次状态'
    default:
      return '文档与图片可混合上传，结果会在下方直接更新'
  }
})
const attachmentSummaryText = computed(() => {
  const summary = attachmentSummary.value
  if (!summary) return '正在分析 Markdown 中的图片引用…'
  return `已匹配 ${summary.matchedImageCount} 张，将参与 OCR/RAG；缺失 ${summary.missingImageCount} 张，未引用 ${summary.unreferencedImageCount} 张`
})
const compactMetrics = computed<CompactMetric[]>(() => [
  { icon: 'files', label: '文件', value: String(totalFiles.value) },
  { icon: 'checks', label: '成功/失败', value: `${succeededFiles.value}/${failedFiles.value}` },
  { icon: 'chunks', label: 'Chunk', value: String(insertedChunks.value) },
])
const resultSectionTitle = computed(() => {
  switch (uploadPhase.value) {
    case 'completed':
      return '处理结果'
    case 'uploading':
      return '当前进度'
    case 'error':
      return '错误与进度'
    default:
      return '当前批次'
  }
})
const emptyStateTitle = computed(() => {
  if (uploadPhase.value === 'error') return '本次上传未完成'
  return '从这里开始整理知识库资料'
})
const emptyStateText = computed(() => {
  if (uploadPhase.value === 'error') {
    return errorMessage.value || '请重新选择文件并再次发起上传。'
  }
  return '选择文件后，会在这里直接看到当前批次和处理结果。'
})
const selectedKnowledgeBaseName = computed(() => {
  if (uploadScope.value === 'unclassified') return '未分类'
  return uploadKnowledgeBases.value.find((item) => item.knowledgeBaseId === uploadScope.value)?.name || '未分类'
})
const guidanceFacts: GuidanceFact[] = [
  { icon: 'format', label: '支持格式', value: 'PDF / TXT / MD / DOCX / PNG / JPG / JPEG / WEBP' },
  { icon: 'limit', label: '单文件限制', value: '10 MB' },
  { icon: 'target', label: '写入目标', value: 'MinIO + pgvector' },
  { icon: 'type', label: '支持类型', value: '文档 / 图片' },
]

watch(uploadProgressPercent, (targetPercent) => {
  animateDisplayedProgress(targetPercent)
})

async function refreshUploadKnowledgeBases() {
  try {
    uploadKnowledgeBases.value = await listRagKnowledgeBases()
    if (uploadScope.value !== 'unclassified' && !uploadKnowledgeBases.value.some((item) => item.knowledgeBaseId === uploadScope.value)) {
      uploadScope.value = 'unclassified'
    }
  } catch (cause) {
    errorMessage.value = cause instanceof Error ? cause.message : '知识库目录加载失败'
  }
}

onMounted(() => {
  void refreshUploadKnowledgeBases()
  window.addEventListener('rag-knowledge-bases-changed', refreshUploadKnowledgeBases)
})
onUnmounted(() => {
  window.removeEventListener('rag-knowledge-bases-changed', refreshUploadKnowledgeBases)
  stopProgressAnimation()
  stopImageEnrichmentPolling()
})

function openFilePicker() {
  if (isUploading.value) return
  fileInputRef.value?.click()
}
function openKnowledgeBasePicker() {
  if (isUploading.value) return
  isKnowledgeBasePickerOpen.value = true
}
function closeKnowledgeBasePicker() {
  isKnowledgeBasePickerOpen.value = false
}
function confirmKnowledgeBase(knowledgeBaseId: string) {
  uploadScope.value = knowledgeBaseId || 'unclassified'
  isKnowledgeBasePickerOpen.value = false
}
function openAttachmentFolderPicker() {
  if (isUploading.value) return
  attachmentFolderInputRef.value?.click()
}
function onFileChange(event: Event) {
  if (isUploading.value) return
  const input = event.target as HTMLInputElement
  syncSelectedFiles(input.files ? Array.from(input.files) : [])
  input.value = ''
}
function onAttachmentFolderChange(event: Event) {
  if (isUploading.value) return
  const input = event.target as HTMLInputElement
  if (!hasMarkdownFiles.value) {
    resetAttachmentSelection()
    errorMessage.value = '请先选择 Markdown 文件，再选择附件文件夹。'
    input.value = ''
    return
  }
  const nonMarkdownCount = selectedFiles.value.filter((file) => !isMarkdownFile(file)).length
  if (nonMarkdownCount > 0) {
    selectedFiles.value = selectedFiles.value.filter(isMarkdownFile)
  }
  const files = input.files ? Array.from(input.files).filter(isSupportedMarkdownAttachment) : []
  selectedAttachmentFiles.value = dedupeAttachmentFiles(files)
  attachmentFolderName.value = resolveAttachmentFolderName(files)
  attachmentSummary.value = null
  resetUploadFeedback()
  if (nonMarkdownCount > 0) {
    errorMessage.value = `已移除 ${nonMarkdownCount} 个非 Markdown 文件；附件模式只处理 Markdown 正文。`
  }
  syncPendingUploadItems()
  void refreshAttachmentSummary()
  input.value = ''
}
function onDrop(event: DragEvent) {
  event.preventDefault()
  isDragOver.value = false
  if (isUploading.value) return
  syncSelectedFiles(event.dataTransfer?.files ? Array.from(event.dataTransfer.files) : [])
}
function syncSelectedFiles(files: File[]) {
  if (files.length === 0) return
  const markdownOnly = hasAttachmentFiles.value
  const acceptedFiles = markdownOnly ? files.filter(isMarkdownFile) : files
  const baseFiles = uploadPhase.value === 'ready' ? selectedFiles.value : []
  const mergedFiles = dedupeFiles([...baseFiles, ...acceptedFiles])
  selectedFiles.value = markdownOnly ? mergedFiles.filter(isMarkdownFile) : mergedFiles
  resetUploadFeedback()
  if (markdownOnly && (acceptedFiles.length !== files.length || selectedFiles.value.length !== mergedFiles.length)) {
    errorMessage.value = '已选择附件文件夹，此模式只能上传 Markdown 正文。'
  }
  syncPendingUploadItems()
  void refreshAttachmentSummary()
}
function removeFile(index: number) {
  if (isUploading.value) return
  selectedFiles.value = selectedFiles.value.filter((_, itemIndex) => itemIndex !== index)
  if (!hasMarkdownFiles.value) resetAttachmentSelection()
  resetUploadFeedback()
  syncPendingUploadItems()
  void refreshAttachmentSummary()
}
function clearFiles() {
  if (isUploading.value) return
  selectedFiles.value = []
  resetAttachmentSelection()
  uploadItems.value = []
  resetUploadFeedback()
}
function clearAttachmentFiles() {
  if (isUploading.value) return
  resetAttachmentSelection()
  resetUploadFeedback()
  syncPendingUploadItems()
}
function resetAttachmentSelection() {
  selectedAttachmentFiles.value = []
  attachmentFolderName.value = ''
  attachmentSummary.value = null
  attachmentInspectVersion += 1
}
async function handleUpload() {
  if (!hasFiles.value || isUploading.value) return
  const filesToUpload = [...selectedFiles.value]
  isUploading.value = true
  resetUploadFeedback()
  abortController = new AbortController()
  const signal = abortController.signal
  uploadItems.value = filesToUpload.map((file, index) => createPendingItem(file, 'queued', index + 1))
  try {
    const callbacks = {
      onEvent: (event: RagUploadProgressEvent) => handleUploadProgressEvent(event, filesToUpload),
    }
    if (hasAttachmentFiles.value) {
      if (filesToUpload.some((file) => !isMarkdownFile(file))) {
        throw new Error('选择附件文件夹后只能上传 Markdown 正文。')
      }
      await uploadMarkdownBundleStream(
        filesToUpload,
        selectedAttachmentFiles.value,
        buildMarkdownUploadManifest(filesToUpload, selectedAttachmentFiles.value),
        callbacks,
        signal,
        documentScope.value
      )
    } else {
      await uploadKnowledgeAssetsStream(filesToUpload, callbacks, signal, documentScope.value)
    }
    if (!uploadSummary.value) {
      uploadSummary.value = buildUploadSummary(uploadItems.value)
    }
    libraryRefreshKey.value += 1
    startImageEnrichmentPolling()
  } catch (error) {
    const message = error instanceof Error ? error.message : '知识库上传失败'
    if (signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
      errorMessage.value = '已取消上传，已保留当前批次进度。'
      const startIndex = activeUploadIndex.value >= 0 ? activeUploadIndex.value : processedFiles.value
      uploadItems.value = markCancelledItems(uploadItems.value, startIndex)
    } else {
      errorMessage.value = message
      uploadItems.value = markInterruptedItems(uploadItems.value, message)
    }
  }
  isUploading.value = false
  if (!uploadSummary.value) {
    uploadSummary.value = buildUploadSummary(uploadItems.value)
  }
  abortController = null
}
function cancelUpload() {
  abortController?.abort()
}
function resetUploadFeedback() {
  stopImageEnrichmentPolling()
  uploadSummary.value = null
  errorMessage.value = ''
  backendUploadProgressPercent.value = null
  animateDisplayedProgress(0)
}

function startImageEnrichmentPolling() {
  uploadImageEnrichmentPoller.start()
}

function stopImageEnrichmentPolling() {
  uploadImageEnrichmentPoller.stop()
}

function syncPendingUploadItems() {
  uploadItems.value = selectedFiles.value.map((file, index) => createPendingItem(file, 'pending', index + 1))
}

function updateUploadImageEnrichment(documentId: string, response: RagDocumentImageEnrichmentResponse) {
  uploadItems.value = uploadItems.value.map((item) =>
    item.documentId === documentId
      ? {
          ...item,
          imageCandidateCount: response.candidateCount,
          imageAnalyzedCount: response.analyzedCount,
          imageIndexedCount: response.indexedCount,
          imageSkippedCount: response.skippedCount,
          imageFailedCount: response.failedCount,
          imageChunkCount: response.chunkCount,
          imageEnrichmentStatus: response.status,
          imageEnrichmentMessage: response.message || null,
          imageRetryAvailable: response.retryAvailable ?? false,
        }
      : item
  )
  if (uploadSummary.value) {
    uploadSummary.value = {
      ...uploadSummary.value,
      files: uploadItems.value.map(toRagUploadFileResult),
    }
  }
}

function animateDisplayedProgress(targetPercent: number) {
  const normalizedTarget = clampProgress(targetPercent)
  if (normalizedTarget <= displayedUploadProgressPercent.value) {
    displayedUploadProgressPercent.value = normalizedTarget
    stopProgressAnimation()
    return
  }
  stopProgressAnimation()
  progressAnimationTimer = window.setInterval(() => {
    const distance = normalizedTarget - displayedUploadProgressPercent.value
    if (distance <= 0) {
      displayedUploadProgressPercent.value = normalizedTarget
      stopProgressAnimation()
      return
    }

    const step = Math.max(1, Math.ceil(distance / 5))
    displayedUploadProgressPercent.value = Math.min(normalizedTarget, displayedUploadProgressPercent.value + step)
  }, 80)
}
function stopProgressAnimation() {
  if (progressAnimationTimer === null) return
  window.clearInterval(progressAnimationTimer)
  progressAnimationTimer = null
}

function handleUploadProgressEvent(event: RagUploadProgressEvent, files: File[]) {
  const eventName = event.event
  syncBackendUploadProgress(event)
  if (eventName === 'file-start' || eventName === 'file-stage') {
    updateActiveUploadItem(event, files)
    return
  }

  if (eventName === 'file-result') {
    updateResultUploadItem(event, files)
    return
  }

  if (eventName === 'batch-complete') {
    const summary = normalizeUploadSummary(
      event.summary ?? event,
      uploadItems.value.map(toRagUploadFileResult)
    )
    if (summary) {
      uploadSummary.value = summary
    }
  }
}

function updateActiveUploadItem(event: RagUploadProgressEvent, files: File[]) {
  const index = resolveEventFileIndex(event, files)
  if (index < 0) return
  const file = files[index]
  if (!file || !uploadItems.value[index]) return
  const stage = normalizeEventStage(event)
  uploadItems.value = patchUploadItem(uploadItems.value, index, {
    ...createPendingItem(file, 'uploading', index + 1),
    stage,
    stageProgress: resolveEventFileProgress(event, stage),
    errorMessage: null,
  })
}

function updateResultUploadItem(event: RagUploadProgressEvent, files: File[]) {
  const index = resolveEventFileIndex(event, files)
  if (index < 0) return
  const file = files[index]
  if (!file) return
  const result = normalizeUploadFileResult(event.result)
  const stage = normalizeEventStage(event)
  uploadItems.value = replaceUploadItem(
    uploadItems.value,
    index,
    result
      ? createResultItem(result, file, index + 1, stage, resolveEventFileProgress(event, stage))
      : createFailedItem(file, index + 1, event.message || '后端未返回文件结果')
  )
}

function syncBackendUploadProgress(event: RagUploadProgressEvent) {
  const progressPercent = normalizeOptionalPercent(event.progressPercent ?? event.progress_percent)
  if (progressPercent === null) return
  backendUploadProgressPercent.value = progressPercent
}

async function refreshAttachmentSummary() {
  const version = ++attachmentInspectVersion
  if (!hasAttachmentFiles.value || !selectedFiles.value.some(isMarkdownFile)) {
    attachmentSummary.value = null
    return
  }
  try {
    const summary = await inspectMarkdownAttachments(
      selectedFiles.value.filter(isMarkdownFile),
      selectedAttachmentFiles.value
    )
    if (version === attachmentInspectVersion) attachmentSummary.value = summary
  } catch {
    if (version === attachmentInspectVersion) {
      attachmentSummary.value = null
      errorMessage.value = '无法读取 Markdown 附件引用，请检查文件编码后重试。'
    }
  }
}
</script>

<template>
  <section class="knowledge-panel">
    <div class="panel-shell">
      <div class="workspace-layout">
        <section class="panel main-card">
          <div
            class="dropzone"
            :class="{ 'is-drag-over': isDragOver, 'is-disabled': isUploading }"
            @dragenter.prevent="isUploading ? undefined : (isDragOver = true)"
            @dragover.prevent="isUploading ? undefined : (isDragOver = true)"
            @dragleave.prevent="isDragOver = false"
            @drop="onDrop"
          >
            <input
              ref="fileInputRef"
              class="file-input"
              type="file"
              multiple
              :accept="ACCEPT"
              @change="onFileChange"
            />
            <input
              ref="attachmentFolderInputRef"
              class="file-input"
              type="file"
              webkitdirectory
              directory
              multiple
              :accept="ATTACHMENT_ACCEPT"
              @change="onAttachmentFolderChange"
            />
            <div class="dropzone-icon" aria-hidden="true">
              <CloudUpload :size="30" stroke-width="1.75" />
            </div>
            <h3>拖拽或选择文件</h3>
            <dl class="upload-facts">
              <div v-for="fact in guidanceFacts" :key="fact.label" class="fact-row">
                <span class="fact-icon" aria-hidden="true">
                  <FileText v-if="fact.icon === 'format'" :size="17" stroke-width="1.8" />
                  <ShieldCheck v-else-if="fact.icon === 'limit'" :size="17" stroke-width="1.8" />
                  <Server v-else-if="fact.icon === 'target'" :size="17" stroke-width="1.8" />
                  <ScanText v-else :size="17" stroke-width="1.8" />
                </span>
                <div>
                  <dt>{{ fact.label }}</dt>
                  <dd>{{ fact.value }}</dd>
                </div>
              </div>
            </dl>
          </div>

          <div class="panel-toolbar">
            <button class="primary-btn" type="button" :disabled="isUploading" @click="openFilePicker">
              <FileUp :size="18" stroke-width="1.9" aria-hidden="true" />
              选择文件
            </button>
            <button
              class="ghost-btn scope-picker-trigger"
              type="button"
              :disabled="isUploading"
              :aria-expanded="isKnowledgeBasePickerOpen"
              aria-haspopup="dialog"
              :aria-label="`选择知识库，当前为${selectedKnowledgeBaseName}`"
              @click="openKnowledgeBasePicker"
            >
              <DatabaseZap :size="17" stroke-width="1.9" aria-hidden="true" />
              <span>选择知识库</span>
              <span class="scope-picker-current">{{ selectedKnowledgeBaseName }}</span>
            </button>
            <button
              v-if="hasMarkdownFiles"
              class="ghost-btn"
              type="button"
              :disabled="isUploading"
              @click="openAttachmentFolderPicker"
            >
              <FolderOpen :size="17" stroke-width="1.9" aria-hidden="true" />
              选择 Markdown 附件文件夹
            </button>
            <button class="ghost-btn" type="button" :disabled="!hasFiles || isUploading" @click="clearFiles">
              <Trash2 :size="17" stroke-width="1.9" aria-hidden="true" />
              清空列表
            </button>
            <button class="ghost-btn" type="button" :disabled="!hasFiles || isUploading" @click="handleUpload">
              <Play :size="17" stroke-width="1.9" aria-hidden="true" />
              开始上传
            </button>
            <button v-if="isUploading" class="ghost-btn danger-btn" type="button" @click="cancelUpload">
              <CircleX :size="17" stroke-width="1.9" aria-hidden="true" />
              取消上传
            </button>
          </div>

          <div v-if="hasMarkdownFiles && hasAttachmentFiles" class="attachment-summary-card">
            <div>
              <strong>Markdown 图片附件</strong>
              <span>{{ attachmentFolderName }} · {{ selectedAttachmentFiles.length }} 个图片文件</span>
            </div>
            <p>{{ attachmentSummaryText }}</p>
            <small>正文引用的图片会参与 OCR/视觉解析，并作为文字 Chunk 加入知识库。</small>
            <button class="attachment-clear-btn" type="button" :disabled="isUploading" @click="clearAttachmentFiles">
              移除附件文件夹
            </button>
          </div>

          <div class="compact-bar">
            <p class="compact-summary">{{ compactSummary }}</p>
            <ul class="metric-list">
              <li v-for="metric in compactMetrics" :key="metric.label" class="metric-item">
                <span class="metric-icon" aria-hidden="true">
                  <FileText v-if="metric.icon === 'files'" :size="19" stroke-width="1.8" />
                  <ListChecks v-else-if="metric.icon === 'checks'" :size="19" stroke-width="1.8" />
                  <Blocks v-else :size="19" stroke-width="1.8" />
                </span>
                <span>
                  <span class="metric-label">{{ metric.label }}</span>
                  <strong>{{ metric.value }}</strong>
                </span>
              </li>
            </ul>
          </div>

          <div
            v-if="hasUploadItems"
            class="upload-progress-card"
            :class="{ 'is-uploading': isUploading, 'is-error': uploadPhase === 'error' }"
          >
            <div class="progress-top">
              <div class="progress-copy">
                <span>文件级进度</span>
                <strong>{{ uploadProgressText }}</strong>
              </div>
              <span class="progress-percent">{{ displayedUploadProgressPercent }}%</span>
            </div>
            <div
              class="progress-track"
              role="progressbar"
              aria-label="知识库上传文件级进度"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="displayedUploadProgressPercent"
            >
              <span :style="{ width: `${displayedUploadProgressPercent}%` }"></span>
            </div>
            <p class="progress-assist">{{ progressAssistText }}</p>
          </div>

          <p v-if="errorMessage" class="panel-error">{{ errorMessage }}</p>

          <div class="result-block">
            <div class="result-head">
              <h3>{{ resultSectionTitle }}</h3>
              <span v-if="hasUploadItems" class="section-count-chip">{{ uploadItems.length }} 项</span>
            </div>

            <div v-if="!hasUploadItems" class="results-empty" :class="{ 'is-error': uploadPhase === 'error' }">
              <div class="empty-mark" aria-hidden="true">
                <DatabaseZap :size="28" stroke-width="1.75" />
              </div>
              <h3>{{ emptyStateTitle }}</h3>
              <p>{{ emptyStateText }}</p>
            </div>

            <ul v-else class="result-list">
              <li
                v-for="(item, index) in uploadItems"
                :key="`${item.fileName}-${index}`"
                class="result-item"
                :class="`result-${item.status}`"
              >
                <span class="source-icon" aria-hidden="true">
                  <ImageIcon v-if="item.sourceType === 'image'" :size="18" stroke-width="1.8" />
                  <FileText v-else :size="18" stroke-width="1.8" />
                </span>

                <div class="result-main">
                  <div class="result-heading">
                    <div>
                      <p class="file-name">{{ item.fileName }}</p>
                      <p class="file-meta">
                        {{ sourceTypeLabel(item.sourceType) }} · {{ formatFileSize(item.fileSize) }} ·
                        {{ ingestSourceLabel(item.ingestSource) }}
                      </p>
                    </div>
                    <span class="status-pill" :class="`status-${item.status}`">{{ statusLabel(item.status) }}</span>
                  </div>

                  <div class="file-submeta">
                    <span>第 {{ item.order }} 个</span>
                    <span v-if="item.stage">阶段 {{ item.stage }}</span>
                    <span>chunk {{ item.chunkCount }}</span>
                    <span>入库 {{ item.insertedCount }}</span>
                    <span v-if="item.referencedImageCount">图片 {{ item.matchedImageCount }}/{{ item.referencedImageCount }}</span>
                  </div>

                  <p v-if="item.imageEnrichmentStatus === 'queued'" class="file-image-summary">
                    图片解析排队中，正文已入库
                  </p>

                  <p v-else-if="item.imageEnrichmentStatus === 'processing'" class="file-image-summary">
                    图片解析中，正文已入库
                  </p>

                  <p
                    v-else-if="item.imageCandidateCount || item.imageFailedCount || item.imageSkippedCount"
                    class="file-image-summary"
                  >
                    图片解析：入库 {{ item.imageIndexedCount || 0 }}、跳过 {{ item.imageSkippedCount || 0 }}、失败
                    {{ item.imageFailedCount || 0 }}<span v-if="item.imageChunkCount">，Chunk {{ item.imageChunkCount }}</span>
                  </p>

                  <p
                    v-if="item.imageEnrichmentMessage && item.imageEnrichmentMessage !== item.errorMessage && !['queued', 'processing'].includes(item.imageEnrichmentStatus || '')"
                    class="file-error"
                  >
                    {{ item.imageEnrichmentMessage }}
                  </p>

                  <div v-if="item.status === 'uploading'" class="file-progress-line" aria-hidden="true">
                    <span></span>
                  </div>

                  <p v-if="item.errorMessage" class="file-error">{{ item.errorMessage }}</p>
                </div>

                <button
                  v-if="uploadPhase === 'ready'"
                  class="remove-btn"
                  type="button"
                  aria-label="删除文件"
                  title="删除文件"
                  @click="removeFile(index)"
                >
                  <Trash2 :size="16" stroke-width="1.9" aria-hidden="true" />
                </button>
              </li>
            </ul>
          </div>
        </section>
        <KnowledgeDocumentLibrary :refresh-key="libraryRefreshKey" />
      </div>
    </div>
    <RagKnowledgeBasePicker
      :open="isKnowledgeBasePickerOpen"
      :model-value="uploadScope"
      :knowledge-bases="uploadKnowledgeBases"
      @close="closeKnowledgeBasePicker"
      @confirm="confirmKnowledgeBase"
    />
  </section>
</template>

<style scoped src="./KnowledgeBasePanel.css"></style>
<style scoped src="./KnowledgeBasePanel.responsive.css"></style>
