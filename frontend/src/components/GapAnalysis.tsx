import React, { useEffect, useRef, useState } from 'react';
import {
  getGapAnalysis,
  getClusterReport,
  planGaps,
  type GapAnalysisResult,
  type CardScore,
  type ClusterInfo,
  type ClusterReport,
  type GapPlan,
} from '../api/gapAnalysis';
import './GapAnalysis.css';

const STATUS_COLORS: Record<string, string> = {
  healthy: '#10b981',
  weak: '#f97316',
  fragmented: '#f59e0b',
  'weak+fragmented': '#ef4444',
};

// 绿(低 gap/高分) → 红(高 gap/低分)
const lerpColor = (t: number): string => {
  const clamped = Math.max(0, Math.min(1, t));
  const r = Math.round(16 + (239 - 16) * clamped);
  const g = Math.round(185 + (68 - 185) * clamped);
  const b = Math.round(129 + (68 - 129) * clamped);
  return `rgb(${r}, ${g}, ${b})`;
};

const sortClusters = (a: ClusterInfo, b: ClusterInfo): number => {
  const aWeak = a.status !== 'healthy' ? 0 : 1;
  const bWeak = b.status !== 'healthy' ? 0 : 1;
  if (aWeak !== bWeak) return aWeak - bWeak;
  return b.priority - a.priority;
};

const DIM_COLS: { key: string; label: string; color: string; get: (c: CardScore) => number }[] = [
  { key: 'gap', label: 'gap', color: '#ef4444', get: (c) => c.gap_score },
  { key: 'structure', label: '结构', color: '#3b82f6', get: (c) => c.structure_score },
  { key: 'semantic', label: '语义', color: '#8b5cf6', get: (c) => c.semantic_score },
  { key: 'graph', label: '图论', color: '#f59e0b', get: (c) => c.graph_score },
  { key: 'confidence', label: '置信度', color: '#14b8a6', get: (c) => c.confidence_score },
];

interface Props {
  sessionId: string | null;
  onClose: () => void;
  onOpenAgent?: (prompt: string) => void;
}

