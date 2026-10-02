/**
 * 用户菜单入口测试：头像下拉里「日间/夜间模式」下方应有「⚙️ API 配置」，
 * 点击后打开 API 配置弹窗。
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import UserMenu from '../components/UserMenu'
import { ThemeProvider } from '../contexts/ThemeContext'

const VIEW = {
  api_url: 'https://api.deepseek.com',
  model: 'deepseek-v4-flash',
  api_key_set: false,
  api_key_masked: '',
  api_key_source: 'none',
  overrides: [],
  env_path: '/tmp/.env',
  defaults: { api_url: 'https://api.deepseek.com', api_key: '', model: 'deepseek-v4-flash' },
}

describe('UserMenu 的 API 配置入口', () => {
  beforeEach(() => {
    ;(global as any).fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => VIEW,
      text: async () => '',
    }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('主题切换按钮下方有「API 配置」，点击后打开弹窗', async () => {
    render(
      <ThemeProvider storageKey="test-theme">
        <UserMenu username="tester" onLogout={() => {}} onGoProfile={() => {}} />
      </ThemeProvider>,
    )

    fireEvent.click(screen.getByTitle('tester'))

    const themeBtn = screen.getByText(/日间模式|夜间模式/)
    const apiBtn = screen.getByText('⚙️ API 配置')
    expect(themeBtn).toBeTruthy()
    expect(apiBtn).toBeTruthy()
    // 菜单顺序：主题切换在 API 配置之前
    expect(themeBtn.compareDocumentPosition(apiBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(apiBtn)

    expect(await screen.findByRole('dialog')).toBeTruthy()
    expect(await screen.findByDisplayValue('https://api.deepseek.com')).toBeTruthy()
  })
})
