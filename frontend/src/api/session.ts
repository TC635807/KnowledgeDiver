import type { Session, SessionCreate, SessionUpdate } from '../types/session'
import type { Card } from '../types/card'
import { authFetchWithToken, getToken } from './auth'

export async function listSessions(): Promise<Session[]> {
  const sessions = await authFetchWithToken<Session[]>('/api/sessions')
  return sessions.map(s => ({
    ...s,
    created_at: new Date(s.created_at),
    updated_at: new Date(s.updated_at)
  }))
}

export async function createSession(name: string): Promise<Session> {
  const sessionData: SessionCreate = { name }
  const session = await authFetchWithToken<Session>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify(sessionData)
  })
  return {
    ...session,
    created_at: new Date(session.created_at),
    updated_at: new Date(session.updated_at)
  }
}

export async function getSession(sessionId: string): Promise<Session> {
  const session = await authFetchWithToken<Session>(`/api/sessions/${sessionId}`)
  return {
    ...session,
    created_at: new Date(session.created_at),
    updated_at: new Date(session.updated_at)
  }
}

export async function updateSession(sessionId: string, name: string): Promise<Session> {
  const sessionData: SessionUpdate = { name }
  const session = await authFetchWithToken<Session>(`/api/sessions/${sessionId}`, {
    method: 'PUT',
    body: JSON.stringify(sessionData)
  })
  return {
    ...session,
    created_at: new Date(session.created_at),
    updated_at: new Date(session.updated_at)
  }
}

export async function deleteSession(sessionId: string): Promise<void> {
  await authFetchWithToken(`/api/sessions/${sessionId}`, {
    method: 'DELETE'
  })
}

export async function moveCardsToSession(
  sourceSessionId: string,
  targetSessionId: string,
  cardIds: string[]
): Promise<{ status: string; moved_count: number }> {
  return authFetchWithToken(`/api/sessions/${sourceSessionId}/move-cards`, {
    method: 'POST',
    body: JSON.stringify({
      target_session_id: targetSessionId,
      card_ids: cardIds
    })
  })
}

export async function downloadSession(sessionId: string, sessionName: string): Promise<void> {
  const token = getToken()
  if (!token) throw new Error('未认证')

  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/download`, {
    headers: { 'Authorization': `Bearer ${token}` },
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '下载失败' }))
    throw new Error(error.detail || '下载失败')
  }
  const blob = await response.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${sessionName}.zip`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}

export async function uploadSession(file: File): Promise<{ status: string; session: Session; card_count: number }> {
  const token = getToken()
  if (!token) throw new Error('未认证')

  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch('/api/sessions/upload', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '上传失败' }))
    throw new Error(error.detail || '上传失败')
  }
  return response.json()
}

export async function shareSession(sessionId: string): Promise<{ token: string; share_url: string }> {
  return authFetchWithToken(`/api/sessions/${encodeURIComponent(sessionId)}/share`, {
    method: 'POST',
  })
}

export async function revokeShare(sessionId: string): Promise<void> {
  await authFetchWithToken(`/api/sessions/${encodeURIComponent(sessionId)}/share`, {
    method: 'DELETE',
  })
}

export async function getSharedSession(token: string): Promise<{ session: Session; cards: Card[] }> {
  const response = await fetch(`/api/share/${encodeURIComponent(token)}`)
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '无法访问共享会话' }))
    throw new Error(error.detail || '无法访问共享会话')
  }
  return response.json()
}