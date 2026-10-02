/**
 * AuthContext 双身份测试（05-实施契约 §3.2/§3.4）：
 * - 启动即换本地 token → localReady=true（P0 验收第 1 条：无登录动作进工作区）；
 * - bootstrap 失败 → localError 有可读文案（不许静默空工作区）；
 * - 存有 serverToken → 校验并填充云端账号，且不影响本地身份。
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AuthProvider, useAuth } from '../contexts/AuthContext'
import { LOCAL_TOKEN_KEY, LEGACY_TOKEN_KEY } from '../api/localAccount'
import { SERVER_TOKEN_KEY, setServerToken, setAuthExpiredHandler } from '../api/remote'
import { __resetToasts } from '../utils/toast'

const Probe: React.FC = () => {
  const { localReady, localError, serverStatus, user } = useAuth()
  return (
    <div>
      <span data-testid="local-ready">{String(localReady)}</span>
      <span data-testid="local-error">{localError ?? ''}</span>
      <span data-testid="server-status">{serverStatus}</span>
      <span data-testid="cloud-user">{user?.username ?? ''}</span>
    </div>
  )
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

describe('AuthContext 双身份', () => {
  beforeEach(() => {
    localStorage.clear()
    __resetToasts()
    setAuthExpiredHandler(null)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('启动即为内置本地账号换 token，localReady=true（无登录动作）', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url) === '/api/auth/local-session') {
        return jsonResponse({ access_token: 'local-jwt', token_type: 'bearer', username: 'local' })
      }
      return jsonResponse({}, 404)
    })
    ;(global as any).fetch = fetchMock

    render(<AuthProvider><Probe /></AuthProvider>)

    await waitFor(() => {
      expect(screen.getByTestId('local-ready').textContent).toBe('true')
    })
    expect(screen.getByTestId('local-error').textContent).toBe('')
    expect(localStorage.getItem(LOCAL_TOKEN_KEY)).toBe('local-jwt')
    // 没有云端账号时不校验 me
    expect(fetchMock.mock.calls.every((c) => String(c[0]) !== '/api/auth/me')).toBe(true)
  })

  it('bootstrap 失败时给出可读 localError（不再静默空工作区）', async () => {
    ;(global as any).fetch = vi.fn(async () => jsonResponse({ detail: '本地账号未启用', status_code: 403 }, 403))

    render(<AuthProvider><Probe /></AuthProvider>)

    await waitFor(() => {
      expect(screen.getByTestId('local-error').textContent).toContain('本地账号未启用')
    })
    expect(screen.getByTestId('local-ready').textContent).toBe('false')
  })

  it('启动时清理旧的共用键 knowledgeDiver.token', async () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'old-shared-token')
    ;(global as any).fetch = vi.fn(async (url: string) => {
      if (String(url) === '/api/auth/local-session') {
        return jsonResponse({ access_token: 'local-jwt', username: 'local' })
      }
      return jsonResponse({}, 404)
    })

    render(<AuthProvider><Probe /></AuthProvider>)

    await waitFor(() => {
      expect(screen.getByTestId('local-ready').textContent).toBe('true')
    })
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBeNull()
  })

  it('存有 serverToken 时拉取云端账号，且本地身份同时就绪', async () => {
    localStorage.setItem(LOCAL_TOKEN_KEY, 'local-jwt')
    setServerToken('server-jwt')
    ;(global as any).fetch = vi.fn(async (url: string) => {
      if (String(url) === '/api/auth/me') {
        return jsonResponse({ username: 'alice', created_at: '2026-01-01T00:00:00Z', avatar_url: null })
      }
      return jsonResponse({}, 404)
    })

    render(<AuthProvider><Probe /></AuthProvider>)

    await waitFor(() => {
      expect(screen.getByTestId('cloud-user').textContent).toBe('alice')
    })
    expect(screen.getByTestId('local-ready').textContent).toBe('true')
    expect(screen.getByTestId('server-status').textContent).toBe('online')
    expect(localStorage.getItem(SERVER_TOKEN_KEY)).toBe('server-jwt')
  })
})
