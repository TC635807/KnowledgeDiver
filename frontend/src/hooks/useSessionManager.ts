import { useState, useCallback, useRef } from 'react'
import { Session } from '../types/session'
import { Card } from '../types/card'
import { authFetchWithToken } from '../api/auth'
import {
  listSessions,
  createSession,
  updateSession,
  deleteSession,
  downloadSession,
  uploadSession,
  shareSession,
} from '../api/session'
import { shareToHub } from '../api/hub'

export interface UseSessionManagerReturn {
  sessions: Session[]
  selectedSessionId: string
  setSelectedSessionId: (id: string) => void
  sessionManagementMode: boolean
  selectedSessionIds: Set<string>
  sessionDeleting: boolean
  creatingSession: boolean
  newSessionName: string
  setNewSessionName: (v: string) => void
  editingSessionId: string | null
  editingSessionName: string
  setEditingSessionName: (v: string) => void
  downloadingId: string | null
  uploadingFile: boolean
  showUploadMenu: boolean
  setShowUploadMenu: (v: boolean) => void
  uploadBtnRef: React.RefObject<HTMLButtonElement | null>
  loadSessions: () => void
  handleSelectSession: (sessionId: string) => void
  handleCreateSession: () => Promise<void>
  handleUpdateSession: (sessionId: string, name: string) => Promise<void>
  handleDeleteSelectedSessions: () => Promise<void>
  handleStartEditSession: (sessionId: string, currentName: string) => void
  handleCancelEditSession: () => void
  handleToggleSessionSelect: (sessionId: string) => void
  handleSessionSelectAll: () => void
  handleSessionSelectNone: () => void
  handleEnterSessionManagement: () => void
  handleExitSessionManagement: () => void
  handleDownloadSession: (sessionId: string, sessionName: string) => Promise<void>
  handleUploadFromFile: () => void
  handleShareSession: (sessionId: string) => Promise<void>
  handleShareToHub: (sessionId: string) => Promise<void>
}

