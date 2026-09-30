import { useState, useCallback, useRef, useEffect } from 'react'
import { SearchTask, BackendTask, TaskType } from '../types/pipeline'
import { Card } from '../types/card'
import { startCollection } from '../api/stream'
import { uploadDocument } from '../api/documents'
import { listRunningTasks, getTaskStreamUrl, cancelTask as cancelBackendTask, removeTask as removeBackendTask } from '../api/task'

export interface UseTaskManagerReturn {
  searchTasks: Map<string, SearchTask>
  keyword: string
  setKeyword: (v: string) => void
  showCollectSearchMenu: boolean
  setShowCollectSearchMenu: (v: boolean) => void
  fileInputRef: React.RefObject<HTMLInputElement | null>
  handleStartCollection: (searchLevel?: string) => void
  handleDocumentUpload: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>
  handleExpandSearch: (cardId: string, title: string, searchLevel?: string) => void
  handleSearchByKeyword: (cardId: string, keyword: string) => void
  handleCollectionComplete: (searchId: string, newCards: Card[]) => void
  handleCollectionError: (searchId: string, error: Error) => void
  handleCancelSearch: (searchId: string) => Promise<void>
  handleRemoveSearch: (searchId: string) => Promise<void>
  handleClearCompletedSearches: () => void
  handleTaskIdReceived: (tempId: string, realTaskId: string) => void
  attachBackendTask: (taskId: string, keyword: string, taskType?: TaskType) => void
  restoreRunningTasks: () => Promise<void>
}

