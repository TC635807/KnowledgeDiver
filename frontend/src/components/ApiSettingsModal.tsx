import React, { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  AISettingsView,
  ServerSettingsView,
  ServerTestResult,
  getAISettings,
  getServerSettings,
  resetAISettings,
  saveAISettings,
  saveServerSettings,
  testAISettings,
  testServerSettings,
} from '../api/settings'
import './ApiSettingsModal.css'

type TabKey = 'ai' | 'server'

type Props = {
  onClose: () => void
  onSaved?: (view: AISettingsView) => void
  /** 打开时默认显示哪个 tab（UserMenu 的「账号与服务器」直接落到 server） */
  initialTab?: TabKey
  /** 云端登录态（仅「账号与服务器」tab 使用；不传 = 本地模式） */
  serverUsername?: string | null
  serverStatus?: 'checking' | 'online' | 'offline'
  onLogin?: () => void
  onLogout?: () => void
}

type Status = { kind: 'ok' | 'err' | 'info'; text: string } | null

const STATUS_TEXT: Record<string, string> = {
  checking: '检查中…',
  online: '在线',
  offline: '离线，连不上服务器',
}

// ────────────────────────────── AI 模型 tab ──────────────────────────────

const AISettingsPanel: React.FC<Pick<Props, 'onSaved'>> = ({ onSaved }) => {
  const [view, setView] = useState<AISettingsView | null>(null)
  const [apiUrl, setApiUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState<'load' | 'test' | 'save' | 'reset' | ''>('load')
  const [status, setStatus] = useState<Status>(null)

  const applyView = useCallback((v: AISettingsView) => {
    setView(v)
    setApiUrl(v.api_url)
    setModel(v.model)
    setApiKey('') // 服务端不回传明文 Key，输入框保持空白 = 不修改
  }, [])

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const v = await getAISettings()
        if (alive) applyView(v)
      } catch (e) {
        if (alive) setStatus({ kind: 'err', text: (e as Error).message })
      } finally {
        if (alive) setBusy('')
      }
    }
    load()
    return () => {
      alive = false
    }
  }, [applyView])

  const handleSave = async () => {
    setBusy('save')
    setStatus(null)
    try {
      const payload: { api_url: string; model: string; api_key?: string } = {
        api_url: apiUrl.trim(),
        model: model.trim(),
      }
      if (apiKey.trim()) payload.api_key = apiKey.trim()
      const v = await saveAISettings(payload)
      applyView(v)
      setStatus({ kind: 'ok', text: '已保存，配置立即生效（无需重启后端）' })
      if (onSaved) onSaved(v)
    } catch (e) {
      setStatus({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  const handleTest = async () => {
    setBusy('test')
    setStatus(null)
    try {
      const key = apiKey.trim()
      const result = await testAISettings({
        api_url: apiUrl.trim(),
        model: model.trim(),
        ...(key ? { api_key: key } : {}),
      })
      setStatus({ kind: result.ok ? 'ok' : 'err', text: result.message })
    } catch (e) {
      setStatus({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  const handleClearKey = async () => {
    setBusy('save')
    setStatus(null)
    try {
      const v = await saveAISettings({ api_key: null })
      applyView(v)
      setStatus({ kind: 'info', text: '已从 .env 删除 API Key' })
      if (onSaved) onSaved(v)
    } catch (e) {
      setStatus({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  const handleReset = async () => {
    setBusy('reset')
    setStatus(null)
    try {
      const v = await resetAISettings()
      applyView(v)
      setStatus({ kind: 'info', text: '已从 .env 删除 AI 配置，回到代码默认值' })
      if (onSaved) onSaved(v)
    } catch (e) {
      setStatus({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  const loading = busy === 'load'
  const keyPlaceholder = view && view.api_key_set
    ? '已保存 ' + view.api_key_masked + '，留空保持不变'
    : '请输入 API Key（例如 sk-...）'

  if (loading) {
    return <div className="api-settings-status api-settings-status--info">读取中…</div>
  }

  return (
    <>
      <p className="api-settings-hint">
        卡片生成与 Agent 共用同一个模型。保存后直接改写项目根目录的
        <code> .env </code>
        （不新增配置文件，其它行与注释保持不动）并立即生效；
        API Key 只存在服务端，不会回传到浏览器。
      </p>

      <div className="api-settings-field">
        <label htmlFor="api-settings-url">API 地址</label>
        <input
          id="api-settings-url"
          type="text"
          value={apiUrl}
          onChange={(e) => setApiUrl(e.target.value)}
          placeholder="https://api.deepseek.com"
          spellCheck={false}
        />
      </div>

      <div className="api-settings-field">
        <label htmlFor="api-settings-key">API Key</label>
        <input
          id="api-settings-key"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={keyPlaceholder}
          autoComplete="off"
        />
        <span className="api-settings-sub">
          {view && view.api_key_set
            ? '当前 Key 来源：' + (view.api_key_source === 'env_file' ? '.env 文件' : '进程环境变量') + '（已脱敏）'
            : '未检测到 API Key'}
          {view && view.api_key_set && (
            <button type="button" className="api-settings-link" onClick={handleClearKey} disabled={busy !== ''}>
              清除已保存的 Key
            </button>
          )}
        </span>
      </div>

      <div className="api-settings-field">
        <label htmlFor="api-settings-model">模型名称</label>
        <input
          id="api-settings-model"
          type="text"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="deepseek-v4-flash"
          spellCheck={false}
        />
        <span className="api-settings-sub">卡片生成与 Agent 共用此模型</span>
      </div>

      {view && view.overrides.length > 0 && (
        <p className="api-settings-sub">
          .env 中已配置的字段：{view.overrides.join('、')}
        </p>
      )}

      <div className="api-settings-actions">
        <button
          type="button"
          className="api-settings-btn api-settings-btn--primary"
          onClick={handleSave}
          disabled={busy !== ''}
        >
          {busy === 'save' ? '保存中…' : '保存'}
        </button>
        <button
          type="button"
          className="api-settings-btn"
          onClick={handleTest}
          disabled={busy !== ''}
        >
          {busy === 'test' ? '测试中…' : '测试连接'}
        </button>
        <button
          type="button"
          className="api-settings-btn api-settings-btn--ghost"
          onClick={handleReset}
          disabled={busy !== ''}
        >
          删除 .env 中的配置
        </button>
      </div>

      {status && (
        <div
          className={'api-settings-status api-settings-status--' + status.kind}
          role="status"
        >
          {status.text}
        </div>
      )}
    </>
  )
}

// ───────────────────────── 「账号与服务器」tab ─────────────────────────

const ServerSettingsPanel: React.FC<Pick<Props, 'serverUsername' | 'serverStatus' | 'onLogin' | 'onLogout'>> = ({
  serverUsername,
  serverStatus = 'checking',
  onLogin,
  onLogout,
}) => {
  const [view, setView] = useState<ServerSettingsView | null>(null)
  const [serverUrl, setServerUrl] = useState('')
  const [busy, setBusy] = useState<'load' | 'save' | 'test' | ''>('load')
  const [status, setStatus] = useState<Status>(null)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const v = await getServerSettings()
        if (!alive) return
        setView(v)
        setServerUrl(v && typeof v.server_url === 'string' ? v.server_url : '')
      } catch (e) {
        if (alive) setStatus({ kind: 'err', text: (e as Error).message })
      } finally {
        if (alive) setBusy('')
      }
    }
    load()
    return () => {
      alive = false
    }
  }, [])

  const handleSave = async () => {
    setBusy('save')
    setStatus(null)
    try {
      const v = await saveServerSettings({ server_url: serverUrl.trim() })
      setView(v)
      setServerUrl(v && typeof v.server_url === 'string' ? v.server_url : '')
      setStatus({ kind: 'ok', text: '已保存到 .env，立即生效（空值 = 回到默认）' })
    } catch (e) {
      setStatus({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  const handleTest = async () => {
    setBusy('test')
    setStatus(null)
    try {
      const result: ServerTestResult = await testServerSettings(serverUrl)
      const latency = typeof result.latency_ms === 'number' ? '，' + result.latency_ms + 'ms' : ''
      setStatus({
        kind: result.ok ? 'ok' : 'err',
        text: result.ok
          ? '连接成功' + latency + (result.remote ? '（' + result.remote + '）' : '')
          : (result.detail || '连接失败'),
      })
    } catch (e) {
      setStatus({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy('')
    }
  }

  if (busy === 'load') {
    return <div className="api-settings-status api-settings-status--info">读取中…</div>
  }

  return (
    <>
      <p className="api-settings-hint">
        客户端只把「身份 + 论坛 + 迁移」转发到云端服务器；抓取、AI、卡片、向量索引全部留在本机。
        修改后写回项目根目录的 <code> .env </code>，立即生效。
      </p>

      <div className="api-settings-field">
        <label htmlFor="server-settings-url">服务器地址</label>
        <input
          id="server-settings-url"
          type="text"
          value={serverUrl}
          onChange={(e) => setServerUrl(e.target.value)}
          placeholder="https://knowledgediver.cloud"
          spellCheck={false}
        />
        <span className="api-settings-sub">
          {view ? '当前来源：' + (
            view.server_url_source === 'env_file' ? '.env 文件'
              : view.server_url_source === 'env' ? '进程环境变量'
                : '代码默认值'
          ) + '；留空并保存 = 纯本地模式（不启用代理）' : ''}
        </span>
      </div>

      <div className="api-settings-field">
        <label>云端账号</label>
        <span className="api-settings-sub" data-testid="cloud-account-state">
          {serverUsername
            ? '已登录：' + serverUsername + '（' + (STATUS_TEXT[serverStatus] || serverStatus) + '）'
            : '未登录（本地模式，论坛可浏览，点赞/评论/分享需登录）'}
        </span>
        <div className="api-settings-actions" style={{ marginTop: 8 }}>
          {serverUsername ? (
            onLogout && (
              <button type="button" className="api-settings-btn api-settings-btn--ghost" onClick={onLogout} disabled={busy !== ''}>
                退出云端账号
              </button>
            )
          ) : (
            onLogin && (
              <button type="button" className="api-settings-btn" onClick={onLogin} disabled={busy !== ''}>
                登录 / 注册
              </button>
            )
          )}
        </div>
      </div>

      <div className="api-settings-actions">
        <button
          type="button"
          className="api-settings-btn api-settings-btn--primary"
          onClick={handleSave}
          disabled={busy !== ''}
        >
          {busy === 'save' ? '保存中…' : '保存'}
        </button>
        <button
          type="button"
          className="api-settings-btn"
          onClick={handleTest}
          disabled={busy !== ''}
        >
          {busy === 'test' ? '测试中…' : '测试连接'}
        </button>
      </div>

      {status && (
        <div
          className={'api-settings-status api-settings-status--' + status.kind}
          role="status"
        >
          {status.text}
        </div>
      )}
    </>
  )
}

// ────────────────────────────── 弹窗外壳 ──────────────────────────────

const ApiSettingsModal: React.FC<Props> = ({
  onClose,
  onSaved,
  initialTab = 'ai',
  serverUsername,
  serverStatus,
  onLogin,
  onLogout,
}) => {
  const [tab, setTab] = useState<TabKey>(initialTab)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return createPortal(
    <div
      className="api-settings-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="api-settings-modal" role="dialog" aria-modal="true" aria-label="设置">
        <div className="api-settings-head">
          <h3 className="api-settings-title">⚙️ 设置</h3>
          <button className="api-settings-close" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <div className="api-settings-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'ai'}
            className={'api-settings-tab' + (tab === 'ai' ? ' api-settings-tab--active' : '')}
            onClick={() => setTab('ai')}
          >
            AI 模型
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'server'}
            className={'api-settings-tab' + (tab === 'server' ? ' api-settings-tab--active' : '')}
            onClick={() => setTab('server')}
          >
            账号与服务器
          </button>
        </div>

        {tab === 'ai' ? (
          <AISettingsPanel onSaved={onSaved} />
        ) : (
          <ServerSettingsPanel
            serverUsername={serverUsername}
            serverStatus={serverStatus}
            onLogin={onLogin}
            onLogout={onLogout}
          />
        )}
      </div>
    </div>,
    document.body,
  )
}

export default ApiSettingsModal
