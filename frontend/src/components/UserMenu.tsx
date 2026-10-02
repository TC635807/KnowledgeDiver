import React, { useState, useRef, useEffect } from 'react'
import AvatarCropModal from './AvatarCropModal'
import ApiSettingsModal from './ApiSettingsModal'
import { useTheme } from '../contexts/ThemeContext'
import { remoteFetch } from '../api/remote'
import { showToast } from '../utils/toast'
import './UserMenu.css'

export type ServerStatus = 'checking' | 'online' | 'offline'

type Props = {
  /** 头像按钮标题 / 本地或云端显示名 */
  username: string
  avatarUrl?: string | null
  onLogout: () => void
  onGoProfile: () => void
  /** 云端账号用户名；为空 = 本地模式（三态之一：本地模式） */
  serverUsername?: string | null
  /** 云端可达性：账号 + online = 正常；账号 + offline/checking = 离线态 */
  serverStatus?: ServerStatus
  /** 本地模式下点击「登录 / 注册」 */
  onLogin?: () => void
  /** 打开「账号与服务器」设置（默认会打开 API 配置弹窗） */
  onOpenServerSettings?: () => void
}

const STATUS_TEXT: Record<ServerStatus, string> = {
  checking: '检查中…',
  online: '在线',
  offline: '离线，连不上服务器',
}

const UserMenu: React.FC<Props> = ({
  username,
  avatarUrl,
  onLogout,
  onGoProfile,
  serverUsername,
  serverStatus = 'checking',
  onLogin,
  onOpenServerSettings,
}) => {
  const { theme, toggleTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const [cropFile, setCropFile] = useState<File | null>(null)
  const [apiSettingsOpen, setApiSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<'ai' | 'server'>('ai')
  const [uploading, setUploading] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const isCloud = !!serverUsername

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const handleAvatarClick = () => setOpen(!open)

  const handleUploadClick = () => {
    fileInputRef.current?.click()
    setOpen(false)
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // Reset the input so re-selecting the same file works
    e.target.value = ''
    setCropFile(file)
  }

  const handleCropCancel = () => setCropFile(null)

  const handleCropConfirm = async (blob: Blob) => {
    setCropFile(null)
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', blob, 'avatar.png')
      // 头像属于云端账号：走代理白名单，携带 serverToken（绝不带本地 token）
      await remoteFetch('/api/auth/avatar', { method: 'POST', body: formData })
      window.location.reload()
    } catch (err) {
      console.warn('Avatar upload failed:', err)
      showToast('头像上传失败：' + (err instanceof Error ? err.message : '未知错误'), 'err')
    } finally {
      setUploading(false)
    }
  }

  const openApiSettings = () => {
    setSettingsTab('ai')
    setApiSettingsOpen(true)
    setOpen(false)
  }

  const openServerSettings = () => {
    setSettingsTab('server')
    setApiSettingsOpen(true)
    setOpen(false)
  }

  const initial = username.charAt(0).toUpperCase()

  return (
    <div className="usermenu" ref={menuRef}>
      <button className="usermenu__avatar-btn" onClick={handleAvatarClick} title={username}>
        {avatarUrl ? (
          <img
            className="usermenu__avatar-img"
            src={avatarUrl}
            alt={username}
          />
        ) : (
          <span className="usermenu__avatar-initial">{initial}</span>
        )}
      </button>
      {open && (
        <div className="usermenu__dropdown">
          <div className="usermenu__dropdown-header">
            {isCloud ? (
              <>
                <span className="usermenu__dropdown-name">{serverUsername} @ knowledgediver.cloud</span>
                <span
                  data-testid="server-status"
                  style={{ display: 'block', fontSize: 11, opacity: 0.75, marginTop: 2 }}
                >
                  {STATUS_TEXT[serverStatus]}
                </span>
              </>
            ) : (
              <>
                <span className="usermenu__dropdown-name">本地模式</span>
                <span
                  data-testid="server-status"
                  style={{ display: 'block', fontSize: 11, opacity: 0.75, marginTop: 2 }}
                >
                  {username} · 本地工作区无需登录
                </span>
              </>
            )}
          </div>

          {!isCloud && onLogin && (
            <button className="usermenu__dropdown-item" onClick={() => { onLogin(); setOpen(false) }}>
              🔑 登录 / 注册 KnowledgeDiver 账号
            </button>
          )}

          {isCloud && (
            <>
              <button className="usermenu__dropdown-item" onClick={handleUploadClick} disabled={uploading}>
                📷 {uploading ? '上传中...' : '更换头像'}
              </button>
              <button className="usermenu__dropdown-item" onClick={() => { onGoProfile(); setOpen(false) }}>
                👤 个人主页
              </button>
            </>
          )}

          <button className="usermenu__dropdown-item" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️ 日间模式' : '🌙 夜间模式'}
          </button>
          <button
            className="usermenu__dropdown-item"
            onClick={openApiSettings}
          >
            ⚙️ API 配置
          </button>
          <button
            className="usermenu__dropdown-item"
            onClick={onOpenServerSettings ? () => { onOpenServerSettings(); setOpen(false) } : openServerSettings}
          >
            🌐 账号与服务器
          </button>

          {isCloud && (
            <>
              <div className="usermenu__dropdown-sep" />
              <button className="usermenu__dropdown-item usermenu__dropdown-item--danger" onClick={() => { onLogout(); setOpen(false) }}>
                退出登录（本地工作区不受影响）
              </button>
            </>
          )}
        </div>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />
      {cropFile && (
        <AvatarCropModal
          file={cropFile}
          onConfirm={handleCropConfirm}
          onCancel={handleCropCancel}
        />
      )}
      {apiSettingsOpen && (
        <ApiSettingsModal
          onClose={() => setApiSettingsOpen(false)}
          initialTab={settingsTab}
          serverUsername={serverUsername ?? null}
          serverStatus={serverStatus}
          onLogin={onLogin}
          onLogout={onLogout}
        />
      )}
    </div>
  )
}

export default UserMenu
