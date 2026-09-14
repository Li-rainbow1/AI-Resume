import type { RagMarkdownUploadManifest } from '@/api/ragApi'

export const MARKDOWN_ATTACHMENT_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp'] as const

export type MarkdownAttachmentSummary = {
  referencedImageCount: number
  matchedImageCount: number
  missingImageCount: number
  unreferencedImageCount: number
}

const IMAGE_REFERENCE_PATTERN = /!\[[^\]\r\n]*\]\(\s*(?:<([^>\r\n]+)>|([^\s)\r\n]+))/gu
const REFERENCE_IMAGE_PATTERN = /!\[([^\]\r\n]*)\]\[\s*([^\]\r\n]*)\s*\]/gu
const SHORTCUT_IMAGE_PATTERN = /!\[([^\]\r\n]+)\](?!\s*[[(])/gu
const REFERENCE_DEFINITION_PATTERN = /^\s{0,3}\[([^\]\r\n]+)\]:\s*(?:<([^>\r\n]+)>|([^\s\r\n]+))(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$/u
const FENCE_PATTERN = /^\s*(`{3,}|~{3,})/

export function getMarkdownBundleRelativePath(file: File): string {
  return file.webkitRelativePath || file.name
}

export function isSupportedMarkdownAttachment(file: File): boolean {
  return MARKDOWN_ATTACHMENT_EXTENSIONS.includes(getFileExtension(file.name) as (typeof MARKDOWN_ATTACHMENT_EXTENSIONS)[number])
}

export function normalizeMarkdownAttachmentPath(rawPath: string): string | null {
  let value = rawPath.trim()
  if (!value) return null
  try {
    value = decodeURIComponent(value)
  } catch {
    return null
  }
  value = value.replace(/\\/gu, '/')
  if (value.startsWith('/') || value.startsWith('//') || /^[a-z][a-z\d+.-]*:/iu.test(value)) return null
  if (/^[a-z]:\//iu.test(value)) return null
  value = (value.split('#', 1)[0] ?? '').split('?', 1)[0]?.trim() ?? ''
  const parts = value.split('/').filter((part) => part && part !== '.')
  if (parts.length === 0 || parts.some((part) => part === '..')) return null
  return parts.join('/')
}

export async function inspectMarkdownAttachments(
  documents: File[],
  attachments: File[]
): Promise<MarkdownAttachmentSummary> {
  const attachmentPaths = new Set(
    attachments
      .filter(isSupportedMarkdownAttachment)
      .map((file) => normalizeMarkdownAttachmentPath(getMarkdownBundleRelativePath(file)))
      .filter((path): path is string => path !== null)
  )
  const referencedPaths = new Set<string>()
  let referencedImageCount = 0
  let matchedImageCount = 0
  let missingImageCount = 0

  for (const document of documents) {
    const source = await document.text()
    const paths = extractMarkdownImagePaths(source)
    referencedImageCount += paths.length
    for (const path of paths) {
      referencedPaths.add(path)
      if (attachmentPaths.has(path)) matchedImageCount += 1
      else missingImageCount += 1
    }
  }

  return {
    referencedImageCount,
    matchedImageCount,
    missingImageCount,
    unreferencedImageCount: [...attachmentPaths].filter((path) => !referencedPaths.has(path)).length,
  }
}

export function buildMarkdownUploadManifest(
  documents: File[],
  attachments: File[]
): RagMarkdownUploadManifest {
  return {
    documents: documents.map((file, index) => ({
      index,
      relativePath: getMarkdownBundleRelativePath(file),
    })),
    attachments: attachments.map((file, index) => ({
      index: documents.length + index,
      relativePath: getMarkdownBundleRelativePath(file),
    })),
  }
}

export function extractMarkdownImagePaths(source: string): string[] {
  const paths: string[] = []
  const seen = new Set<string>()
  const lines = source.split(/\r?\n/u)
  const referenceDefinitions = new Map<string, string>()
  let inFence = false
  let fenceMarker = ''
  for (const line of lines) {
    const fenceMatch = line.match(FENCE_PATTERN)
    if (fenceMatch) {
      const marker = fenceMatch[1]?.[0]
      if (!marker) continue
      if (!inFence) {
        inFence = true
        fenceMarker = marker
      } else if (marker === fenceMarker) {
        inFence = false
        fenceMarker = ''
      }
      continue
    }
    if (inFence) continue
    const definitionMatch = line.match(REFERENCE_DEFINITION_PATTERN)
    if (definitionMatch) {
      const label = definitionMatch[1]
      if (label) {
        referenceDefinitions.set(normalizeReferenceLabel(label), definitionMatch[2] || definitionMatch[3] || '')
      }
    }
  }

  inFence = false
  fenceMarker = ''
  for (const line of lines) {
    const fenceMatch = line.match(FENCE_PATTERN)
    if (fenceMatch) {
      const marker = fenceMatch[1]?.[0]
      if (!marker) continue
      if (!inFence) {
        inFence = true
        fenceMarker = marker
      } else if (marker === fenceMarker) {
        inFence = false
        fenceMarker = ''
      }
      continue
    }
    if (inFence) continue
    for (const match of line.matchAll(IMAGE_REFERENCE_PATTERN)) {
      appendSupportedPath(paths, seen, match[1] || match[2] || '')
    }
    for (const match of line.matchAll(REFERENCE_IMAGE_PATTERN)) {
      const label = match[2] || match[1] || ''
      appendSupportedPath(paths, seen, referenceDefinitions.get(normalizeReferenceLabel(label)) || '')
    }
    for (const match of line.matchAll(SHORTCUT_IMAGE_PATTERN)) {
      appendSupportedPath(paths, seen, referenceDefinitions.get(normalizeReferenceLabel(match[1] || '')) || '')
    }
  }
  return paths
}

function appendSupportedPath(paths: string[], seen: Set<string>, rawPath: string): void {
  const path = normalizeMarkdownAttachmentPath(rawPath)
  if (!path || !isSupportedMarkdownImagePath(path) || seen.has(path)) return
  seen.add(path)
  paths.push(path)
}

function normalizeReferenceLabel(label: string): string {
  return label.trim().replace(/\s+/gu, ' ').toLocaleLowerCase()
}

export function isSupportedMarkdownImagePath(path: string): boolean {
  return MARKDOWN_ATTACHMENT_EXTENSIONS.includes(getFileExtension(path) as (typeof MARKDOWN_ATTACHMENT_EXTENSIONS)[number])
}

function getFileExtension(fileName: string): string {
  const dotIndex = fileName.lastIndexOf('.')
  return dotIndex >= 0 ? fileName.slice(dotIndex).toLowerCase() : ''
}
