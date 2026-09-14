import type { RagDocumentItem } from '@/api/ragApi'

export function formatFileSize(size: number): string {
  if (size <= 0) return '0 B'
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

export function formatCreatedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value || '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function formatFileType(document: RagDocumentItem): string {
  const extension = document.fileName.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] || ''
  const extensionLabels: Record<string, string> = {
    pdf: 'PDF',
    doc: 'DOC',
    docx: 'DOCX',
    txt: 'TXT',
    md: 'MD',
    markdown: 'MD',
    png: 'PNG',
    jpg: 'JPG',
    jpeg: 'JPEG',
    webp: 'WEBP',
  }
  if (extensionLabels[extension]) return extensionLabels[extension]

  const mimeType = document.contentType.toLowerCase().split(';', 1)[0]?.trim() || ''
  const mimeLabels: Record<string, string> = {
    'application/pdf': 'PDF',
    'application/msword': 'DOC',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
    'text/plain': 'TXT',
    'text/markdown': 'MD',
    'image/png': 'PNG',
    'image/jpeg': 'JPEG',
    'image/webp': 'WEBP',
  }
  return mimeLabels[mimeType] || {
    pdf: 'PDF',
    word: 'DOC/DOCX',
    txt: 'TXT',
    md: 'MD',
    image: '图片',
    other: '其他文件',
  }[document.fileType] || '其他文件'
}

export function formatFileTypeBadge(document: RagDocumentItem): string {
  const label = formatFileType(document)
  return label === '其他文件' ? '其他' : label
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    ready: '已入库',
    legacy: '历史数据',
    processing: '处理中',
    deleting: '删除中',
    cleanup_failed: '待清理',
  }
  return labels[status] || status || '未知'
}

export function statusClass(status: string): string {
  if (status === 'ready') return 'is-ready'
  if (status === 'legacy') return 'is-legacy'
  if (status === 'cleanup_failed') return 'is-warning'
  return 'is-pending'
}