export function useTaskManager(
  selectedSessionId: string,
  cards: Card[],
  loadCards: () => void,
  refreshUser: () => void,
  collecterExpanded: boolean,
  setCollecterExpanded: (v: boolean) => void
): UseTaskManagerReturn {
  const [searchTasks, setSearchTasks] = useState<Map<string, SearchTask>>(new Map())
  const [keyword, setKeyword] = useState('')
  const [showCollectSearchMenu, setShowCollectSearchMenu] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const _reloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const _addTask = useCallback((id: string, kw: string, streamUrl: string, taskType: TaskType = 'collect') => {
    const newTask: SearchTask = {
      id,
      keyword: kw,
      streamUrl,
      status: 'running',
      createdAt: new Date(),
      cards: [],
      taskType,
      sessionId: selectedSessionId,
    }
    setSearchTasks((prev) => {
      const next = new Map(prev)
      next.set(id, newTask)
      return next
    })
    if (!collecterExpanded) {
      setCollecterExpanded(true)
    }
  }, [selectedSessionId, collecterExpanded, setCollecterExpanded])

  const handleStartCollection = useCallback(
    (searchLevel: string = 'default') => {
      if (!keyword.trim()) return
      const url = `/api/pipeline/collect?keyword=${encodeURIComponent(keyword)}&max_sources=5&session_id=${encodeURIComponent(selectedSessionId)}&search_level=${encodeURIComponent(searchLevel)}`
      _addTask(`temp_${Date.now()}`, keyword, url, 'collect')
    },
    [keyword, selectedSessionId, _addTask]
  )

  const handleDocumentUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file) return
      try {
        const result = await uploadDocument(file, selectedSessionId)
        _addTask(result.task_id, result.keyword, getTaskStreamUrl(result.task_id), 'document')
      } catch (err) {
        console.error('[DocumentUpload] Failed:', err)
        alert('文档上传失败: ' + (err instanceof Error ? err.message : '未知错误'))
      }
      e.target.value = ''
    },
    [selectedSessionId, _addTask]
  )

  const handleExpandSearch = useCallback(
    (cardId: string, title: string, searchLevel: string = 'default') => {
      const card = cards.find((c) => c.id === cardId)
      if (!card) {
        console.error('Card not found:', cardId)
        return
      }
      const url = `/api/pipeline/expand?source_card_id=${encodeURIComponent(cardId)}&card_title=${encodeURIComponent(title)}&max_topics=5&session_id=${encodeURIComponent(selectedSessionId)}&search_level=${encodeURIComponent(searchLevel)}`
      _addTask(`temp_${Date.now()}`, `延申: ${title}`, url, 'expand')
    },
    [cards, selectedSessionId, _addTask]
  )

  // 自定义关键词搜索：用户直接指定关键词（等价后端 search_by_keyword），
  // 走 /api/pipeline/collect + source_card_id 挂载到当前卡片下
  const handleSearchByKeyword = useCallback(
    (cardId: string, keyword: string) => {
      if (!keyword.trim()) return
      const url = `/api/pipeline/collect?keyword=${encodeURIComponent(keyword)}&max_sources=5&session_id=${encodeURIComponent(selectedSessionId)}&source_card_id=${encodeURIComponent(cardId)}&search_level=default`
      _addTask(`temp_${Date.now()}`, keyword, url, 'collect')
    },
    [selectedSessionId, _addTask]
  )

  const handleCollectionComplete = useCallback(
    (searchId: string, newCards: Card[]) => {
      loadCards()
      refreshUser()
      // 额外延迟重载确保后端 DB 已提交
      const timer = setTimeout(() => loadCards(), 1200)
      setSearchTasks((prev) => {
        const next = new Map(prev)
        next.delete(searchId)
        return next
      })
      // 清理之前的定时器，仅保留最新的
      const prev = _reloadTimerRef.current
      if (prev) clearTimeout(prev)
      _reloadTimerRef.current = timer
    },
    [loadCards, refreshUser]
  )

  const handleCollectionError = useCallback(
    (searchId: string, error: Error) => {
      console.error('Collection error for', searchId, error)
      refreshUser()
      setSearchTasks((prev) => {
        const next = new Map(prev)
        const task = next.get(searchId)
        if (task) {
          next.set(searchId, { ...task, status: 'error', error: error.message })
        }
        return next
      })
    },
    [refreshUser]
  )

  const handleCancelSearch = useCallback(
    async (searchId: string) => {
      const task = searchTasks.get(searchId)
      const backendTaskId = task?.realTaskId || searchId
      try {
        await cancelBackendTask(backendTaskId)
      } catch (err) {
        console.error('Failed to cancel task on backend:', err)
      }
      setSearchTasks((prev) => {
        const next = new Map(prev)
        const t = next.get(searchId)
        if (t && t.status === 'running') {
          next.set(searchId, { ...t, status: 'cancelled' })
        }
        return next
      })
    },
    [searchTasks]
  )

  const handleRemoveSearch = useCallback(
    async (searchId: string) => {
      const task = searchTasks.get(searchId)
      const backendTaskId = task?.realTaskId || searchId
      try {
        await removeBackendTask(backendTaskId)
      } catch (err) {
        console.error('Failed to remove task on backend:', err)
      }
      setSearchTasks((prev) => {
        const next = new Map(prev)
        next.delete(searchId)
        return next
      })
    },
    [searchTasks]
  )

  const handleClearCompletedSearches = useCallback(() => {
    setSearchTasks((prev) => {
      const next = new Map(prev)
      for (const [id, task] of next) {
        if (task.status !== 'running') {
          next.delete(id)
        }
      }
      return next
    })
  }, [])

  const handleTaskIdReceived = useCallback((tempId: string, realTaskId: string) => {
    setSearchTasks((prev) => {
      const next = new Map(prev)
      const task = next.get(tempId)
      if (task && !task.realTaskId) {
        next.set(tempId, { ...task, realTaskId, streamUrl: getTaskStreamUrl(realTaskId) })
      }
      return next
    })
  }, [])

  const attachBackendTask = useCallback((taskId: string, kw: string, taskType: TaskType = 'gap_driven') => {
    _addTask(taskId, kw, getTaskStreamUrl(taskId), taskType)
  }, [_addTask])

  const restoreRunningTasks = useCallback(async () => {
    try {
      const runningTasks = await listRunningTasks()
      for (const backendTask of runningTasks) {
        const streamUrl = getTaskStreamUrl(backendTask.task_id)
        const newTask: SearchTask = {
          id: backendTask.task_id,
          keyword: backendTask.keyword,
          streamUrl: streamUrl,
          status: 'running',
          createdAt: new Date(backendTask.created_at),
          cards: [],
          taskType: backendTask.task_type,
          sessionId: backendTask.session_id,
        }
        setSearchTasks((prev) => {
          const next = new Map(prev)
          next.set(backendTask.task_id, newTask)
          return next
        })
      }
      if (runningTasks.length > 0 && !collecterExpanded) {
        setCollecterExpanded(true)
      }
    } catch (err) {
      console.error('[Restore] Failed to restore running tasks:', err)
    }
  }, [collecterExpanded, setCollecterExpanded])

  return {
    searchTasks,
    keyword,
    setKeyword,
    showCollectSearchMenu,
    setShowCollectSearchMenu,
    fileInputRef,
    handleStartCollection,
    handleDocumentUpload,
    handleExpandSearch,
    handleSearchByKeyword,
    handleCollectionComplete,
    handleCollectionError,
    handleCancelSearch,
    handleRemoveSearch,
    handleClearCompletedSearches,
    handleTaskIdReceived,
    attachBackendTask,
    restoreRunningTasks,
  }
}
