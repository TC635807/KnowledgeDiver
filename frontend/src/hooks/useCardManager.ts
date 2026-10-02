import { useState, useCallback, useRef } from 'react'
import { Card } from '../types/card'
import { authFetchWithToken } from '../api/auth'
import { showToast } from '../utils/toast'

export interface UseCardManagerReturn {
  cards: Card[]
  selectedCardId: string | null
  editingCardId: string | null
  editingTitle: string
  editingContent: string
  editingLinks: string[]
  savingCard: boolean
  managementMode: boolean
  selectedCardIds: Set<string>
  deleting: boolean
  loadCards: (sessionId?: string) => void
  handleSelectCard: (id: string) => void
  handleStartEditCard: (cardId: string) => void
  handleEditTitleChange: (value: string) => void
  handleEditContentChange: (value: string) => void
  handleEditLinksChange: (links: string[]) => void
  handleSaveEdit: () => void
  handleCancelEdit: () => void
  handleToggleSelect: (cardId: string) => void
  handleSelectAll: () => void
  handleSelectNone: () => void
  handleDeleteSelected: () => void
  handleEnterManagement: () => void
  handleExitManagement: () => void
  handleCreateEmptyCard: (parentCardId: string) => Promise<string | undefined>
  handleCreateOrphanCard: () => Promise<string | undefined>
}

