import type { AuthResponse, LoginRequest, RegisterRequest, AuthUser } from '../types/auth'

const TOKEN_KEY = 'knowledgeDiver.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

async function authFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  }
  if (token) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }))
    const message = extractErrorMessage(error)
    throw new Error(message)
  }

  return response.json()
}

function extractErrorMessage(error: any): string {
  const detail = error.detail
  if (typeof detail === 'string') return detail || '请求失败'
  if (Array.isArray(detail)) {
    return detail.map((e: any) => e.msg || JSON.stringify(e)).join('; ')
  }
  return String(detail || '请求失败')
}

export async function login(data: LoginRequest): Promise<AuthResponse> {
  const result = await authFetch<AuthResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
  })
  setToken(result.access_token)
  return result
}

export async function register(data: RegisterRequest): Promise<AuthResponse> {
  const result = await authFetch<AuthResponse>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(data),
  })
  setToken(result.access_token)
  return result
}

export async function getMe(): Promise<AuthUser> {
  return authFetch<AuthUser>('/api/auth/me')
}

export function logout(): void {
  clearToken()
}

export async function authFetchWithToken<T>(url: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  if (!token) {
    throw new Error('未认证')
  }
  
  const headers: Record<string, string> = {
    'Authorization': `Bearer ${token}`,
  }
  // Do not set Content-Type for FormData — the browser sets multipart boundary automatically
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
    const message = extractErrorMessage(error)
    throw new Error(message)
  }

  return response.json()
}
