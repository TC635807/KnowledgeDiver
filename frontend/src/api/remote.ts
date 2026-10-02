/**
 * 云端（KnowledgeDiver 服务器）调用通道。
 *
 * 设计（见 05-实施契约 §3.1/§3.3）：
 * - `knowledgeDiver.serverToken`：云端账号登录后拿到，**只被本模块读取**，
 *   仅用于经本地代理白名单的三组路径（身份 / 论坛 / /api/remote/sessions/*）；
 * - 本地请求永远不携带它（本地用 api/localAccount.ts 的 localToken）。
 */

export const SERVER_TOKEN_KEY = 'knowledgeDiver.serverToken'

/** 默认云端域名；实际请求都走本地代理的相对路径，这里只用于界面文案。 */
export const DEFAULT_SERVER_ORIGIN = 'https://knowledgediver.cloud'

export function getServerToken(): string | null {
  try {
    return localStorage.getItem(SERVER_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setServerToken(token: string): void {
  try {
    localStorage.setItem(SERVER_TOKEN_KEY, token)
  } catch {
    /* ignore */
  }
}

export function clearServerToken(): void {
  try {
    localStorage.removeItem(SERVER_TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export class RemoteError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'RemoteError'
    this.status = status
  }
}

/** 云端不可达（502/503/504/网络错误/代理超时）：论坛应显示"离线"。 */
export class RemoteUnavailableError extends RemoteError {
  constructor(message = '离线，连不上服务器') {
    super(message, 502)
    this.name = 'RemoteUnavailableError'
  }
}

/** 云端 401：token 失效，已清除 serverToken。 */
export class RemoteAuthError extends RemoteError {
  constructor(message = '登录已失效，请重新登录') {
    super(message, 401)
    this.name = 'RemoteAuthError'
  }
}

/** 云端 429：限流。注意服务器响应体可能是 {error:...} 而不是 {detail:...}。 */
export class RemoteRateLimitError extends RemoteError {
  /** 服务器原始 error/detail，仅用于诊断，不作为用户文案 */
  serverDetail?: string
  constructor(message = '操作过于频繁，请稍后再试') {
    super(message, 429)
    this.name = 'RemoteRateLimitError'
  }
}

export function isRemoteOffline(err: unknown): boolean {
  return err instanceof RemoteUnavailableError
}

export function isRemoteAuthError(err: unknown): boolean {
  return err instanceof RemoteAuthError
}

/** 401 时通知 AuthContext 清掉登录态（避免每个调用点都处理）。 */
type AuthExpiredHandler = () => void
let authExpiredHandler: AuthExpiredHandler | null = null

export function setAuthExpiredHandler(handler: AuthExpiredHandler | null): void {
  authExpiredHandler = handler
}

function extractRemoteDetail(body: unknown): string {
  if (!body || typeof body !== 'object') return ''
  const { detail, error } = body as { detail?: unknown; error?: unknown }
  const value = detail ?? error
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((v) => (v && (v as { msg?: string }).msg) || JSON.stringify(v)).join('；')
  }
  return value ? String(value) : ''
}

function buildHeaders(options?: RequestInit): Headers {
  const headers = new Headers((options?.headers as HeadersInit) || undefined)
  const token = getServerToken()
  if (token) headers.set('Authorization', 'Bearer ' + token)
  const body = options?.body
  if (body && !(body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return headers
}

async function toRemoteError(res: Response): Promise<RemoteError> {
  const body = await res.json().catch(() => null)
  const detail = extractRemoteDetail(body)
  if (res.status === 401) {
    clearServerToken()
    if (authExpiredHandler) authExpiredHandler()
    return new RemoteAuthError(detail || undefined)
  }
  if (res.status === 429) {
    // 契约 §3.4：429 一律给固定友好文案（服务器响应体是 {error:...}，仅作诊断保留）
    const err = new RemoteRateLimitError()
    if (detail) err.serverDetail = detail
    return err
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return new RemoteUnavailableError(detail || undefined)
  }
  return new RemoteError(detail || ('HTTP ' + res.status), res.status)
}

async function doRemoteFetch(url: string, options?: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(url, { ...options, headers: buildHeaders(options) })
  } catch {
    throw new RemoteUnavailableError()
  }
  if (!res.ok) throw await toRemoteError(res)
  return res
}

/** 云端 JSON 调用。失败抛 Remote* 分类错误。 */
export async function remoteFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await doRemoteFetch(url, options)
  if (res.status === 204) return undefined as unknown as T
  return (await res.json()) as T
}

/** 云端二进制调用（例如 /api/remote/sessions/download/{sid} 的 ZIP）。 */
export async function remoteFetchBlob(url: string, options?: RequestInit): Promise<Blob> {
  const res = await doRemoteFetch(url, options)
  return res.blob()
}

/** 云端文本调用（错误体不是 JSON 时兜底）。 */
export async function remoteFetchText(url: string, options?: RequestInit): Promise<string> {
  const res = await doRemoteFetch(url, options)
  return res.text()
}
