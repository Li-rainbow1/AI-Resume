import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/workplace-general-preview.svg'

export const WORKPLACE_GENERAL_TEMPLATE: ResumeTemplateDefinition = {
  key: 'workplace-general',
  name: '通用职场模板',
  previewImage,
  component: ResumeTemplate,
}
