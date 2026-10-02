/**
 * token 分派测试（05-实施契约 §2.1 / §3.3）：
 * - 本地调用（authFetchWithToken）必须带 **localToken**；
 * - 云端调用（remoteFetch）必须带 **serverToken**；
 * - 两者绝不串用（这是"登录云端后本地 53 个端点整体 401"的根因）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { authFetchWithToken } from '../api/auth'
import {
  RemoteAuthError,
  RemoteRateLimitError,
  RemoteUnavailableError,
  SERVER_TOKEN_KEY,
  clearServerToken,
  getServerToken,
  isRemoteOffline,
  remoteFetch,
  setAuthExpiredHandler,
  setServerToken,
} from '../api/remote'
import { LOCAL_TOKEN_KEY, setLocalToken } from '../api/localAccount'

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function headerOf(options: RequestInit | undefined, name: string): string | null {
  const headers = options?.headers
  if (!headers) return null
  if (typeof (headers as Headers).get === 'function') return (headers as Headers).get(name)
  return (headers as Record<string, string>)[name] ?? null
}

describe('token 分派', () => {
  beforeEach(() => {
    localStorage.clear()
    setAuthExpiredHandler(null)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('authFetchWithToken 带 localToken，且绝不带 serverToken', async () => {
    setLocalToken('LOCAL-JWT')
    setServerToken('SERVER-JWT')
    const fetchMock = vi.fn(async (_url: string, _options?: RequestInit) => jsonResponse([]))
    ;(global as any).fetch = fetchMock

    await authFetchWithToken('/api/cards?session_id=default')

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/cards?session_id=default')
    expect(headerOf(options as RequestInit, 'Authorization')).toBe('Bearer LOCAL-JWT')
    expect(headerOf(options as RequestInit, 'Authorization')).not.toContain('SERVER-JWT')
  })

  it('remoteFetch 带 serverToken，本地键不参与', async () => {
    setLocalToken('LOCAL-JWT')
    setServerToken('SERVER-JWT')
    const fetchMock = vi.fn(async (_url: string, _options?: RequestInit) => jsonResponse({ status: 'ok' }))
    ;(global as any).fetch = fetchMock

    await remoteFetch('/api/hub/share', { method: 'POST', body: JSON.stringify({ a: 1 }) })

    const [, options] = fetchMock.mock.calls[0]
    expect(headerOf(options as RequestInit, 'Authorization')).toBe('Bearer SERVER-JWT')
    expect(headerOf(options as RequestInit, 'Content-Type')).toBe('application/json')
    expect(localStorage.getItem(SERVER_TOKEN_KEY)).toBe('SERVER-JWT')
  })

  it('本地身份未就绪时 authFetchWithToken 抛出可读错误', async () => {
    // 不设置 localToken
    await expect(authFetchWithToken('/api/cards')).rejects.toThrow(/本地身份未就绪/)
  })

  it('云端 401 → 清 serverToken + 触发 authExpired 回调', async () => {
    setServerToken('EXPIRED')
    const expired = vi.fn()
    setAuthExpiredHandler(expired)
    ;(global as any).fetch = vi.fn(async () => jsonResponse({ detail: '认证凭据无效' }, 401))

    await expect(remoteFetch('/api/hub')).rejects.toBeInstanceOf(RemoteAuthError)
    expect(getServerToken()).toBeNull()
    expect(localStorage.getItem(LOCAL_TOKEN_KEY)).toBeNull() // 本地键不受影响
    expect(expired).toHaveBeenCalledTimes(1)
  })

  it('云端 429 → 固定文案（响应体是 {error:...} 也认）', async () => {
    setServerToken('OK')
    ;(global as any).fetch = vi.fn(async () => jsonResponse({ error: 'too many requests' }, 429))

    const err = await remoteFetch('/api/hub').catch((e) => e)
    expect(err).toBeInstanceOf(RemoteRateLimitError)
    expect((err as Error).message).toBe('操作过于频繁，请稍后再试')
  })

  it('云端 502 → RemoteUnavailableError（论坛离线态）', async () => {
    setServerToken('OK')
    ;(global as any).fetch = vi.fn(async () => jsonResponse({ detail: '远程服务器不可达' }, 502))

    const err = await remoteFetch('/api/hub').catch((e) => e)
    expect(err).toBeInstanceOf(RemoteUnavailableError)
    expect(isRemoteOffline(err)).toBe(true)
  })

  it('网络错误 → RemoteUnavailableError（论坛离线态）', async () => {
    setServerToken('OK')
    ;(global as any).fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })

    const err = await remoteFetch('/api/hub').catch((e) => e)
    expect(isRemoteOffline(err)).toBe(true)
  })
})
