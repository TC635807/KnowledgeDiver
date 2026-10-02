import React, { useState, useEffect, useCallback } from 'react'
import GraphView from './GraphView'
import { searchHub, getHubSession, likeHubSession, dislikeHubSession, addHubComment, deleteHubComment } from '../api/hub'
import { isRemoteOffline } from '../api/remote'
import { showToast } from '../utils/toast'
import type { HubSessionSummary, HubSessionDetail, HubSortBy, HubComment } from '../types/hub'
import type { Card } from '../types/card'
import type { Orientation } from '../hooks/useDeviceOrientation'
import './HubPage.css'

type HubMobileTab = 'browse' | 'detail' | 'graph'

type Props = {
  onImport: (username: string, sessionName: string) => Promise<void>
  user: { username: string } | null
  orientation: Orientation
  onGoProfile: (username: string) => void
  /** 云端可达性；offline 时论坛顶部显示离线提示（本地工作区不受影响） */
  serverStatus?: 'checking' | 'online' | 'offline'
  /** 论坛请求失败/成功时回报可达性，用于 UserMenu 与离线提示 */
  onServerStatus?: (status: 'online' | 'offline') => void
}

const mobileNavItems: { id: HubMobileTab; label: string; icon: string }[] = [
  { id: 'browse', label: '浏览', icon: '🔍' },
  { id: 'detail', label: '详情', icon: '📄' },
  { id: 'graph', label: '图谱', icon: '📊' },
]

