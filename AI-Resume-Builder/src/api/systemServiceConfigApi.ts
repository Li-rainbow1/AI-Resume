import { API_BASE_PATH } from './apiBase'

export type SystemServiceKey = 'embedding' | 'chat' | 'vision' | 'realtime' | 'rag' | 'smtp'

export interface SystemServiceDraftPayload {
  config: Record<string, unknown>
  secrets: Record<string, string>
}

export interface SystemServiceUpdatePayload extends SystemServiceDraftPayload {
  expectedVersion: number
}

export function getSystemServicesEndpoint(): string {
  return `${API_BASE_PATH}/admin/system-services`
}

export function getSystemServiceEndpoint(serviceKey: SystemServiceKey): string {
  return `${getSystemServicesEndpoint()}/${encodeURIComponent(serviceKey)}`
}

export function getSystemServiceTestEndpoint(serviceKey: SystemServiceKey): string {
  return `${getSystemServiceEndpoint(serviceKey)}/test`
}
