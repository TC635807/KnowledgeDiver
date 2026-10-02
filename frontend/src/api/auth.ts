import type { AuthResponse, LoginRequest, RegisterRequest, AuthUser } from '../types/auth'
import { getLocalToken } from './localAccount'
import { clearServerToken, remoteFetch, setServerToken } from './remote'

/**
 * 旧的共用 token 读取口。
 * @deprecated 本地请求请直接用 api/localAccount.ts 的 getLocalToken()；云端用 api/remote.ts 的 getServerToken()。
 * 保留此别名只为兼容仍在 import 的调用点（值 = 本地 token，绝不会返回服务器 token）。
 */
export function getToken(): string | null {
  return getLocalToken()
}

export function extractErrorMessage(error: unknown): string {
  const detail = (error as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail || '请求失败'
  if (Array.isArray(detail)) {
    return detail.map((e) => (e && (e as { msg?: string }).msg) || JSON.stringify(e)).join('; ')
  }
  return String(detail || '请求失败')
}

/**
 * 本地工作区调用（**始终**附带本地 token）。
 * 45 处调用点名字不变；失败时抛出的错误带有后端 detail，便于界面直接展示。
 */
export async function authFetchWithToken<T>(url: string, options: RequestInit = {}): Promise<T> {
  const token = getLocalToken()
  if (!token) {
    throw new Error('本地身份未就绪：请先完成本地登录（POST /api/auth/local-session）')
  }

  const headers: Record<string, string> = {
    Authorization: 'Bearer ' + token,
  }
  // FormData 不能手工设置 Content-Type（浏览器要自己加 multipart boundary）
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  if (options.headers) {
    Object.assign(headers, options.headers)
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(extractErrorMessage(error))
  }

  if (response.status === 204) return undefined as unknown as T
  return (await response.json()) as T
}

// ────────────────────────────────────────────────────────────────
// 云端账号：一律经本地反向代理白名单，携带 serverToken
// ────────────────────────────────────────────────────────────────

export async function login(data: LoginRequest): Promise<AuthResponse> {
  const result = await remoteFetch<AuthResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
  })
  setServerToken(result.access_token)
  return result
}

export async function register(data: RegisterRequest): Promise<AuthResponse> {
  const result = await remoteFetch<AuthResponse>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(data),
  })
  setServerToken(result.access_token)
  return result
}

export async function getMe(): Promise<AuthUser> {
  return remoteFetch<AuthUser>('/api/auth/me')
}

/** 退出云端账号登录（本地身份不受影响）。 */
export function logout(): void {
  clearServerToken()
}
