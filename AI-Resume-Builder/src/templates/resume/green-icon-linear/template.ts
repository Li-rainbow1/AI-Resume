import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/green-icon-linear-preview.svg'

export const GREEN_ICON_LINEAR_TEMPLATE: ResumeTemplateDefinition = {
  key: 'green-icon-linear',
  name: '绿色图标线性模板',
  previewImage,
  component: ResumeTemplate,
}
