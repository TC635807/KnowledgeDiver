/**
 * HubPage 游客态测试（05-实施契约 §3.4 / 04 §2.3）：
 * - 游客（无云端账号）**可以**导入到本地工作区（导入按钮不再 disabled）；
 * - 点赞/评论需要云端账号 → 点 👍 只提示登录，不发请求；
 * - 云端离线时显示离线提示，且本地工作区不受影响。
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import HubPage from '../components/HubPage'
import { getToasts, __resetToasts } from '../utils/toast'

const SUMMARY = {
  name: 'SharedDeck',
  description: 'desc',
  creator: 'alice',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  card_count: 3,
  topics: ['ai'],
  likes: 2,
  dislikes: 0,
  comment_count: 0,
}

const DETAIL = {
  manifest: {
    name: 'SharedDeck',
    description: 'desc',
    creator: 'alice',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    card_count: 3,
    topics: ['ai'],
    graph: { nodes: [], edges: [] },
    likes: 2,
    dislikes: 0,
    liked_by: [],
    disliked_by: [],
    comments: [],
  },
  cards: [],
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function mockHubFetch(): string[] {
  const calls: string[] = []
  ;(global as any).fetch = vi.fn(async (url: string) => {
    const u = String(url)
    calls.push(u)
    if (u.includes('/api/hub?')) return jsonResponse([SUMMARY])
    if (u === '/api/hub/alice/SharedDeck') return jsonResponse(DETAIL)
    return jsonResponse({})
  })
  return calls
}

async function openDetail() {
  const { container } = render(
    <HubPage
      onImport={importSpy as unknown as (u: string, s: string) => Promise<void>}
      user={null}
      orientation={'landscape' as never}
      onGoProfile={() => {}}
      serverStatus="online"
      onServerStatus={() => {}}
    />,
  )
  await screen.findByText('by')
  const item = container.querySelector('.hub-session-item')
  expect(item).toBeTruthy()
  fireEvent.click(item as Element)
  await screen.findByRole('button', { name: /导入到工作区/ })
  return container
}

let importSpy: ReturnType<typeof vi.fn>

describe('HubPage 游客态', () => {
  beforeEach(() => {
    __resetToasts()
    importSpy = vi.fn(async () => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('游客可以直接导入（按钮不再因未登录而禁用）', async () => {
    const calls = mockHubFetch()
    await openDetail()

    const btn = screen.getByRole('button', { name: /导入到工作区/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(false)

    fireEvent.click(btn)
    await waitFor(() => {
      expect(importSpy).toHaveBeenCalledWith('alice', 'SharedDeck')
    })
    // 导入打的是本地端点，不经过论坛写接口
    expect(calls.some((u) => u.includes('/import'))).toBe(false)
  })

  it('游客点赞只提示登录，不发请求', async () => {
    const calls = mockHubFetch()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: /👍/ }))

    expect(getToasts().some((t) => /登录/.test(t.text))).toBe(true)
    expect(calls.some((u) => u.includes('/like'))).toBe(false)
  })

  it('游客看不到评论输入框，但能看到导入入口', async () => {
    mockHubFetch()
    await openDetail()

    expect(screen.queryByPlaceholderText('添加评论...')).toBeNull()
    expect(screen.getByText(/导入到本地工作区无需登录/)).toBeTruthy()
  })

  it('云端离线时显示离线提示，但导入按钮仍可用', async () => {
    mockHubFetch()
    render(
      <HubPage
        onImport={importSpy as unknown as (u: string, s: string) => Promise<void>}
        user={null}
        orientation={'landscape' as never}
        onGoProfile={() => {}}
        serverStatus="offline"
        onServerStatus={() => {}}
      />,
    )

    expect(await screen.findByTestId('hub-offline')).toBeTruthy()
  })
})
