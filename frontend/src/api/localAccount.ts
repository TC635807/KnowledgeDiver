/**
 * 本地内置账号（local identity）的 token 与启动 bootstrap。
 *
 * 设计（见 05-实施契约 §3.1/§3.2）：
 * - `knowledgeDiver.localToken`：客户端本地后端签发的长期 JWT，**所有本地请求**使用；
 * - 旧的 `knowledgeDiver.token` 是"本地/云端共用"的历史遗留键，启动时必须清理，
 *   否则登录云端后本地请求会带上服务器 JWT，导致 53 个本地端点整体 401。
 */

export const LOCAL_TOKEN_KEY = 'knowledgeDiver.localToken'
export const LEGACY_TOKEN_KEY = 'knowledgeDiver.token'

/** 本地账号用户名（同时是本地数据命名空间），后端由 LOCAL_ACCOUNT_USER 决定。 */
let localUsername = 'local'

export function getLocalUsername(): string {
  return localUsername
}

export function getLocalToken(): string | null {
  try {
    return localStorage.getItem(LOCAL_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setLocalToken(token: string, username?: string): void {
  try {
    localStorage.setItem(LOCAL_TOKEN_KEY, token)
  } catch {
    /* localStorage 不可用（隐私模式）时静默降级 */
  }
  if (username) localUsername = username
}

export function clearLocalToken(): void {
  try {
    localStorage.removeItem(LOCAL_TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

/**
 * 清理旧的共用键（启动时调用一次）。
 * 只删旧键、不做迁移：旧键里可能是服务器 token，绝不能拿它去调本地端点。
 */
export function clearLegacyToken(): void {
  try {
    if (localStorage.getItem(LEGACY_TOKEN_KEY) !== null) {
      localStorage.removeItem(LEGACY_TOKEN_KEY)
      console.info(
        '[token] 已移除旧的 ' + LEGACY_TOKEN_KEY + '（本地/云端 token 已拆分为 localToken / serverToken）',
      )
    }
  } catch {
    /* ignore */
  }
}

export interface LocalSessionResponse {
  access_token: string
  token_type?: string
  username?: string
}

function extractDetail(body: unknown): string {
  if (!body || typeof body !== 'object') return ''
  const detail = (body as { detail?: unknown; error?: unknown }).detail
  const error = (body as { detail?: unknown; error?: unknown }).error
  const value = detail ?? error
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((v) => (v && (v as { msg?: string }).msg) || JSON.stringify(v)).join('；')
  }
  return value ? String(value) : ''
}

/**
 * 启动 bootstrap：没有 localToken 就向本地后端换取一个内置账号 token。
 * 失败必须抛出**可读错误**（由 AuthContext 呈现在界面上），
 * 不允许出现"静默空工作区"。
 */
/**
 * 探测已存 token 是否仍被本地后端接受。
 * true=有效 / false=确定无效(401) / null=无法判断（后端未起或网络问题）。
 * 后端换了 JWT_SECRET（.env 被删、或曾直接用 uvicorn 启动）时会是 false，
 * 此时必须重新 bootstrap，否则本地工作区会永久 401 且无法自愈。
 */
async function probeLocalToken(token: string): Promise<boolean | null> {
  try {
    const res = await fetch('/api/sessions', {
      headers: { Authorization: 'Bearer ' + token },
    })
    if (res.status === 401) return false
    return res.ok ? true : null
  } catch {
    return null
  }
}

export async function ensureLocalSession(): Promise<string> {
  const existing = getLocalToken()
  if (existing) {
    const healthy = await probeLocalToken(existing)
    if (healthy !== false) return existing
    clearLocalToken()
  }

  let res: Response
  try {
    res = await fetch('/api/auth/local-session', { method: 'POST' })
  } catch {
    throw new Error('无法连接本地服务：后端未启动或端口未就绪，本地工作区不可用。')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = extractDetail(body)
    if (res.status === 403) {
      throw new Error(detail || '本地账号未启用（LOCAL_ACCOUNT=0），本地工作区不可用。')
    }
    throw new Error(detail || ('本地登录失败（HTTP ' + res.status + '）'))
  }

  const data = (await res.json()) as LocalSessionResponse
  if (!data || !data.access_token) {
    throw new Error('本地登录失败：服务端未返回 access_token。')
  }
  setLocalToken(data.access_token, data.username || 'local')
  return data.access_token
}
