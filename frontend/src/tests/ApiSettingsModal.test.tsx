/**
 * API 配置弹窗测试（开源版：把 AI 接口配置做成前端可改）。
 *
 * 锁定：
 * - 打开后从 /api/settings/ai 读取生效配置并回填（Key 只显示脱敏值）；
 * - 「保存」发 PUT，且 Key 留空时不提交 api_key（= 保持原值）；
 * - 「测试连接」发 POST /api/settings/ai/test 并展示后端返回的成败信息；
 * - 「清除已保存的 Key」发 api_key: null。
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ApiSettingsModal from '../components/ApiSettingsModal'

const VIEW = {
  api_url: 'https://api.deepseek.com',
  model: 'deepseek-v4-flash',
  api_key_set: true,
  api_key_masked: 'sk-e22******216e',
  api_key_source: 'env_file',
  overrides: ['api_url', 'model'],
  env_path: '/tmp/.env',
  defaults: { api_url: 'https://api.deepseek.com', api_key: '', model: 'deepseek-v4-flash' },
}

type Call = { url: string; method: string; body: any }

function mockFetch(responses: Record<string, any> = {}): Call[] {
  const calls: Call[] = []
  const impl = vi.fn(async (url: string, options: any = {}) => {
    const method = String(options.method || 'GET').toUpperCase()
    calls.push({ url, method, body: options.body ? JSON.parse(options.body) : null })
    const body = responses[method + ' ' + url] ?? VIEW
    return { ok: true, status: 200, json: async () => body, text: async () => '' }
  })
  ;(global as any).fetch = impl
  return calls
}

describe('ApiSettingsModal', () => {
  beforeEach(() => {
    mockFetch()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('打开时读取生效配置并回填字段', async () => {
    render(<ApiSettingsModal onClose={() => {}} />)
    expect(await screen.findByDisplayValue('https://api.deepseek.com')).toBeTruthy()
    expect(screen.getByDisplayValue('deepseek-v4-flash')).toBeTruthy()
    // Key 不回显明文，只给脱敏提示
    const keyInput = document.getElementById('api-settings-key') as HTMLInputElement
    expect(keyInput.value).toBe('')
    expect(keyInput.placeholder).toContain('sk-e22******216e')
  })

  it('保存时发 PUT，Key 留空则不提交 api_key', async () => {
    const calls = mockFetch()
    render(<ApiSettingsModal onClose={() => {}} />)
    await screen.findByDisplayValue('https://api.deepseek.com')

    fireEvent.change(document.getElementById('api-settings-model') as HTMLInputElement, {
      target: { value: 'deepseek-v4-flash-new' },
    })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => {
      const put = calls.find((c) => c.method === 'PUT')
      expect(put).toBeTruthy()
      expect(put!.url).toBe('/api/settings/ai')
      expect(put!.body.model).toBe('deepseek-v4-flash-new')
      expect('api_key' in put!.body).toBe(false)
    })
    expect(await screen.findByText(/已保存/)).toBeTruthy()
  })

  it('测试连接发 POST 并展示结果', async () => {
    const calls = mockFetch({
      'POST /api/settings/ai/test': { ok: true, message: '连接成功（deepseek-v4-flash，120ms）' },
    })
    render(<ApiSettingsModal onClose={() => {}} />)
    await screen.findByDisplayValue('https://api.deepseek.com')

    fireEvent.click(screen.getByText('测试连接'))

    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/settings/ai/test')).toBe(true)
    })
    expect(await screen.findByText(/连接成功/)).toBeTruthy()
  })

  it('清除已保存的 Key 时提交 api_key: null', async () => {
    const calls = mockFetch()
    render(<ApiSettingsModal onClose={() => {}} />)
    await screen.findByDisplayValue('https://api.deepseek.com')

    fireEvent.click(screen.getByText('清除已保存的 Key'))

    await waitFor(() => {
      const put = calls.find((c) => c.method === 'PUT')
      expect(put).toBeTruthy()
      expect(put!.body.api_key).toBeNull()
    })
  })
})
