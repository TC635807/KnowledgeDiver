import { authFetchWithToken, getToken } from './auth';

export interface CardScore {
  card_id: string;
  title: string;
  gap_score: number;
  quality_score: number;
  structure_score: number;
  semantic_score: number;
  graph_score: number;
  confidence_score: number;
  dimensions: {
    content: number;
    sources: number;
    links: number;
  };
  graph_dimensions: {
    betweenness: number;
    clustering: number;
    community_size: number;
  };
}

export interface GapAnalysisResult {
  total: number;
  avg_gap_score: number;
  avg_structure_score: number;
  avg_semantic_score: number;
  avg_graph_score: number;
  avg_confidence_score: number;
  weakest_count: number;
  weakest_ratio: number;
  top_weakest: CardScore[];
  top_best: CardScore[];
  scores: CardScore[];
}

export async function getGapAnalysis(
  sessionId: string,
  gapThreshold: number = 0.5,
): Promise<GapAnalysisResult> {
  const params = new URLSearchParams({
    session_id: sessionId,
    gap_threshold: gapThreshold.toString(),
  });
  return authFetchWithToken<GapAnalysisResult>(`/api/pipeline/gap-analysis?${params}`);
}

export interface ClusterDims {
  content: number;
  sources: number;
  links: number;
}

export interface ClusterInfo {
  cluster_id: number;
  size: number;
  card_ids: string[];
  titles: string[];
  avg_gap: number;
  worst_gap: number;
  compactness: number | null;
  link_density: number | null;
  link_density_low: boolean;
  dims: ClusterDims;
  status: string; // "healthy" | "weak" | "fragmented" | "weak+fragmented"
  priority: number;
}

export interface UndercoveredCard {
  card_id: string;
  title: string;
  gap_score: number;
  cluster_size: number;
}

export interface ClusterReport {
  n_cards: number;
  n_clusters: number;
  n_undercovered: number;
  median_avg_gap: number | null;
  clusters: ClusterInfo[];
  undercovered: UndercoveredCard[];
}

export interface GapPlanItem {
  subtopic: string;
  keyword: string;
}

export interface GapPlan {
  cluster_id: number;
  domain: string;
  gaps: GapPlanItem[];
}

export async function getClusterReport(sessionId: string): Promise<ClusterReport> {
  const params = new URLSearchParams({ session_id: sessionId });
  return authFetchWithToken<ClusterReport>(`/api/pipeline/cluster-report?${params}`);
}

export async function planGaps(sessionId: string, clusterId?: number): Promise<GapPlan> {
  const params = new URLSearchParams({ session_id: sessionId });
  if (clusterId !== undefined) params.set('cluster_id', clusterId.toString());
  return authFetchWithToken<GapPlan>(`/api/pipeline/plan-gaps?${params}`, { method: 'POST' });
}

/**
 * 启动 Gap-Driven 探索任务（非阻塞）。
 *
 * 连接到 SSE 端点，收到第一个 status 事件（含 task_id）后立即返回，
 * 不等待流水线完成。调用方通过返回的 taskId 订阅 /api/tasks/{id}/stream
 * 观看实时进度。后端后台任务（asyncio.create_task）独立运行，
 * 不受前端断开 SSE 连接影响。
 */
export async function startGapDrivenExploration(
  keyword: string,
  sessionId: string,
  options: {
    maxSources?: number;
    gapThreshold?: number;
    structureThreshold?: number;
    maxIterations?: number;
    searchLevel?: string;
    searchProvider?: string;
  } = {},
): Promise<string> {
  const token = getToken();
  if (!token) throw new Error('未认证');

  const params = new URLSearchParams({
    keyword,
    session_id: sessionId,
    max_sources: (options.maxSources || 5).toString(),
    gap_threshold: (options.gapThreshold || 0.5).toString(),
    structure_threshold: (options.structureThreshold || 0.5).toString(),
    max_iterations: (options.maxIterations || 3).toString(),
    search_level: options.searchLevel || 'default',
    search_provider: options.searchProvider || 'free',
  });

  const controller = new AbortController();
  const response = await fetch(`/api/pipeline/gap-driven?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: controller.signal,
  });

  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(body || `HTTP ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    // 只读到第一个 status 事件获取 taskId，不等待流水线完成
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data: ')) continue;

        try {
          const event = JSON.parse(trimmed.slice(6));
          if (event.type === 'status' && event.data?.task_id) {
            return event.data.task_id;
          }
        } catch {
          // skip unparseable events
        }
      }
    }
  } finally {
    // 断开 SSE 连接释放浏览器资源，后端后台任务（asyncio.create_task）独立继续
    controller.abort();
  }

  throw new Error('未获取到任务 ID');
}
