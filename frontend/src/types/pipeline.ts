import type { Card } from './card'

export type PipelineStage = 'searching' | 'scraping' | 'summarizing' | 'generating' | 'expanding' | 'complete' | 'error';

export interface PipelineProgress {
  stage: PipelineStage;
  message: string;
  progress: number;
  timestamp: string;
  current_item?: string;
  ai_output?: string;
}

export interface PipelineEvent {
  type: 'progress' | 'card' | 'error' | 'complete' | 'status';
  data: PipelineProgress | Card | Error | { status: string; message: string } | { card_count: number };
}

export type SearchTaskStatus = 'pending' | 'running' | 'completed' | 'error' | 'cancelled';

export type TaskType = 'collect' | 'expand' | 'refresh' | 'document' | 'gap_driven';

export interface BackendTask {
  task_id: string;
  task_type: TaskType;
  username: string;
  session_id: string;
  keyword: string;
  params: Record<string, unknown>;
  status: SearchTaskStatus;
  progress: PipelineProgress | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  error: string | null;
  generated_card_ids: string[];
  generated_card_count: number;
}

export interface SearchTask {
  id: string;
  keyword: string;
  streamUrl: string;
  status: SearchTaskStatus;
  createdAt: Date;
  cards: Card[];
  error?: string;
  taskType?: TaskType;
  sessionId?: string;
  realTaskId?: string; // 后端返回的真实 task_id，用于取消/删除操作
}
