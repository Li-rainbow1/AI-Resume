<script setup lang="ts">
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { fetchRagDocumentAsset } from '@/api/ragApi'
import {
  isSupportedMarkdownImagePath,
  normalizeMarkdownAttachmentPath,
} from '@/services/knowledgeMarkdownAttachmentService'

interface Props {
  documentId: string
  fileName: string
  contentType: string
  blob: Blob
  legacy: boolean
  legacyWarning?: string | null
}

type PreviewKind = 'pdf' | 'image' | 'text' | 'markdown' | 'docx' | 'legacy-text' | 'unsupported'

const props = defineProps<Props>()

const markdownRenderer = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: false,
}).enable(['table', 'strikethrough'])
const cjkEmphasisMarker = '\uE000'
const objectUrl = ref('')
const textContent = ref('')
const markdownHtml = ref('')
const isLoading = ref(false)
const errorMessage = ref('')
const docxContainer = ref<HTMLElement | null>(null)
let renderVersion = 0
let markdownObjectUrls: string[] = []

const fileExtension = computed(() => {
  const fileName = props.fileName.toLowerCase()
  const dotIndex = fileName.lastIndexOf('.')
  return dotIndex >= 0 ? fileName.slice(dotIndex) : ''
})

const previewKind = computed<PreviewKind>(() => {
  if (props.legacy) return 'legacy-text'
  if (fileExtension.value === '.pdf') return 'pdf'
  if (['.png', '.jpg', '.jpeg', '.webp'].includes(fileExtension.value)) return 'image'
  if (fileExtension.value === '.txt') return 'text'
  if (fileExtension.value === '.md') return 'markdown'
  if (fileExtension.value === '.docx') return 'docx'
  return 'unsupported'
})

const legacyMessage = computed(
  () => props.legacyWarning || '历史数据无法预览原文件，当前显示已入库的解析文本。'
)

watch(
  [() => props.documentId, () => props.fileName, () => props.contentType, () => props.blob, () => props.legacy],
  () => {
    void renderPreview()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  renderVersion += 1
  releaseObjectUrl()
  releaseMarkdownObjectUrls()
})

async function renderPreview() {
  const currentVersion = ++renderVersion
  releaseObjectUrl()
  releaseMarkdownObjectUrls()
  textContent.value = ''
  markdownHtml.value = ''
  errorMessage.value = ''
  docxContainer.value?.replaceChildren()
  isLoading.value = true

  try {
    if (previewKind.value === 'pdf' || previewKind.value === 'image') {
      objectUrl.value = URL.createObjectURL(getPreviewBlob())
    } else if (previewKind.value === 'text' || previewKind.value === 'legacy-text') {
      textContent.value = await decodeText(getPreviewBlob())
    } else if (previewKind.value === 'markdown') {
      const sourceText = await decodeText(getPreviewBlob())
      if (currentVersion !== renderVersion) return
      const sanitizedHtml = DOMPurify.sanitize(
        markdownRenderer
          .render(normalizeMarkdownSource(sourceText))
          .replace(/<\/strong> \uE000/gu, '</strong>')
      )
      const resolvedHtml = await resolveMarkdownImages(sanitizedHtml, currentVersion)
      if (currentVersion !== renderVersion) return
      markdownHtml.value = resolvedHtml
    } else if (previewKind.value === 'docx') {
      await renderDocx(currentVersion)
    } else {
      errorMessage.value = '暂不支持此文件类型的预览。'
    }
  } catch {
    if (currentVersion === renderVersion) {
      errorMessage.value = previewKind.value === 'docx' ? 'DOCX 原文件渲染失败，请检查文件内容。' : '文件预览失败。'
    }
  } finally {
    if (currentVersion === renderVersion) isLoading.value = false
  }
}

async function resolveMarkdownImages(sanitizedHtml: string, currentVersion: number): Promise<string> {
  const template = document.createElement('template')
  template.innerHTML = sanitizedHtml
  const failedPaths = new Set<string>()
  const objectUrls = new Map<string, string>()

  for (const image of Array.from(template.content.querySelectorAll('img'))) {
    if (currentVersion !== renderVersion) return ''
    const relativePath = normalizeMarkdownAttachmentPath(image.getAttribute('src') || '')
    if (!relativePath || !isSupportedMarkdownImagePath(relativePath) || !props.documentId) {
      replaceMissingImage(image)
      continue
    }
    if (failedPaths.has(relativePath)) {
      replaceMissingImage(image)
      continue
    }

    let objectUrl = objectUrls.get(relativePath)
    if (!objectUrl) {
      try {
        const asset = await fetchRagDocumentAsset(props.documentId, relativePath)
        objectUrl = URL.createObjectURL(asset.blob)
        if (currentVersion !== renderVersion) {
          URL.revokeObjectURL(objectUrl)
          return ''
        }
        objectUrls.set(relativePath, objectUrl)
        markdownObjectUrls.push(objectUrl)
      } catch {
        failedPaths.add(relativePath)
        replaceMissingImage(image)
        continue
      }
    }
    image.setAttribute('src', objectUrl)
    image.removeAttribute('srcset')
  }
  return template.innerHTML
}

