import React, { useEffect, useState, useCallback } from 'react'
import { getUserProfile, getUserSessions, unshareFromHub, type UserProfile } from '../api/hub'
import type { HubSessionSummary } from '../types/hub'
import type { AuthUser } from '../types/auth'
import './ProfilePage.css'

type Props = {
  username: string
  currentUser: AuthUser | null
  onBack: () => void
  onImport: (creator: string, sessionName: string) => void
  onViewSession: (creator: string, sessionName: string) => void
}

const ProfilePage: React.FC<Props> = ({ username, currentUser, onBack, onImport, onViewSession }) => {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [sessions, setSessions] = useState<HubSessionSummary[]>([])
  const [tab, setTab] = useState<'popular' | 'all'>('popular')
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState<string | null>(null)

  const isOwn = currentUser?.username === username

  useEffect(() => {
    setLoading(true)
    getUserProfile(username)
      .then(setProfile)
      .finally(() => setLoading(false))
  }, [username])

  const loadSessions = useCallback(() => {
    getUserSessions(username, { sort_by: tab === 'popular' ? 'most_likes' : 'newest', page_size: 50 })
      .then(setSessions)
  }, [username, tab])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  const handleDelete = async (sessionName: string) => {
    if (!confirm(`确定要删除 "${sessionName}" 吗？此操作不可撤销。`)) return
    setDeleting(sessionName)
    try {
      await unshareFromHub(username, sessionName)
      setSessions(prev => prev.filter(s => s.name !== sessionName))
      setProfile(prev => prev ? { ...prev, total_sessions: prev.total_sessions - 1 } : null)
    } catch (err) {
      console.error('Failed to delete session:', err)
    } finally {
      setDeleting(null)
    }
  }

  if (loading) {
    return (
      <div className="profile-loading">
        <div className="skeleton" style={{ width: 80, height: 80, borderRadius: '50%' }} />
        <div className="skeleton" style={{ width: 120, height: 20, marginTop: 12 }} />
      </div>
    )
  }

  return (
    <div className="profile-page">
      <div className="profile-header">
        <button className="profile-back-btn" onClick={onBack}>← 返回 Hub</button>
      </div>

      <div className="profile-body">
        {/* ── Left sidebar ── */}
        <aside className="profile-sidebar">
          <div className="profile-avatar">
            <img
              src={`/api/auth/avatar/${encodeURIComponent(username)}`}
              alt={username}
              onError={e => {
                (e.target as HTMLImageElement).style.display = 'none'
                const fallback = (e.target as HTMLImageElement).nextElementSibling
                if (fallback) (fallback as HTMLElement).style.display = 'flex'
              }}
            />
            <div className="profile-avatar-fallback" style={{ display: 'none' }}>
              {username.charAt(0).toUpperCase()}
            </div>
          </div>
          <h1 className="profile-username">{username}</h1>

          <div className="profile-stats">
            <div className="profile-stat">
              <span className="profile-stat-value">{profile?.total_sessions ?? 0}</span>
              <span className="profile-stat-label">Hub 会话</span>
            </div>
            <div className="profile-stat">
              <span className="profile-stat-value">{profile?.total_likes ?? 0}</span>
              <span className="profile-stat-label">获赞</span>
            </div>
          </div>

          {isOwn && (
            <p className="profile-edit-hint">你可以在下方管理自己的 Hub 会话</p>
          )}
        </aside>

        {/* ── Right content ── */}
        <main className="profile-content">
          <nav className="profile-tabs">
            <button
              className={`profile-tab ${tab === 'popular' ? 'profile-tab--active' : ''}`}
              onClick={() => setTab('popular')}
            >
              热门
            </button>
            <button
              className={`profile-tab ${tab === 'all' ? 'profile-tab--active' : ''}`}
              onClick={() => setTab('all')}
            >
              全部会话
            </button>
          </nav>

          {sessions.length === 0 ? (
            <div className="profile-empty">
              <p>暂无 Hub 会话</p>
            </div>
          ) : (
            <div className="profile-session-list">
              {sessions.map(s => (
                <div
                  key={`${s.creator}/${s.name}`}
                  className="profile-session-item"
                  onClick={() => onViewSession(s.creator, s.name)}
                >
                  <div className="profile-session-main">
                    <h3 className="profile-session-name">{s.name}</h3>
                    <p className="profile-session-desc">{s.description || '暂无描述'}</p>
                    <div className="profile-session-meta">
                      <span className="profile-session-topic">
                        {s.topics?.length > 0 ? s.topics.slice(0, 4).map(t => (
                          <span key={t} className="topic-tag">{t}</span>
                        )) : <span className="topic-tag topic-tag--empty">无标签</span>}
                      </span>
                      <span className="profile-session-info">
                        📄 {s.card_count} 卡片 · 👍 {s.likes} · 💬 {s.comment_count} · {new Date(s.updated_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  <div className="profile-session-actions">
                    <button
                      className="profile-action-btn"
                      onClick={e => { e.stopPropagation(); onImport(s.creator, s.name) }}
                    >
                      导入
                    </button>
                    {isOwn && (
                      <button
                        className="profile-action-btn profile-action-btn--danger"
                        onClick={e => { e.stopPropagation(); handleDelete(s.name) }}
                        disabled={deleting === s.name}
                      >
                        {deleting === s.name ? '删除中...' : '删除'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

export default ProfilePage
