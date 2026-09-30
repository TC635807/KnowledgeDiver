import React from 'react'
import type { PipelineStage } from '../types/pipeline'

type Props = {
  stage: PipelineStage
  progress: number
  status: 'pending' | 'running' | 'complete' | 'error'
}

const stageLabelMap: Record<string, string> = {
  'searching': '搜索',
  'scraping': '抓取',
  'summarizing': '总结',
  'generating': '生成',
  'expanding': '扩展',
  'complete': '完成',
  'error': '错误'
}

export const ProgressIndicator: React.FC<Props> = ({ stage, progress, status }) => {
  const label = stageLabelMap[stage] || stage

  const stateClass =
    status === 'error' ? 'pi-fill--error' : status === 'complete' ? 'pi-fill--complete' : ''

  return (
    <div className="pi-item" data-testid={`stage-${stage}`}>
      <div className="pi-label" aria-label={`${label} 阶段`}>{label}</div>
      <div className="pi-bar" aria-valuenow={progress} aria-valuemax={100} role="progressbar">
        <div className={`pi-fill ${stateClass}`} style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
      </div>
    </div>
  )
}
