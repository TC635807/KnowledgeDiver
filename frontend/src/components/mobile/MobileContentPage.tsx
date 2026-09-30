import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { Card } from '../../types/card';
import { LinksEditor } from '../LinksEditor';
import RawContentSection from '../RawContentSection';
import '../BottomNavBar.css';
import { sanitizeUrl } from '../../utils/markdown';

type MobileContentPageProps = {
  selectedCard: Card | null;
  editingCardId: string | null;
  editingTitle: string;
  editingContent: string;
  editingLinks: string[];
  allCards: Card[];
  sessionId: string;
  onEditTitleChange: (value: string) => void;
  onEditContentChange: (value: string) => void;
  onLinksChange: (links: string[]) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  savingCard: boolean;
};

export const MobileContentPage: React.FC<MobileContentPageProps> = ({
  selectedCard,
  editingCardId,
  editingTitle,
  editingContent,
  editingLinks,
  allCards,
  sessionId,
  onEditTitleChange,
  onEditContentChange,
  onLinksChange,
  onSaveEdit,
  onCancelEdit,
  savingCard,
}) => {
  if (!selectedCard) {
    return (
      <div className="mobile-page">
        <div className="mobile-page__header">
          <h2 className="mobile-page__title">内容</h2>
        </div>
        <div className="mobile-page__empty">
          <div className="mobile-page__empty-icon">📄</div>
          <div>选择一张卡片查看内容</div>
        </div>
      </div>
    );
  }

  const isEditing = editingCardId === selectedCard.id;

  return (
    <div className="mobile-page">
      <div className="mobile-page__header">
        <h2 className="mobile-page__title">{isEditing ? '编辑卡片' : selectedCard.title}</h2>
      </div>
      <div className="mobile-page__content" style={{ padding: '16px', display: 'flex', flexDirection: 'column', flex: 1 }}>
        {isEditing ? (
          <>
            <input
              type="text"
              value={editingTitle}
              onChange={e => onEditTitleChange(e.target.value)}
              placeholder="卡片标题"
              style={{
                width: '100%',
                padding: '10px 12px',
                borderRadius: 6,
                border: '1px solid var(--border, #374151)',
                background: 'var(--bg, #030712)',
                color: 'var(--text, #e5e7eb)',
                fontSize: 16,
                fontWeight: 'bold',
                marginBottom: 12,
                boxSizing: 'border-box',
              }}
            />
            <LinksEditor
              links={editingLinks}
              allCards={allCards}
              currentCardId={editingCardId!}
              onLinksChange={onLinksChange}
            />
            <textarea
              value={editingContent}
              onChange={e => onEditContentChange(e.target.value)}
              placeholder="卡片内容 (Markdown)"
              style={{
                flex: 1,
                padding: '10px 12px',
                borderRadius: 6,
                border: '1px solid var(--border, #374151)',
                background: 'var(--bg, #030712)',
                color: 'var(--text, #e5e7eb)',
                fontSize: 14,
                resize: 'none',
                fontFamily: 'monospace',
                lineHeight: 1.6,
              }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
              <button
                onClick={onCancelEdit}
                style={{
                  padding: '8px 20px',
                  borderRadius: 6,
                  border: '1px solid var(--border, #374151)',
                  background: 'transparent',
                  color: 'var(--text-secondary, #9ca3af)',
                  cursor: 'pointer',
                  fontSize: 14,
                }}
              >
                取消
              </button>
              <button
                onClick={onSaveEdit}
                disabled={savingCard || !editingTitle.trim()}
                style={{
                  padding: '8px 20px',
                  borderRadius: 6,
                  border: 'none',
                  background: (savingCard || !editingTitle.trim()) ? 'var(--border, #374151)' : 'var(--accent, #7c3aed)',
                  color: '#fff',
                  cursor: (savingCard || !editingTitle.trim()) ? 'not-allowed' : 'pointer',
                  fontSize: 14,
                }}
              >
                {savingCard ? '保存中...' : '保存'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="mobile-markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={{
                  h1: ({ children }) => <h1 style={{ color: 'var(--text-h, #f9fafb)', borderBottom: '1px solid var(--border, #374151)', paddingBottom: 8 }}>{children}</h1>,
                  h2: ({ children }) => <h2 style={{ color: 'var(--text-h, #f9fafb)', marginTop: 16 }}>{children}</h2>,
                  h3: ({ children }) => <h3 style={{ color: 'var(--text-h, #f9fafb)' }}>{children}</h3>,
                  strong: ({ children }) => <strong style={{ color: 'var(--accent, #a78bfa)' }}>{children}</strong>,
                  a: ({ href, children }) => <a href={sanitizeUrl(href || '')} style={{ color: 'var(--accent, #a78bfa)' }} target="_blank" rel="noopener noreferrer">{children}</a>,
                  ul: ({ children }) => <ul style={{ marginLeft: 16 }}>{children}</ul>,
                  ol: ({ children }) => <ol style={{ marginLeft: 16 }}>{children}</ol>,
                  code: ({ className, children }) => {
                    const isInline = !className;
                    return isInline
                      ? <code style={{ background: 'var(--bg-secondary, #111827)', padding: '2px 6px', borderRadius: 4, fontSize: 13 }}>{children}</code>
                      : <pre style={{ background: 'var(--bg-secondary, #111827)', padding: 12, borderRadius: 8, overflow: 'auto' }}><code>{children}</code></pre>;
                  },
                  blockquote: ({ children }) => <blockquote style={{ borderLeft: '3px solid var(--accent, #a78bfa)', paddingLeft: 12, marginLeft: 0, color: 'var(--text-secondary, #9ca3af)' }}>{children}</blockquote>,
                }}
              >
                {selectedCard.content}
              </ReactMarkdown>
            </div>

            <RawContentSection cardId={selectedCard.id} sessionId={sessionId} />

            {selectedCard.metadata && Object.keys(selectedCard.metadata).length > 0 && (
              <div className="mobile-metadata">
                <div className="mobile-metadata__title">元数据</div>
                {Object.entries(selectedCard.metadata).map(([k, v]) => (
                  <div key={k} className="mobile-metadata__item">
                    <span className="mobile-metadata__key">{k}:</span> {v}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
