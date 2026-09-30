import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react'

type Theme = 'dark' | 'light'

type ThemeContextValue = {
  theme: Theme
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStoredTheme(key: string): Theme {
  if (typeof window === 'undefined') return 'dark'
  const stored = localStorage.getItem(key)
  if (stored === 'light' || stored === 'dark') return stored
  return 'dark'
}

export const ThemeProvider: React.FC<{ storageKey: string; children: React.ReactNode }> = ({
  storageKey,
  children,
}) => {
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme(storageKey))
  const storageKeyRef = useRef(storageKey)
  const prevKeyRef = useRef(storageKey)

  // Keep ref in sync so the persist effect always writes to the correct key
  storageKeyRef.current = storageKey

  // When the namespaced key changes (user login / switch), reload from storage
  useEffect(() => {
    if (prevKeyRef.current === storageKey) return
    prevKeyRef.current = storageKey
    setTheme(readStoredTheme(storageKey))
  }, [storageKey])

  // Persist theme to DOM + localStorage — only fires on explicit user toggle,
  // never on storageKey changes (decoupled via storageKeyRef).
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem(storageKeyRef.current, theme)
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'))
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
