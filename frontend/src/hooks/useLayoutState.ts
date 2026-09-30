import { useState, useCallback } from 'react'

export interface UseLayoutStateReturn {
  cardsExpanded: boolean
  cardsReady: boolean
  sessionsExpanded: boolean
  sessionsReady: boolean
  collecterExpanded: boolean
  agentExpanded: boolean
  setCollecterExpanded: (v: boolean) => void
  setCardsReady: (v: boolean) => void
  setSessionsReady: (v: boolean) => void
  toggleCards: () => void
  toggleSessions: () => void
  toggleCollector: () => void
  toggleAgent: () => void
}

export function useLayoutState(): UseLayoutStateReturn {
  const [cardsExpanded, setCardsExpanded] = useState(window.innerWidth > 768)
  const [cardsReady, setCardsReady] = useState(false)
  const [sessionsExpanded, setSessionsExpanded] = useState(window.innerWidth > 768)
  const [sessionsReady, setSessionsReady] = useState(false)
  const [collecterExpanded, setCollecterExpanded] = useState(false)
  const [agentExpanded, setAgentExpanded] = useState(true)

  const toggleCollector = useCallback(() => setCollecterExpanded((v) => !v), [])
  const toggleAgent = useCallback(() => setAgentExpanded((v) => !v), [])

  const toggleCards = useCallback(() => {
    if (cardsExpanded) {
      setCardsReady(false)
      setCardsExpanded(false)
    } else {
      setCardsExpanded(true)
      setTimeout(() => setCardsReady(true), 350)
    }
  }, [cardsExpanded])

  const toggleSessions = useCallback(() => {
    if (sessionsExpanded) {
      setSessionsReady(false)
      setSessionsExpanded(false)
    } else {
      setSessionsExpanded(true)
      setTimeout(() => setSessionsReady(true), 350)
    }
  }, [sessionsExpanded])

  return {
    cardsExpanded,
    cardsReady,
    sessionsExpanded,
    sessionsReady,
    collecterExpanded,
    agentExpanded,
    setCollecterExpanded,
    setCardsReady,
    setSessionsReady,
    toggleCards,
    toggleSessions,
    toggleCollector,
    toggleAgent,
  }
}