export function useSessionManager(
  loadCardsFn: (sessionId?: string) => void
): UseSessionManagerReturn {
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedSessionId, setSelectedSessionId] = useState<string>('default')
  const [sessionManagementMode, setSessionManagementMode] = useState(false)
  const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(new Set())
  const [sessionDeleting, setSessionDeleting] = useState(false)
  const [creatingSession, setCreatingSession] = useState(false)
  const [newSessionName, setNewSessionName] = useState('')
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editingSessionName, setEditingSessionName] = useState('')
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [uploadingFile, setUploadingFile] = useState(false)
  const [showUploadMenu, setShowUploadMenu] = useState(false)
  const uploadBtnRef = useRef<HTMLButtonElement>(null)

  const loadSessions = useCallback(() => {
    listSessions()
      .then((data) => {
        setSessions(data)
      })
      .catch((err) => {
        console.error('[loadSessions] Failed to load sessions:', err)
      })
  }, [])

  const handleSelectSession = useCallback(
    (sessionId: string) => {
      setSelectedSessionId(sessionId)
      loadCardsFn(sessionId)
    },
    [loadCardsFn]
  )

  const handleCreateSession = useCallback(async () => {
    if (!newSessionName.trim()) return
    setCreatingSession(true)
    try {
      const session = await createSession(newSessionName.trim())
      setSessions((prev) => [...prev, session])
      setNewSessionName('')
      loadSessions()
    } catch (err) {
      console.error('Failed to create session:', err)
      alert('创建会话失败，请重试')
    } finally {
      setCreatingSession(false)
    }
  }, [newSessionName, loadSessions])

  const handleUpdateSession = useCallback(async (sessionId: string, name: string) => {
    if (!name.trim()) return
    try {
      const updatedSession = await updateSession(sessionId, name.trim())
      setSessions((prev) => prev.map((s) => (s.id === sessionId ? updatedSession : s)))
      setEditingSessionId(null)
      setEditingSessionName('')
    } catch (err) {
      console.error('Failed to update session:', err)
      alert('更新会话失败，请重试')
    }
  }, [])

  const handleDeleteSelectedSessions = useCallback(async () => {
    if (selectedSessionIds.size === 0) return
    if (
      !confirm(
        `确定要删除选中的 ${selectedSessionIds.size} 个会话吗？此操作不可撤销，会话中的卡牌也将被删除。`
      )
    )
      return
    setSessionDeleting(true)
    try {
      const deletePromises = Array.from(selectedSessionIds).map((sessionId) =>
        deleteSession(sessionId)
      )
      await Promise.all(deletePromises)
      setSelectedSessionIds(new Set())
      loadSessions()
      if (selectedSessionIds.has(selectedSessionId)) {
        setSelectedSessionId('default')
        loadCardsFn('default')
      }
    } catch (err) {
      console.error('Failed to delete sessions:', err)
      alert('删除会话失败，请重试')
    } finally {
      setSessionDeleting(false)
    }
  }, [selectedSessionIds, selectedSessionId, loadSessions, loadCardsFn])

  const handleStartEditSession = useCallback((sessionId: string, currentName: string) => {
    setEditingSessionId(sessionId)
    setEditingSessionName(currentName)
  }, [])

  const handleCancelEditSession = useCallback(() => {
    setEditingSessionId(null)
    setEditingSessionName('')
  }, [])

  const handleToggleSessionSelect = useCallback((sessionId: string) => {
    if (sessionId === 'default') return
    setSelectedSessionIds((prev) => {
      const next = new Set(prev)
      if (next.has(sessionId)) {
        next.delete(sessionId)
      } else {
        next.add(sessionId)
      }
      return next
    })
  }, [])

  const handleSessionSelectAll = useCallback(() => {
    setSelectedSessionIds(new Set(sessions.map((s) => s.id)))
  }, [sessions])

  const handleSessionSelectNone = useCallback(() => {
    setSelectedSessionIds(new Set())
  }, [])

  const handleEnterSessionManagement = useCallback(() => {
    setSessionManagementMode(true)
    setSelectedSessionIds(new Set())
  }, [])

  const handleExitSessionManagement = useCallback(() => {
    setSessionManagementMode(false)
    setSelectedSessionIds(new Set())
  }, [])

  const handleDownloadSession = useCallback(
    async (sessionId: string, sessionName: string) => {
      setDownloadingId(sessionId)
      try {
        await downloadSession(sessionId, sessionName)
      } catch (err) {
        console.error('Failed to download session:', err)
        alert('下载失败，请重试')
      } finally {
        setDownloadingId(null)
      }
    },
    []
  )

  const handleUploadFromFile = useCallback(() => {
    setShowUploadMenu(false)
    setTimeout(() => {
      const input = document.createElement('input')
      input.type = 'file'
      input.accept = '.zip'
      input.onchange = async (e) => {
        const file = (e.target as HTMLInputElement).files?.[0]
        if (!file) return
        setUploadingFile(true)
        try {
          const result = await uploadSession(file)
          alert(`会话「${result.session.name}」导入成功，包含 ${result.card_count} 张卡片`)
          loadSessions()
        } catch (err) {
          console.error('Failed to upload session:', err)
          alert('上传失败：' + (err instanceof Error ? err.message : '未知错误'))
        } finally {
          setUploadingFile(false)
        }
      }
      input.click()
    }, 0)
  }, [loadSessions])

  const handleShareSession = useCallback(async (sessionId: string) => {
    try {
      const result = await shareSession(sessionId)
      await navigator.clipboard.writeText(result.share_url)
      alert('分享链接已复制到剪贴板')
    } catch (err) {
      console.error('Failed to share session:', err)
      alert('生成分享链接失败，请重试')
    }
  }, [])

  const handleShareToHub = useCallback(
    async (sessionId: string) => {
      const session = sessions.find((s) => s.id === sessionId)
      if (!session) return
      const description = prompt('请输入简介（可选）：', '') || ''
      const topicsStr = prompt('请输入主题标签（逗号分隔，可选）：', '') || ''
      const topics = topicsStr.split(',').map((t) => t.trim()).filter(Boolean)
      try {
        await shareToHub(sessionId, session.name, description, topics)
        alert('已分享到 Hub！')
      } catch (err) {
        console.error('Failed to share to hub:', err)
        alert('分享到 Hub 失败，请重试')
      }
    },
    [sessions]
  )

  return {
    sessions,
    selectedSessionId,
    setSelectedSessionId,
    sessionManagementMode,
    selectedSessionIds,
    sessionDeleting,
    creatingSession,
    newSessionName,
    setNewSessionName,
    editingSessionId,
    editingSessionName,
    setEditingSessionName,
    downloadingId,
    uploadingFile,
    showUploadMenu,
    setShowUploadMenu,
    uploadBtnRef,
    loadSessions,
    handleSelectSession,
    handleCreateSession,
    handleUpdateSession,
    handleDeleteSelectedSessions,
    handleStartEditSession,
    handleCancelEditSession,
    handleToggleSessionSelect,
    handleSessionSelectAll,
    handleSessionSelectNone,
    handleEnterSessionManagement,
    handleExitSessionManagement,
    handleDownloadSession,
    handleUploadFromFile,
    handleShareSession,
    handleShareToHub,
  }
}
