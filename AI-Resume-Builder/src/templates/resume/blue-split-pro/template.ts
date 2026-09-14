import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/blue-split-pro-preview.svg'

export const BLUE_SPLIT_PRO_TEMPLATE: ResumeTemplateDefinition = {
  key: 'blue-split-pro',
  name: '蓝色分栏专业模板',
  previewImage,
  component: ResumeTemplate,
}
