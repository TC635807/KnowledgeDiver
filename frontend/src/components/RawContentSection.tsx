import React, { useState } from 'react';
import { fetchCardRaw, RawSource } from '../api/cards';

interface Props {
  cardId: string;
  sessionId: string;
}

/**
 * 「原文全文」折叠区：从 raw_store 拉取卡片来源的原始网页全文。
 * 卡片 content 是信息保全式摘要，原文用于查看完整细节。
 */
const RawContentSection: React.FC<Props> = ({ cardId, sessionId }) => {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState<RawSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (sources !== null) return;
    setLoading(true);
    setError(null);
    try {
      setSources(await fetchCardRaw(cardId, sessionId));
    } catch (e) {
      setError((e as Error).message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  const btnStyle: React.CSSProperties = {
    padding: '6px 14px',
    borderRadius: 6,
    border: '1px solid var(--border, #374151)',
    background: 'transparent',
    color: 'var(--text-secondary, #9ca3af)',
    cursor: 'pointer',
    fontSize: 13,
  };

  return (
    <div style={{ marginTop: 20 }}>
      <button onClick={toggle} style={btnStyle}>
        {open ? '📄 收起原文' : `📄 查看原文全文${sources && sources.length > 0 ? ` (${sources.length})` : ''}`}
      </button>
      {open && (
        <div style={{ marginTop: 10 }}>
          {loading && <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>加载原文中...</div>}
          {error && <div style={{ color: '#f87171', fontSize: 13 }}>❌ {error}</div>}
          {!loading && !error && sources !== null && sources.length === 0 && (
            <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>该卡片没有原始来源记录</div>
          )}
          {sources?.map((s, i) => (
            <div key={s.url} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                [{i + 1}] {s.title || s.url}
                {s.fetched_at ? ` · ${s.fetched_at.slice(0, 16).replace('T', ' ')}` : ''}
              </div>
              <div
                style={{
                  maxHeight: 400,
                  overflow: 'auto',
                  background: 'var(--bg-secondary, #111827)',
                  border: '1px solid var(--border, #374151)',
                  borderRadius: 8,
                  padding: 12,
                  fontSize: 13,
                  lineHeight: 1.7,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  color: 'var(--text, #e5e7eb)',
                }}
              >
                {s.content}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default RawContentSection;
