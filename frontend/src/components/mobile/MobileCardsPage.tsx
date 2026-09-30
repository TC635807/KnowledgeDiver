import React from 'react';
import { Card } from '../../types/card';
import TreeBrowser from '../TreeBrowser';
import '../BottomNavBar.css';

type MobileCardsPageProps = {
  cards: Card[];
  selectedCardId: string | null;
  managementMode: boolean;
  selectedCardIds: Set<string>;
  onSelectCard: (id: string) => void;
  onExpandSearch: (cardId: string, title: string, searchLevel?: string) => void;
  onCreateEmptyCard: (cardId: string) => void;
  onCreateOrphanCard: () => void;
  onEditCard: (cardId: string) => void;
  onToggleSelect: (cardId: string) => void;
  onEnterManagement: () => void;
  onExitManagement: () => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
  onDeleteSelected: () => void;
  deleting: boolean;
  sessionName: string;
};

export const MobileCardsPage: React.FC<MobileCardsPageProps> = ({
  cards,
  selectedCardId,
  managementMode,
  selectedCardIds,
  onSelectCard,
  onExpandSearch,
  onCreateEmptyCard,
  onCreateOrphanCard,
  onEditCard,
  onToggleSelect,
  onEnterManagement,
  onExitManagement,
  onSelectAll,
  onSelectNone,
  onDeleteSelected,
  deleting,
  sessionName,
}) => {
  return (
    <div className="mobile-page">
      <div className="mobile-page__header">
        <h2 className="mobile-page__title">{sessionName} ({cards.length})</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          {managementMode ? (
            <>
              <button onClick={onSelectAll} className="mobile-header-btn">全选</button>
              <button onClick={onExitManagement} className="mobile-header-btn mobile-header-btn--primary">完成</button>
            </>
          ) : (
            <button onClick={onEnterManagement} className="mobile-header-btn">管理</button>
          )}
        </div>
      </div>

      {managementMode && selectedCardIds.size > 0 && (
        <div className="mobile-action-bar">
          <span>已选 {selectedCardIds.size} 项</span>
          <button onClick={onDeleteSelected} disabled={deleting} className="mobile-delete-btn">
            {deleting ? '删除中...' : '删除'}
          </button>
        </div>
      )}

      <div className="mobile-page__content">
        {cards.length === 0 ? (
          <div className="mobile-page__empty">
            <div className="mobile-page__empty-icon">📚</div>
            <div style={{ marginBottom: 4 }}>暂无卡片</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary, #9ca3af)', lineHeight: 1.6 }}>
              选中卡片后点击 [+] 可添加子卡片<br/>或切换到底部「收集」页搜索新卡片
            </div>
          </div>
        ) : (
          <TreeBrowser
            cards={cards}
            selectedId={selectedCardId}
            onSelectCard={onSelectCard}
            onExpandSearch={onExpandSearch}
            onCreateEmptyCard={onCreateEmptyCard}
            onCreateOrphanCard={onCreateOrphanCard}
            onEditCard={onEditCard}
            managementMode={managementMode}
            selectedCardIds={selectedCardIds}
            onToggleSelect={onToggleSelect}
          />
        )}
      </div>
    </div>
  );
};
