import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { beforeAll, afterAll, test, expect, vi, describe, beforeEach } from 'vitest'
import { StreamingOutput } from '../components/StreamingOutput'
import type { Card } from '../types/card'

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  onopen?: () => void
  onmessage?: (ev: MessageEvent) => void
  onerror?: (ev: Event) => void
  closed = false
  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
    setTimeout(() => {
      this.onopen?.()
    }, 0)
  }
  close() { this.closed = true }
  static emitToLast(data: any) {
    const inst = MockEventSource.instances[MockEventSource.instances.length - 1]
    if (inst) {
      const event = { data: JSON.stringify(data) } as any
      inst.onmessage?.(event)
    }
  }
  static reset() {
    MockEventSource.instances = []
  }
}

beforeAll(() => {
  (global as any).EventSource = MockEventSource
})

afterAll(() => {
  delete (global as any).EventSource
})

type CardSample = Card

function makeCard(id: string, title: string, content: string): CardSample {
  return { 
    id, 
    title, 
    content, 
    metadata: { source: 'test' }, 
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString()
  }
}

describe('StreamingOutput', () => {
  beforeEach(() => {
    MockEventSource.reset()
  })

  test('renders and displays cards from stream', async () => {
    const onComplete = vi.fn()
    const onError = vi.fn()
    const onCancel = vi.fn()
    const onRemove = vi.fn()
    render(<StreamingOutput 
      streamUrl="/api/pipeline/collect" 
      searchId="test-search-1"
      keyword="test keyword"
      onComplete={onComplete} 
      onError={onError}
      onCancel={onCancel}
      onRemove={onRemove}
    />)

    await act(async () => {
      MockEventSource.emitToLast({ type: 'card', data: makeCard('c1', 'Card 1', 'Some content for card 1') })
      await new Promise(r => setTimeout(r, 0))
    })
    expect(await screen.findByText('Card 1')).toBeTruthy()
  })

  test('handles error events and displays error', async () => {
    const onComplete = vi.fn()
    const onError = vi.fn()
    const onCancel = vi.fn()
    const onRemove = vi.fn()
    render(<StreamingOutput 
      streamUrl="/api/pipeline/collect" 
      searchId="test-search-2"
      keyword="test keyword"
      onComplete={onComplete} 
      onError={onError}
      onCancel={onCancel}
      onRemove={onRemove}
    />)

    await act(async () => {
      MockEventSource.emitToLast({ type: 'error', data: { message: 'Something went wrong' } })
      await new Promise(r => setTimeout(r, 0))
    })
    const errorEl = await screen.findByTestId('error')
    expect(errorEl).toBeTruthy()
    expect(errorEl.textContent).toContain('Something went wrong')
  })

  test('cancel button changes text to Cancelled when clicked', async () => {
    const onComplete = vi.fn()
    const onError = vi.fn()
    const onCancel = vi.fn()
    const onRemove = vi.fn()
    render(<StreamingOutput 
      streamUrl="/api/pipeline/collect" 
      searchId="test-search-3"
      keyword="test keyword"
      onComplete={onComplete} 
      onError={onError}
      onCancel={onCancel}
      onRemove={onRemove}
    />)
    
    const btn = await screen.findByRole('button', { name: /Cancel streaming/i })
    expect(btn.textContent).toBe('Cancel')
    
    await act(async () => {
      fireEvent.click(btn)
      await new Promise(r => setTimeout(r, 10))
    })
    
    expect(btn.textContent).toBe('Cancelled')
  })
})
