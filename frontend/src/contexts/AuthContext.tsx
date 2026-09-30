import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import type { AuthUser } from '../types/auth'
import { getToken, setToken, clearToken, getMe, login as apiLogin, register as apiRegister, logout as apiLogout } from '../api/auth'

interface AuthContextType {
  user: AuthUser | null
  loading: boolean
  error: string | null
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadUser = useCallback(async () => {
    const token = getToken()
    if (!token) {
      setLoading(false)
      return
    }

    try {
      const userData = await getMe()
      setUser(userData)
    } catch (err) {
      console.warn('User load failed:', err)
      clearToken()
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadUser()
  }, [loadUser])

  const login = useCallback(async (username: string, password: string) => {
    setError(null)
    try {
      await apiLogin({ username, password })
      const userData = await getMe()
      setUser(userData)
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
    } catch (err) {
      const message = err instanceof Error ? err.message : '注册失败'
      setError(message)
      throw err
    }
  }, [])

  const logout = useCallback(() => {
    apiLogout()
    setUser(null)
  }, [])

  const refreshUser = useCallback(async () => {
    try {
      const userData = await getMe()
      setUser(userData)
    } catch (err) {
      console.warn('User refresh failed:', err)
      // keep current user data on refresh failure
    }
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        error,
        login,
        register,
        logout,
        refreshUser,
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
