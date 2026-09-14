export function resolveTemplatePreviewImage(pathOrUrl: string, importMetaUrl: string): string {
  const value = pathOrUrl.trim()
  if (!value) return ''
  if (/^(https?:)?\/\//i.test(value)) return value
  if (/^(data|blob):/i.test(value)) return value
  return new URL(value, importMetaUrl).href
}
