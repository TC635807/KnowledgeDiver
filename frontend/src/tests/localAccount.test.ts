/**
 * P0 本地身份 bootstrap 测试（05-实施契约 §3.1/§3.2）。
 *
 * 锁定：
 * - 没有 localToken → POST /api/auth/local-session 换取并落盘；
 * - 已有 localToken → 不重复请求；
 * - 失败**必须抛出可读错误**（不许静默空工作区）；
 * - 启动时清理旧的共用键 knowledgeDiver.token。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  LOCAL_TOKEN_KEY,
  LEGACY_TOKEN_KEY,
  clearLegacyToken,
  clearLocalToken,
  ensureLocalSession,
  getLocalToken,
  setLocalToken,
} from '../api/localAccount'

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

describe('localAccount bootstrap', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('已有 localToken 时不再请求 local-session', async () => {
    setLocalToken('cached-local-jwt')
    const fetchMock = vi.fn()
    ;(global as any).fetch = fetchMock

    await expect(ensureLocalSession()).resolves.toBe('cached-local-jwt')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(getLocalToken()).toBe('cached-local-jwt')
  })

  it('没有 localToken 时 POST /api/auth/local-session 并落盘', async () => {
    const fetchMock = vi.fn(async (_url: string, _options?: RequestInit) =>
      jsonResponse({ access_token: 'fresh-local-jwt', token_type: 'bearer', username: 'local' }),
    )
    ;(global as any).fetch = fetchMock

    await expect(ensureLocalSession()).resolves.toBe('fresh-local-jwt')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auth/local-session')
    expect((options as RequestInit).method).toBe('POST')
    // bootstrap 请求本身不带任何 token
    expect((options as RequestInit).headers).toBeUndefined()
    expect(localStorage.getItem(LOCAL_TOKEN_KEY)).toBe('fresh-local-jwt')
  })

  it('LOCAL_ACCOUNT=0（403）时抛出后端 detail', async () => {
    ;(global as any).fetch = vi.fn(async () => jsonResponse({ detail: '本地账号未启用', status_code: 403 }, 403))

    await expect(ensureLocalSession()).rejects.toThrow(/本地账号未启用/)
  })

  it('后端不可达时抛出可读错误（不再静默）', async () => {
    ;(global as any).fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })

    await expect(ensureLocalSession()).rejects.toThrow(/无法连接本地服务/)
  })

  it('清理旧的共用键 knowledgeDiver.token', () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'server-jwt-from-old-version')
    clearLegacyToken()
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBeNull()
  })

  it('clearLocalToken 只删本地键', () => {
    setLocalToken('local-jwt')
    localStorage.setItem(LEGACY_TOKEN_KEY, 'legacy')
    clearLocalToken()
    expect(localStorage.getItem(LOCAL_TOKEN_KEY)).toBeNull()
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBe('legacy')
  })
})
