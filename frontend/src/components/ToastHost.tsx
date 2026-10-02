import React, { useEffect, useState } from 'react'
import { ToastItem, dismissToast, getToasts, subscribeToasts } from '../utils/toast'

const PALETTE: Record<string, { background: string; border: string; color: string; icon: string }> = {
  info: { background: 'rgba(17,24,39,0.96)', border: 'rgba(139,92,246,0.45)', color: '#e5e7eb', icon: 'ℹ️' },
  ok: { background: 'rgba(6,78,59,0.96)', border: 'rgba(16,185,129,0.5)', color: '#d1fae5', icon: '✅' },
  err: { background: 'rgba(69,10,10,0.96)', border: 'rgba(239,68,68,0.5)', color: '#fee2e2', icon: '⚠️' },
}

/**
 * 全局提示挂载点。挂在 App 根部，任何模块调用 showToast() 都会在这里显示。
 */
const ToastHost: React.FC = () => {
  const [items, setItems] = useState<ToastItem[]>(getToasts())

  useEffect(() => subscribeToasts(() => setItems(getToasts())), [])

  if (items.length === 0) return null

  return (
    <div
      data-testid="toast-host"
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        left: '50%',
        bottom: 24,
        transform: 'translateX(-50%)',
        zIndex: 100000,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        maxWidth: 'min(90vw, 460px)',
        pointerEvents: 'none',
      }}
    >
      {items.map((item) => {
        const palette = PALETTE[item.kind] || PALETTE.info
        return (
          <div
            key={item.id}
            data-testid="toast-item"
            style={{
              pointerEvents: 'auto',
              display: 'flex',
              alignItems: 'flex-start',
              gap: 8,
              padding: '10px 12px',
              borderRadius: 10,
              background: palette.background,
              border: '1px solid ' + palette.border,
              color: palette.color,
              fontSize: 13,
              lineHeight: 1.5,
              boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
            }}
          >
            <span aria-hidden="true">{palette.icon}</span>
            <span style={{ flex: 1 }}>{item.text}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => dismissToast(item.id)}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'inherit',
                opacity: 0.7,
                cursor: 'pointer',
                fontSize: 14,
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </div>
        )
      })}
    </div>
  )
}

export default ToastHost