function replaceMissingImage(image: HTMLImageElement) {
  const placeholder = document.createElement('span')
  placeholder.className = 'rag-original-preview__missing-image'
  placeholder.textContent = '图片附件未上传'
  image.replaceWith(placeholder)
}

async function renderDocx(currentVersion: number) {
  await nextTick()
  if (currentVersion !== renderVersion || !docxContainer.value) return

  const docxPreview = await import('docx-preview')
  if (currentVersion !== renderVersion || !docxContainer.value) return

  await docxPreview.renderAsync(getPreviewBlob(), docxContainer.value, undefined, {
    inWrapper: true,
    breakPages: true,
    renderHeaders: true,
    renderFooters: true,
    renderFootnotes: true,
    renderEndnotes: true,
  })
  if (currentVersion !== renderVersion) docxContainer.value.replaceChildren()
}

async function decodeText(blob: Blob): Promise<string> {
  const bytes = await blob.arrayBuffer()
  return new TextDecoder('utf-8').decode(bytes).replace(/^\uFEFF/, '')
}

function normalizeMarkdownSource(sourceText: string): string {
  let inFence = false
  let fenceMarker = ''

  return sourceText
    .split('\n')
    .map((line) => {
      const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/)
      if (fenceMatch) {
        const marker = fenceMatch[1]?.[0]
        if (!marker) return line
        if (!inFence) {
          inFence = true
          fenceMarker = marker
        } else if (marker === fenceMarker) {
          inFence = false
          fenceMarker = ''
        }
        return line
      }
      if (inFence) return line

      return line.replace(
        /(\*\*([^*\r\n]+?)\*\*|__([^_\r\n]+?)__)(?=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])/gu,
        `$1 ${cjkEmphasisMarker}`
      )
    })
    .join('\n')
}

function getPreviewBlob(): Blob {
  if (props.blob.type || !props.contentType) return props.blob
  return props.blob.slice(0, props.blob.size, props.contentType)
}

function releaseObjectUrl() {
  if (!objectUrl.value) return
  URL.revokeObjectURL(objectUrl.value)
  objectUrl.value = ''
}

function releaseMarkdownObjectUrls() {
  for (const url of markdownObjectUrls) URL.revokeObjectURL(url)
  markdownObjectUrls = []
}
</script>

<template>
  <div class="rag-original-preview">
    <p v-if="props.legacy" class="rag-original-preview__legacy-tip">{{ legacyMessage }}</p>

    <div v-if="previewKind === 'docx'" class="rag-original-preview__docx">
      <div ref="docxContainer" class="rag-original-preview__docx-document"></div>
      <div v-if="isLoading" class="rag-original-preview__loading">正在渲染 DOCX 原文件…</div>
      <p v-else-if="errorMessage" class="rag-original-preview__error">{{ errorMessage }}</p>
    </div>
    <div v-else-if="isLoading" class="rag-original-preview__loading">正在读取原文件…</div>
    <p v-else-if="errorMessage" class="rag-original-preview__error">{{ errorMessage }}</p>
    <iframe
      v-else-if="previewKind === 'pdf'"
      class="rag-original-preview__pdf"
      :src="objectUrl"
      :title="props.fileName"
    ></iframe>
    <div v-else-if="previewKind === 'image'" class="rag-original-preview__image-wrap">
      <img class="rag-original-preview__image" :src="objectUrl" :alt="props.fileName" />
    </div>
    <article v-else-if="previewKind === 'markdown'" class="rag-original-preview__markdown" v-html="markdownHtml"></article>
    <pre v-else-if="previewKind === 'text' || previewKind === 'legacy-text'" class="rag-original-preview__text">{{ textContent }}</pre>
  </div>
</template>

<style scoped>
.rag-original-preview {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}

.rag-original-preview__legacy-tip {
  flex: 0 0 auto;
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff4d6;
  color: #8a5a00;
  font-size: 13px;
  line-height: 1.6;
}

.rag-original-preview__loading,
.rag-original-preview__error {
  display: flex;
  min-height: 220px;
  align-items: center;
  justify-content: center;
  padding: 20px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--surface-base);
  color: var(--text-secondary);
  text-align: center;
}

.rag-original-preview__error {
  color: var(--accent-red);
}

.rag-original-preview__pdf,
.rag-original-preview__image-wrap,
.rag-original-preview__text,
.rag-original-preview__markdown,
.rag-original-preview__docx {
  width: 100%;
  min-height: 0;
  flex: 1 1 0;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--surface-base);
}

.rag-original-preview__pdf {
  display: block;
}

.rag-original-preview__image-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  padding: 18px;
}

.rag-original-preview__image {
  max-width: 100%;
  max-height: 62vh;
  object-fit: contain;
}

