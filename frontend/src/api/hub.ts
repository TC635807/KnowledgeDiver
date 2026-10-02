import { authFetchWithToken } from './auth'
import { RemoteError, RemoteUnavailableError, remoteFetch } from './remote'
import type { HubSessionSummary, HubSessionDetail, HubSortBy } from '../types/hub'

const HUB_BASE = '/api/hub'

/**
 * 论坛**浏览**类调用：公开接口（服务端本来就不需要鉴权），经本地反向代理转发。
 * 这里只做错误分类：把 502/503/504/网络错误 统一成 RemoteUnavailableError，
 * 让 HubPage 能显示"离线，连不上服务器"而不是静默失败（契约 §3.4）。
 */
async function browseFetch<T>(url: string, fallback: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new RemoteUnavailableError()
  }
  if (!res.ok) {
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new RemoteUnavailableError()
    }
    const body = await res.json().catch(() => null)
    const detail = body && ((body as { detail?: unknown }).detail ?? (body as { error?: unknown }).error)
    throw new RemoteError(typeof detail === 'string' && detail ? detail : fallback, res.status)
  }
  return (await res.json()) as T
}

export async function searchHub(params: {
  query?: string
  sort_by?: HubSortBy
  page?: number
  page_size?: number
  creator?: string
}): Promise<HubSessionSummary[]> {
  const sp = new URLSearchParams()
  if (params.query) sp.set('query', params.query)
  if (params.sort_by) sp.set('sort_by', params.sort_by)
  if (params.page) sp.set('page', String(params.page))
  if (params.page_size) sp.set('page_size', String(params.page_size))
  if (params.creator) sp.set('creator', params.creator)
  const url = `${HUB_BASE}?${sp.toString()}`
  return browseFetch<HubSessionSummary[]>(url, '无法加载论坛列表')
}

export interface UserProfile {
  username: string
  total_sessions: number
  total_likes: number
  popular_sessions: HubSessionSummary[]
}

export async function getUserProfile(username: string): Promise<UserProfile> {
  return browseFetch<UserProfile>(
    `${HUB_BASE}/user/${encodeURIComponent(username)}/profile`,
    '无法加载用户主页',
  )
}

export async function getUserSessions(
  username: string,
  params: { sort_by?: HubSortBy; page?: number; page_size?: number } = {}
): Promise<HubSessionSummary[]> {
  const sp = new URLSearchParams()
  if (params.sort_by) sp.set('sort_by', params.sort_by)
  if (params.page) sp.set('page', String(params.page))
  if (params.page_size) sp.set('page_size', String(params.page_size))
  const url = `${HUB_BASE}/user/${encodeURIComponent(username)}/sessions?${sp.toString()}`
  return browseFetch<HubSessionSummary[]>(url, '无法加载用户分享')
}

export async function getHubSession(username: string, sessionName: string): Promise<HubSessionDetail> {
  return browseFetch<HubSessionDetail>(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}`,
    '无法加载分享详情',
  )
}

// ────────────────────────────────────────────────────────────────
// 互动类：需要云端账号 → 一律走 remoteFetch（携带 serverToken）
// ────────────────────────────────────────────────────────────────

export async function shareToHub(
  sessionId: string,
  sessionName: string,
  description: string,
  topics: string[]
): Promise<void> {
  await remoteFetch(`${HUB_BASE}/share`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, session_name: sessionName, description, topics }),
  })
}

export async function unshareFromHub(username: string, sessionName: string): Promise<void> {
  await remoteFetch(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}`,
    { method: 'DELETE' }
  )
}

export interface HubImportResult {
  session_id: string
  session_name?: string
}

/**
 * 从 Hub 导入到**本地**工作区。
 *
 * 注意：绝不能调用 `POST /api/hub/{u}/{s}/import` —— 那是把内容复制进**云端账号**的
 * 工作区，本地目录下什么都不会发生（见 00 文档 §4.3 实测结论）。
 * 正确链路：本地端点 `POST /api/local/hub-import`，identity = local，游客也能用。
 */
export async function importFromHub(
  username: string,
  sessionName: string
): Promise<HubImportResult> {
  try {
    return await authFetchWithToken<HubImportResult>('/api/local/hub-import', {
      method: 'POST',
      body: JSON.stringify({ creator: username, session_name: sessionName }),
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (/404|405|not found|method not allowed/i.test(msg)) {
      throw new Error('本地导入接口尚未启用（POST /api/local/hub-import），请更新客户端后端后重试。')
    }
    throw err
  }
}

export async function likeHubSession(username: string, sessionName: string): Promise<{ likes: number; dislikes: number }> {
  return remoteFetch(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/like`,
    { method: 'POST' }
  )
}

export async function dislikeHubSession(username: string, sessionName: string): Promise<{ likes: number; dislikes: number }> {
  return remoteFetch(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/dislike`,
    { method: 'POST' }
  )
}

export async function addHubComment(
  username: string,
  sessionName: string,
  content: string
): Promise<{ comments: unknown[] }> {
  return remoteFetch(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/comment`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    }
  )
}

export async function deleteHubComment(
  username: string,
  sessionName: string,
  index: number
): Promise<{ comments: unknown[] }> {
  return remoteFetch(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/comment/${index}`,
    { method: 'DELETE' }
  )
}