export function useCardManager(
  selectedSessionId: string,
  isPortrait: boolean,
  goMobilePage: (page: string, cardId?: string) => void
): UseCardManagerReturn {
  const [cards, setCards] = useState<Card[]>([])
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null)
  const [editingCardId, setEditingCardId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [editingContent, setEditingContent] = useState('')
  const [editingLinks, setEditingLinks] = useState<string[]>([])
  const [savingCard, setSavingCard] = useState(false)
  const [managementMode, setManagementMode] = useState(false)
  const [selectedCardIds, setSelectedCardIds] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const refreshingRef = useRef(false)

  const loadCards = useCallback(
    (sessionId?: string) => {
      if (refreshingRef.current) {
        console.log('Refresh already in progress, skipping')
        return
      }
      refreshingRef.current = true
      let targetSessionId = sessionId || selectedSessionId
      if (!targetSessionId || targetSessionId.trim() === '') {
        console.warn('[loadCards] Invalid session ID, defaulting to "default"')
        targetSessionId = 'default'
      }
      const url = `/api/cards?session_id=${encodeURIComponent(targetSessionId)}`
      authFetchWithToken<Card[]>(url)
        .then((data) => {
          if (!Array.isArray(data)) {
            console.error('[loadCards] Invalid response data, expected array:', data)
            return
          }
          const processed = data.map((c) => ({
            ...c,
            created_at: new Date(c.created_at),
            updated_at: new Date(c.updated_at),
          }))
          setCards(processed)
        })
        .catch((err) => {
          console.error('[loadCards] Failed to load cards:', err)
          // 不再静默：否则 401/网络问题永远表现为"空列表"（契约 §3.4）
          showToast('卡片列表加载失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
        })
        .finally(() => {
          refreshingRef.current = false
        })
    },
    [selectedSessionId]
  )

  const handleSelectCard = useCallback(
    (id: string) => {
      setSelectedCardId(id)
      if (isPortrait && goMobilePage) {
        // Caller should handle the mobile navigation
      }
    },
    [isPortrait]
  )

  const handleStartEditCard = useCallback(
    (cardId: string) => {
      const card = cards.find((c) => c.id === cardId)
      if (!card) return
      setEditingCardId(cardId)
      setEditingTitle(card.title)
      setEditingContent(card.content)
      setEditingLinks(card.links || [])
      if (isPortrait) {
        goMobilePage('content', cardId)
      }
    },
    [cards, isPortrait, goMobilePage]
  )

  const handleEditTitleChange = useCallback(
    (value: string) => {
      setEditingTitle(value)
    },
    []
  )

  const handleEditContentChange = useCallback(
    (value: string) => {
      setEditingContent(value)
    },
    []
  )

  const handleEditLinksChange = useCallback(
    (links: string[]) => {
      setEditingLinks(links)
    },
    []
  )

  const handleSaveEdit = useCallback(async () => {
    if (!editingCardId) return
    setSavingCard(true)
    try {
      const body = JSON.stringify({
        title: editingTitle,
        content: editingContent,
        links: editingLinks,
      })
      await authFetchWithToken(
        `/api/cards/${editingCardId}?session_id=${encodeURIComponent(selectedSessionId)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body,
        }
      )
      setEditingCardId(null)
      setEditingTitle('')
      setEditingContent('')
      setEditingLinks([])
      loadCards()
    } catch (err) {
      console.error('Failed to update card:', err)
      alert('保存失败，请重试')
    } finally {
      setSavingCard(false)
    }
  }, [editingCardId, editingTitle, editingContent, editingLinks, selectedSessionId, loadCards])

  const handleCancelEdit = useCallback(() => {
    setEditingCardId(null)
    setEditingTitle('')
    setEditingContent('')
    setEditingLinks([])
  }, [])

  const handleToggleSelect = useCallback((cardId: string) => {
    setSelectedCardIds((prev) => {
      const next = new Set(prev)
      if (next.has(cardId)) {
        next.delete(cardId)
      } else {
        next.add(cardId)
      }
      return next
    })
  }, [])

  const handleSelectAll = useCallback(() => {
    setSelectedCardIds(new Set(cards.map((c) => c.id)))
  }, [cards])

  const handleSelectNone = useCallback(() => {
    setSelectedCardIds(new Set())
  }, [])

  const handleDeleteSelected = useCallback(async () => {
    if (selectedCardIds.size === 0) return
    if (!confirm(`确定要删除选中的 ${selectedCardIds.size} 张卡牌吗？此操作不可撤销。`)) return
    setDeleting(true)
    try {
      const deletePromises = Array.from(selectedCardIds).map((cardId) =>
        authFetchWithToken(
          `/api/cards/${cardId}?session_id=${encodeURIComponent(selectedSessionId)}`,
          { method: 'DELETE' }
        )
      )
      await Promise.all(deletePromises)
      setSelectedCardIds(new Set())
      loadCards()
    } catch (err) {
      console.error('Failed to delete cards:', err)
      alert('删除失败，请重试')
    } finally {
      setDeleting(false)
    }
  }, [selectedCardIds, selectedSessionId, loadCards])

  const handleEnterManagement = useCallback(() => {
    setManagementMode(true)
    setSelectedCardIds(new Set())
  }, [])

  const handleExitManagement = useCallback(() => {
    setManagementMode(false)
    setSelectedCardIds(new Set())
  }, [])

  const handleCreateEmptyCard = useCallback(
    async (parentCardId: string) => {
      try {
        const body = JSON.stringify({
          title: '新卡片',
          content: '',
          parent_id: parentCardId,
        })
        const newCard = await authFetchWithToken<Card>(
          `/api/cards?session_id=${encodeURIComponent(selectedSessionId)}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
          }
        )
        setCards((prev) => [...prev, newCard])
        setSelectedCardId(newCard.id)
        setEditingCardId(newCard.id)
        setEditingTitle(newCard.title)
        setEditingContent(newCard.content)
        if (isPortrait) {
          goMobilePage('content', newCard.id)
        }
        loadCards()
        return newCard.id
      } catch (err) {
        console.error('Failed to create empty card:', err)
        return undefined
      }
    },
    [selectedSessionId, loadCards, isPortrait, goMobilePage]
  )

  const handleCreateOrphanCard = useCallback(async () => {
    try {
      const body = JSON.stringify({
        title: '新卡片',
        content: '',
      })
      const newCard = await authFetchWithToken<Card>(
        `/api/cards?session_id=${encodeURIComponent(selectedSessionId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
        }
      )
      setCards((prev) => [...prev, newCard])
      setSelectedCardId(newCard.id)
      setEditingCardId(newCard.id)
      setEditingTitle(newCard.title)
      setEditingContent(newCard.content)
      if (isPortrait) {
        goMobilePage('content', newCard.id)
      }
      loadCards()
      return newCard.id
    } catch (err) {
      console.error('Failed to create orphan card:', err)
      return undefined
    }
  }, [selectedSessionId, loadCards, isPortrait, goMobilePage])

  return {
    cards,
    selectedCardId,
    editingCardId,
    editingTitle,
    editingContent,
    editingLinks,
    savingCard,
    managementMode,
    selectedCardIds,
    deleting,
    loadCards,
    handleSelectCard,
    handleStartEditCard,
    handleEditTitleChange,
    handleEditContentChange,
    handleEditLinksChange,
    handleSaveEdit,
    handleCancelEdit,
    handleToggleSelect,
    handleSelectAll,
    handleSelectNone,
    handleDeleteSelected,
    handleEnterManagement,
    handleExitManagement,
    handleCreateEmptyCard,
    handleCreateOrphanCard,
  }
}
