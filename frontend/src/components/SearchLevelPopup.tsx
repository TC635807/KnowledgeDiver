import React, { useEffect, useRef, useState } from 'react';
import './SearchLevelPopup.css';

const LEVELS = [
  { value: 'default', label: '默认搜索', icon: '🔍' },
  { value: 'downstream', label: '下级搜索', icon: '⬇' },
  { value: 'peer', label: '平级搜索', icon: '↔' },
  { value: 'upstream', label: '上级搜索', icon: '⬆' },
];

interface SearchLevelPopupProps {
  open: boolean;
  onSelect: (level: string) => void;
  onClose: () => void;
  onCustomSearch?: (keyword: string) => void;
}

const SearchLevelPopup: React.FC<SearchLevelPopupProps> = ({ open, onSelect, onClose, onCustomSearch }) => {
  const dropdownRef = useRef<HTMLDivElement>(null);
  const customInputRef = useRef<HTMLInputElement>(null);
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customKeyword, setCustomKeyword] = useState('');

  // 每次打开时重置自定义关键词输入状态
  useEffect(() => {
    if (open) {
      setShowCustomInput(false);
      setCustomKeyword('');
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClick);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClick);
    };
  }, [open, onClose]);

  const handleCustomClick = () => {
    setShowCustomInput(true);
    setTimeout(() => customInputRef.current?.focus(), 0);
  };

  const handleCustomConfirm = () => {
    const kw = customKeyword.trim();
    if (!kw) return;
    onCustomSearch?.(kw);
    setCustomKeyword('');
    setShowCustomInput(false);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="search-level-popup" ref={dropdownRef}>
      <div className="search-level-popup__dropdown">
        {LEVELS.map((level) => (
          <button
            key={level.value}
            className="search-level-popup__item"
            onClick={() => {
              onSelect(level.value);
              onClose();
            }}
          >
            <span className="search-level-popup__icon">{level.icon}</span>
            <span>{level.label}</span>
          </button>
        ))}
        {onCustomSearch && (
          <>
            <button
              className="search-level-popup__item"
              onClick={handleCustomClick}
            >
              <span className="search-level-popup__icon">⌨️</span>
              <span>自定义关键词</span>
            </button>
            {showCustomInput && (
              <div className="search-level-popup__custom">
                <input
                  ref={customInputRef}
                  className="search-level-popup__custom-input"
                  value={customKeyword}
                  onChange={(e) => setCustomKeyword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleCustomConfirm();
                    else if (e.key === 'Escape') { setShowCustomInput(false); setCustomKeyword(''); }
                  }}
                  placeholder="输入搜索关键词"
                />
                <button
                  className="search-level-popup__custom-confirm"
                  onClick={handleCustomConfirm}
                  disabled={!customKeyword.trim()}
                >
                  搜索
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default SearchLevelPopup;