const HubPage: React.FC<Props> = ({ onImport, user, orientation, onGoProfile, serverStatus, onServerStatus }) => {
  const [sessions, setSessions] = useState<HubSessionSummary[]>([])
  const [selectedUsername, setSelectedUsername] = useState<string | null>(null)
  const [selectedSessionName, setSelectedSessionName] = useState<string | null>(null)
  const [detail, setDetail] = useState<HubSessionDetail | null>(null)
  const [query, setQuery] = useState('')
  const [sortBy, setSortBy] = useState<HubSortBy>('newest')
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null)
  const [commentText, setCommentText] = useState('')
  const [commentSubmitting, setCommentSubmitting] = useState(false)
  const [mobileTab, setMobileTab] = useState<HubMobileTab>('browse')
  const [remoteOffline, setRemoteOffline] = useState(false)

  const offline = remoteOffline || serverStatus === 'offline'

  const isPortrait = orientation === 'portrait'

  // Auto-open session from URL param (?open=username/sessionName)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const open = params.get('open')
    if (open) {
      const slashIdx = open.indexOf('/')
      if (slashIdx > 0) {
        const u = open.substring(0, slashIdx)
        const s = open.substring(slashIdx + 1)
        handleSelectSession(u, s)
        // Clean URL after opening
        const newUrl = window.location.pathname
        window.history.replaceState(null, '', newUrl)
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const loadList = useCallback(async (q?: string, sort?: HubSortBy) => {
    setLoading(true)
    try {
      const result = await searchHub({ query: q ?? query, sort_by: sort ?? sortBy, page_size: 50 })
      setSessions(result)
      setRemoteOffline(false)
      if (onServerStatus) onServerStatus('online')
    } catch (err) {
      console.warn('Hub search failed:', err)
      if (isRemoteOffline(err)) {
        setRemoteOffline(true)
        if (onServerStatus) onServerStatus('offline')
        showToast('离线，连不上服务器；本地工作区不受影响。', 'info')
      } else {
        showToast('论坛加载失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
      }
    } finally {
      setLoading(false)
    }
  }, [query, sortBy, onServerStatus])

  useEffect(() => {
    loadList()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = () => {
    loadList()
  }

  const handleSortChange = (s: HubSortBy) => {
    setSortBy(s)
    loadList(query, s)
  }

  const handleSelectSession = async (username: string, sessionName: string) => {
    setSelectedUsername(username)
    setSelectedSessionName(sessionName)
    setSelectedCardId(null)
    setDetailLoading(true)
    try {
      const d = await getHubSession(username, sessionName)
      setDetail(d)
      if (isPortrait) {
        setMobileTab('detail')
      }
    } catch (err) {
      console.warn('Hub session detail failed:', err)
      setDetail(null)
      if (isRemoteOffline(err)) {
        setRemoteOffline(true)
        if (onServerStatus) onServerStatus('offline')
      }
    } finally {
      setDetailLoading(false)
    }
  }

  const requireLogin = (): boolean => {
    if (user) return false
    showToast('登录 KnowledgeDiver 账号后才能点赞 / 评论 / 分享（浏览与导入无需登录）', 'info')
    return true
  }

  const handleLike = async () => {
    if (!selectedUsername || !selectedSessionName || !detail) return
    if (requireLogin()) return
    try {
      const r = await likeHubSession(selectedUsername, selectedSessionName)
      setDetail({ ...detail, manifest: { ...detail.manifest, likes: r.likes, dislikes: r.dislikes } })
    } catch (err) {
      console.warn('Hub like failed:', err)
      showToast('点赞失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
    }
  }

  const handleDislike = async () => {
    if (!selectedUsername || !selectedSessionName || !detail) return
    if (requireLogin()) return
    try {
      const r = await dislikeHubSession(selectedUsername, selectedSessionName)
      setDetail({ ...detail, manifest: { ...detail.manifest, likes: r.likes, dislikes: r.dislikes } })
    } catch (err) {
      console.warn('Hub dislike failed:', err)
      showToast('操作失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
    }
  }

  const handleSubmitComment = async () => {
    if (!commentText.trim() || !selectedUsername || !selectedSessionName || !detail) return
    if (requireLogin()) return
    setCommentSubmitting(true)
    try {
      const r = await addHubComment(selectedUsername, selectedSessionName, commentText.trim())
      const comments = r.comments as HubComment[]
      setDetail({ ...detail, manifest: { ...detail.manifest, comments } })
      setCommentText('')
    } catch (err) {
      console.warn('Hub comment submit failed:', err)
      showToast('评论失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
    }
    setCommentSubmitting(false)
  }

  const handleDeleteComment = async (index: number) => {
    if (!selectedUsername || !selectedSessionName || !detail) return
    try {
      const r = await deleteHubComment(selectedUsername, selectedSessionName, index)
      const comments = r.comments as HubComment[]
      setDetail({ ...detail, manifest: { ...detail.manifest, comments } })
    } catch (err) {
      console.warn('Hub comment delete failed:', err)
      showToast('删除评论失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
    }
  }

  const handleImport = async () => {
    if (!selectedUsername || !selectedSessionName) return
    setImporting(true)
    try {
      await onImport(selectedUsername, selectedSessionName)
    } finally {
      setImporting(false)
    }
  }

  const graphCards = (detail?.cards || []) as Card[]

  // ── Shared sub-components ──

  const sessionListContent = (
    <>
      <div className="hub-search">
        <input
          type="text"
          className="hub-search__input"
          placeholder="搜索标题、描述、主题..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
        />
        <button className="hub-search__btn" onClick={handleSearch}>搜索</button>
      </div>
      {offline && (
        <div
          data-testid="hub-offline"
          style={{
            margin: '8px 0',
            padding: '8px 10px',
            borderRadius: 8,
            background: 'rgba(248,113,113,0.12)',
            border: '1px solid rgba(248,113,113,0.3)',
            color: '#fca5a5',
            fontSize: 12,
            lineHeight: 1.5,
          }}
        >
          离线，连不上服务器（论坛不可用）；本地工作区不受影响。
        </div>
      )}
      <div className="hub-sort">
        <button
          className={`hub-sort__btn${sortBy === 'newest' ? ' hub-sort__btn--active' : ''}`}
          onClick={() => handleSortChange('newest')}
        >最新</button>
        <button
          className={`hub-sort__btn${sortBy === 'most_likes' ? ' hub-sort__btn--active' : ''}`}
          onClick={() => handleSortChange('most_likes')}
        >最多赞</button>
        <button
          className={`hub-sort__btn${sortBy === 'trending' ? ' hub-sort__btn--active' : ''}`}
          onClick={() => handleSortChange('trending')}
        >热门</button>
      </div>
      <div className="hub-session-list">
        {loading && <div className="hub-loading">加载中...</div>}
        {!loading && sessions.length === 0 && (
          <div className="hub-empty">暂无分享的 session</div>
        )}
        {sessions.map(s => (
          <div
            key={`${s.creator}/${s.name}`}
            className={`hub-session-item${selectedUsername === s.creator && selectedSessionName === s.name ? ' hub-session-item--active' : ''}`}
            onClick={() => handleSelectSession(s.creator, s.name)}
          >
            <div className="hub-session-item__name">{s.name}</div>
            <div className="hub-session-item__creator">
              by{' '}
              <span
                className="hub-creator-link"
                onClick={e => { e.stopPropagation(); onGoProfile(s.creator) }}
              >
                {s.creator}
              </span>
            </div>
            <div className="hub-session-item__meta">
              <span>{s.card_count} 卡片</span>
              <span>👍 {s.likes}</span>
              <span>💬 {s.comment_count}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  )

  const sessionDetailContent = (
    <>
      {offline && (
        <div data-testid="hub-detail-offline" style={{ marginBottom: 10, color: '#fca5a5', fontSize: 12 }}>
          离线，连不上服务器。
        </div>
      )}
      {!selectedUsername && !detailLoading && (
        <div className="hub-placeholder">← 从左侧选择一个 session 查看详情</div>
      )}
      {detailLoading && <div className="hub-loading">加载详情...</div>}
      {detail && (
        <div className="hub-detail">
          <h2 className="hub-detail__title">{detail.manifest.name}</h2>
          <p className="hub-detail__creator">
            作者：{' '}
            <span className="hub-creator-link" onClick={() => onGoProfile(detail.manifest.creator)}>
              {detail.manifest.creator}
            </span>
            {' '}· {detail.manifest.card_count} 张卡片
          </p>
          {detail.manifest.description && (
            <p className="hub-detail__desc">{detail.manifest.description}</p>
          )}
          {detail.manifest.topics.length > 0 && (
            <div className="hub-detail__topics">
              {detail.manifest.topics.map(t => (
                <span key={t} className="hub-tag">{t}</span>
              ))}
            </div>
          )}

          {graphCards.length > 0 && (
            <div className="hub-detail__cards">
              <h4>卡片列表</h4>
              <div className="hub-card-list">
                {graphCards.map(c => (
                  <div
                    key={c.id}
                    className={`hub-card-item${selectedCardId === c.id ? ' hub-card-item--active' : ''}`}
                    onClick={() => setSelectedCardId(c.id === selectedCardId ? null : c.id)}
                  >
                    {c.title}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="hub-actions">
            <button className="hub-btn hub-btn-like" onClick={handleLike}>
              👍 {detail.manifest.likes}
            </button>
            <button className="hub-btn hub-btn-dislike" onClick={handleDislike}>
              👎 {detail.manifest.dislikes}
            </button>
            <button
              className="hub-btn hub-btn-import"
              onClick={handleImport}
              disabled={importing}
            >
              {importing ? '导入中...' : '📥 导入到工作区'}
            </button>
          </div>

          <div className="hub-comments">
            <h4>评论 ({detail.manifest.comments.length})</h4>
            <div className="hub-comments__list">
              {detail.manifest.comments.map((c, i) => (
                <div key={i} className="hub-comment">
                  <span className="hub-comment__user">{c.username}</span>
                  <span className="hub-comment__time">{new Date(c.created_at).toLocaleDateString()}</span>
                  {user && user.username === c.username && (
                    <button className="hub-comment__delete" onClick={() => handleDeleteComment(i)}>删除</button>
                  )}
                  <p className="hub-comment__content">{c.content}</p>
                </div>
              ))}
            </div>
            {user && (
              <div className="hub-comment-form">
                <textarea
                  className="hub-comment-form__input"
                  placeholder="添加评论..."
                  value={commentText}
                  onChange={e => setCommentText(e.target.value)}
                  rows={2}
                />
                <button
                  className="hub-btn hub-btn-comment"
                  onClick={handleSubmitComment}
                  disabled={commentSubmitting || !commentText.trim()}
                >
                  {commentSubmitting ? '发送中...' : '发送'}
                </button>
              </div>
            )}
            {!user && (
              <p className="hub-login-hint">登录后可以点赞和评论（导入到本地工作区无需登录）</p>
            )}
          </div>
        </div>
      )}
    </>
  )

  const graphContent = (visible: boolean) => (
    <div className="hub-graph-container">
      <GraphView
        cards={graphCards}
        selectedId={selectedCardId}
        onSelectCard={id => setSelectedCardId(id === selectedCardId ? null : id)}
        visible={visible}
      />
    </div>
  )

  // ── Portrait / Mobile layout ──
  if (isPortrait) {
    return (
      <div className="hub-mobile">
        <div className="mobile-page">
          {mobileTab === 'browse' && (
            <div className="mobile-page__content">
              {sessionListContent}
            </div>
          )}
          {mobileTab === 'detail' && (
            <div className="mobile-page__content" style={{ overflow: 'auto' }}>
              {sessionDetailContent}
            </div>
          )}
          {/* Keep GraphView always mounted to avoid vis-network re-initialization */}
          <div className="mobile-page__content" style={{ padding: 0, display: mobileTab === 'graph' ? 'flex' : 'none' }}>
            {graphContent(mobileTab === 'graph')}
          </div>
        </div>
        <nav className="bottom-nav">
          {mobileNavItems.map((item) => (
            <button
              key={item.id}
              className={`bottom-nav__item ${mobileTab === item.id ? 'bottom-nav__item--active' : ''}`}
              onClick={() => setMobileTab(item.id)}
            >
              <span className="bottom-nav__icon">{item.icon}</span>
              <span className="bottom-nav__label">{item.label}</span>
            </button>
          ))}
        </nav>
      </div>
    )
  }

  // ── Desktop layout ──
  return (
    <div className="hub-layout">
      <aside className="hub-sidebar hub-sidebar--left">
        <div className="hub-sidebar__header">知识 Hub</div>
        {sessionListContent}
      </aside>

      <main className="hub-center">
        {sessionDetailContent}
      </main>

      <aside className="hub-sidebar hub-sidebar--right">
        {graphContent(true)}
      </aside>
    </div>
  )
}

export default HubPage
