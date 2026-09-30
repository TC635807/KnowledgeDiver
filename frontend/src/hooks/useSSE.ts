import { useEffect, useRef, useState, useCallback } from 'react'
import type { PipelineEvent } from '../types/pipeline'
import { parseSSEMessage } from '../api/stream'
import { getToken } from '../api/auth'

type SSEHookResult = {
  isConnected: boolean
  disconnect: () => void
}

export function useSSE(url: string, onMessage: (ev: PipelineEvent) => void, onError: (err: Error) => void): SSEHookResult {
  const abortControllerRef = useRef<AbortController | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const onMessageRef = useRef(onMessage)
  const onErrorRef = useRef(onError)

  onMessageRef.current = onMessage
  onErrorRef.current = onError

  useEffect(() => {
    if (!url) return

    const token = getToken()
    const abortController = new AbortController()
    abortControllerRef.current = abortController

    const connect = async () => {
      try {
        const response = await fetch(url, {
          headers: token ? { 'Authorization': `Bearer ${token}` } : {},
          signal: abortController.signal,
        })

        if (!response.ok) {
          let detail = `HTTP ${response.status}`
          try {
            const errBody = await response.json()
            if (errBody.detail) detail = errBody.detail
          } catch (err) { console.warn('SSE error body parse failed:', err) }
          throw new Error(detail)
        }

        setIsConnected(true)
        const reader = response.body?.getReader()
        if (!reader) throw new Error('No response body')

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()

          if (value) {
            buffer += decoder.decode(value, { stream: true })
          }

          // On stream end, flush decoder and process any remaining buffer
          // before breaking — otherwise partial final SSE events (e.g., the
          // last "AI正在生成..." characters and the completion signal) are
          // silently dropped, leaving the UI stuck on "generating".
          if (done) {
            // Flush TextDecoder internal state (final flush, not streaming)
            buffer += decoder.decode()

            if (buffer) {
              const finalLines = buffer.split('\n')
              for (const line of finalLines) {
                const trimmed = line.trim()
                if (trimmed.startsWith('data: ')) {
                  try {
                    const dataStr = trimmed.slice(6)
                    const parsed = JSON.parse(dataStr)
                    onMessageRef.current(parsed as PipelineEvent)
                  } catch (e) {
                    console.warn('SSE final parse failed:', e)
                    // Final parse failure — silently skip malformed data
                  }
                }
              }
            }
            break
          }

          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const dataStr = line.slice(6)
                const parsed = JSON.parse(dataStr)
                onMessageRef.current(parsed as PipelineEvent)
              } catch (e) {
                console.warn('SSE line parse failed:', e)
              }
            }
          }
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        setIsConnected(false)
        onErrorRef.current(err instanceof Error ? err : new Error('SSE 连接错误'))
      }
    }

    connect()

    return () => {
      abortController.abort()
      abortControllerRef.current = null
      setIsConnected(false)
    }
  }, [url])

  const disconnect = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      setIsConnected(false)
    }
  }, [])

  return { isConnected, disconnect }
}
