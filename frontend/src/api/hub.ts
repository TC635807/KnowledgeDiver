import { authFetchWithToken } from './auth'
import type { HubSessionSummary, HubSessionDetail, HubSortBy } from '../types/hub'

const HUB_BASE = '/api/hub'

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
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to search hub')
  return res.json()
}

export interface UserProfile {
  username: string
  total_sessions: number
  total_likes: number
  popular_sessions: HubSessionSummary[]
}

export async function getUserProfile(username: string): Promise<UserProfile> {
  const res = await fetch(`${HUB_BASE}/user/${encodeURIComponent(username)}/profile`)
  if (!res.ok) throw new Error('Failed to get user profile')
  return res.json()
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
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to get user sessions')
  return res.json()
}

export async function getHubSession(username: string, sessionName: string): Promise<HubSessionDetail> {
  const res = await fetch(`${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}`)
  if (!res.ok) throw new Error('Failed to get hub session')
  return res.json()
}

export async function shareToHub(
  sessionId: string,
  sessionName: string,
  description: string,
  topics: string[]
): Promise<void> {
  await authFetchWithToken(`${HUB_BASE}/share`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, session_name: sessionName, description, topics }),
  })
}

export async function unshareFromHub(username: string, sessionName: string): Promise<void> {
  await authFetchWithToken(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}`,
    { method: 'DELETE' }
  )
}

export async function importFromHub(
  username: string,
  sessionName: string
): Promise<{ session_id: string; session_name: string }> {
  return authFetchWithToken(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/import`,
    { method: 'POST' }
  )
}

export async function likeHubSession(username: string, sessionName: string): Promise<{ likes: number; dislikes: number }> {
  return authFetchWithToken(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/like`,
    { method: 'POST' }
  )
}

export async function dislikeHubSession(username: string, sessionName: string): Promise<{ likes: number; dislikes: number }> {
  return authFetchWithToken(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/dislike`,
    { method: 'POST' }
  )
}

export async function addHubComment(
  username: string,
  sessionName: string,
  content: string
): Promise<{ comments: unknown[] }> {
  return authFetchWithToken(
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
  return authFetchWithToken(
    `${HUB_BASE}/${encodeURIComponent(username)}/${encodeURIComponent(sessionName)}/comment/${index}`,
    { method: 'DELETE' }
  )
}