.rag-original-preview__text {
  overflow: auto;
  margin: 0;
  padding: 16px;
  color: var(--text-primary);
  font-family: 'Cascadia Mono', 'Microsoft YaHei UI', monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.rag-original-preview__markdown {
  overflow: auto;
  padding: 18px 22px;
  color: var(--text-primary);
  line-height: 1.75;
}

.rag-original-preview__markdown :deep(:first-child) {
  margin-top: 0;
}

.rag-original-preview__markdown :deep(:last-child) {
  margin-bottom: 0;
}

.rag-original-preview__markdown :deep(pre) {
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: var(--surface-muted, #f3f5f7);
}

.rag-original-preview__markdown :deep(code) {
  padding: 2px 4px;
  border-radius: 4px;
  background: var(--surface-muted, #f3f5f7);
  font-family: 'Cascadia Mono', monospace;
}

.rag-original-preview__markdown :deep(pre code) {
  padding: 0;
  background: transparent;
}

.rag-original-preview__markdown :deep(h1),
.rag-original-preview__markdown :deep(h2),
.rag-original-preview__markdown :deep(h3),
.rag-original-preview__markdown :deep(h4),
.rag-original-preview__markdown :deep(h5),
.rag-original-preview__markdown :deep(h6) {
  margin: 1.25em 0 0.55em;
  color: var(--text-primary);
  font-weight: 700;
  line-height: 1.35;
}

.rag-original-preview__markdown :deep(h1) {
  font-size: 1.55em;
}

.rag-original-preview__markdown :deep(h2) {
  padding-bottom: 0.35em;
  border-bottom: 1px solid var(--border-color);
  font-size: 1.3em;
}

.rag-original-preview__markdown :deep(h3) {
  font-size: 1.12em;
}

.rag-original-preview__markdown :deep(p) {
  margin: 0.65em 0;
}

.rag-original-preview__markdown :deep(ul),
.rag-original-preview__markdown :deep(ol) {
  margin: 0.65em 0;
  padding-left: 1.65em;
}

.rag-original-preview__markdown :deep(ul) {
  list-style: disc;
}

.rag-original-preview__markdown :deep(ol) {
  list-style: decimal;
}

.rag-original-preview__markdown :deep(li) {
  margin: 0.3em 0;
  padding-left: 0.2em;
}

.rag-original-preview__markdown :deep(strong) {
  font-weight: 700;
  color: var(--text-primary);
}

.rag-original-preview__markdown :deep(blockquote) {
  margin: 0.9em 0;
  padding: 0.65em 1em;
  border-left: 3px solid var(--accent-blue, #2563eb);
  background: var(--surface-muted, #f3f5f7);
  color: var(--text-secondary);
}

.rag-original-preview__markdown :deep(table) {
  width: 100%;
  min-width: 620px;
  margin: 1em 0;
  border-collapse: collapse;
  font-size: 0.95em;
}

.rag-original-preview__markdown :deep(th),
.rag-original-preview__markdown :deep(td) {
  padding: 9px 12px;
  border: 1px solid var(--border-color);
  text-align: left;
  vertical-align: top;
}

.rag-original-preview__markdown :deep(th) {
  background: var(--surface-muted, #f3f5f7);
  font-weight: 700;
}

.rag-original-preview__markdown :deep(tbody tr:nth-child(even)) {
  background: rgb(37 99 235 / 3%);
}

.rag-original-preview__markdown :deep(a) {
  color: var(--accent-blue, #2563eb);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.rag-original-preview__markdown :deep(img) {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0.9em auto;
  border-radius: 8px;
}

.rag-original-preview__missing-image {
  display: block;
  width: fit-content;
  margin: 0.9em auto;
  padding: 8px 12px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: var(--surface-muted, #f3f5f7);
  color: var(--text-secondary);
  font-size: 13px;
}

.rag-original-preview__markdown :deep(hr) {
  margin: 1.25em 0;
  border: 0;
  border-top: 1px solid var(--border-color);
}

.rag-original-preview__docx {
  display: flex;
  position: relative;
  min-height: 0;
  flex: 1 1 auto;
  flex-direction: column;
  overflow: hidden;
  background: #f3f5f7;
}

.rag-original-preview__docx-document {
  min-height: 0;
  flex: 1 1 auto;
  overflow-x: auto;
  overflow-y: auto;
  padding: 16px;
  scrollbar-gutter: stable;
}

.rag-original-preview__docx-document :deep(.docx-wrapper) {
  min-height: 100%;
  margin: 0 auto;
}

.rag-original-preview__docx-document :deep(.docx) {
  max-width: 100%;
}

.rag-original-preview__docx .rag-original-preview__loading,
.rag-original-preview__docx .rag-original-preview__error {
  position: absolute;
  inset: 0;
  min-height: 0;
  border: 0;
  border-radius: 0;
  background: rgb(255 255 255 / 82%);
}

@media (max-width: 760px) {
  .rag-original-preview__pdf,
  .rag-original-preview__image-wrap,
  .rag-original-preview__text,
  .rag-original-preview__markdown,
  .rag-original-preview__docx {
    min-height: 0;
  }

  .rag-original-preview__markdown {
    padding: 16px;
  }
}
</style>
