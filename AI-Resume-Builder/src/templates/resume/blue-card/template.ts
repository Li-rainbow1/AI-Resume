import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/blue-card-preview.svg'

export const BLUE_CARD_TEMPLATE: ResumeTemplateDefinition = {
  key: 'blue-card',
  name: '蓝色卡片模板',
  previewImage,
  component: ResumeTemplate,
}
