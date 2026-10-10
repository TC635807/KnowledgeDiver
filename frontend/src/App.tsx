import React, { useState, useCallback, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { Navigate, Routes, Route } from 'react-router-dom'
import TreeBrowser from './components/TreeBrowser'
import GraphView from './components/GraphView'
import { BottomNavBar } from './components/BottomNavBar'
import AgentDrawer from './components/AgentDrawer'
import AgentPanel from './components/AgentPanel'
import GapAnalysis from './components/GapAnalysis'
import RawContentSection from './components/RawContentSection'
import FloatingCardWindow, { FloatingWindowState } from './components/FloatingCardWindow'
import ProfilePage from './components/ProfilePage'
import SearchLevelPopup from './components/SearchLevelPopup'
import { useDeviceOrientation } from './hooks/useDeviceOrientation'
import { useRouteState } from './hooks/useRouteState'
import {
  MobileSessionsPage,
  MobileCardsPage,
  MobileContentPage,
  MobileCollectorPage,
  MobileGraphPage
} from './components/mobile'
import { StreamingOutput } from './components/StreamingOutput'
import HubPage from './components/HubPage'
import UserMenu from './components/UserMenu'
import { TutorialPopover } from './components/TutorialPopover'
import ToastHost from './components/ToastHost'
import { showToast } from './utils/toast'
import { importFromHub as importFromHubApi } from './api/hub'
import { AuthPage } from './components/AuthPage'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { authFetchWithToken } from './api/auth'
import { getSharedSession, createSession } from './api/session'
import { useCardManager } from './hooks/useCardManager'
import { useSessionManager } from './hooks/useSessionManager'
import { useTaskManager } from './hooks/useTaskManager'
import { useLayoutState } from './hooks/useLayoutState'
import type { TaskType } from './types/pipeline'
import './components/BottomNavBar.css'

// ────────────────────────────── 本地身份启动态 ──────────────────────────────

/**
 * P0：本地身份就绪前 / 失败时的界面。
 * 失败必须显式呈现（不许再出现"静默空工作区"，见 05-实施契约 §3.2）。
 */
const LocalBootstrapScreen: React.FC<{ error: string | null; onRetry: () => void }> = ({ error, onRetry }) => (
  <div style={{
    height: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
    gap: 12, padding: 24, textAlign: 'center',
    background: 'var(--bg, #030712)', color: 'var(--text-secondary, #9ca3af)',
  }}>
    {error ? (
      <>
        <div style={{ fontSize: 32 }}>⚠️</div>
        <div style={{ fontSize: 16, color: 'var(--text, #e5e7eb)' }}>本地工作区启动失败</div>
        <div data-testid="local-bootstrap-error" style={{ maxWidth: 520, lineHeight: 1.6 }}>{error}</div>
        <button onClick={onRetry} style={{
          marginTop: 4, padding: '8px 20px', borderRadius: 8, border: '1px solid rgba(139,92,246,0.5)',
          background: 'rgba(139,92,246,0.15)', color: 'var(--accent, #a78bfa)', cursor: 'pointer', fontSize: 13,
        }}>重试</button>
        <div style={{ fontSize: 12, opacity: 0.7, maxWidth: 520, lineHeight: 1.6 }}>
          请确认客户端后端已启动（默认 http://127.0.0.1:8000），且未通过 LOCAL_ACCOUNT=0 关闭内置本地账号。
        </div>
      </>
    ) : (
      <>
        <div style={{ fontSize: 28 }}>🧠</div>
        <div>正在准备本地工作区…</div>
      </>
    )}
  </div>
)

// ────────────────────────────── AppContent ──────────────────────────────

const AppContent: React.FC = () => {
  const {
    user, localReady, localError, localUsername, serverStatus,
    logout, refreshUser, retryLocalSession, notifyServerStatus,
  } = useAuth()
  const orientation = useDeviceOrientation()
  const {
    workspaceMode, forumPage, profileUsername,
    mobilePage, routeCardId,
    navigate, goWorkspace, goForum, goProfile, goMobilePage
  } = useRouteState()
  const isPortrait = orientation === 'portrait'

  const layout = useLayoutState()
  const sessionMgr = useSessionManager(
    (sessionId?: string) => cardMgr.loadCards(sessionId)
  )
  const cardMgr = useCardManager(
    sessionMgr.selectedSessionId,
    isPortrait,
    goMobilePage
  )
  const taskMgr = useTaskManager(
    sessionMgr.selectedSessionId,
    cardMgr.cards,
    cardMgr.loadCards,
    refreshUser,
    layout.collecterExpanded,
    layout.setCollecterExpanded
  )

  const effectiveCardId = isPortrait && routeCardId ? routeCardId : cardMgr.selectedCardId
  const selectedCard = cardMgr.cards.find((c) => c.id === effectiveCardId) || null

  // ── shared state (not extracted into hooks) ──
  const [shareView, setShareView] = useState(false)
  const [shareViewLoading, setShareViewLoading] = useState(false)
  const [importingFromShare, setImportingFromShare] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [agentInitialMessage, setAgentInitialMessage] = useState<string | undefined>(undefined)
  const [gapAnalysisOpen, setGapAnalysisOpen] = useState(false)

  // ── floating card windows (multi-open) ──
  const [openWindows, setOpenWindows] = useState<FloatingWindowState[]>([])
  const windowSeqRef = React.useRef(0)
  const zSeqRef = React.useRef(10)

  const handleOpenCardWindow = useCallback(
    (cardId: string, startEditing = false) => {
      setOpenWindows((prev) => {
        // If a window for this card already exists and is not minimized, focus it
        const existing = prev.find((w) => w.cardId === cardId && !w.minimized)
        if (existing) {
          zSeqRef.current += 1
          return prev.map((w) => (w.id === existing.id ? { ...w, zIndex: zSeqRef.current, initialEditing: startEditing || w.initialEditing } : w))
        }
        const winId = `cw-${windowSeqRef.current++}`
        zSeqRef.current += 1
        const count = prev.length
        const baseX = 80 + (count % 5) * 40
        const baseY = 60 + (count % 5) * 40
        const win: FloatingWindowState = {
          id: winId,
          cardId,
          x: baseX,
          y: baseY,
          width: 440,
          height: 440,
          minimized: false,
          zIndex: zSeqRef.current,
          initialEditing: startEditing,
        }
        return [...prev, win]
      })
    },
    []
  )

  const handleCloseWindow = useCallback((windowId: string) => {
    setOpenWindows((prev) => prev.filter((w) => w.id !== windowId))
  }, [])

  const handleFocusWindow = useCallback((windowId: string) => {
    setOpenWindows((prev) => {
      zSeqRef.current += 1
      return prev.map((w) => (w.id === windowId ? { ...w, zIndex: zSeqRef.current } : w))
    })
  }, [])

  const handleMoveWindow = useCallback((windowId: string, x: number, y: number) => {
    setOpenWindows((prev) => prev.map((w) => (w.id === windowId ? { ...w, x, y } : w)))
  }, [])

  const handleResizeWindow = useCallback((windowId: string, width: number, height: number) => {
    setOpenWindows((prev) => prev.map((w) => (w.id === windowId ? { ...w, width, height } : w)))
  }, [])

  const handleMinimizeWindow = useCallback((windowId: string) => {
    setOpenWindows((prev) => prev.map((w) => (w.id === windowId ? { ...w, minimized: !w.minimized } : w)))
  }, [])

  const handleSaveCardFromWindow = useCallback(
    async (_windowId: string, cardId: string, title: string, content: string, links: string[]) => {
      try {
        await authFetchWithToken(
          `/api/cards/${cardId}?session_id=${encodeURIComponent(sessionMgr.selectedSessionId)}`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, content, links }),
          }
        )
        cardMgr.loadCards()
      } catch (err) {
        console.error('Failed to update card from window:', err)
        alert('保存失败，请重试')
        throw err
      }
    },
    [sessionMgr.selectedSessionId, cardMgr.loadCards]
  )

  // Close windows whose card no longer exists
  useEffect(() => {
    const cardIds = new Set(cardMgr.cards.map((c) => c.id))
    setOpenWindows((prev) => {
      const filtered = prev.filter((w) => cardIds.has(w.cardId))
      return filtered.length === prev.length ? prev : filtered
    })
  }, [cardMgr.cards])

  // ── session validation ──
  useEffect(() => {
    if (sessionMgr.sessions.length > 0) {
      const sessionExists = sessionMgr.sessions.some((s) => s.id === sessionMgr.selectedSessionId)
      if (!sessionExists) {
        sessionMgr.setSelectedSessionId('default')
        cardMgr.loadCards('default')
      }
    }
  }, [sessionMgr.sessions, sessionMgr.selectedSessionId])

  // ── panel ready timing ──
  useEffect(() => {
    // On initial mount, mark expanded panels as ready so content renders immediately.
    // Subsequent toggle open/close is handled by toggleSessions/toggleCards with
    // a 350ms animation delay before setting ready=true.
    if (layout.sessionsExpanded) layout.setSessionsReady(true)
    if (layout.cardsExpanded) layout.setCardsReady(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── identity init ──
  // P0：本地身份就绪即加载数据；云端是否登录与本地工作区无关
  useEffect(() => {
    if (localReady) {
      sessionMgr.loadSessions()
      cardMgr.loadCards()
      taskMgr.restoreRunningTasks()
    }
  }, [localReady])

  // ── shared session URL detection ──
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('share')
    if (token) {
      setShareView(true)
      setShareViewLoading(true)
      getSharedSession(token)
        .then((data) => {
          cardMgr.handleSelectNone() // clear card state
          // shared session is read-only handled in JSX
        })
        .catch((err) => {
          console.error('[Share] Failed to load shared session:', err)
          alert('无法加载共享会话：' + (err instanceof Error ? err.message : ''))
        })
        .finally(() => setShareViewLoading(false))
    }
  }, [])

  // ── handlers that cross hook boundaries ──
  const handleImportFromHub = useCallback(
    async (creatorUsername: string, sessionName: string) => {
      try {
        const result = await importFromHubApi(creatorUsername, sessionName)
        await sessionMgr.loadSessions()
        if (result.session_id) sessionMgr.setSelectedSessionId(result.session_id)
        goWorkspace()
        goMobilePage('cards')
        showToast('已导入到本地工作区', 'ok')
      } catch (err) {
        console.error('Failed to import from hub:', err)
        showToast('导入失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
      }
    },
    [sessionMgr.loadSessions]
  )

  const handleGoProfile = useCallback(() => {
    if (user?.username) goProfile(user.username)
  }, [user?.username, goProfile])

  const handleViewHubSession = useCallback(
    (creator: string, sessionName: string) => {
      navigate(`/forum?open=${encodeURIComponent(creator)}/${encodeURIComponent(sessionName)}`)
    },
    [navigate]
  )

  const importFromShareUrl = useCallback(
    async (inputUrl: string) => {
      let token = inputUrl.trim()
      try {
        const url = new URL(token)
        token = url.searchParams.get('share') || token
      } catch (err) { console.warn('Share URL parse failed:', err) }
      setImportingFromShare(true)
      try {
        const data = await getSharedSession(token)
        const newSession = await createSession(data.session.name || '导入的会话')
        for (const card of data.cards) {
          await authFetchWithToken(
            `/api/cards?session_id=${encodeURIComponent(newSession.id)}`,
            {
              method: 'POST',
              body: JSON.stringify({
                title: card.title,
                content: card.content,
                metadata: card.metadata || {},
                sources: card.sources || [],
                tags: card.tags || [],
              }),
            }
          )
        }
        alert(`会话「${newSession.name}」导入成功，包含 ${data.cards.length} 张卡片`)
        sessionMgr.loadSessions()
      } catch (err) {
        console.error('Failed to import from share:', err)
        alert('导入失败：' + (err instanceof Error ? err.message : '未知错误'))
      } finally {
        setImportingFromShare(false)
      }
    },
    [sessionMgr.loadSessions]
  )

  const handleImportFromSharePrompt = useCallback(() => {
    const url = prompt('请输入分享链接：')
    if (url?.trim()) importFromShareUrl(url.trim())
  }, [importFromShareUrl])

  // ── identity states ──
  // P0：本地内置账号就绪 = 直接进工作区，**没有登录硬闸门**（05-实施契约 §3.4）
  if (!localReady) {
    return <LocalBootstrapScreen error={localError} onRetry={() => { void retryLocalSession() }} />
  }

  // 云端登录/注册：AuthPage 降级为可选页面，登录的是服务器账号（论坛用）
  if (location.pathname === '/login') {
    return (
      <AuthPage
        onDone={() => navigate('/workspace/cards')}
        onBack={() => navigate('/workspace/cards')}
      />
    )
  }

  if (location.pathname === '/' && !location.search.includes('share=')) {
    return <Navigate to="/workspace/cards" replace />
  }

  // ── shared session read-only view ──
  if (shareView) {
    if (shareViewLoading) {
      return (
        <div style={{
          height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'var(--bg)', color: 'var(--text-secondary)'
        }}>加载共享会话...</div>
      )
    }
    return (
      <div className="app-shell">
        <header className="app-header" style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)' }}>
          <h1 className="app-title">KnowledgeDiver</h1>
          <div className="workspace-toggle">
            <button className={`workspace-toggle__btn${workspaceMode === 'workspace' ? ' workspace-toggle__btn--active' : ''}`} onClick={goWorkspace}>工作区</button>
            <button className={`workspace-toggle__btn${workspaceMode === 'forum' ? ' workspace-toggle__btn--active' : ''}`} onClick={goForum}>论坛</button>
          </div>
          <span style={{ color: 'var(--text-secondary)', fontSize: 13, marginLeft: 12 }}>
            📋 共享会话：{sessionMgr.sessions[0]?.name || '未知'}（只读）
          </span>
        </header>
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          <aside style={{ width: 280, borderRight: '1px solid var(--border)', background: 'var(--glass-bg)', backdropFilter: 'blur(12px)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: 10, borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)', fontSize: 12 }}>
              卡片 ({cardMgr.cards.length})
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}>
              <TreeBrowser cards={cardMgr.cards} selectedId={cardMgr.selectedCardId} onSelectCard={cardMgr.handleSelectCard} sessionId={sessionMgr.selectedSessionId} />
            </div>
          </aside>
          <main style={{ flex: 1, padding: 20, overflow: 'auto', background: 'var(--bg)' }}>
            {selectedCard && (
              <>
                <h2 style={{ margin: '0 0 16px', color: 'var(--text-h)' }}>{selectedCard.title}</h2>
                <div style={{ color: 'var(--text)', lineHeight: 1.7 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{
                    h1: ({ children }) => <h1 style={{ color: 'var(--text-h)', borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>{children}</h1>,
                    h2: ({ children }) => <h2 style={{ color: 'var(--text-h)', marginTop: 20 }}>{children}</h2>,
                    h3: ({ children }) => <h3 style={{ color: 'var(--text-h)' }}>{children}</h3>,
                    strong: ({ children }) => <strong style={{ color: 'var(--accent)' }}>{children}</strong>,
                    a: ({ href, children }) => <a href={href} style={{ color: 'var(--accent)' }} target="_blank" rel="noopener noreferrer">{children}</a>,
                    ul: ({ children }) => <ul style={{ marginLeft: 20 }}>{children}</ul>,
                    ol: ({ children }) => <ol style={{ marginLeft: 20 }}>{children}</ol>,
                    code: ({ className, children }) => {
                      const isInline = !className
                      return isInline ? <code style={{ background: 'var(--bg-secondary)', padding: '2px 6px', borderRadius: 4 }}>{children}</code>
                        : <pre style={{ background: 'var(--bg-secondary)', padding: 12, borderRadius: 8, overflow: 'auto' }}><code>{children}</code></pre>
                    },
                  }}>
                    {selectedCard.content}
                  </ReactMarkdown>
                </div>
                <RawContentSection cardId={selectedCard.id} sessionId={sessionMgr.selectedSessionId} />
              </>
            )}
            {!selectedCard && cardMgr.cards.length > 0 && (
              <div style={{ color: 'var(--text-secondary)', textAlign: 'center', marginTop: 40 }}>选择左侧卡片查看内容（只读）</div>
            )}
          </main>
        </div>
      </div>
    )
  }

  // ── mobile layout ──
  if (orientation === 'portrait') {
    return (
      <div className="app-shell portrait-shell">
        <header className="app-header portrait-header">
          <h1 className="app-title">KnowledgeDiver</h1>
          <div className="workspace-toggle">
            <button className={`workspace-toggle__btn${workspaceMode === 'workspace' ? ' workspace-toggle__btn--active' : ''}`} onClick={goWorkspace}>工作区</button>
            <button className={`workspace-toggle__btn${workspaceMode === 'forum' ? ' workspace-toggle__btn--active' : ''}`} onClick={goForum}>论坛</button>
          </div>
          <div className="spacer" />
          <UserMenu
            username={user?.username || localUsername}
            avatarUrl={user?.avatar_url}
            serverUsername={user?.username ?? null}
            serverStatus={serverStatus}
            onLogout={logout}
            onGoProfile={handleGoProfile}
            onLogin={() => navigate('/login')}
          />
        </header>

        <Routes>
          <Route path="/forum/profile/:username" element={
            profileUsername ? (
              <ProfilePage username={profileUsername} currentUser={user ?? null} onBack={goForum} onImport={handleImportFromHub} onViewSession={handleViewHubSession} onServerStatus={notifyServerStatus} />
            ) : (
              <Navigate to="/forum" replace />
            )
          } />
          <Route path="/forum" element={
            <HubPage onImport={handleImportFromHub} user={user ?? null} orientation={orientation} onGoProfile={goProfile} serverStatus={serverStatus} onServerStatus={notifyServerStatus} />
          } />
          <Route path="/workspace/*" element={
            <>
              {mobilePage === 'sessions' && (
                <MobileSessionsPage
                  sessions={sessionMgr.sessions} selectedSessionId={sessionMgr.selectedSessionId}
                  sessionManagementMode={sessionMgr.sessionManagementMode} selectedSessionIds={sessionMgr.selectedSessionIds}
                  newSessionName={sessionMgr.newSessionName} creatingSession={sessionMgr.creatingSession}
                  sessionDeleting={sessionMgr.sessionDeleting} editingSessionId={sessionMgr.editingSessionId}
                  editingSessionName={sessionMgr.editingSessionName}
                  onSelectSession={sessionMgr.handleSelectSession} onToggleSessionSelect={sessionMgr.handleToggleSessionSelect}
                  onEnterManagement={sessionMgr.handleEnterSessionManagement} onExitManagement={sessionMgr.handleExitSessionManagement}
                  onSelectAll={sessionMgr.handleSessionSelectAll} onSelectNone={sessionMgr.handleSessionSelectNone}
                  onDeleteSelected={sessionMgr.handleDeleteSelectedSessions} onCreateSession={sessionMgr.handleCreateSession}
                  onUpdateSession={sessionMgr.handleUpdateSession} onStartEditSession={sessionMgr.handleStartEditSession}
                  onCancelEditSession={sessionMgr.handleCancelEditSession} onNewSessionNameChange={sessionMgr.setNewSessionName}
                  onEditingSessionNameChange={sessionMgr.setEditingSessionName}
                  uploadingFile={sessionMgr.uploadingFile} downloadingId={sessionMgr.downloadingId}
                  onUploadSession={sessionMgr.handleUploadFromFile} onDownloadSession={sessionMgr.handleDownloadSession}
                  onShareSession={sessionMgr.handleShareSession} onShareToHub={sessionMgr.handleShareToHub}
                  onImportFromShare={handleImportFromSharePrompt}
                />
              )}
              <div style={{ display: mobilePage === 'cards' ? 'flex' : 'none', flex: 1, flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
                <MobileCardsPage cards={cardMgr.cards} selectedCardId={cardMgr.selectedCardId}
                  managementMode={cardMgr.managementMode} selectedCardIds={cardMgr.selectedCardIds}
                  onSelectCard={cardMgr.handleSelectCard} onExpandSearch={taskMgr.handleExpandSearch}
                  onCreateEmptyCard={cardMgr.handleCreateEmptyCard} onCreateOrphanCard={cardMgr.handleCreateOrphanCard}
                  onEditCard={cardMgr.handleStartEditCard} onToggleSelect={cardMgr.handleToggleSelect}
                  onEnterManagement={cardMgr.handleEnterManagement} onExitManagement={cardMgr.handleExitManagement}
                  onSelectAll={cardMgr.handleSelectAll} onSelectNone={cardMgr.handleSelectNone}
                  onDeleteSelected={cardMgr.handleDeleteSelected} deleting={cardMgr.deleting}
                  sessionName={sessionMgr.sessions.find((s) => s.id === sessionMgr.selectedSessionId)?.name || '卡片'}
                />
                <button
                  onClick={() => setGapAnalysisOpen(true)}
                  style={{
                    position: 'absolute', bottom: 16, right: 68, zIndex: 50,
                    width: 44, height: 44, borderRadius: '50%',
                    border: '1px solid rgba(16, 185, 129, 0.35)',
                    background: 'rgba(10, 10, 26, 0.85)',
                    backdropFilter: 'blur(8px)', color: '#10b981',
                    cursor: 'pointer', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
                  }}
                >
                  📊
                </button>
                <button
                  onClick={() => { setAgentOpen(!agentOpen); setAgentInitialMessage(undefined); }}
                  style={{
                    position: 'absolute', bottom: 16, right: 16, zIndex: 50,
                    width: 44, height: 44, borderRadius: '50%',
                    border: '1px solid rgba(139, 92, 246, 0.35)',
                    background: agentOpen ? 'rgba(139, 92, 246, 0.2)' : 'rgba(10, 10, 26, 0.85)',
                    backdropFilter: 'blur(8px)', color: 'var(--accent, #a78bfa)',
                    cursor: 'pointer', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
                  }}
                >
                  💬
                </button>
              </div>
              <div style={{ display: mobilePage === 'content' ? 'flex' : 'none', flex: 1, flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
                <MobileContentPage selectedCard={selectedCard} editingCardId={cardMgr.editingCardId}
                  editingTitle={cardMgr.editingTitle} editingContent={cardMgr.editingContent} editingLinks={cardMgr.editingLinks}
                  allCards={cardMgr.cards} sessionId={sessionMgr.selectedSessionId} onEditTitleChange={cardMgr.handleEditTitleChange}
                  onEditContentChange={cardMgr.handleEditContentChange}
                  onLinksChange={cardMgr.handleEditLinksChange} onSaveEdit={cardMgr.handleSaveEdit} onCancelEdit={cardMgr.handleCancelEdit}
                  savingCard={cardMgr.savingCard}
                />
                <button
                  onClick={() => setGapAnalysisOpen(true)}
                  style={{
                    position: 'absolute', bottom: 16, right: 68, zIndex: 50,
                    width: 44, height: 44, borderRadius: '50%',
                    border: '1px solid rgba(16, 185, 129, 0.35)',
                    background: 'rgba(10, 10, 26, 0.85)',
                    backdropFilter: 'blur(8px)', color: '#10b981',
                    cursor: 'pointer', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
                  }}
                >
                  📊
                </button>
                <button
                  onClick={() => { setAgentOpen(!agentOpen); setAgentInitialMessage(undefined); }}
                  style={{
                    position: 'absolute', bottom: 16, right: 16, zIndex: 50,
                    width: 44, height: 44, borderRadius: '50%',
                    border: '1px solid rgba(139, 92, 246, 0.35)',
                    background: agentOpen ? 'rgba(139, 92, 246, 0.2)' : 'rgba(10, 10, 26, 0.85)',
                    backdropFilter: 'blur(8px)', color: 'var(--accent, #a78bfa)',
                    cursor: 'pointer', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
                  }}
                >
                  💬
                </button>
                {gapAnalysisOpen && (
                  <GapAnalysis
                    sessionId={sessionMgr.selectedSessionId}
                    onClose={() => setGapAnalysisOpen(false)}
                    onOpenAgent={(prompt) => {
                      setGapAnalysisOpen(false);
                      setAgentOpen(true);
                      setAgentInitialMessage(prompt);
                    }}
                  />
                )}
                {agentOpen && (
                  <AgentDrawer sessionId={sessionMgr.selectedSessionId} onClose={() => setAgentOpen(false)} initialMessage={agentInitialMessage} onTaskCreated={(taskId, keyword, taskType) => taskMgr.attachBackendTask(taskId, keyword, taskType as TaskType)} />
                )}
              </div>
              <div style={{ display: mobilePage === 'collector' ? 'flex' : 'none', flex: 1, flexDirection: 'column', overflow: 'hidden' }}>
                <MobileCollectorPage
                  searchTasks={new Map(Array.from(taskMgr.searchTasks).filter(([_, t]) => t.sessionId === sessionMgr.selectedSessionId || !t.sessionId))}
                  keyword={taskMgr.keyword} onKeywordChange={taskMgr.setKeyword}
                  onStartCollection={taskMgr.handleStartCollection} onCollectionComplete={taskMgr.handleCollectionComplete}
                  onCollectionError={taskMgr.handleCollectionError} onCancelSearch={taskMgr.handleCancelSearch}
                  onRemoveSearch={taskMgr.handleRemoveSearch} onTaskIdReceived={taskMgr.handleTaskIdReceived}
                  onDocumentUpload={taskMgr.handleDocumentUpload}
                />
              </div>
              <div style={{ display: mobilePage === 'graph' ? 'flex' : 'none', flex: 1, flexDirection: 'column', overflow: 'hidden' }}>
                <MobileGraphPage cards={cardMgr.cards} selectedCardId={cardMgr.selectedCardId} onSelectCard={cardMgr.handleSelectCard} visible={mobilePage === 'graph'} />
              </div>
              <BottomNavBar selectedCardId={effectiveCardId} />
            </>
          } />
        </Routes>
        <TutorialPopover />
      </div>
    )
  }

  // ── desktop layout ──
  return (
    <div className="app-shell app-main-shell">
      <header className="app-header app-main-header" style={{ background: 'var(--glass-bg)', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)' }}>
        <h1 className="app-title">KnowledgeDiver</h1>
        <div className="workspace-toggle">
          <button className={`workspace-toggle__btn${workspaceMode === 'workspace' ? ' workspace-toggle__btn--active' : ''}`} onClick={goWorkspace}>工作区</button>
          <button className={`workspace-toggle__btn${workspaceMode === 'forum' ? ' workspace-toggle__btn--active' : ''}`} onClick={goForum}>论坛</button>
        </div>
        <div style={{ flex: 1 }} />
        <UserMenu
          username={user?.username || localUsername}
          avatarUrl={user?.avatar_url}
          serverUsername={user?.username ?? null}
          serverStatus={serverStatus}
          onLogout={logout}
          onGoProfile={handleGoProfile}
          onLogin={() => navigate('/login')}
        />
      </header>

      <Routes>
        <Route path="/forum/profile/:username" element={
          profileUsername ? (
            <ProfilePage username={profileUsername} currentUser={user ?? null} onBack={goForum} onImport={handleImportFromHub} onViewSession={handleViewHubSession} onServerStatus={notifyServerStatus} />
          ) : (
            <Navigate to="/forum" replace />
          )
        } />
        <Route path="/forum" element={
          <HubPage onImport={handleImportFromHub} user={user ?? null} orientation={orientation} onGoProfile={goProfile} serverStatus={serverStatus} onServerStatus={notifyServerStatus} />
        } />
        <Route path="/workspace/*" element={
          <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          {/* Sessions sidebar */}
          <aside style={{ width: layout.sessionsExpanded ? 240 : 40, borderRight: '1px solid var(--border, #374151)', display: 'flex', flexDirection: 'column', background: 'var(--glass-bg, rgba(15,15,35,0.65))', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', transition: 'width 0.3s cubic-bezier(0.4,0,0.2,1)', position: 'relative' }}>
            <button onClick={layout.toggleSessions} style={{ position: 'absolute', right: -16, top: '50%', transform: 'translateY(-50%)', width: 32, height: 32, borderRadius: '50%', border: '1px solid var(--border, #374151)', background: 'var(--bg-secondary, #111827)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }} title={layout.sessionsExpanded ? '收起' : '展开'}>
              {layout.sessionsExpanded ? '‹' : '›'}
            </button>
            {layout.sessionsExpanded && layout.sessionsReady && (
              <>
                <div style={{ padding: 10, borderBottom: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', fontSize: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>会话 ({sessionMgr.sessions.length})</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {sessionMgr.sessionManagementMode ? (
                      <>
                        <button onClick={sessionMgr.handleSessionSelectAll} style={{ background: 'transparent', border: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>全选</button>
                        <button onClick={sessionMgr.handleSessionSelectNone} style={{ background: 'transparent', border: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>取消</button>
                        <button onClick={sessionMgr.handleExitSessionManagement} style={{ background: 'transparent', border: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>完成</button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => sessionMgr.loadSessions()} style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 6px', borderRadius: 4, fontSize: 12 }} title="刷新会话列表">↻</button>
                        <div style={{ position: 'relative' }}>
                          <button ref={sessionMgr.uploadBtnRef} onClick={() => sessionMgr.setShowUploadMenu(!sessionMgr.showUploadMenu)} disabled={sessionMgr.uploadingFile} style={{ background: sessionMgr.showUploadMenu ? 'rgba(16,185,129,0.2)' : 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', color: '#34d399', cursor: sessionMgr.uploadingFile ? 'not-allowed' : 'pointer', padding: '2px 8px', borderRadius: 5, fontSize: 11, fontWeight: 500 }} title="导入会话">
                            {sessionMgr.uploadingFile ? '…' : '↑ 导入'}
                          </button>
                          {sessionMgr.showUploadMenu && (() => {
                            const rect = sessionMgr.uploadBtnRef.current?.getBoundingClientRect()
                            const top = rect ? rect.bottom + 6 : 0
                            const left = rect ? rect.right - 200 : 0
                            return (
                              <>
                                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 9998 }} onClick={() => sessionMgr.setShowUploadMenu(false)} />
                                <div style={{ position: 'fixed', top, left, zIndex: 9999, background: 'var(--bg-elevated)', borderRadius: 8, border: '1px solid var(--border)', boxShadow: 'var(--shadow-lg)', padding: 6, width: 200 }}>
                                  <button onClick={sessionMgr.handleUploadFromFile} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', border: 'none', borderRadius: 6, background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 12, textAlign: 'left' as const }}
                                    onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(16,185,129,0.1)' }}
                                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                                    📁 从文件导入
                                  </button>
                                  <button onClick={() => { sessionMgr.setShowUploadMenu(false); handleImportFromSharePrompt() }} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', border: 'none', borderRadius: 6, background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 12, textAlign: 'left' as const }}
                                    onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(139,92,246,0.1)' }}
                                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                                    🔗 从链接导入
                                  </button>
                                </div>
                              </>
                            )
                          })()}
                        </div>
                        <button onClick={sessionMgr.handleEnterSessionManagement} style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 6px', borderRadius: 4, fontSize: 12 }} title="管理会话">⚙</button>
                      </>
                    )}
                  </div>
                </div>
                {sessionMgr.sessionManagementMode && sessionMgr.selectedSessionIds.size > 0 && (
                  <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border, #374151)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg, #030712)' }}>
                    <span style={{ color: 'var(--text-secondary, #9ca3af)', fontSize: 12 }}>已选 {sessionMgr.selectedSessionIds.size} 项</span>
                    <button onClick={sessionMgr.handleDeleteSelectedSessions} disabled={sessionMgr.sessionDeleting} style={{ background: sessionMgr.sessionDeleting ? 'var(--border, #374151)' : '#dc2626', border: 'none', color: '#fff', cursor: sessionMgr.sessionDeleting ? 'not-allowed' : 'pointer', padding: '4px 12px', borderRadius: 4, fontSize: 12 }}>
                      {sessionMgr.sessionDeleting ? '删除中...' : '删除选中'}
                    </button>
                  </div>
                )}
                <div style={{ padding: 10, borderBottom: '1px solid var(--border, #374151)' }}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <input type="text" value={sessionMgr.newSessionName} onChange={(e) => sessionMgr.setNewSessionName(e.target.value)} placeholder="新会话名称" style={{ flex: 1, padding: '6px 8px', borderRadius: 4, border: '1px solid var(--border, #374151)', background: 'var(--bg, #030712)', color: 'var(--text, #e5e7eb)', fontSize: 12 }}
                      onKeyDown={(e) => e.key === 'Enter' && sessionMgr.handleCreateSession()} />
                    <button onClick={sessionMgr.handleCreateSession} disabled={!sessionMgr.newSessionName.trim() || sessionMgr.creatingSession} style={{ padding: '6px 12px', borderRadius: 4, border: 'none', background: sessionMgr.newSessionName.trim() && !sessionMgr.creatingSession ? 'var(--accent, #7c3aed)' : 'var(--border, #374151)', color: '#fff', cursor: sessionMgr.newSessionName.trim() && !sessionMgr.creatingSession ? 'pointer' : 'not-allowed', fontSize: 12 }}>
                      {sessionMgr.creatingSession ? '创建中...' : '+'}
                    </button>
                  </div>
                </div>
                <div style={{ flex: 1, overflow: 'auto' }}>
                  {sessionMgr.sessions.length === 0 && (
                    <div style={{ textAlign: 'center', padding: '30px 20px', color: 'var(--text-secondary, #9ca3af)' }}>
                      <div style={{ fontSize: 36, marginBottom: 12 }}>📁</div>
                      <div style={{ fontSize: 12, marginBottom: 4 }}>暂无会话</div>
                      <div style={{ fontSize: 11 }}>在上方输入名称创建新会话</div>
                    </div>
                  )}
                  {sessionMgr.sessions.map((session) => (
                    <div key={session.id} style={{ padding: '8px 10px', borderBottom: '1px solid var(--border, #374151)', background: session.id === sessionMgr.selectedSessionId ? 'var(--bg, #030712)' : 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}
                      onClick={() => { if (sessionMgr.sessionManagementMode) { sessionMgr.handleToggleSessionSelect(session.id) } else { sessionMgr.handleSelectSession(session.id) } }}>
                      {sessionMgr.sessionManagementMode && session.id !== 'default' && <input type="checkbox" checked={sessionMgr.selectedSessionIds.has(session.id)} onChange={() => sessionMgr.handleToggleSessionSelect(session.id)} style={{ margin: 0 }} />}
                      {sessionMgr.sessionManagementMode && session.id === 'default' && <span style={{ opacity: 0.3, fontSize: 12 }}>🔒</span>}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        {sessionMgr.editingSessionId === session.id ? (
                          <div style={{ display: 'flex', gap: 4 }}>
                            <input type="text" value={sessionMgr.editingSessionName} onChange={(e) => sessionMgr.setEditingSessionName(e.target.value)} style={{ flex: 1, padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border, #374151)', background: 'var(--bg, #030712)', color: 'var(--text, #e5e7eb)', fontSize: 12 }}
                              onKeyDown={(e) => { if (e.key === 'Enter') sessionMgr.handleUpdateSession(session.id, sessionMgr.editingSessionName); else if (e.key === 'Escape') sessionMgr.handleCancelEditSession() }} />
                            <button onClick={() => sessionMgr.handleUpdateSession(session.id, sessionMgr.editingSessionName)} style={{ padding: '4px 8px', borderRadius: 4, border: 'none', background: 'var(--accent, #7c3aed)', color: '#fff', cursor: 'pointer', fontSize: 11 }}>保存</button>
                            <button onClick={sessionMgr.handleCancelEditSession} style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border, #374151)', background: 'transparent', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', fontSize: 11 }}>取消</button>
                          </div>
                        ) : (
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <div style={{ flex: 1, overflow: 'hidden' }}>
                              <div style={{ fontSize: 13, color: session.id === sessionMgr.selectedSessionId ? 'var(--accent, #a78bfa)' : 'var(--text, #e5e7eb)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{session.name}</div>
                              <div style={{ fontSize: 11, color: 'var(--text-secondary, #9ca3af)', marginTop: 2 }}>{session.card_count} 张卡片</div>
                            </div>
                            {!sessionMgr.sessionManagementMode && (
                              <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
                                <button onClick={(e) => { e.stopPropagation(); sessionMgr.handleShareSession(session.id) }} style={{ width: 26, height: 26, border: '1px solid rgba(139,92,246,0.3)', background: 'rgba(139,92,246,0.08)', color: 'var(--accent-light, #a78bfa)', cursor: 'pointer', borderRadius: 6, fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="分享会话">↗</button>
                                <button onClick={(e) => { e.stopPropagation(); sessionMgr.handleDownloadSession(session.id, session.name) }} disabled={sessionMgr.downloadingId === session.id} style={{ width: 26, height: 26, border: '1px solid rgba(16,185,129,0.3)', background: sessionMgr.downloadingId === session.id ? 'rgba(16,185,129,0.05)' : 'rgba(16,185,129,0.08)', color: '#34d399', cursor: sessionMgr.downloadingId === session.id ? 'not-allowed' : 'pointer', borderRadius: 6, fontSize: 13, opacity: sessionMgr.downloadingId === session.id ? 0.5 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="下载会话">{sessionMgr.downloadingId === session.id ? '…' : '↓'}</button>
                                <button onClick={(e) => { e.stopPropagation(); sessionMgr.handleShareToHub(session.id) }} style={{ width: 26, height: 26, border: '1px solid rgba(245,158,11,0.3)', background: 'rgba(245,158,11,0.08)', color: '#fbbf24', cursor: 'pointer', borderRadius: 6, fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="分享到 Hub">🚀</button>
                                <button onClick={(e) => { e.stopPropagation(); sessionMgr.handleStartEditSession(session.id, session.name) }} style={{ width: 26, height: 26, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', borderRadius: 6, fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="重命名">✎</button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </aside>

          {/* Cards sidebar */}
          <aside style={{ width: layout.cardsExpanded ? 280 : 40, borderRight: '1px solid var(--border, #374151)', display: 'flex', flexDirection: 'column', background: 'var(--glass-bg, rgba(15,15,35,0.65))', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', transition: 'width 0.3s cubic-bezier(0.4,0,0.2,1)', position: 'relative' }}>
            <button onClick={layout.toggleCards} style={{ position: 'absolute', right: -16, top: '50%', transform: 'translateY(-50%)', width: 32, height: 32, borderRadius: '50%', border: '1px solid var(--border, #374151)', background: 'var(--bg-secondary, #111827)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }} title={layout.cardsExpanded ? '收起' : '展开'}>
              {layout.cardsExpanded ? '‹' : '›'}
            </button>
            {layout.cardsExpanded && (
              <>
                <div style={{ padding: 10, borderBottom: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', fontSize: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>{sessionMgr.sessions.find((s) => s.id === sessionMgr.selectedSessionId)?.name || '卡片'} ({cardMgr.cards.length})</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {cardMgr.managementMode ? (
                      <>
                        <button onClick={cardMgr.handleSelectAll} style={{ background: 'transparent', border: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>全选</button>
                        <button onClick={cardMgr.handleSelectNone} style={{ background: 'transparent', border: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>取消</button>
                        <button onClick={cardMgr.handleExitManagement} style={{ background: 'transparent', border: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 8px', borderRadius: 4, fontSize: 11 }}>完成</button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => cardMgr.loadCards()} style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 6px', borderRadius: 4, fontSize: 12 }} title="刷新卡片列表">↻</button>
                        <button onClick={cardMgr.handleEnterManagement} style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', padding: '2px 6px', borderRadius: 4, fontSize: 12 }} title="管理卡牌">⚙</button>
                      </>
                    )}
                  </div>
                </div>
                {cardMgr.managementMode && cardMgr.selectedCardIds.size > 0 && (
                  <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border, #374151)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg, #030712)' }}>
                    <span style={{ color: 'var(--text-secondary, #9ca3af)', fontSize: 12 }}>已选 {cardMgr.selectedCardIds.size} 项</span>
                    <button onClick={cardMgr.handleDeleteSelected} disabled={cardMgr.deleting} style={{ background: cardMgr.deleting ? 'var(--border, #374151)' : '#dc2626', border: 'none', color: '#fff', cursor: cardMgr.deleting ? 'not-allowed' : 'pointer', padding: '4px 12px', borderRadius: 4, fontSize: 12 }}>
                      {cardMgr.deleting ? '删除中...' : '删除选中'}
                    </button>
                  </div>
                )}
                {layout.cardsReady && (
                  <div style={{ flex: 1, overflow: 'auto' }}>
                    <TreeBrowser cards={cardMgr.cards} selectedId={cardMgr.selectedCardId} onSelectCard={cardMgr.handleSelectCard} onExpandSearch={taskMgr.handleExpandSearch} onSearchByKeyword={taskMgr.handleSearchByKeyword} onCreateEmptyCard={async (pid) => { const id = await cardMgr.handleCreateEmptyCard(pid); if (id) handleOpenCardWindow(id, true); }} onCreateOrphanCard={async () => { const id = await cardMgr.handleCreateOrphanCard(); if (id) handleOpenCardWindow(id, true); }} onEditCard={(id) => handleOpenCardWindow(id, true)} managementMode={cardMgr.managementMode} selectedCardIds={cardMgr.selectedCardIds} onToggleSelect={cardMgr.handleToggleSelect} sessionId={sessionMgr.selectedSessionId} />
                  </div>
                )}
              </>
            )}
          </aside>

          {/* Graph panel — moved to center (largest area) */}
          <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--bg)', position: 'relative', minWidth: 0 }}>
            <div style={{ padding: 10, borderBottom: '1px solid var(--border, #374151)', color: 'var(--text-secondary, #9ca3af)', fontSize: 12, flexShrink: 0 }}>知识图谱</div>
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', position: 'relative' }}>
              <GraphView cards={cardMgr.cards} selectedId={cardMgr.selectedCardId} onSelectCard={cardMgr.handleSelectCard} onOpenCard={handleOpenCardWindow} visible={true} />

              {/* Gap Analysis FAB */}
              <button
                onClick={() => setGapAnalysisOpen(true)}
                title="知识库质量分析"
                style={{
                  position: 'absolute', bottom: 16, right: 16, zIndex: 50,
                  width: 46, height: 46, borderRadius: '50%',
                  border: '1px solid rgba(16, 185, 129, 0.35)',
                  background: 'rgba(10, 10, 26, 0.88)',
                  backdropFilter: 'blur(10px)',
                  color: '#10b981', cursor: 'pointer', fontSize: 20,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: '0 2px 16px rgba(0,0,0,0.25)',
                  transition: 'background 0.2s, transform 0.2s',
                }}
              >
                📊
              </button>
            </div>

            {gapAnalysisOpen && (
              <GapAnalysis
                sessionId={sessionMgr.selectedSessionId}
                onClose={() => setGapAnalysisOpen(false)}
                onOpenAgent={(prompt) => {
                  setGapAnalysisOpen(false);
                  setAgentOpen(true);
                  setAgentInitialMessage(prompt);
                }}
              />
            )}
          </main>

          {/* Collector panel */}
          <aside style={{ width: layout.collecterExpanded ? 400 : 40, borderLeft: '1px solid var(--border, #374151)', display: 'flex', flexDirection: 'column', background: 'var(--glass-bg, rgba(15,15,35,0.65))', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', transition: 'width 0.3s cubic-bezier(0.4,0,0.2,1)', position: 'relative' }}>
            <button onClick={layout.toggleCollector} style={{ position: 'absolute', left: -16, top: '50%', transform: 'translateY(-50%)', width: 32, height: 32, borderRadius: '50%', border: '1px solid var(--border, #374151)', background: 'var(--bg-secondary, #111827)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }} title={layout.collecterExpanded ? '收起' : '展开'}>
              {layout.collecterExpanded ? '›' : '‹'}
            </button>
            <div style={{ padding: layout.collecterExpanded ? 10 : 0, borderBottom: layout.collecterExpanded ? '1px solid var(--border, #374151)' : 'none', color: 'var(--text-secondary, #9ca3af)', fontSize: 12, display: layout.collecterExpanded ? 'block' : 'none' }}>收集器</div>
            <div style={{ padding: layout.collecterExpanded ? 10 : 0, borderBottom: layout.collecterExpanded ? '1px solid var(--border, #374151)' : 'none', display: layout.collecterExpanded ? 'flex' : 'none', gap: 6, flexWrap: 'wrap' }}>
              <input type="text" value={taskMgr.keyword} onChange={(e) => taskMgr.setKeyword(e.target.value)} placeholder="输入关键词..." style={{ flex: 1, minWidth: 120, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--input-bg)', color: 'var(--text)', fontSize: 13 }}
                onKeyDown={(e) => e.key === 'Enter' && taskMgr.handleStartCollection()} />
              <div style={{ position: 'relative' }}>
                <button onClick={() => taskMgr.setShowCollectSearchMenu(!taskMgr.showCollectSearchMenu)} disabled={!taskMgr.keyword.trim()} style={{ padding: '6px 14px', borderRadius: 6, border: 'none', background: taskMgr.keyword.trim() ? 'var(--accent)' : 'var(--border)', color: '#fff', cursor: taskMgr.keyword.trim() ? 'pointer' : 'not-allowed', fontWeight: 500, fontSize: 13 }}>收集</button>
                <SearchLevelPopup open={taskMgr.showCollectSearchMenu} onSelect={(level) => taskMgr.handleStartCollection(level)} onClose={() => taskMgr.setShowCollectSearchMenu(false)} />
              </div>
              <input type="file" ref={taskMgr.fileInputRef} style={{ display: 'none' }} accept=".txt,.md,.pdf,.docx" onChange={taskMgr.handleDocumentUpload} />
              <button onClick={() => taskMgr.fileInputRef.current?.click()} title="上传本地文档（txt, md, pdf, docx）" style={{ padding: '6px 14px', borderRadius: 6, border: '1px solid var(--accent-border)', background: 'transparent', color: 'var(--accent)', cursor: 'pointer', fontWeight: 500, fontSize: 13, whiteSpace: 'nowrap' }}>上传</button>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: layout.collecterExpanded ? 10 : 0, display: layout.collecterExpanded ? 'block' : 'none' }}>
              {(() => {
                // 运行中的任务不受会话过滤：只渲染当前会话会让「切会话」把运行中的任务卡
                // 卸载 → SSE 断开 → 后端空闲计时器可能在任务跑完前把它取消，而前端此时
                // 没有连接、收不到任何事件，任务卡就永久停在最后一帧（实测三个 expand 被静默杀掉）。
                const sessionTasks = Array.from(taskMgr.searchTasks).filter(
                  ([_, t]) => t.sessionId === sessionMgr.selectedSessionId || !t.sessionId || t.status === 'running'
                )
                if (sessionTasks.length === 0) {
                  return <div style={{ color: 'var(--text-secondary, #9ca3af)', textAlign: 'center', padding: 20 }}>输入关键词点击"收集"搜索，或点击"上传"分析本地文档</div>
                }
                return sessionTasks.map(([_, task]) => (
                  <div key={task.id} style={{ height: 680, border: '1px solid var(--border, #374151)', borderRadius: 8, overflow: 'hidden', marginBottom: 10, flexShrink: 0 }}>
                    <StreamingOutput searchId={task.id} keyword={task.keyword} streamUrl={task.streamUrl} onComplete={taskMgr.handleCollectionComplete} onError={taskMgr.handleCollectionError} onCancel={taskMgr.handleCancelSearch} onRemove={taskMgr.handleRemoveSearch} onTaskIdReceived={taskMgr.handleTaskIdReceived} />
                  </div>
                ))
              })()}
            </div>
          </aside>

          {/* Agent panel — rightmost persistent chat column (collapsible like collector) */}
          <aside style={{ width: layout.agentExpanded ? 360 : 40, borderLeft: '1px solid var(--border, #374151)', display: 'flex', flexDirection: 'column', background: 'var(--glass-bg, rgba(15,15,35,0.65))', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', transition: 'width 0.3s cubic-bezier(0.4,0,0.2,1)', position: 'relative' }}>
            <button onClick={layout.toggleAgent} style={{ position: 'absolute', left: -16, top: '50%', transform: 'translateY(-50%)', width: 32, height: 32, borderRadius: '50%', border: '1px solid var(--border, #374151)', background: 'var(--bg-secondary, #111827)', color: 'var(--text-secondary, #9ca3af)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }} title={layout.agentExpanded ? '收起' : '展开'}>
              {layout.agentExpanded ? '›' : '‹'}
            </button>
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: layout.agentExpanded ? 'block' : 'none' }}>
              <AgentPanel sessionId={sessionMgr.selectedSessionId} initialMessage={agentInitialMessage} onTaskCreated={(taskId, keyword, taskType) => taskMgr.attachBackendTask(taskId, keyword, taskType as TaskType)} />
            </div>
          </aside>
        </div>
      } />
      </Routes>

      {/* Floating card windows (desktop workspace) — overlay layer */}
      {openWindows.map((win) => (
        <FloatingCardWindow
          key={win.id}
          win={win}
          card={cardMgr.cards.find((c) => c.id === win.cardId)}
          allCards={cardMgr.cards}
          sessionId={sessionMgr.selectedSessionId}
          onClose={handleCloseWindow}
          onFocus={handleFocusWindow}
          onMove={handleMoveWindow}
          onResize={handleResizeWindow}
          onMinimize={handleMinimizeWindow}
          onSaveCard={handleSaveCardFromWindow}
          onExpandSearch={taskMgr.handleExpandSearch}
          onSearchByKeyword={taskMgr.handleSearchByKeyword}
        />
      ))}

      <TutorialPopover />
    </div>
  )
}

// ────────────────────────────── App Wrappers ──────────────────────────────

const ThemedApp: React.FC = () => {
  const { user, localUsername } = useAuth()
  // 本地身份是默认身份；云端登录后主题按账号名再分一层
  const storageKey = user ? `theme:${user.username}` : `theme:${localUsername}`
  return (
    <ThemeProvider storageKey={storageKey}>
      <AppContent />
      <ToastHost />
    </ThemeProvider>
  )
}

const App: React.FC = () => {
  return (
    <AuthProvider>
      <ThemedApp />
    </AuthProvider>
  )
}

export default App