const GapAnalysis: React.FC<Props> = ({ sessionId, onClose, onOpenAgent }) => {
  const [analysis, setAnalysis] = useState<GapAnalysisResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [gapThreshold, setGapThreshold] = useState(0.5);
  const [closing, setClosing] = useState(false);
  const [animating, setAnimating] = useState(false);

  const [tab, setTab] = useState<'cards' | 'clusters'>('cards');
  const [report, setReport] = useState<ClusterReport | null>(null);
  const [clusterLoading, setClusterLoading] = useState(false);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [plans, setPlans] = useState<Record<number, GapPlan>>({});
  const [planning, setPlanning] = useState<Set<number>>(new Set());
  const [planError, setPlanError] = useState<Record<number, string>>({});
  const [copied, setCopied] = useState<string | null>(null);
  const autoPlannedRef = useRef(false);
  const [hlCluster, setHlCluster] = useState<number | null>(null);
  const hlTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setAnimating(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    getGapAnalysis(sessionId, gapThreshold)
      .then(setAnalysis)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [sessionId, gapThreshold]);

  useEffect(() => {
    if (!sessionId || tab !== 'clusters' || report !== null || clusterLoading) return;
    setClusterLoading(true);
    setClusterError(null);
    getClusterReport(sessionId)
      .then(setReport)
      .catch((err) => setClusterError(err.message))
      .finally(() => setClusterLoading(false));
  }, [sessionId, tab, report, clusterLoading]);

  const handleClose = () => {
    setClosing(true);
    setTimeout(onClose, 400);
  };

  const handleStartGapDriven = () => {
    if (!sessionId || !analysis) return;

    const confirmed = window.confirm(
      `即将委托 AI 助手改进薄弱卡片：\n\n` +
      `• 未过目标值卡片：${analysis.weakest_count} 张\n` +
      `• 目标阈值：gap_score ≥ ${gapThreshold}\n\n` +
      `AI 助手会逐一评估每张卡片，自主决定：\n` +
      `  - 内容不足 → 刷新卡片\n` +
      `  - 需要扩展 → 生成子卡片\n\n` +
      `是否继续？`
    );

    if (!confirmed) return;

    const cardList = (analysis.top_weakest.length > 0
      ? analysis.top_weakest
      : analysis.top_best
    ).slice(0, 5);

    const lines = cardList.map(c => {
      const gd = c.graph_dimensions;
      const graphPart = gd ? `, 介数=${gd.betweenness.toFixed(3)}, 聚集=${gd.clustering.toFixed(3)}, 社区=${gd.community_size}` : '';
      return `- 「${c.title}」(gap=${c.gap_score.toFixed(2)}, struct=${c.structure_score.toFixed(2)}, sem=${c.semantic_score.toFixed(2)}, graph=${c.graph_score.toFixed(2)}, conf=${c.confidence_score.toFixed(2)}${graphPart})`;
    });

    const prompt = [
      `知识库共 ${analysis.total} 张卡片，当前薄弱比例 ${(analysis.weakest_ratio * 100).toFixed(0)}%。`,
      '以下是需要改进的卡片：',
      ...lines,
      '',
      '请依次分析每张卡片的薄弱原因，使用以下策略改进：',
      '- structure_score 低 → 用 search_by_keyword 搜索卡片标题补充内容',
      '- semantic_score 低 → 用 expand_from_card 生成子卡片扩展知识面',
      '- graph_score(介数/聚集/社区) 低 → 用 expand_from_card 建立更多主题关联',
      '每次改进后重新调用 assess_card_quality 确认效果，直到薄弱比例降到可接受水平。',
    ].join('\n');

    onOpenAgent?.(prompt);
  };

  useEffect(() => {
    if (!report || tab !== 'clusters' || autoPlannedRef.current) return;
    autoPlannedRef.current = true;
    report.clusters
      .filter((c) => c.status !== 'healthy')
      .forEach((c) => handlePlanGaps(c.cluster_id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report, tab]);

  const handlePlanGaps = async (clusterId: number) => {
    if (!sessionId) return;
    setPlanning((prev) => new Set(prev).add(clusterId));
    setPlanError((prev) => ({ ...prev, [clusterId]: '' }));
    try {
      const plan = await planGaps(sessionId, clusterId);
      setPlans((prev) => ({ ...prev, [clusterId]: plan }));
    } catch (err) {
      setPlanError((prev) => ({ ...prev, [clusterId]: (err as Error).message }));
    } finally {
      setPlanning((prev) => {
        const next = new Set(prev);
        next.delete(clusterId);
        return next;
      });
    }
  };

  const copyKeyword = async (keyword: string) => {
    try {
      await navigator.clipboard.writeText(keyword);
      setCopied(keyword);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // 剪贴板不可用时静默失败
    }
  };

  const focusCluster = (clusterId: number) => {
    document.getElementById(`cluster-card-${clusterId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setHlCluster(clusterId);
    if (hlTimerRef.current !== null) window.clearTimeout(hlTimerRef.current);
    hlTimerRef.current = window.setTimeout(() => setHlCluster(null), 1800);
  };

  const STATUS_META: Record<string, { label: string; css: string }> = {
    healthy: { label: '健康', css: 'healthy' },
    weak: { label: '薄弱', css: 'weak' },
    fragmented: { label: '碎片化', css: 'fragmented' },
    'weak+fragmented': { label: '薄弱+碎片化', css: 'weak' },
  };

  const renderClusterView = (rep: ClusterReport) => {
    const sorted = [...rep.clusters].sort(sortClusters);

    return (
      <div className="cluster-view">
        <div className="cluster-stats">
          <div className="gap-stat-item">
            <div className="gap-stat-value">{rep.n_cards}</div>
            <div className="gap-stat-label">卡片总数</div>
          </div>
          <div className="gap-stat-item">
            <div className="gap-stat-value">{rep.n_clusters}</div>
            <div className="gap-stat-label">簇数量</div>
          </div>
          <div className="gap-stat-item">
            <div className="gap-stat-value gap-stat-value--warn">{rep.n_undercovered}</div>
            <div className="gap-stat-label">覆盖不足卡数</div>
          </div>
          <div className="gap-stat-item">
            <div className="gap-stat-value">
              {rep.median_avg_gap !== null ? rep.median_avg_gap.toFixed(3) : '—'}
            </div>
            <div className="gap-stat-label">簇间 gap 中位数</div>
          </div>
        </div>

        {renderDonut(rep)}
        {rep.clusters.length > 0 && renderClusterBars(rep)}

        <div className="cluster-section">
          <h4>📦 簇列表（薄弱优先）</h4>
          <div className="cluster-list">
            {sorted.map((c) => {
              const meta = STATUS_META[c.status] || { label: c.status, css: 'weak' };
              const err = planError[c.cluster_id];
              return (
                <div
                  key={c.cluster_id}
                  id={`cluster-card-${c.cluster_id}`}
                  className={`cluster-card${c.status === 'healthy' ? ' cluster-card--healthy' : ' cluster-card--weak'}${hlCluster === c.cluster_id ? ' gap-cluster-hl' : ''}`}
                >
                  <div className="cluster-card-head">
                    <span className={`cluster-status cluster-status--${meta.css}`}>{meta.label}</span>
                    {c.status !== 'healthy' && <span className="cluster-flag">需补强</span>}
                    <span className="cluster-id">#{c.cluster_id}</span>
                  </div>

                  <div className="cluster-metrics">
                    <span className="gap-badge" data-type="gap">avg_gap {(c.avg_gap * 100).toFixed(0)}%</span>
                    <span className="gap-badge" data-type="gap">worst {(c.worst_gap * 100).toFixed(0)}%</span>
                    <span className="gap-badge" data-type="structure">size {c.size}</span>
                    <span className="gap-badge" data-type="confidence">
                      compact {c.compactness !== null ? c.compactness.toFixed(2) : '—'}
                    </span>
                    <span className="gap-badge" data-type="graph">
                      链接密度 {c.link_density !== null ? c.link_density.toFixed(2) : '—'}
                      {c.link_density_low ? ' (低)' : ''}
                    </span>
                  </div>

                  {(c.dims.content < 0.5 || c.dims.sources < 0.5 || c.dims.links < 0.5) && (
                    <div className="cluster-dims">
                      {c.dims.content < 0.5 && <span className="cluster-dim">内容短</span>}
                      {c.dims.sources < 0.5 && <span className="cluster-dim">缺来源</span>}
                      {c.dims.links < 0.5 && <span className="cluster-dim">缺链接</span>}
                    </div>
                  )}

                  <div className="cluster-titles">
                    {c.titles.length > 0 ? `代表卡：${c.titles.slice(0, 3).join('、')}` : '（无代表卡标题）'}
                  </div>

                  <button
                    className="cluster-plan-btn"
                    disabled={planning.size > 0}
                    onClick={() => handlePlanGaps(c.cluster_id)}
                  >
                    {planning.has(c.cluster_id) ? '⏳ LLM 盘点缺失主题中...' : '🤖 生成补缺建议'}
                  </button>

                  {err && (
                    <div className="gap-error">
                      <span>⚠️ {err}</span>
                      <button onClick={() => setPlanError((prev) => ({ ...prev, [c.cluster_id]: '' }))}>关闭</button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="cluster-section">
          <h4>🧠 未覆盖主题（LLM 盘点）</h4>
          {renderUndercovered(report)}
        </div>
      </div>
    );
  };

  const renderUndercovered = (rep: ClusterReport) => {
    const weakClusters = rep.clusters.filter((c) => c.status !== 'healthy');
    if (weakClusters.length === 0) {
      return <div className="gap-all-good">✅ 无未覆盖主题</div>;
    }

    const attempted = new Set<number>([
      ...Object.keys(plans).map(Number),
      ...Object.keys(planError).map(Number),
    ]);
    const allDone = weakClusters.every((c) => attempted.has(c.cluster_id));

    if (!allDone) {
      return (
        <div className="gap-loading">
          <div className="gap-spinner" />
          <span>LLM 盘点缺失主题中...</span>
        </div>
      );
    }

    const allGaps = Object.values(plans).flatMap((p) =>
      p.gaps.map((g) => ({ domain: p.domain, ...g })),
    );

    if (allGaps.length === 0) {
      return <div className="gap-all-good">✅ LLM 盘点未发现缺失子主题</div>;
    }

    return (
      <ul className="cluster-plan-gaps">
        {allGaps.map((g, i) => (
          <li key={i} className="cluster-plan-item">
            <span className="cluster-plan-subtopic">{g.subtopic}</span>
            <button
              className={`cluster-keyword${copied === g.keyword ? ' cluster-keyword--copied' : ''}`}
              onClick={() => copyKeyword(g.keyword)}
              title="点击复制关键词"
            >
              {copied === g.keyword ? '✓ 已复制' : g.keyword}
            </button>
          </li>
        ))}
      </ul>
    );
  };

  const renderScoreBar = (score: number, label: string, color: string) => (
    <div className="gap-score-bar">
      <span className="gap-score-label">{label}</span>
      <div className="gap-score-track">
        <div
          className="gap-score-fill"
          style={{ width: `${score * 100}%`, background: color }}
        />
      </div>
      <span className="gap-score-value">{(score * 100).toFixed(1)}%</span>
    </div>
  );

  const renderHistogram = (cards: CardScore[]) => {
    const bins = new Array(10).fill(0);
    cards.forEach((c) => {
      const idx = Math.min(9, Math.max(0, Math.floor(c.gap_score * 10)));
      bins[idx] += 1;
    });
    const max = Math.max(1, ...bins);
    return (
      <div className="gap-viz-section">
        <h4>📊 gap 分布直方图</h4>
        <div className="gap-viz-scroll">
          <div
            className="gap-histogram"
            role="img"
            aria-label={`gap 分布直方图，共 ${cards.length} 张卡片，10 个区间，目标阈值 ${(gapThreshold * 100).toFixed(0)}%`}
          >
            <div className="gap-hist-plot">
              <div className="gap-hist-threshold" style={{ left: `${gapThreshold * 100}%` }}>
                <span>{`阈值 ${(gapThreshold * 100).toFixed(0)}%`}</span>
              </div>
              {bins.map((count, i) => {
                const low = i / 10;
                const high = (i + 1) / 10;
                return (
                  <div key={i} className="gap-hist-bin" title={`gap ${low.toFixed(1)}–${high.toFixed(1)}：${count} 张`}>
                    {count > 0 && <span className="gap-hist-count">{count}</span>}
                    <div
                      className="gap-hist-bar"
                      style={{
                        height: `${count === 0 ? 0 : Math.max(4, (count / max) * 78)}%`,
                        background: lerpColor(i / 9),
                      }}
                    />
                  </div>
                );
              })}
            </div>
            <div className="gap-hist-axis" aria-hidden="true">
              <span>0.0</span>
              <span>0.5</span>
              <span>1.0</span>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const renderHeatmap = (cards: CardScore[]) => {
    const rows = cards.slice(0, 30);
    return (
      <div className="gap-viz-section">
        <h4>🔥 卡片 × 维度热图（最弱 {rows.length} 张）</h4>
        <div className="gap-viz-legend">
          <span className="gap-viz-legend-label">低</span>
          <span className="gap-viz-legend-ramp" aria-hidden="true" />
          <span className="gap-viz-legend-label">高</span>
        </div>
        <div className="gap-viz-scroll">
          <div className="gap-heatmap" role="table" aria-label="最弱卡片各维度得分热图">
            <div className="gap-heatmap-corner" role="columnheader">卡片 \ 维度</div>
            {DIM_COLS.map((d) => (
              <div key={d.key} className="gap-heatmap-head" role="columnheader" style={{ color: d.color }}>{d.label}</div>
            ))}
            {rows.map((c) => (
              <React.Fragment key={c.card_id}>
                <div className="gap-heatmap-label" role="rowheader" title={c.title}>{c.title}</div>
                {DIM_COLS.map((d) => (
                  <div
                    key={d.key}
                    className="gap-heatmap-cell"
                    role="cell"
                    style={{ background: lerpColor(d.get(c)) }}
                    title={`${d.label} ${(d.get(c) * 100).toFixed(0)}%`}
                  >
                    {(d.get(c) * 100).toFixed(0)}%
                  </div>
                ))}
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderDonut = (rep: ClusterReport) => {
    if (rep.n_clusters === 0) {
      return (
        <div className="gap-viz-section">
          <h4>🍩 簇状态分布</h4>
          <div className="gap-viz-empty">暂无簇</div>
        </div>
      );
    }
    const counts: Record<string, number> = {};
    rep.clusters.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1; });
    const segs = [
      { status: 'healthy', color: STATUS_COLORS.healthy },
      { status: 'weak', color: STATUS_COLORS.weak },
      { status: 'fragmented', color: STATUS_COLORS.fragmented },
      { status: 'weak+fragmented', color: STATUS_COLORS['weak+fragmented'] },
    ]
      .map((s) => ({
        ...s,
        label: (STATUS_META[s.status] || { label: s.status }).label,
        count: counts[s.status] || 0,
      }))
      .filter((s) => s.count > 0);
    if (rep.n_undercovered > 0) {
      segs.push({ status: 'undercovered', label: '覆盖不足', count: rep.n_undercovered, color: '#8b5cf6' });
    }
    const total = segs.reduce((sum, s) => sum + s.count, 0);
    const R = 54;
    const C = 2 * Math.PI * R;
    let offset = 0;
    return (
      <div className="gap-viz-section">
        <h4>🍩 簇状态分布</h4>
        <div className="gap-donut-wrap">
          <svg
            className="gap-donut"
            viewBox="0 0 140 140"
            role="img"
            aria-label={`簇状态分布，共 ${rep.n_clusters} 个簇`}
          >
            <circle cx="70" cy="70" r={R} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="18" />
            {segs.map((s) => {
              const frac = s.count / total;
              const el = (
                <circle
                  key={s.status}
                  cx="70"
                  cy="70"
                  r={R}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="18"
                  strokeDasharray={`${frac * C} ${C}`}
                  strokeDashoffset={-offset * C}
                  transform="rotate(-90 70 70)"
                >
                  <title>{`${s.label}：${s.count}`}</title>
                </circle>
              );
              offset += frac;
              return el;
            })}
            <text x="70" y="68" textAnchor="middle" className="gap-donut-num">{rep.n_clusters}</text>
            <text x="70" y="86" textAnchor="middle" className="gap-donut-cap">簇</text>
          </svg>
          <div className="gap-donut-legend">
            {segs.map((s) => (
              <div key={s.status} className="gap-donut-legend-item">
                <span className="gap-donut-swatch" style={{ background: s.color }} />
                <span className="gap-donut-legend-label">{s.label}</span>
                <span className="gap-donut-legend-count">{s.count}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderClusterBars = (rep: ClusterReport) => {
    const sorted = [...rep.clusters].sort(sortClusters);
    return (
      <div className="gap-viz-section">
        <h4>📊 簇质量对比</h4>
        <div className="gap-viz-scroll">
          <div className="gap-cluster-bars">
            {sorted.map((c) => (
              <div
                key={c.cluster_id}
                className="gap-bar-row"
                role="button"
                tabIndex={0}
                title={`点击定位到簇 #${c.cluster_id}`}
                aria-label={`簇 ${c.cluster_id}，平均 gap ${(c.avg_gap * 100).toFixed(0)}%`}
                onClick={() => focusCluster(c.cluster_id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    focusCluster(c.cluster_id);
                  }
                }}
              >
                <div className="gap-bar-track">
                  <div
                    className="gap-bar-fill"
                    style={{
                      width: `${(c.avg_gap * 100).toFixed(1)}%`,
                      background: STATUS_COLORS[c.status] || STATUS_COLORS.weak,
                    }}
                  />
                </div>
                <span className="gap-bar-label">簇#{c.cluster_id} size={c.size} avg_gap={(c.avg_gap * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderCardItem = (card: CardScore, isWeakest: boolean) => {
    const gd = card.graph_dimensions;
    const graphLabel = gd
      ? `B:${(gd.betweenness * 100).toFixed(0)} C:${(gd.clustering * 100).toFixed(0)} S:${gd.community_size}`
      : null;
    return (
      <div key={card.card_id} className={`gap-card-item ${isWeakest ? 'gap-card-item--weak' : 'gap-card-item--good'}`}>
        <div className="gap-card-title">{card.title}</div>
        <div className="gap-card-scores">
          <span className="gap-badge" data-type="gap">
            gap: {(card.gap_score * 100).toFixed(0)}%
          </span>
          <span className="gap-badge" data-type="structure">
            S: {(card.structure_score * 100).toFixed(0)}%
          </span>
          <span className="gap-badge" data-type="semantic">
            V: {(card.semantic_score * 100).toFixed(0)}%
          </span>
          <span className="gap-badge" data-type="graph">
            G: {(card.graph_score * 100).toFixed(0)}%
          </span>
          <span className="gap-badge" data-type="confidence">
            C: {(card.confidence_score * 100).toFixed(0)}%
          </span>
        </div>
        {graphLabel && (
          <div className="gap-card-graph-dims">
            <span className="gap-graph-detail">介数={gd.betweenness.toFixed(3)}</span>
            <span className="gap-graph-detail">聚集={gd.clustering.toFixed(3)}</span>
            <span className="gap-graph-detail">社区={gd.community_size}</span>
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      className={`gap-analysis-overlay${animating && !closing ? ' gap-analysis--open' : ''}${closing ? ' gap-analysis--closing' : ''}`}
      onClick={handleClose}
    >
      <div className="gap-analysis-panel" onClick={(e) => e.stopPropagation()}>
        <div className="gap-analysis-header">
          <span className="gap-analysis-title">📊 知识库质量分析</span>
          <button className="gap-analysis-close" onClick={handleClose}>✕</button>
        </div>

        <div className="gap-analysis-content">
          <div className="gap-tabs">
            <button
              className={`gap-tab${tab === 'cards' ? ' gap-tab--active' : ''}`}
              onClick={() => setTab('cards')}
            >
              单卡排行
            </button>
            <button
              className={`gap-tab${tab === 'clusters' ? ' gap-tab--active' : ''}`}
              onClick={() => setTab('clusters')}
            >
              簇级健康度
            </button>
          </div>

          {tab === 'cards' && (
            <>
          {loading && (
            <div className="gap-loading">
              <div className="gap-spinner" />
              <span>正在分析卡片质量...</span>
            </div>
          )}

          {error && (
            <div className="gap-error">
              <span>⚠️ {error}</span>
              <button onClick={() => setError(null)}>关闭</button>
            </div>
          )}

          {analysis && !loading && (
            <>
              <div className="gap-stats">
                <div className="gap-stat-item">
                  <div className="gap-stat-value">{analysis.total}</div>
                  <div className="gap-stat-label">卡片总数</div>
                </div>
                <div className="gap-stat-item">
                  <div className="gap-stat-value gap-stat-value--warn">
                    {analysis.weakest_count}
                  </div>
                  <div className="gap-stat-label">未过目标值</div>
                </div>
                <div className="gap-stat-item">
                  <div className="gap-stat-value">
                    {(analysis.weakest_ratio * 100).toFixed(1)}%
                  </div>
                  <div className="gap-stat-label">薄弱比例</div>
                </div>
              </div>

              <div className="gap-averages">
                {renderScoreBar(1 - analysis.avg_gap_score, '平均质量', '#10b981')}
                {renderScoreBar(analysis.avg_structure_score, '平均结构分', '#3b82f6')}
                {renderScoreBar(analysis.avg_semantic_score, '平均语义分', '#8b5cf6')}
                {renderScoreBar(analysis.avg_graph_score, '平均图论分', '#f59e0b')}
                {renderScoreBar(analysis.avg_confidence_score, '平均置信度', '#14b8a6')}
              </div>


              <div className="gap-threshold-control">
                <label>
                  目标阈值：
                  <input
                    type="range"
                    min="0.3"
                    max="0.8"
                    step="0.05"
                    value={gapThreshold}
                    onChange={(e) => setGapThreshold(parseFloat(e.target.value))}
                  />
                  <span>{(gapThreshold * 100).toFixed(0)}%</span>
                </label>
              </div>

              {analysis.scores && analysis.scores.length > 0 && renderHistogram(analysis.scores)}
              {analysis.scores && analysis.scores.length > 1 && renderHeatmap(analysis.scores)}

              {analysis.top_weakest.length > 0 && (
                <div className="gap-card-section">
                  <h4>🔴 最薄弱卡片（需要改进）</h4>
                  <div className="gap-card-list">
                    {analysis.top_weakest.map((card) => renderCardItem(card, true))}
                  </div>
                </div>
              )}

              {analysis.top_best.length > 0 && (
                <div className="gap-card-section">
                  <h4>🟢 最优质卡片</h4>
                  <div className="gap-card-list">
                    {analysis.top_best.map((card) => renderCardItem(card, false))}
                  </div>
                </div>
              )}

              {analysis.weakest_count > 0 && (
                <div className="gap-actions">
                  <button
                    className="gap-btn gap-btn--primary"
                    onClick={handleStartGapDriven}
                  >
                    {`🤖 委托 AI 助手改进 ${analysis.weakest_count} 张薄弱卡片`}
                  </button>
                  <div className="gap-action-hint">
                    AI 助手将自主评估每张卡片，决定刷新或扩展策略
                  </div>
                </div>
              )}

              {analysis.weakest_count === 0 && (
                <div className="gap-all-good">
                  ✅ 所有卡片质量达标！
                </div>
              )}
            </>
            )}
          </>
          )}

          {tab === 'clusters' && (
            <>
              {clusterLoading && (
                <div className="gap-loading">
                  <div className="gap-spinner" />
                  <span>正在分析簇级健康度...</span>
                </div>
              )}

              {clusterError && (
                <div className="gap-error">
                  <span>⚠️ {clusterError}</span>
                  <button onClick={() => setClusterError(null)}>关闭</button>
                </div>
              )}

              {report && !clusterLoading && renderClusterView(report)}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default GapAnalysis;
