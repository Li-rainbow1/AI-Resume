import { API_BASE_PATH } from './apiBase'
import { fetchWithAuth } from '@/services/authService'
import type { RagDocumentItem } from './ragApi'

export interface RagKnowledgeBase {
  knowledgeBaseId: string
  name: string
  aliases: string[]
}

/** 旧项目接口的响应类型，供一版兼容调用保留。 */
export interface RagProject {
  projectId: string
  name: string
  aliases: string[]
}

export interface RagDocumentScope {
  scopeKind: 'knowledge_base' | 'unclassified' | 'project' | 'general'
  knowledgeBaseId?: string | null
  /** 旧字段只用于兼容旧上传调用。 */
  projectId?: string | null
}

export interface RagListFilters {
  archive?: 'active' | 'archived' | 'all'
  scopeKind?: string
  knowledgeBaseId?: string
  /** 旧筛选字段只用于兼容旧页面。 */
  projectId?: string
}

export interface RagQueryResult {
  answer: string
  knowledgeBaseIds: string[]
  knowledgeBaseNames: string[]
  /** 旧响应字段，后端仍返回一版。 */
  projectIds: string[]
  sources: Array<{ sourceId: string; content: string; metadata: Record<string, unknown> }>
}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetchWithAuth(API_BASE_PATH + '/ai/rag' + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '知识库操作失败')
  return result as T
}

export const listRagKnowledgeBases = () => request<RagKnowledgeBase[]>('/knowledge-bases')

export async function saveRagKnowledgeBase(name: string, aliases: string[], knowledgeBaseId?: string) {
  const path = knowledgeBaseId
    ? '/knowledge-bases/' + encodeURIComponent(knowledgeBaseId)
    : '/knowledge-bases'
  const result = await request<RagKnowledgeBase>(path, knowledgeBaseId ? 'PATCH' : 'POST', { name, aliases })
  window.dispatchEvent(new Event('rag-knowledge-bases-changed'))
  return result
}

export async function deleteRagKnowledgeBase(knowledgeBaseId: string) {
  const result = await request<{ knowledgeBaseId: string; deleted: boolean }>(
    '/knowledge-bases/' + encodeURIComponent(knowledgeBaseId),
    'DELETE'
  )
  window.dispatchEvent(new Event('rag-knowledge-bases-changed'))
  return result
}

export const setRagDocumentArchived = (id: string, archived: boolean) =>
  request<RagDocumentItem>('/documents/' + encodeURIComponent(id) + (archived ? '/archive' : '/restore'), 'POST')

export const changeRagDocumentKnowledgeBase = (id: string, knowledgeBaseId: string | null) =>
  request<RagDocumentItem>('/documents/' + encodeURIComponent(id) + '/knowledge-base', 'PATCH', {
    scopeKind: knowledgeBaseId ? 'knowledge_base' : 'unclassified',
    knowledgeBaseId,
  })

/** 旧函数名兼容入口，新的页面只使用知识库字段。 */
export const changeRagDocumentScope = (id: string, scope: RagDocumentScope) =>
  request<RagDocumentItem>('/documents/' + encodeURIComponent(id) + '/scope', 'PATCH', {
    scopeKind: scope.scopeKind === 'knowledge_base' ? 'project' : scope.scopeKind,
    projectId: scope.projectId || scope.knowledgeBaseId || null,
  })

export const queryRagKnowledge = (query: string) =>
  request<RagQueryResult>('/query', 'POST', { query })

export function appendRagScope(form: FormData, scope?: RagDocumentScope) {
  if (!scope) return
  const knowledgeBaseId = scope.knowledgeBaseId || scope.projectId || null
  if (!knowledgeBaseId || scope.scopeKind === 'unclassified' || scope.scopeKind === 'general') {
    form.append('scopeKind', 'unclassified')
    return
  }
  form.append('scopeKind', 'knowledge_base')
  form.append('knowledgeBaseId', knowledgeBaseId)
}

// 旧函数名保留一版，继续访问旧路径和旧字段，防止未同时升级的管理页面失效。
export const listRagProjects = () => request<RagProject[]>('/projects')

export async function saveRagProject(name: string, aliases: string[], projectId?: string) {
  const path = projectId
    ? '/projects/' + encodeURIComponent(projectId)
    : '/projects'
  const result = await request<RagProject>(path, projectId ? 'PATCH' : 'POST', { name, aliases })
  window.dispatchEvent(new Event('rag-projects-changed'))
  return result
}
