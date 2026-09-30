import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Card } from '../types/card';

type LinksEditorProps = {
  links: string[];
  allCards: Card[];
  currentCardId: string;
  onLinksChange: (links: string[]) => void;
};

export const LinksEditor: React.FC<LinksEditorProps> = ({
  links,
  allCards,
  currentCardId,
  onLinksChange,
}) => {
  const [search, setSearch] = useState('');
  const [showDropdown, setShowDropdown] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const linkTargets = useMemo(() => {
    return links
      .map(id => allCards.find(c => c.id === id))
      .filter(Boolean) as Card[];
  }, [links, allCards]);

  const availableCards = useMemo(() => {
    if (!search.trim()) return [];
    const q = search.toLowerCase();
    return allCards.filter(
      c =>
        c.id !== currentCardId &&
        !links.includes(c.id) &&
        c.title.toLowerCase().includes(q)
    ).slice(0, 8);
  }, [search, allCards, currentCardId, links]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const addLink = (cardId: string) => {
    onLinksChange([...links, cardId]);
    setSearch('');
    setShowDropdown(false);
  };

  const removeLink = (cardId: string) => {
    onLinksChange(links.filter(id => id !== cardId));
  };

  const borderColor = 'var(--border, #374151)';
  const bgColor = 'var(--bg, #030712)';
  const textColor = 'var(--text, #e5e7eb)';
  const accentColor = 'var(--accent, #7c3aed)';
  const textSecondary = 'var(--text-secondary, #9ca3af)';

  return (
    <div ref={containerRef} style={{ marginBottom: 12 }}>
      <label style={{ display: 'block', fontSize: 13, color: textSecondary, marginBottom: 6 }}>
        前链（出链）
      </label>

      {/* Current links */}
      {linkTargets.length > 0 ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {linkTargets.map(card => (
            <span
              key={card.id}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '3px 8px',
                borderRadius: 4,
                background: accentColor,
                color: '#fff',
                fontSize: 12,
              }}
            >
              {card.title}
              <button
                onClick={() => removeLink(card.id)}
                title="移除此前链"
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'rgba(255,255,255,0.7)',
                  cursor: 'pointer',
                  padding: 0,
                  fontSize: 14,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: textSecondary, marginBottom: 8 }}>暂无前链</div>
      )}

      {/* Search + dropdown */}
      <div style={{ position: 'relative' }}>
        <input
          type="text"
          value={search}
          onChange={e => { setSearch(e.target.value); setShowDropdown(true); }}
          onFocus={() => setShowDropdown(true)}
          placeholder="搜索卡片标题以添加前链..."
          style={{
            width: '100%',
            padding: '6px 10px',
            borderRadius: 4,
            border: `1px solid ${borderColor}`,
            background: bgColor,
            color: textColor,
            fontSize: 13,
            boxSizing: 'border-box',
          }}
        />
        {showDropdown && availableCards.length > 0 && (
          <div
            style={{
              position: 'absolute',
              top: '100%',
              left: 0,
              right: 0,
              zIndex: 100,
              background: bgColor,
              border: `1px solid ${borderColor}`,
              borderRadius: 4,
              marginTop: 2,
              maxHeight: 180,
              overflowY: 'auto',
            }}
          >
            {availableCards.map(card => (
              <div
                key={card.id}
                onClick={() => addLink(card.id)}
                style={{
                  padding: '6px 10px',
                  cursor: 'pointer',
                  fontSize: 13,
                  color: textColor,
                  borderBottom: `1px solid ${borderColor}`,
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-overlay, rgba(255,255,255,0.04))')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                {card.title}
              </div>
            ))}
          </div>
        )}
        {showDropdown && search.trim() && availableCards.length === 0 && (
          <div
            style={{
              position: 'absolute',
              top: '100%',
              left: 0,
              right: 0,
              zIndex: 100,
              background: bgColor,
              border: `1px solid ${borderColor}`,
              borderRadius: 4,
              marginTop: 2,
              padding: '8px 10px',
              fontSize: 12,
              color: textSecondary,
            }}
          >
            无匹配卡片
          </div>
        )}
      </div>
    </div>
  );
};
