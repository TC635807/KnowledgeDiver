import React from 'react';
import { Card } from '../../types/card';
import GraphView from '../GraphView';
import '../BottomNavBar.css';

type MobileGraphPageProps = {
  cards: Card[];
  selectedCardId: string | null;
  onSelectCard: (id: string) => void;
  visible: boolean;
};

export const MobileGraphPage: React.FC<MobileGraphPageProps> = ({
  cards,
  selectedCardId,
  onSelectCard,
  visible,
}) => {
  return (
    <div className="mobile-page">
      <div className="mobile-page__header">
        <h2 className="mobile-page__title">知识图谱</h2>
      </div>
      <div className="mobile-page__content" style={{ padding: 0, display: 'flex', flexDirection: 'column' }}>
        {cards.length === 0 ? (
          <div className="mobile-page__empty">
            <div className="mobile-page__empty-icon">📊</div>
            <div>暂无卡片</div>
            <div style={{ fontSize: 12, marginTop: 8, color: 'var(--text-secondary, #9ca3af)' }}>创建卡片后查看知识图谱</div>
          </div>
        ) : (
          <div style={{ flex: 1, minHeight: 0 }}>
            <GraphView
              cards={cards}
              selectedId={selectedCardId}
              onSelectCard={onSelectCard}
              visible={visible}
            />
          </div>
        )}
      </div>
    </div>
  );
};
