import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export type MobilePage = 'sessions' | 'cards' | 'content' | 'collector' | 'graph'
export type ForumPage = 'hub' | 'profile'

export function useRouteState() {
  const location = useLocation()
  const navigate = useNavigate()

  const isForum = location.pathname.startsWith('/forum')
  const workspaceMode: 'workspace' | 'forum' = isForum ? 'forum' : 'workspace'

  // Derive forum sub-page
  let forumPage: ForumPage = 'hub'
  const profileMatch = location.pathname.match(/\/forum\/profile\/(.+)/)
  if (profileMatch) {
    forumPage = 'profile'
  }
  const profileUsername = profileMatch ? decodeURIComponent(profileMatch[1]) : null

  // Derive mobile page from path
  let mobilePage: MobilePage = 'cards'
  if (/\/workspace\/sessions/.test(location.pathname)) {
    mobilePage = 'sessions'
  } else if (/\/workspace\/card\//.test(location.pathname)) {
    mobilePage = 'content'
  } else if (/\/workspace\/collector/.test(location.pathname)) {
    mobilePage = 'collector'
  } else if (/\/workspace\/graph/.test(location.pathname)) {
    mobilePage = 'graph'
  }

  // Parse card ID from portrait route /workspace/card/:id
  const cardMatch = location.pathname.match(/\/workspace\/card\/(.+)/)
  const routeCardId = cardMatch ? decodeURIComponent(cardMatch[1]) : null

  const goWorkspace = useCallback(() => navigate('/workspace'), [navigate])
  const goForum = useCallback(() => navigate('/forum'), [navigate])
  const goProfile = useCallback((username: string) => navigate(`/forum/profile/${encodeURIComponent(username)}`), [navigate])
  const goMobilePage = useCallback((page: MobilePage, cardId?: string) => {
    switch (page) {
      case 'sessions': navigate('/workspace/sessions'); break
      case 'cards': navigate('/workspace/cards'); break
      case 'content': navigate(cardId ? `/workspace/card/${cardId}` : '/workspace/cards'); break
      case 'collector': navigate('/workspace/collector'); break
      case 'graph': navigate('/workspace/graph'); break
    }
  }, [navigate])

  return {
    workspaceMode,
    forumPage,
    profileUsername,
    mobilePage,
    routeCardId,
    navigate,
    location,
    goWorkspace,
    goForum,
    goProfile,
    goMobilePage,
  }
}
