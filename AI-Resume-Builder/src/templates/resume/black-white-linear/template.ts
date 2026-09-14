import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/black-white-linear-preview.svg'

export const BLACK_WHITE_LINEAR_TEMPLATE: ResumeTemplateDefinition = {
  key: 'black-white-linear',
  name: '黑白线性模板',
  previewImage,
  component: ResumeTemplate,
}
