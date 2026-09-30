import React, { useEffect, useMemo, useState, useRef, useCallback } from 'react'
import { useSSE } from '../hooks/useSSE'
import type { Card } from '../types/card'
import type { PipelineEvent, PipelineProgress } from '../types/pipeline'
import { ProgressIndicator } from './ProgressIndicator'
import { GeneratedCard } from './GeneratedCard'
import './StreamingOutput.css'
import { PipelineStage } from '../types/pipeline'

type StreamingOutputProps = {
  searchId: string
  keyword: string
  streamUrl: string
  onComplete: (searchId: string, cards: Card[]) => void
  onError: (searchId: string, error: Error) => void
  onCancel: (searchId: string) => void
  onRemove: (searchId: string) => void
  onTaskIdReceived?: (tempId: string, realTaskId: string) => void
}

type StageState = {
  stage: PipelineStage
  progress: number
  status: 'pending' | 'running' | 'complete' | 'error'
  message?: string
}

export const StreamingOutput: React.FC<StreamingOutputProps> = ({ 
  searchId, 
  keyword, 
  streamUrl, 
  onComplete, 
  onError, 
  onCancel, 
  onRemove,
  onTaskIdReceived
}) => {
  const [lines, setLines] = useState<string[]>([])
  const [cards, setCards] = useState<Card[]>([])
  const [error, setError] = useState<string | null>(null)
  const [cancelled, setCancelled] = useState<boolean>(false)
  const [aiOutputs, setAiOutputs] = useState<Record<string, string>>({})
  const lastLoggedStageRef = useRef<string>('')
  const lastConsoleMessageRef = useRef<string>('')
  const completedRef = useRef(false)
  const taskIdReceivedRef = useRef(false)
  const isReplayRef = useRef(false)  // 回放已完成任务，不触发自动删除
  const cardsRef = useRef<Card[]>([])
  const [isReplay, setIsReplay] = useState(false)

  const [stages, setStages] = useState<StageState[]>([
    { stage: 'searching', progress: 0, status: 'pending' },
    { stage: 'scraping', progress: 0, status: 'pending' },
    { stage: 'summarizing', progress: 0, status: 'pending' },
    { stage: 'generating', progress: 0, status: 'pending' },
    { stage: 'expanding', progress: 0, status: 'pending' },
  ])

  const handleComplete = useCallback((finalCards: Card[]) => {
    if (completedRef.current) return
    completedRef.current = true
    onComplete(searchId, finalCards)
  }, [onComplete, searchId])

  const stageNameMap: Record<string, string> = {
    'searching': '搜索',
    'scraping': '抓取',
    'summarizing': '总结',
    'generating': '生成',
    'expanding': '扩展',
    'complete': '完成',
    'error': '错误'
  }

  const translateMessage = (message: string): string => {
    let translated = message
    // 常见英文消息的中文翻译
    const translations: Record<string, string> = {
      'Found': '找到',
      'sources': '个来源',
      'Scraped': '抓取',
      'Summarizing': '总结',
      'in parallel': '并行',
      'Extracting related topics': '提取相关主题',
      'Collecting': '收集',
      'related topics': '相关主题',
      'AI正在整理': 'AI正在整理',
      '个来源的信息': '个来源的信息',
      'AI正在生成内容': 'AI正在生成内容',
      'Pipeline complete': '流程完成',
      'Search attempt': '搜索尝试',
      'returned': '返回',
      'results': '个结果',
      'max': '最大',
      'successfully': '成功',
      'out of': '个，共',
      'URLs': '个URL'
    }
    // 按长度降序排序，优先匹配长字符串
    const sortedEntries = Object.entries(translations).sort((a, b) => b[0].length - a[0].length)
    for (const [en, zh] of sortedEntries) {
      translated = translated.replace(new RegExp(en, 'gi'), zh)
    }
    return translated
  }

  const onMessage = useCallback((ev: any) => {
    const event = ev as PipelineEvent
    
    if (event.type === 'status' && onTaskIdReceived && !taskIdReceivedRef.current) {
      const statusData = event.data as { task_id?: string; status?: string }
      if (statusData.task_id) {
        taskIdReceivedRef.current = true
        onTaskIdReceived(searchId, statusData.task_id)
      }
      // 已完成/取消/出错任务的回放：不自动删除
      if (statusData.status === 'completed' || statusData.status === 'cancelled' || statusData.status === 'error') {
        isReplayRef.current = true
        setIsReplay(true)
        if (statusData.status === 'completed') {
          setStages((prev) => prev.map((s) => ({ ...s, progress: 100, status: 'complete' as const })))
        }
      }
    }
    
    if (event.type === 'progress') {
      const p = event.data as PipelineProgress
      // 回放已完成任务时 stages 已在 status handler 中设为 100%，仅跳过进度条更新
      if (!isReplayRef.current) {
        const progressPercent = Math.round(p.progress * 100)
        setStages((prev) => {
          const next = prev.map((s) => {
            if (s.stage === p.stage) {
              const newStatus: StageState['status'] = progressPercent >= 100 ? 'complete' : 'running'
              return { ...s, progress: progressPercent, status: newStatus, message: p.message }
            }
            return s
          })
          return next
        })
      }
      const stageName = stageNameMap[p.stage] || p.stage
      const translatedMessage = translateMessage(p.message)
      const fullMessage = `[${stageName}] ${translatedMessage}`
      
      if (p.stage !== 'generating' && fullMessage !== lastConsoleMessageRef.current) {
        setLines((l) => {
          const next = [...l, fullMessage]
          return next.length > 200 ? next.slice(-200) : next
        })
        lastConsoleMessageRef.current = fullMessage
      }
      
      if (p.ai_output && p.current_item) {
        setAiOutputs((prev) => {
          const next = { ...prev, [p.current_item!]: p.ai_output! }
          const keys = Object.keys(next)
          if (keys.length > 20) {
            keys.slice(0, keys.length - 20).forEach(k => delete next[k])
          }
          return next
        })
      }
      
      if (p.stage === 'complete') {
        // 仅更新进度条为完成状态，不触发 handleComplete
        // 真正的完成由 type='complete' SSE 事件负责
      }
    } else if (event.type === 'complete') {
      if (isReplayRef.current) return  // 回放已完成任务，不触发自动删除
      handleComplete(cardsRef.current)
      setCards([])
      setAiOutputs({})
    } else if (event.type === 'card') {
      const card = event.data as Card
      setCards((c) => {
        const next = [...c, card]
        cardsRef.current = next
        return next
      })
    } else if (event.type === 'error') {
      const err = event.data as Error
      setError(err.message)
      onError(searchId, err)
    }
  }, [onError, handleComplete, searchId, onTaskIdReceived])

  const onErrorWrapper = useCallback((err: Error) => {
    setError(err.message)
    onError(searchId, err)
  }, [onError, searchId])

  const { isConnected, disconnect } = useSSE(streamUrl, onMessage, onErrorWrapper)

  const handleCancel = () => {
    setCancelled(true)
    disconnect()
    onCancel(searchId)
  }

  const generatedCards = cards
  const terminalText = lines.join('\n')

  return (
    <div className="streaming-output" aria-label="流式输出">
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
        marginBottom: 8,
        padding: '8px 12px',
        background: 'var(--bg-tertiary, #1f2937)',
        borderRadius: 4
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: 'var(--accent, #a78bfa)', fontWeight: 500 }}>{keyword}</span>
          {cancelled && <span style={{ color: 'var(--text-secondary, #9ca3af)', fontSize: 12 }}>(已取消)</span>}
          {error && <span style={{ color: '#ef4444', fontSize: 12 }}>(错误)</span>}
        </div>
        <button
          onClick={() => onRemove(searchId)}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-secondary, #9ca3af)',
            cursor: 'pointer',
            fontSize: 16,
            padding: '2px 6px'
          }}
          title="移除此搜索"
        >
          ×
        </button>
      </div>

      <div className="terminal" role="region" aria-label="stream-output" data-testid="terminal-output">
        {terminalText ? terminalText : '连接中...'}
      </div>

      <div className="progress-area" aria-label="pipeline-progress">
        {stages.map((s) => (
          <ProgressIndicator key={s.stage} stage={s.stage} progress={s.progress} status={s.status} />
        ))}
      </div>

      {Object.entries(aiOutputs).length > 0 && (
        <div className="ai-outputs-container" aria-label="ai-streaming-outputs">
          {Object.entries(aiOutputs).map(([topic, output]) => (
            <div className="ai-output-panel" key={topic} aria-label={`ai-streaming-${topic}`}>
              <div className="ai-output-header">AI 生成: {topic}...</div>
              <pre className="ai-output-content">{output}</pre>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="error-line" data-testid="error" role="alert">错误：{error}</div>
      )}

      {generatedCards.length > 0 && (
        <div className="cards-grid" aria-label="generated-cards">
          {generatedCards.map((c) => (
            <GeneratedCard key={c.id} card={c} />
          ))}
        </div>
      )}

      {!cancelled && !error && (
        <button className="cancel-btn" onClick={handleCancel} aria-label={isReplay ? "关闭已完成任务" : "取消流式输出"}>
          {isReplay ? '关闭' : '取消'}
        </button>
      )}
    </div>
  )
}
