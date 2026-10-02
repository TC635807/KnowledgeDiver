import React, { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import './AuthPage.css'

type AuthMode = 'login' | 'register'

type Props = {
  /** 登录/注册成功后回调（通常导航回工作区） */
  onDone?: () => void
  /** 「先不登录，进入本地工作区」；不传则不显示该入口 */
  onBack?: () => void
}

export const AuthPage: React.FC<Props> = ({ onDone, onBack }) => {
  const [mode, setMode] = useState<AuthMode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { login, register } = useAuth()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (username.length > 16) {
      setError('用户名不能超过16个字符')
      return
    }
    if (password.length > 20) {
      setError('密码不能超过20个字符')
      return
    }
    if (!username.trim()) {
      setError('用户名不能为空')
      return
    }
    if (!password.trim()) {
      setError('密码不能为空')
      return
    }

    setLoading(true)
    try {
      if (mode === 'login') {
        await login(username, password)
      } else {
        await register(username, password)
      }
      if (onDone) onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-container">
        <div className="auth-brand">
          <span className="auth-brand__icon">🧠</span>
          <h1 className="auth-brand__name">KnowledgeDiver</h1>
          <p className="auth-brand__tagline">AI 驱动的知识收集系统</p>
          <p className="auth-brand__tagline" style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>
            这里登录的是 KnowledgeDiver 云端账号（用于论坛与头像）；本地工作区无需登录。
          </p>
        </div>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={`auth-tab ${mode === 'login' ? 'auth-tab--active' : ''}`}
            onClick={() => { setMode('login'); setError(null) }}
            disabled={loading}
          >
            <span className="auth-tab__icon">🔑</span>
            <span className="auth-tab__label">登录</span>
            <span className="auth-tab__desc">已有账号</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            className={`auth-tab ${mode === 'register' ? 'auth-tab--active' : ''}`}
            onClick={() => { setMode('register'); setError(null) }}
            disabled={loading}
          >
            <span className="auth-tab__icon">✨</span>
            <span className="auth-tab__label">注册</span>
            <span className="auth-tab__desc">创建新账号</span>
          </button>
        </div>

        <div className="auth-mode-title">
          {mode === 'login' ? '欢迎回来' : '创建账号'}
        </div>

        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="auth-field">
            <label htmlFor="username">用户名</label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户名"
              maxLength={16}
              disabled={loading}
            />
            <span className="auth-hint">最多16个字符</span>
          </div>

          <div className="auth-field">
            <label htmlFor="password">密码</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码"
              maxLength={20}
              disabled={loading}
            />
            <span className="auth-hint">最多20个字符</span>
          </div>

          {error && <div className="auth-error">{error}</div>}

          <button
            type="submit"
            className={`auth-submit ${mode === 'login' ? 'auth-submit--login' : 'auth-submit--register'}`}
            disabled={loading}
          >
            {loading ? '处理中...' : mode === 'login' ? '🔑 登录' : '✨ 注册'}
          </button>
        </form>

        <div className="auth-switch">
          {mode === 'login' ? (
            <span>
              还没有账号？{' '}
              <button type="button" onClick={() => setMode('register')} disabled={loading}>
                立即注册
              </button>
            </span>
          ) : (
            <span>
              已有账号？{' '}
              <button type="button" onClick={() => setMode('login')} disabled={loading}>
                立即登录
              </button>
            </span>
          )}
        </div>

        {onBack && (
          <div className="auth-switch" style={{ marginTop: 10 }}>
            <button type="button" onClick={onBack} disabled={loading}>
              ← 先不登录，直接进入本地工作区
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
