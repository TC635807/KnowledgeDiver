import React, { useState } from 'react';
import { Card } from '../types/card';
import { TreeNode as TreeNodeType } from '../utils/tree';
import SearchLevelPopup from './SearchLevelPopup';

interface TreeNodeProps {
  node: TreeNodeType;
  depth: number;
  isExpanded: boolean;
  onToggleExpand: (cardId: string) => void;
  onSelect: (cardId: string) => void;
  selectedId: string | null;
  onExpandSearch?: (cardId: string, title: string, searchLevel?: string) => void;
  onSearchByKeyword?: (cardId: string, keyword: string) => void;
  onCreateEmptyCard?: (cardId: string) => void;
  onEditCard?: (cardId: string) => void;
  managementMode?: boolean;
  isCardSelected?: (cardId: string) => boolean;
  onToggleSelect?: (cardId: string) => void;
}

const TreeNodeComponent: React.FC<TreeNodeProps> = ({
  node,
  depth,
  isExpanded,
  onToggleExpand,
  onSelect,
  selectedId,
  onExpandSearch,
  onSearchByKeyword,
  onCreateEmptyCard,
  onEditCard,
  managementMode = false,
  isCardSelected,
  onToggleSelect,
}) => {
  const { card, children } = node;
  const hasChildren = children.length > 0;
  const isSelected = selectedId === card.id;
  const checked = isCardSelected ? isCardSelected(card.id) : false;
  
  const [showSearchMenu, setShowSearchMenu] = useState(false);

  const handleExpandSearchClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowSearchMenu(prev => !prev);
  };

  const handleSearchLevelSelect = (level: string) => {
    if (onExpandSearch) {
      onExpandSearch(card.id, card.title, level);
    }
  };

  const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation();
    if (onToggleSelect) {
      onToggleSelect(card.id);
    }
  };
  
  return (
    <div className={`tree-node ${isSelected ? 'tree-node--selected' : ''} ${managementMode ? 'tree-node--management' : ''}`}>
      <div className="tree-node__row" style={{ paddingLeft: depth * 18 }}>
        {managementMode && (
          <input
            type="checkbox"
            checked={checked}
            onChange={handleCheckboxChange}
            className="tree-node__checkbox"
            onClick={(e) => e.stopPropagation()}
          />
        )}
        {!managementMode && hasChildren && (
          <button
            className="tree-node__expand"
            onClick={(e) => { e.stopPropagation(); onToggleExpand(card.id); }}
            aria-label="展开"
          >
            {isExpanded ? '−' : '+'}
          </button>
        )}
        <span className="tree-node__title" onClick={() => !managementMode && onSelect(card.id)} data-testid={`node-${card.id}`}>
          {card.title}
        </span>
        {!managementMode && isSelected && (
          <div className="tree-node__actions">
            {onExpandSearch && (
              <div style={{ position: 'relative' }}>
                <button
                  className="tree-node__expand-search"
                  onClick={handleExpandSearchClick}
                  aria-label="联想搜索"
                  title="联想搜索"
                >
                  +
                </button>
                <SearchLevelPopup
                  open={showSearchMenu}
                  onSelect={handleSearchLevelSelect}
                  onClose={() => setShowSearchMenu(false)}
                  onCustomSearch={onSearchByKeyword ? (kw) => onSearchByKeyword(card.id, kw) : undefined}
                />
              </div>
            )}
            {onCreateEmptyCard && (
              <button
                className="tree-node__create-empty"
                onClick={(e) => { e.stopPropagation(); onCreateEmptyCard(card.id); }}
                aria-label="创建空卡片"
                title="添加空卡牌"
              >
                +
              </button>
            )}
            {onEditCard && (
              <button
                className="tree-node__edit-card"
                onClick={(e) => { e.stopPropagation(); onEditCard(card.id); }}
                aria-label="编辑卡片"
                title="编辑卡牌"
              >
                ✎
              </button>
            )}
          </div>
        )}
      </div>
      {hasChildren && isExpanded && (
        <div className="tree-node__children">
          {children.map((child) => (
            <TreeNodeComponent
              key={child.card.id}
              node={child}
              depth={depth + 1}
              isExpanded={child.isExpanded ?? false}
              onToggleExpand={onToggleExpand}
              onSelect={onSelect}
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
      )}
    </div>
  );
};

export default TreeNodeComponent;
