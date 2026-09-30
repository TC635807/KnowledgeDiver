import React from 'react'
import { NavLink, useLocation } from 'react-router-dom'

export type MobilePage = 'sessions' | 'cards' | 'content' | 'collector' | 'graph'

type BottomNavBarProps = {
  selectedCardId: string | null
}

function getActivePage(pathname: string): MobilePage {
  if (/\/workspace\/sessions/.test(pathname)) return 'sessions'
  if (/\/workspace\/card\//.test(pathname)) return 'content'
  if (/\/workspace\/collector/.test(pathname)) return 'collector'
  if (/\/workspace\/graph/.test(pathname)) return 'graph'
  return 'cards'
}

export const BottomNavBar: React.FC<BottomNavBarProps> = ({ selectedCardId }) => {
  const location = useLocation()
  const activePage = getActivePage(location.pathname)
  const contentTo = selectedCardId
    ? `/workspace/card/${encodeURIComponent(selectedCardId)}`
    : '/workspace/cards'

  return (
    <nav className="bottom-nav">
      <NavLink
        to="/workspace/sessions"
        className={`bottom-nav__item ${activePage === 'sessions' ? 'bottom-nav__item--active' : ''}`}
      >
        <span className="bottom-nav__icon">📁</span>
        <span className="bottom-nav__label">会话</span>
      </NavLink>
      <NavLink
        to="/workspace/cards"
        className={`bottom-nav__item ${activePage === 'cards' ? 'bottom-nav__item--active' : ''}`}
      >
        <span className="bottom-nav__icon">📚</span>
        <span className="bottom-nav__label">卡片</span>
      </NavLink>
      <NavLink
        to={contentTo}
        className={`bottom-nav__item ${activePage === 'content' ? 'bottom-nav__item--active' : ''}`}
      >
        <span className="bottom-nav__icon">📄</span>
        <span className="bottom-nav__label">内容</span>
      </NavLink>
      <NavLink
        to="/workspace/collector"
        className={`bottom-nav__item ${activePage === 'collector' ? 'bottom-nav__item--active' : ''}`}
      >
        <span className="bottom-nav__icon">🔍</span>
        <span className="bottom-nav__label">收集</span>
      </NavLink>
      <NavLink
        to="/workspace/graph"
        className={`bottom-nav__item ${activePage === 'graph' ? 'bottom-nav__item--active' : ''}`}
      >
        <span className="bottom-nav__icon">📊</span>
        <span className="bottom-nav__label">图谱</span>
      </NavLink>
    </nav>
  )
}
