import React, { useState } from 'react';
import { Card } from '../../types/card';
import { SearchTask } from '../../types/pipeline';
import { StreamingOutput } from '../StreamingOutput';
import SearchLevelPopup from '../SearchLevelPopup';
import '../BottomNavBar.css';

type MobileCollectorPageProps = {
  searchTasks: Map<string, SearchTask>;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  onStartCollection: (searchLevel?: string) => void;
  onCollectionComplete: (searchId: string, cards: Card[]) => void;
  onCollectionError: (searchId: string, error: Error) => void;
  onCancelSearch: (searchId: string) => void;
  onRemoveSearch: (searchId: string) => void;
  onTaskIdReceived?: (tempId: string, realTaskId: string) => void;
  onDocumentUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
};

export const MobileCollectorPage: React.FC<MobileCollectorPageProps> = ({
  searchTasks,
  keyword,
  onKeywordChange,
  onStartCollection,
  onCollectionComplete,
  onCollectionError,
  onCancelSearch,
  onRemoveSearch,
  onTaskIdReceived,
  onDocumentUpload,
}) => {
  const [showSearchMenu, setShowSearchMenu] = useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  return (
    <div className="mobile-page">
      <div className="mobile-page__header">
        <h2 className="mobile-page__title">收集器</h2>
      </div>
      
      <div className="mobile-search-bar">
        <input
          type="text"
          value={keyword}
          onChange={(e) => onKeywordChange(e.target.value)}
          placeholder="输入关键词收集..."
          className="mobile-input mobile-input--full"
          onKeyDown={(e) => e.key === 'Enter' && onStartCollection()}
        />
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowSearchMenu(prev => !prev)}
            disabled={!keyword.trim()}
            className="mobile-search-btn"
          >
            收集
          </button>
          <SearchLevelPopup
            open={showSearchMenu}
            onSelect={(level) => onStartCollection(level)}
            onClose={() => setShowSearchMenu(false)}
          />
        </div>
        <input
          type="file"
          ref={fileInputRef}
          style={{ display: 'none' }}
          accept=".txt,.md,.pdf,.docx"
          onChange={onDocumentUpload}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="mobile-search-btn"
          style={{ background: 'transparent', border: '1px solid var(--accent)' }}
        >
          上传
        </button>
      </div>

      <div className="mobile-page__content">
        {searchTasks.size === 0 ? (
          <div className="mobile-page__empty">
            <div className="mobile-page__empty-icon">🔍</div>
            <div>输入关键词开始收集</div>
          </div>
        ) : (
          <div className="mobile-collector-list">
            {Array.from(searchTasks.values()).map((task) => (
              <div key={task.id} className="mobile-collector-item">
                <StreamingOutput
                  searchId={task.id}
                  keyword={task.keyword}
                  streamUrl={task.streamUrl}
                  onComplete={onCollectionComplete}
                  onError={onCollectionError}
                  onCancel={onCancelSearch}
                  onRemove={onRemoveSearch}
                  onTaskIdReceived={onTaskIdReceived}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
