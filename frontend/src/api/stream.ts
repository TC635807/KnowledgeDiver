import { PipelineEvent } from '../types/pipeline'
import type { Card } from '../types/card'

// Start the SSE stream for collection. In a real app this would hit the backend SSE endpoint.
export function startCollection(keyword: string, maxSources: number): EventSource {
  // Build a query string to reflect the requested operation.
  const url = `/api/pipeline/collect?keyword=${encodeURIComponent(keyword)}&max_sources=${encodeURIComponent(maxSources)}`
  return new EventSource(url)
}

// Parse a raw SSE message into a PipelineEvent container.
export function parseSSEMessage(event: MessageEvent): PipelineEvent {
  try {
    const payload = JSON.parse(event.data)
    // If the payload already uses the PipelineEvent shape, pass through
    if (payload && (payload as any).type) {
      return payload as PipelineEvent
    }
    // Otherwise, assume it's a progress payload
    return { type: 'progress', data: payload as any } as PipelineEvent
  } catch (err) {
    // If we can't parse JSON, treat as an error with the message text
    return { type: 'error', data: new Error(String(event.data)) } as PipelineEvent
  }
}
