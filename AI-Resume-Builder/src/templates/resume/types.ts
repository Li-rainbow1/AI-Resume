import type { Component } from 'vue'

export interface ResumeTemplateModel {
  key: string
  name: string
  previewImage: string
}

export interface ResumeTemplateDefinition extends ResumeTemplateModel {
  component: Component
}

export type ResumeTemplateKey = ResumeTemplateDefinition['key']
