export type BailianHotwordValidation = {
  normalized: string
  error: string
}

const BAILIAN_HOTWORD_LIMITS: Record<string, number> = {
  'qwen-audio-3.0-asr-flash-streaming': 2000,
  'fun-asr-realtime': 2000,
}
const DEFAULT_BAILIAN_HOTWORD_LIMIT = 2000
const MAX_NON_ASCII_HOTWORD_CHARS = 15
const MAX_ASCII_HOTWORD_SEGMENTS = 7

export function getBailianHotwordLimit(model: unknown): number {
  const normalizedModel = String(model ?? '').trim().toLowerCase()
  return BAILIAN_HOTWORD_LIMITS[normalizedModel] ?? DEFAULT_BAILIAN_HOTWORD_LIMIT
}

export function normalizeBailianHotwords(value: unknown, model: unknown): BailianHotwordValidation {
  const terms: string[] = []
  const seen = new Set<string>()
  const lines = String(value ?? '').split(/\r\n?|\n/)

  for (let index = 0; index < lines.length; index += 1) {
    const term = lines[index]?.trim().split(/\s+/).filter(Boolean).join(' ') || ''
    if (!term) continue

    const lineNumber = index + 1
    const hasNonAscii = Array.from(term).some((character) => character.charCodeAt(0) > 0x7f)
    if (hasNonAscii && Array.from(term).length > MAX_NON_ASCII_HOTWORD_CHARS) {
      return {
        normalized: terms.join('\n'),
        error: `第 ${lineNumber} 行热词含非 ASCII 字符，长度不能超过 ${MAX_NON_ASCII_HOTWORD_CHARS} 个字符`,
      }
    }
    if (!hasNonAscii && term.split(' ').length > MAX_ASCII_HOTWORD_SEGMENTS) {
      return {
        normalized: terms.join('\n'),
        error: `第 ${lineNumber} 行纯 ASCII 热词按空格分隔后不能超过 ${MAX_ASCII_HOTWORD_SEGMENTS} 个片段`,
      }
    }

    const key = term.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    terms.push(term)
  }

  const maxTerms = getBailianHotwordLimit(model)
  if (terms.length > maxTerms) {
    return {
      normalized: terms.join('\n'),
      error: `热词最多配置 ${maxTerms} 条，当前为 ${terms.length} 条`,
    }
  }
  return { normalized: terms.join('\n'), error: '' }
}
