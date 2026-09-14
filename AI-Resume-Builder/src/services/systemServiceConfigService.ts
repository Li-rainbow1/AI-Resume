import {
  getSystemServiceEndpoint,
  getSystemServiceTestEndpoint,
  getSystemServicesEndpoint,
  type SystemServiceDraftPayload,
  type SystemServiceKey,
  type SystemServiceUpdatePayload,
} from '@/api/systemServiceConfigApi'
import { fetchWithAuth } from '@/services/authService'

export type { SystemServiceKey } from '@/api/systemServiceConfigApi'

export interface SecretState {
  configured: boolean
  lastFour: string
  masked: string
}

export interface SystemServiceSnapshot {
  serviceKey: SystemServiceKey
  source: 'deployment' | 'database' | string
  version: number
  config: Record<string, unknown>
  secrets: Record<string, SecretState>
  status: string
  validationMessage: string
  validatedAt: string | null
  embeddingProfile: string | null
  requiresRebuild: boolean
  /** 后端根据部署环境提供的 Ollama 主机地址，不是密钥。 */
  ollamaBaseUrl: string | null
}

export interface SystemServiceTestResult {
  serviceKey: SystemServiceKey
  success: boolean
  message: string
  elapsedMs: number
}

function isServiceKey(value: unknown): value is SystemServiceKey {
  return ['embedding', 'chat', 'vision', 'realtime', 'rag', 'smtp'].includes(String(value))
}

function normalizeSnapshot(value: unknown): SystemServiceSnapshot | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  if (!isServiceKey(raw.serviceKey)) return null
  const config = raw.config && typeof raw.config === 'object' ? (raw.config as Record<string, unknown>) : {}
  const rawSecrets = raw.secrets && typeof raw.secrets === 'object' ? (raw.secrets as Record<string, unknown>) : {}
  const secrets: Record<string, SecretState> = {}
  Object.entries(rawSecrets).forEach(([name, state]) => {
    if (!state || typeof state !== 'object') return
    const rawState = state as Record<string, unknown>
    secrets[name] = {
      configured: rawState.configured === true,
      lastFour: String(rawState.lastFour ?? ''),
      masked: String(rawState.masked ?? ''),
    }
  })
  return {
    serviceKey: raw.serviceKey,
    source: String(raw.source ?? 'deployment'),
    version: Number.isFinite(Number(raw.version)) ? Number(raw.version) : 0,
    config,
    secrets,
    status: String(raw.status ?? 'unknown'),
    validationMessage: String(raw.validationMessage ?? ''),
    validatedAt: raw.validatedAt ? String(raw.validatedAt) : null,
    embeddingProfile: raw.embeddingProfile ? String(raw.embeddingProfile) : null,
    requiresRebuild: raw.requiresRebuild === true,
    ollamaBaseUrl: raw.ollamaBaseUrl ? String(raw.ollamaBaseUrl) : null,
  }
}

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  const payload = (await response.clone().json().catch(() => null)) as Record<string, unknown> | null
  const detail = payload?.detail
  return typeof detail === 'string' && detail.trim() ? detail.trim() : fallback
}

export async function fetchSystemServiceSnapshots(): Promise<SystemServiceSnapshot[]> {
  const response = await fetchWithAuth(getSystemServicesEndpoint(), {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(await readErrorMessage(response, '系统服务配置加载失败'))
  const payload = (await response.json().catch(() => [])) as unknown
  if (!Array.isArray(payload)) throw new Error('系统服务配置响应格式无效')
  return payload.map(normalizeSnapshot).filter((item): item is SystemServiceSnapshot => item !== null)
}

export async function testSystemService(
  serviceKey: SystemServiceKey,
  payload: SystemServiceDraftPayload,
): Promise<SystemServiceTestResult> {
  const response = await fetchWithAuth(getSystemServiceTestEndpoint(serviceKey), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response, '系统服务连接测试失败'))
  const value = (await response.json().catch(() => null)) as Record<string, unknown> | null
  return {
    serviceKey,
    success: value?.success === true,
    message: String(value?.message ?? '连接测试未返回结果'),
    elapsedMs: Math.max(0, Number(value?.elapsedMs ?? 0)),
  }
}

export async function saveSystemService(
  serviceKey: SystemServiceKey,
  payload: SystemServiceUpdatePayload,
): Promise<SystemServiceSnapshot> {
  const response = await fetchWithAuth(getSystemServiceEndpoint(serviceKey), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await readErrorMessage(response, '系统服务保存失败'))
  const snapshot = normalizeSnapshot(await response.json().catch(() => null))
  if (!snapshot) throw new Error('系统服务保存响应格式无效')
  return snapshot
}

export async function restoreSystemService(
  serviceKey: SystemServiceKey,
  expectedVersion: number,
): Promise<SystemServiceSnapshot> {
  const response = await fetchWithAuth(`${getSystemServiceEndpoint(serviceKey)}?expectedVersion=${encodeURIComponent(expectedVersion)}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await readErrorMessage(response, '恢复部署默认配置失败'))
  const snapshot = normalizeSnapshot(await response.json().catch(() => null))
  if (!snapshot) throw new Error('恢复默认配置响应格式无效')
  return snapshot
}
