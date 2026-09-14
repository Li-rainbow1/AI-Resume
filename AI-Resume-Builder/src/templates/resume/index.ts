import { BLACK_WHITE_LINEAR_TEMPLATE } from './black-white-linear/template'
import { BLUE_CARD_TEMPLATE } from './blue-card/template'
import { BLUE_LINEAR_TEMPLATE } from './blue-linear/template'
import { BLUE_SIDEBAR_CAREER_TEMPLATE } from './blue-sidebar-career/template'
import { BLUE_SPLIT_PRO_TEMPLATE } from './blue-split-pro/template'
import { DEFAULT_TEMPLATE } from './default/template'
import { GREEN_ICON_LINEAR_TEMPLATE } from './green-icon-linear/template'
import { RED_GRADIENT_TEMPLATE } from './red-gradient-template/template'
import { WORKPLACE_GENERAL_TEMPLATE } from './workplace-general/template'
import type { ResumeTemplateDefinition, ResumeTemplateKey } from './types'

export type { ResumeTemplateDefinition, ResumeTemplateKey, ResumeTemplateModel } from './types'

const LEGACY_TEMPLATE_ALIAS: Record<string, ResumeTemplateKey> = {
  'classic-blue': 'blue-linear',
  'red-gradient-model': 'red-gradient-template',
}

function buildTemplateRegistry(templates: ResumeTemplateDefinition[]): ResumeTemplateDefinition[] {
  const keySet = new Set<string>()

  templates.forEach((template) => {
    if (!template.key?.trim()) {
      throw new Error('Resume template key is required.')
    }
    if (!template.name?.trim()) {
      throw new Error(`Resume template name is required. key=${template.key}`)
    }
    if (!template.previewImage?.trim()) {
      throw new Error(`Resume template previewImage is required. key=${template.key}`)
    }
    if (keySet.has(template.key)) {
      throw new Error(`Duplicate resume template key: ${template.key}`)
    }
    keySet.add(template.key)
  })

  return templates
}

export const RESUME_TEMPLATES = buildTemplateRegistry([
  DEFAULT_TEMPLATE,
  BLUE_LINEAR_TEMPLATE,
  GREEN_ICON_LINEAR_TEMPLATE,
  BLACK_WHITE_LINEAR_TEMPLATE,
  WORKPLACE_GENERAL_TEMPLATE,
  BLUE_SIDEBAR_CAREER_TEMPLATE,
  BLUE_SPLIT_PRO_TEMPLATE,
  BLUE_CARD_TEMPLATE,
  RED_GRADIENT_TEMPLATE,
])

const TEMPLATE_MAP = RESUME_TEMPLATES.reduce<Record<string, ResumeTemplateDefinition>>((acc, template) => {
  acc[template.key] = template
  return acc
}, {})

const FALLBACK_TEMPLATE: ResumeTemplateDefinition = (() => {
  const template = TEMPLATE_MAP.default ?? RESUME_TEMPLATES[0]
  if (!template) {
    throw new Error('At least one resume template must be registered.')
  }
  return template
})()

export function isResumeTemplateKey(value: string): value is ResumeTemplateKey {
  return Boolean(TEMPLATE_MAP[value])
}

export function getResumeTemplateByKey(key: ResumeTemplateKey): ResumeTemplateDefinition {
  return TEMPLATE_MAP[key] ?? FALLBACK_TEMPLATE
}

export function normalizeResumeTemplateKey(value: unknown): ResumeTemplateKey {
  if (typeof value !== 'string') return FALLBACK_TEMPLATE.key
  const normalized = LEGACY_TEMPLATE_ALIAS[value] ?? value
  return isResumeTemplateKey(normalized) ? normalized : FALLBACK_TEMPLATE.key
}
