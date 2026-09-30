import React, { useEffect, useRef } from 'react';

type MenuItem = {
  id: string;
  label: string;
  action: () => void;
};

interface ContextMenuProps {
  x: number;
  y: number;
  cardId: string;
  options: MenuItem[];
  onClose: () => void;
}

const ContextMenu: React.FC<ContextMenuProps> = ({ x, y, options, onClose }) => {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, [onClose]);

  return (
    <div ref={ref} className="context-menu" style={{ position: 'fixed', left: x, top: y, zIndex: 9999 }}>
      <ul>
        {options.map((opt) => (
          <li key={opt.id} onClick={() => { opt.action(); onClose(); }} className="context-menu__item">
            {opt.label}
          </li>
        ))}
      </ul>
    </div>
  );
};

export default ContextMenu;
