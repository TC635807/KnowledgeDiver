/**
 * UserMenu 三态测试（05-实施契约 §3.4）：
 * 1) 本地模式（无云端账号）→ 显示「本地模式」+ 登录/注册入口；
 * 2) 云端账号 + online → 「用户名 @ knowledgediver.cloud」+ 在线 + 退出；
 * 3) 云端账号 + offline → 显示「离线，连不上服务器」。
 */
import React from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import UserMenu from '../components/UserMenu'
import { ThemeProvider } from '../contexts/ThemeContext'

type MenuProps = React.ComponentProps<typeof UserMenu>

function renderMenu(props: Partial<MenuProps> = {}) {
  const merged: MenuProps = {
    username: 'local',
    onLogout: () => {},
    onGoProfile: () => {},
    ...props,
  }
  return render(
    <ThemeProvider storageKey="test-theme">
      <UserMenu {...merged} />
    </ThemeProvider>,
  )
}

describe('UserMenu 三态', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('本地模式：显示「本地模式」与登录/注册入口，点击后回调 onLogin', () => {
    const onLogin = vi.fn()
    renderMenu({ username: 'local', onLogin })

    fireEvent.click(screen.getByTitle('local'))

    expect(screen.getByText('本地模式')).toBeTruthy()
    expect(screen.queryByText(/@ knowledgediver\.cloud/)).toBeNull()

    // 既有的 API 配置入口不能丢（点击登录会关闭下拉，所以先断言）
    expect(screen.getByText('⚙️ API 配置')).toBeTruthy()
    expect(screen.getByText('🌐 账号与服务器')).toBeTruthy()

    const loginBtn = screen.getByText('🔑 登录 / 注册 KnowledgeDiver 账号')
    expect(loginBtn).toBeTruthy()
    fireEvent.click(loginBtn)
    expect(onLogin).toHaveBeenCalledTimes(1)
  })

  it('云端账号在线：显示用户名 @ 域名 + 在线，且没有登录入口', () => {
    renderMenu({ username: 'alice', serverUsername: 'alice', serverStatus: 'online' })

    fireEvent.click(screen.getByTitle('alice'))

    expect(screen.getByText(/alice @ knowledgediver\.cloud/)).toBeTruthy()
    expect(screen.getByTestId('server-status').textContent).toContain('在线')
    expect(screen.queryByText('🔑 登录 / 注册 KnowledgeDiver 账号')).toBeNull()
    expect(screen.getByText(/退出登录/)).toBeTruthy()
    // 云端账号才有头像上传 / 个人主页
    expect(screen.getByText(/更换头像/)).toBeTruthy()
    expect(screen.getByText(/个人主页/)).toBeTruthy()
  })

  it('云端账号离线：显示「离线，连不上服务器」', () => {
    renderMenu({ username: 'alice', serverUsername: 'alice', serverStatus: 'offline' })

    fireEvent.click(screen.getByTitle('alice'))

    expect(screen.getByTestId('server-status').textContent).toContain('离线，连不上服务器')
  })

  it('本地模式下不显示头像上传与退出登录', () => {
    renderMenu({ username: 'local' })
    fireEvent.click(screen.getByTitle('local'))

    expect(screen.queryByText(/更换头像/)).toBeNull()
    expect(screen.queryByText(/退出登录/)).toBeNull()
  })
})
