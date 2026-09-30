import React, { useState, useRef, useCallback, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { Card } from '../types/card';
import { sanitizeUrl } from '../utils/markdown';
import { LinksEditor } from './LinksEditor';
import RawContentSection from './RawContentSection';
import SearchLevelPopup from './SearchLevelPopup';
import './FloatingCardWindow.css';

export interface FloatingWindowState {
  id: string;
  cardId: string;
  x: number;
  y: number;
  width: number;
  height: number;
  minimized: boolean;
  zIndex: number;
  initialEditing?: boolean;
}

interface FloatingCardWindowProps {
  // The card to display. If the card is deleted while the window is open,
  // this may be undefined — the parent should close such windows.
  card: Card | undefined;
  // Full card list (for the links editor + for pointing at other cards)
  allCards: Card[];
  sessionId: string;
  // Window manager state owned by the parent
  win: FloatingWindowState;
  onClose: (windowId: string) => void;
  onFocus: (windowId: string) => void;
  onMove: (windowId: string, x: number, y: number) => void;
  onResize: (windowId: string, width: number, height: number) => void;
  onMinimize: (windowId: string) => void;
  // Card operations (delegated to parent so each window edits its own draft,
  // then the parent persists via the card manager)
  onSaveCard: (windowId: string, cardId: string, title: string, content: string, links: string[]) => void;
  // Expand search action (moved from TreeNode)
  onExpandSearch?: (cardId: string, title: string, searchLevel?: string) => void;
  // Custom keyword search (挂载到当前卡片下)
  onSearchByKeyword?: (cardId: string, keyword: string) => void;
}

// ── Markdown renderer shared by read-only preview ──
const markdownComponents = {
  h1: ({ children }: any) => <h1 style={{ color: 'var(--text-h, #f9fafb)', borderBottom: '1px solid var(--border, #374151)', paddingBottom: 8, fontSize: 20, margin: '10px 0' }}>{children}</h1>,
  h2: ({ children }: any) => <h2 style={{ color: 'var(--text-h, #f9fafb)', marginTop: 16, fontSize: 17 }}>{children}</h2>,
  h3: ({ children }: any) => <h3 style={{ color: 'var(--text-h, #f9fafb)', fontSize: 15 }}>{children}</h3>,
  strong: ({ children }: any) => <strong style={{ color: 'var(--accent, #a78bfa)' }}>{children}</strong>,
  a: ({ href, children }: any) => <a href={sanitizeUrl(href || '')} style={{ color: 'var(--accent, #a78bfa)' }} target="_blank" rel="noopener noreferrer">{children}</a>,
  ul: ({ children }: any) => <ul style={{ marginLeft: 18 }}>{children}</ul>,
  ol: ({ children }: any) => <ol style={{ marginLeft: 18 }}>{children}</ol>,
  code: ({ className, children }: any) => {
    const isInline = !className;
    return isInline
      ? <code style={{ background: 'var(--bg-secondary, #111827)', padding: '2px 6px', borderRadius: 4, fontSize: 12 }}>{children}</code>
      : <pre style={{ background: 'var(--bg-secondary, #111827)', padding: 10, borderRadius: 8, overflow: 'auto' }}><code style={{ fontSize: 12 }}>{children}</code></pre>;
  },
  blockquote: ({ children }: any) => <blockquote style={{ borderLeft: '3px solid var(--accent, #a78bfa)', paddingLeft: 12, marginLeft: 0, color: 'var(--text-secondary, #9ca3af)' }}>{children}</blockquote>,
};

const FloatingCardWindow: React.FC<FloatingCardWindowProps> = ({
  card,
  allCards,
  sessionId,
  win,
  onClose,
  onFocus,
  onMove,
  onResize,
  onMinimize,
  onSaveCard,
  onExpandSearch,
  onSearchByKeyword,
}) => {
  // Per-window independent draft state (solves the singleton editing conflict)
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [draftLinks, setDraftLinks] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [showSearchMenu, setShowSearchMenu] = useState(false);

  const isDragging = useRef(false);
  const isResizing = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, winX: 0, winY: 0 });
  const resizeStart = useRef({ x: 0, y: 0, width: 0, height: 0 });

  // Reset draft when the card identity changes or edit mode toggles on
  const startEdit = useCallback(() => {
    if (!card) return;
    setDraftTitle(card.title);
    setDraftContent(card.content);
    setDraftLinks(card.links || []);
    setEditing(true);
  }, [card]);

  // Enter edit mode on mount if the window was opened via an "edit" entry point
  useEffect(() => {
    if (win.initialEditing && card && !editing) {
      startEdit();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cancelEdit = useCallback(() => {
    // 方案A：静默丢弃未保存修改
    setEditing(false);
    setDraftTitle('');
    setDraftContent('');
    setDraftLinks([]);
  }, []);

  const handleSave = useCallback(async () => {
    if (!card || !draftTitle.trim()) return;
    setSaving(true);
    try {
      await onSaveCard(win.id, card.id, draftTitle, draftContent, draftLinks);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }, [card, draftTitle, draftContent, draftLinks, win.id, onSaveCard]);

  // ── drag (title bar) ──
  const onTitleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    isDragging.current = true;
    dragStart.current = { x: e.clientX, y: e.clientY, winX: win.x, winY: win.y };
    onFocus(win.id);
    const onMoveHandler = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      onMove(win.id, dragStart.current.winX + (ev.clientX - dragStart.current.x), dragStart.current.winY + (ev.clientY - dragStart.current.y));
    };
    const onUp = () => {
      isDragging.current = false;
      document.removeEventListener('mousemove', onMoveHandler);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMoveHandler);
    document.addEventListener('mouseup', onUp);
  }, [win.x, win.y, win.id, onFocus, onMove]);

  // ── resize (bottom-right handle) ──
  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    isResizing.current = true;
    resizeStart.current = { x: e.clientX, y: e.clientY, width: win.width, height: win.height };
    onFocus(win.id);
    const onMoveHandler = (ev: MouseEvent) => {
      if (!isResizing.current) return;
      const w = Math.max(280, resizeStart.current.width + (ev.clientX - resizeStart.current.x));
      const h = Math.max(200, resizeStart.current.height + (ev.clientY - resizeStart.current.y));
      onResize(win.id, w, h);
    };
    const onUp = () => {
      isResizing.current = false;
      document.removeEventListener('mousemove', onMoveHandler);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMoveHandler);
    document.addEventListener('mouseup', onUp);
  }, [win.width, win.height, win.id, onFocus, onResize]);

  // Keyboard: Escape cancels editing
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancelEdit();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [editing, cancelEdit]);

  if (!card) return null;

  const minimized = win.minimized;

  return (
    <div
      className={`fdw${minimized ? ' fdw--minimized' : ''}`}
      style={{
        position: 'fixed',
        left: win.x,
        top: win.y,
        width: minimized ? 220 : win.width,
        height: minimized ? 'auto' : win.height,
        zIndex: win.zIndex,
      }}
      onMouseDown={() => onFocus(win.id)}
    >
      <div className="fdw__window">
        {/* Title bar */}
        <div className="fdw__titlebar" onMouseDown={onTitleMouseDown} onDoubleClick={minimized ? () => onMinimize(win.id) : undefined}>
          <span className="fdw__title" onClick={minimized ? () => onMinimize(win.id) : undefined}>
            📄 {card.title}
          </span>
          <div className="fdw__controls" onMouseDown={(e) => e.stopPropagation()}>
            {editing && !minimized && (
              <button className="fdw__btn" onClick={handleSave} disabled={saving || !draftTitle.trim()} title="保存">
                {saving ? '…' : '✓'}
              </button>
            )}
            {!minimized && !editing && (
              <button className="fdw__btn" onClick={startEdit} title="编辑">✎</button>
            )}
            <button className="fdw__btn" onClick={() => onMinimize(win.id)} title="最小化">{minimized ? '▢' : '—'}</button>
            <button className="fdw__btn fdw__btn--close" onClick={() => onClose(win.id)} title="关闭">✕</button>
          </div>
        </div>

        {!minimized && (
          <>
            {/* Body */}
            <div className="fdw__body">
              {editing ? (
                <div className="fdw__edit">
                  <input
                    className="fdw__edit-title"
                    type="text"
                    value={draftTitle}
                    onChange={(e) => setDraftTitle(e.target.value)}
                    placeholder="卡片标题"
                  />
                  <LinksEditor links={draftLinks} allCards={allCards} currentCardId={card.id} onLinksChange={setDraftLinks} />
                  <textarea
                    className="fdw__edit-content"
                    value={draftContent}
                    onChange={(e) => setDraftContent(e.target.value)}
                    placeholder="卡片内容 (Markdown)"
                  />
                  <div className="fdw__edit-actions">
                    <button className="fdw__action" onClick={cancelEdit}>取消</button>
                    <button className="fdw__action fdw__action--primary" onClick={handleSave} disabled={saving || !draftTitle.trim()}>
                      {saving ? '保存中...' : '保存'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="fdw__preview">
                  <div className="fdw__preview-head">
                    <div className="fdw__preview-title">{card.title}</div>
                    {onExpandSearch && (
                      <div style={{ position: 'relative' }}>
                        <button
                          className="fdw__action"
                          onClick={() => setShowSearchMenu((prev) => !prev)}
                          title="联想搜索"
                        >
                          +
                        </button>
                        <SearchLevelPopup
                          open={showSearchMenu}
                          onSelect={(level) => onExpandSearch(card.id, card.title, level)}
                          onClose={() => setShowSearchMenu(false)}
                          onCustomSearch={onSearchByKeyword ? (kw) => onSearchByKeyword(card.id, kw) : undefined}
                        />
                      </div>
                    )}
                  </div>
                  <div className="fdw__preview-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={markdownComponents}>
                      {card.content}
                    </ReactMarkdown>
                  </div>
                  {card.metadata && Object.keys(card.metadata).length > 0 && (
                    <div className="fdw__metadata">
                      <div className="fdw__metadata-head">元数据</div>
                      {Object.entries(card.metadata).map(([k, v]) => (
                        <div key={k} className="fdw__metadata-row"><span>{k}:</span> {String(v)}</div>
                      ))}
                    </div>
                  )}
                  <RawContentSection cardId={card.id} sessionId={sessionId} />
                </div>
              )}
            </div>

            {/* Resize handle */}
            <div className="fdw__resize" onMouseDown={onResizeMouseDown} />
          </>
        )}
      </div>
    </div>
  );
};

export default FloatingCardWindow;
