import ResumeTemplate from './ResumeTemplate.vue'
import type { ResumeTemplateDefinition } from '../types'
import previewImage from '../../../assets/templates/resume/blue-sidebar-career-preview.svg'

export const BLUE_SIDEBAR_CAREER_TEMPLATE: ResumeTemplateDefinition = {
  key: 'blue-sidebar-career',
  name: '蓝色侧栏职场模板',
  previewImage,
  component: ResumeTemplate,
}
