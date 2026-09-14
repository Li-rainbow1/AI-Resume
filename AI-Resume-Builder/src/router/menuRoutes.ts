export const PRIMARY_MENU_ROUTE_PATHS = {
  'resume-editor': '/resume-editor',
  'ai-interviewer': '/ai-interviewer',
  'knowledge-base': '/knowledge-base',
  'system-services': '/system-services',
  'account-settings': '/account-settings',
} as const

export const SYSTEM_SERVICE_CONFIG_ROUTE_PATH = PRIMARY_MENU_ROUTE_PATHS['system-services']
export const LEGACY_SYSTEM_SERVICE_CONFIG_ROUTE_PATH = '/account-settings/system-services'

export type PrimaryMenuKey = keyof typeof PRIMARY_MENU_ROUTE_PATHS

export const DEFAULT_PRIMARY_MENU_KEY: PrimaryMenuKey = 'resume-editor'

const PATH_TO_PRIMARY_MENU: Record<string, PrimaryMenuKey> = {
  [PRIMARY_MENU_ROUTE_PATHS['resume-editor']]: 'resume-editor',
  [PRIMARY_MENU_ROUTE_PATHS['ai-interviewer']]: 'ai-interviewer',
  [PRIMARY_MENU_ROUTE_PATHS['knowledge-base']]: 'knowledge-base',
  [PRIMARY_MENU_ROUTE_PATHS['system-services']]: 'system-services',
  [PRIMARY_MENU_ROUTE_PATHS['account-settings']]: 'account-settings',
}

export function normalizePrimaryRoutePath(pathname: string): string {
  const normalized = pathname.replace(/\/+$/, '')
  return normalized || '/'
}

export function resolvePrimaryMenuPath(key: PrimaryMenuKey): string {
  return PRIMARY_MENU_ROUTE_PATHS[key]
}

export function resolvePrimaryMenuFromPath(pathname: string): PrimaryMenuKey {
  const normalized = normalizePrimaryRoutePath(pathname)
  if (normalized === '/') return DEFAULT_PRIMARY_MENU_KEY
  if (normalized === LEGACY_SYSTEM_SERVICE_CONFIG_ROUTE_PATH) return 'system-services'
  return PATH_TO_PRIMARY_MENU[normalized] ?? DEFAULT_PRIMARY_MENU_KEY
}

export function isPrimaryMenuRoutePath(pathname: string): boolean {
  const normalized = normalizePrimaryRoutePath(pathname)
  return normalized === '/' || normalized === LEGACY_SYSTEM_SERVICE_CONFIG_ROUTE_PATH || normalized in PATH_TO_PRIMARY_MENU
}

export function isSystemServiceConfigRoutePath(pathname: string): boolean {
  const normalized = normalizePrimaryRoutePath(pathname)
  return normalized === SYSTEM_SERVICE_CONFIG_ROUTE_PATH || normalized === LEGACY_SYSTEM_SERVICE_CONFIG_ROUTE_PATH
}
