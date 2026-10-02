import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import type { AuthUser } from '../types/auth'
import { getMe, login as apiLogin, register as apiRegister, logout as apiLogout } from '../api/auth'
import { clearLegacyToken, ensureLocalSession, getLocalUsername } from '../api/localAccount'
import { clearServerToken, getServerToken, isRemoteAuthError, isRemoteOffline, setAuthExpiredHandler } from '../api/remote'
import { showToast } from '../utils/toast'

/** 云端服务器可达性（决定论坛"离线"提示与 UserMenu 三态）。 */
export type ServerStatus = 'checking' | 'online' | 'offline'

interface AuthContextType {
  /** 云端账号（可选）。本地身份不用它表示 —— 本地身份永远存在。 */
  user: AuthUser | null
  /** 云端账号检查中 */
  loading: boolean
  /** 本地内置账号（local identity）是否已就绪；就绪即可进工作区 */
  localReady: boolean
  /** 本地 bootstrap 的失败原因（必须显式呈现，不许静默空工作区） */
  localError: string | null
  /** 本地账号用户名（= 本地数据命名空间） */
  localUsername: string
  serverStatus: ServerStatus
  error: string | null
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
  /** 本地 bootstrap 失败后手动重试 */
  retryLocalSession: () => Promise<void>
  /** 论坛请求成功后回报可达性 */
  notifyServerStatus: (status: ServerStatus) => void
  /** @deprecated 语义 = "云端账号已登录"。本地可用性请用 localReady。 */
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [localReady, setLocalReady] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [localUsername, setLocalUsername] = useState('local')
  const [serverStatus, setServerStatus] = useState<ServerStatus>('checking')
  const [error, setError] = useState<string | null>(null)

  /** P0：启动即为内置本地账号换取 token（05-实施契约 §3.2）。 */
  const bootstrapLocal = useCallback(async () => {
    // 旧的 knowledgeDiver.token 是本地/云端共用键，先清掉再谈身份
    clearLegacyToken()
    try {
      await ensureLocalSession()
      setLocalUsername(getLocalUsername())
      setLocalError(null)
      setLocalReady(true)
    } catch (err) {
      setLocalReady(false)
      setLocalError(err instanceof Error ? err.message : '本地账号初始化失败')
    }
  }, [])

  /** P1：如果存有云端 token，则校验并拉取账号信息。 */
  const loadServerUser = useCallback(async () => {
    if (!getServerToken()) {
      setUser(null)
      setServerStatus('online')
      setLoading(false)
      return
    }
    try {
      const userData = await getMe()
      setUser(userData)
      setServerStatus('online')
    } catch (err) {
      setUser(null)
      if (isRemoteOffline(err)) {
        setServerStatus('offline')
      } else if (isRemoteAuthError(err)) {
        // token 已由 remoteFetch 清除
        setServerStatus('online')
        showToast('登录已失效，请重新登录', 'err')
      } else {
        setServerStatus('offline')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void bootstrapLocal()
  }, [bootstrapLocal])

  useEffect(() => {
    void loadServerUser()
  }, [loadServerUser])

  // 任意云端 401 → 统一清登录态并提示（remoteFetch 触发）
  useEffect(() => {
    setAuthExpiredHandler(() => {
      setUser(null)
      setServerStatus('online')
      showToast('登录已失效，请重新登录', 'err')
    })
    return () => setAuthExpiredHandler(null)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    setError(null)
    try {
      await apiLogin({ username, password })
      const userData = await getMe()
      setUser(userData)
      setServerStatus('online')
    } catch (err) {
      const message = err instanceof Error ? err.message : '登录失败'
      setError(message)
      throw err
    }
  }, [])

  const register = useCallback(async (username: string, password: string) => {
    setError(null)
    try {
      await apiRegister({ username, password })
      const userData = await getMe()
      setUser(userData)
      setServerStatus('online')
    } catch (err) {
      const message = err instanceof Error ? err.message : '注册失败'
      setError(message)
      throw err
    }
  }, [])

  const logout = useCallback(() => {
    apiLogout()
    clearServerToken()
    setUser(null)
    setServerStatus('online')
  }, [])

  const refreshUser = useCallback(async () => {
    if (!getServerToken()) return
    try {
      const userData = await getMe()
      setUser(userData)
      setServerStatus('online')
    } catch (err) {
      // 刷新失败保留现有账号信息；离线只更新状态，不打断本地工作区
      if (isRemoteOffline(err)) setServerStatus('offline')
      console.warn('云端账号刷新失败:', err)
    }
  }, [])

  const retryLocalSession = useCallback(async () => {
    await bootstrapLocal()
  }, [bootstrapLocal])

  const notifyServerStatus = useCallback((status: ServerStatus) => {
    setServerStatus(status)
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        localReady,
        localError,
        localUsername,
        serverStatus,
        error,
        login,
        register,
        logout,
        refreshUser,
        retryLocalSession,
        notifyServerStatus,
        isAuthenticated: !!user,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
