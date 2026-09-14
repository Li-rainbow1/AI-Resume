import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/blue-linear-preview.svg'

export const BLUE_LINEAR_TEMPLATE: ResumeTemplateDefinition = {
  key: 'blue-linear',
  name: '蓝色线性模板',
  previewImage,
  component: ResumeTemplate,
}
