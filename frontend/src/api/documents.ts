import { getToken } from './auth'

interface UploadResult {
  task_id: string
  keyword: string
}

export async function uploadDocument(
  file: File,
  sessionId: string
): Promise<UploadResult> {
  const token = getToken()
  if (!token) {
    throw new Error('未认证')
  }

  const formData = new FormData()
  formData.append('file', file)
  formData.append('session_id', sessionId)

  const response = await fetch('/api/documents/upload', {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '上传失败' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return response.json()
}
