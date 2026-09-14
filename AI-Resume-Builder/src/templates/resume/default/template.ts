import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/default-preview.svg'

export const DEFAULT_TEMPLATE: ResumeTemplateDefinition = {
  key: 'default',
  name: '默认模板',
  previewImage,
  component: ResumeTemplate,
}
