import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/red-gradient-template-preview.svg'

export const RED_GRADIENT_TEMPLATE: ResumeTemplateDefinition = {
  key: 'red-gradient-template',
  name: '红色渐变模板',
  previewImage,
  component: ResumeTemplate,
}
