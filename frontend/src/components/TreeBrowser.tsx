import React, { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { Card } from '../types/card';
import { buildTree, SortMode, TreeNode } from '../utils/tree';
import { authFetchWithToken } from '../api/auth';
import TreeNodeComponent from './TreeNode';
import './TreeBrowser.css';

type TreeBrowserProps = {
  cards: Card[];
  selectedId: string | null;
  onSelectCard: (id: string) => void;
  onExpandSearch?: (cardId: string, title: string, searchLevel?: string) => void;
  onSearchByKeyword?: (cardId: string, keyword: string) => void;
  onCreateEmptyCard?: (cardId: string) => void;
  onCreateOrphanCard?: () => void;
  onEditCard?: (cardId: string) => void;
  managementMode?: boolean;
  selectedCardIds?: Set<string>;
  onToggleSelect?: (cardId: string) => void;
  sessionId?: string;
};

const SORT_LABELS: Record<SortMode, string> = {
  tree: '树形',
  newest: '最新',
  alpha: '标题',
};

const TreeBrowser: React.FC<TreeBrowserProps> = ({
  cards,
  selectedId,
  onSelectCard,
  onExpandSearch,
  onSearchByKeyword,
  onCreateEmptyCard,
  onCreateOrphanCard,
  onEditCard,
  managementMode = false,
  selectedCardIds = new Set(),
  onToggleSelect,
  sessionId = 'default',
}) => {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [filterText, setFilterText] = useState('');
  const [sortMode, setSortMode] = useState<SortMode>('tree');
  const [semanticCards, setSemanticCards] = useState<Card[] | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const localFiltered = useMemo(() => {
    if (!filterText.trim()) return cards;
    const q = filterText.toLowerCase();
    return cards.filter(c => c.title.toLowerCase().includes(q));
  }, [cards, filterText]);

  const isSemantic = semanticCards !== null;
  const filteredCards = semanticCards ?? localFiltered;

  const doSemanticSearch = useCallback(async (query: string) => {
    try {
      const data = await authFetchWithToken<{
        results: { card: Card; score: number }[];
        fallback?: boolean;
      }>(`/api/cards/search?query=${encodeURIComponent(query)}&session_id=${sessionId}&limit=20`);
      if (data.fallback || !data.results?.length) {
        setSemanticCards(null);
      } else {
        setSemanticCards(data.results.map(r => r.card));
      }
    } catch (err) {
      console.warn('Semantic search failed:', err);
      setSemanticCards(null);
    }
  }, [sessionId]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!filterText.trim()) {
      setSemanticCards(null);
      return;
    }
    debounceRef.current = setTimeout(() => {
      doSemanticSearch(filterText);
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [filterText, doSemanticSearch]);

  useEffect(() => {
    if (isSemantic) {
      // 语义搜索：保持 API 返回的相关性顺序，不按树形重组
      setTree(filteredCards.map(c => ({ card: c, children: [], isExpanded: false, id: c.id })));
    } else {
      const t = buildTree(filteredCards, sortMode);
      console.log('[TreeBrowser] roots:', t.length, t.map(r => `${r.card.title}(children:${r.children.length})`));
      setTree(t);
    }
  }, [filteredCards, sortMode, isSemantic]);

  const toggleExpand = (cardId: string) => {
    setTree((prev) => {
      const clone = JSON.parse(JSON.stringify(prev)) as TreeNode[];
      const walk = (nodes: TreeNode[]) => {
        for (const n of nodes) {
          if (n.card.id === cardId) {
            n.isExpanded = !n.isExpanded;
            return true;
          }
          if (n.children && n.children.length) {
            if (walk(n.children)) return true;
          }
        }
        return false;
      };
      walk(clone);
      return clone;
    });
  };

  const isCardSelected = (cardId: string) => selectedCardIds.has(cardId);

  const renderNodes = (nodes: TreeNode[], depth: number) => {
    return (
      <div>
        {nodes.map((n) => (
          <TreeNodeComponent
            key={n.id}
            node={n}
            depth={depth}
            isExpanded={Boolean(n.isExpanded)}
            onToggleExpand={toggleExpand}
            onSelect={(id) => onSelectCard(id)}
            selectedId={selectedId}
            onExpandSearch={onExpandSearch}
            onSearchByKeyword={onSearchByKeyword}
            onCreateEmptyCard={onCreateEmptyCard}
            onEditCard={onEditCard}
            managementMode={managementMode}
            isCardSelected={isCardSelected}
            onToggleSelect={onToggleSelect}
          />
        ))}
      </div>
    );
  };

  return (
    <div className="tree-browser">
      <div className="tree-browser__search">
        <input
          type="text"
          value={filterText}
          onChange={e => setFilterText(e.target.value)}
          placeholder="搜索卡片..."
          className="tree-browser__search-input"
        />
        <select
          className="tree-browser__sort-select"
          value={sortMode}
          onChange={e => setSortMode(e.target.value as SortMode)}
          title="排序方式"
        >
          {Object.entries(SORT_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        {filterText ? (
          <button
            className="tree-browser__search-clear"
            onClick={() => setFilterText('')}
            title="清除搜索"
          >
            ×
          </button>
        ) : (
          onCreateOrphanCard && (
            <button
              className="tree-browser__add-orphan"
              onClick={onCreateOrphanCard}
              title="新建孤立卡片"
            >
              +
            </button>
          )
        )}
      </div>
      {tree.length === 0 && !filterText && <div className="tree-empty">暂无卡片<br/>选中一张卡片后点击 [+] 创建子卡片<br/>或在右侧收集面板输入关键词搜索</div>}
      {tree.length === 0 && filterText && <div className="tree-empty">无匹配卡片</div>}
      {tree.length > 0 && renderNodes(tree, 0)}
    </div>
  );
};

export default TreeBrowser;

export type { TreeNode } from '../utils/tree';
