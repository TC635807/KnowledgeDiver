import React, { useState, useRef, useEffect } from 'react'
import AvatarCropModal from './AvatarCropModal'
import { useTheme } from '../contexts/ThemeContext'
import './UserMenu.css'

const TOKEN_KEY = 'knowledgeDiver.token'

type Props = {
  username: string
  avatarUrl?: string | null
  onLogout: () => void
  onGoProfile: () => void
}

const UserMenu: React.FC<Props> = ({ username, avatarUrl, onLogout, onGoProfile }) => {
  const { theme, toggleTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const [cropFile, setCropFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
      const token = localStorage.getItem(TOKEN_KEY)
      const formData = new FormData()
      formData.append('file', blob, 'avatar.png')
      const res = await fetch('/api/auth/avatar', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })
      if (res.ok) {
        window.location.reload()
      }
    } catch (err) {
      console.warn('Avatar upload failed:', err)
    } finally {
      setUploading(false)
    }
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
            <span className="usermenu__dropdown-name">{username}</span>
          </div>
          <button className="usermenu__dropdown-item" onClick={handleUploadClick} disabled={uploading}>
            📷 {uploading ? '上传中...' : '更换头像'}
          </button>
          <button className="usermenu__dropdown-item" onClick={() => { onGoProfile(); setOpen(false) }}>
            👤 个人主页
          </button>
          <button className="usermenu__dropdown-item" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️ 日间模式' : '🌙 夜间模式'}
          </button>
          <div className="usermenu__dropdown-sep" />
          <button className="usermenu__dropdown-item usermenu__dropdown-item--danger" onClick={() => { onLogout(); setOpen(false) }}>
            退出登陆
          </button>
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
    </div>
  )
}

export default UserMenu
