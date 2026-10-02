/**
 * 极轻量 toast（全局提示）总线。
 *
 * 用途：把原先"只 console.error、界面无感"的失败（例如卡片/会话列表 401）
 * 变成用户可见的错误，见 05-实施契约 §3.4 / 00 §5 第 9、10 条。
 * 不引入任何依赖，非 React 代码也可以直接调用 showToast()。
 */

export type ToastKind = 'info' | 'ok' | 'err'

export interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}

type Listener = () => void

let items: ToastItem[] = []
const listeners = new Set<Listener>()
const timers = new Map<number, ReturnType<typeof setTimeout>>()
let seq = 0

const DEFAULT_TTL_MS = 5000

function emit(): void {
  listeners.forEach((listener) => listener())
}

export function getToasts(): ToastItem[] {
  return items
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function dismissToast(id: number): void {
  const timer = timers.get(id)
  if (timer) {
    clearTimeout(timer)
    timers.delete(id)
  }
  const next = items.filter((item) => item.id !== id)
  if (next.length === items.length) return
  items = next
  emit()
}

/**
 * 弹一条提示。相同文案 + 相同类型在未消失前只保留一条（去重），
 * 避免轮询/重试把界面刷满。
 */
export function showToast(text: string, kind: ToastKind = 'info', ttlMs: number = DEFAULT_TTL_MS): number | null {
  if (!text) return null
  const existing = items.find((item) => item.text === text && item.kind === kind)
  if (existing) return existing.id

  const id = ++seq
  items = [...items, { id, kind, text }]
  if (ttlMs > 0) {
    timers.set(
      id,
      setTimeout(() => dismissToast(id), ttlMs),
    )
  }
  emit()
  return id
}

/** 测试用：清空全部提示与定时器。 */
export function __resetToasts(): void {
  timers.forEach((timer) => clearTimeout(timer))
  timers.clear()
  items = []
  emit()
}
