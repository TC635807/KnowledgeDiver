/**
 * AI 接口配置（运行时设置）API。
 *
 * 后端把配置**直接写回项目根目录的 .env**（不新增配置文件），
 * 保存后立即生效、无需重启；API Key 只在服务端保存，读取时仅返回脱敏值。
 */
import { getLocalToken } from './localAccount'

export type AISettingsView = {
  api_url: string
  /** 卡片生成与 Agent 共用同一个模型 */
  model: string
  api_key_set: boolean
  api_key_masked: string
  api_key_source: 'env_file' | 'env' | 'none'
  /** 被 .env 覆盖的字段名 */
  overrides: string[]
  /** 被改写的 .env 绝对路径 */
  env_path: string
  defaults: { api_url: string; api_key: string; model: string }
}

export type AISettingsPatch = {
  api_url?: string
  /** 空串或缺省 = 保持原值；null = 从 .env 删除该行 */
  api_key?: string | null
  model?: string
  reset?: boolean
}

export type AITestResult = {
  ok: boolean
  message: string
  model?: string
  latency_ms?: number
}

function extractErrorMessage(error: any): string {
  if (!error) return '请求失败'
  if (typeof error === 'string') return error
  if (typeof error.detail === 'string') return error.detail
  if (Array.isArray(error.detail)) {
    return error.detail.map((d: any) => (d && d.msg) || JSON.stringify(d)).join('；')
  }
  return '请求失败'
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const token = getLocalToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = 'Bearer ' + token
  const res = await fetch(url, {
    ...options,
    headers: { ...headers, ...((options.headers as Record<string, string>) || {}) },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(extractErrorMessage(body) || 'HTTP ' + res.status)
  }
  return res.json() as Promise<T>
}

/** 读取当前生效配置（Key 已脱敏）。 */
export function getAISettings(): Promise<AISettingsView> {
  return request<AISettingsView>('/api/settings/ai')
}

/** 保存配置到服务端。 */
export function saveAISettings(patch: AISettingsPatch): Promise<AISettingsView> {
  return request<AISettingsView>('/api/settings/ai', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

/** 删除 .env 中的 AI 配置行，回到代码默认值。 */
export function resetAISettings(): Promise<AISettingsView> {
  return request<AISettingsView>('/api/settings/ai/reset', { method: 'POST' })
}

/** 用当前（或传入的）配置发一次最小请求，验证连通性。 */
export function testAISettings(
  payload: { api_url?: string; api_key?: string; model?: string } = {},
): Promise<AITestResult> {
  return request<AITestResult>('/api/settings/ai/test', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ────────────────────────────────────────────────────────────────
// 账号与服务器（云端接入设置，KD-main 独有）
// ────────────────────────────────────────────────────────────────

export type ServerSettingsView = {
  /** 空串 = 纯本地模式（反向代理整体不启用） */
  server_url: string
  server_url_source: 'env_file' | 'env' | 'default'
  defaults: { server_url: string }
  connect_timeout: number
  read_timeout: number
  local_account_user: string
  local_account_enabled: boolean
}

export type ServerTestResult = {
  ok: boolean
  status?: number
  latency_ms?: number
  remote?: string
  detail?: string
}

/** 读取云端服务器地址（.env 就地改写，不新增配置文件）。 */
export function getServerSettings(): Promise<ServerSettingsView> {
  return request<ServerSettingsView>('/api/settings/server')
}

/** 保存云端服务器地址；空串 = 删除该键回到默认。 */
export function saveServerSettings(patch: { server_url: string }): Promise<ServerSettingsView> {
  return request<ServerSettingsView>('/api/settings/server', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

/** 测试与云端服务器的连通性（后端已做超时兜底，不抛异常）。 */
export function testServerSettings(serverUrl?: string): Promise<ServerTestResult> {
  const payload = serverUrl && serverUrl.trim() ? { server_url: serverUrl.trim() } : {}
  return request<ServerTestResult>('/api/settings/server/test', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
