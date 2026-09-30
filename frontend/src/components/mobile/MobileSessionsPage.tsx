import React, { useState, useRef } from 'react';
import { Session } from '../../types/session';
import '../BottomNavBar.css';

type MobileSessionsPageProps = {
  sessions: Session[];
  selectedSessionId: string;
  sessionManagementMode: boolean;
  selectedSessionIds: Set<string>;
  newSessionName: string;
  creatingSession: boolean;
  sessionDeleting: boolean;
  editingSessionId: string | null;
  editingSessionName: string;
  onSelectSession: (id: string) => void;
  onToggleSessionSelect: (id: string) => void;
  onEnterManagement: () => void;
  onExitManagement: () => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
  onDeleteSelected: () => void;
  onCreateSession: () => void;
  onUpdateSession: (id: string, name: string) => void;
  onStartEditSession: (id: string, name: string) => void;
  onCancelEditSession: () => void;
  onNewSessionNameChange: (name: string) => void;
  onEditingSessionNameChange: (name: string) => void;
  uploadingFile: boolean;
  downloadingId: string | null;
  onUploadSession: () => void;
  onDownloadSession: (sessionId: string, sessionName: string) => void;
  onShareSession: (sessionId: string) => void;
  onShareToHub: (sessionId: string) => void;
  onImportFromShare: () => void;
};

export const MobileSessionsPage: React.FC<MobileSessionsPageProps> = ({
  sessions,
  selectedSessionId,
  sessionManagementMode,
  selectedSessionIds,
  newSessionName,
  creatingSession,
  sessionDeleting,
  editingSessionId,
  editingSessionName,
  onSelectSession,
  onToggleSessionSelect,
  onEnterManagement,
  onExitManagement,
  onSelectAll,
  onSelectNone,
  onDeleteSelected,
  onCreateSession,
  onUpdateSession,
  onStartEditSession,
  onCancelEditSession,
  onNewSessionNameChange,
  onEditingSessionNameChange,
  uploadingFile,
  downloadingId,
  onUploadSession,
  onDownloadSession,
  onShareSession,
  onShareToHub,
  onImportFromShare,
}) => {
  const [showUploadMenu, setShowUploadMenu] = useState(false);
  const uploadBtnRef = useRef<HTMLButtonElement>(null);
  return (
    <div className="mobile-page">
      <div className="mobile-page__header">
        <h2 className="mobile-page__title">会话 ({sessions.length})</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          {sessionManagementMode ? (
            <>
              <button
                onClick={onSelectAll}
                className="mobile-header-btn"
              >
                全选
              </button>
              <button
                onClick={onExitManagement}
                className="mobile-header-btn mobile-header-btn--primary"
              >
                完成
              </button>
            </>
          ) : (
            <>
              <div style={{ position: 'relative' }}>
                <button
                  ref={uploadBtnRef}
                  onClick={() => setShowUploadMenu(!showUploadMenu)}
                  disabled={uploadingFile}
                  className="mobile-header-btn"
                  style={{
                    background: 'rgba(16,185,129,0.1)',
                    borderColor: 'rgba(16,185,129,0.3)',
                    color: '#34d399',
                  }}
                  title="导入会话"
                >
                  {uploadingFile ? '…' : '↑ 导入'}
                </button>
                {showUploadMenu && (() => {
                  const rect = uploadBtnRef.current?.getBoundingClientRect()
                  const top = rect ? rect.bottom + 6 : 0
                  const left = rect ? rect.right - 200 : 0
                  return (
                  <>
                    <div
                      style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 9998, background: 'rgba(0,0,0,0.3)' }}
                      onClick={() => setShowUploadMenu(false)}
                    />
                    <div style={{
                      position: 'fixed', top, left, zIndex: 9999,
                      background: 'var(--bg-elevated)', borderRadius: 10,
                      border: '1px solid var(--border)', boxShadow: 'var(--shadow-lg)',
                      padding: 6, width: 200
                    }}>
                      <button
                        onClick={() => { setShowUploadMenu(false); onUploadSession() }}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                          padding: '8px 10px', border: 'none', borderRadius: 6,
                          background: 'transparent', color: 'var(--text)', cursor: 'pointer',
                          fontSize: 13, textAlign: 'left'
                        }}
                      >
                        📁 从文件导入
                      </button>
                      <button
                        onClick={() => { setShowUploadMenu(false); onImportFromShare() }}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                          padding: '8px 10px', border: 'none', borderRadius: 6,
                          background: 'transparent', color: 'var(--text)', cursor: 'pointer',
                          fontSize: 13, textAlign: 'left'
                        }}
                      >
                        🔗 从链接导入
                      </button>
                    </div>
                  </>
                  )
                })()}
              </div>
              <button
                onClick={onEnterManagement}
                className="mobile-header-btn"
              >
                管理
              </button>
            </>
          )}
        </div>
      </div>

      {sessionManagementMode && selectedSessionIds.size > 0 && (
        <div className="mobile-action-bar">
          <span>已选 {selectedSessionIds.size} 项</span>
          <button
            onClick={onDeleteSelected}
            disabled={sessionDeleting}
            className="mobile-delete-btn"
          >
            {sessionDeleting ? '删除中...' : '删除'}
          </button>
        </div>
      )}

      <div className="mobile-page__content">
        <div className="mobile-input-row">
          <input
            type="text"
            value={newSessionName}
            onChange={(e) => onNewSessionNameChange(e.target.value)}
            placeholder="新会话名称"
            className="mobile-input"
            onKeyDown={(e) => e.key === 'Enter' && onCreateSession()}
          />
          <button
            onClick={onCreateSession}
            disabled={!newSessionName.trim() || creatingSession}
            className="mobile-add-btn"
          >
            {creatingSession ? '...' : '+'}
          </button>
        </div>

        <div className="mobile-list">
          {sessions.length === 0 && (
            <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-secondary, #9ca3af)' }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>📁</div>
              <div style={{ marginBottom: 8 }}>暂无会话</div>
              <div style={{ fontSize: 12 }}>在上方输入名称创建新会话</div>
            </div>
          )}
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`mobile-list-item ${session.id === selectedSessionId ? 'mobile-list-item--active' : ''}`}
              onClick={() => sessionManagementMode ? onToggleSessionSelect(session.id) : onSelectSession(session.id)}
            >
              {sessionManagementMode && (
                <input
                  type="checkbox"
                  checked={selectedSessionIds.has(session.id)}
                  onChange={() => onToggleSessionSelect(session.id)}
                  className="mobile-checkbox"
                />
              )}
              <div className="mobile-list-item__content">
                {editingSessionId === session.id ? (
                  <div className="mobile-edit-row">
                    <input
                      type="text"
                      value={editingSessionName}
                      onChange={(e) => onEditingSessionNameChange(e.target.value)}
                      className="mobile-input mobile-input--small"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') onUpdateSession(session.id, editingSessionName);
                        if (e.key === 'Escape') onCancelEditSession();
                      }}
                    />
                    <button onClick={() => onUpdateSession(session.id, editingSessionName)} className="mobile-small-btn mobile-small-btn--primary">✓</button>
                    <button onClick={onCancelEditSession} className="mobile-small-btn">✕</button>
                  </div>
                ) : (
                  <>
                    <div className="mobile-list-item__title">{session.name}</div>
                    <div className="mobile-list-item__subtitle">{session.card_count} 张卡片</div>
                  </>
                )}
              </div>
              {!sessionManagementMode && editingSessionId !== session.id && (
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); onShareSession(session.id); }}
                    style={{
                      width: 28, height: 28, border: '1px solid rgba(139,92,246,0.3)',
                      background: 'rgba(139,92,246,0.1)', color: '#a78bfa',
                      cursor: 'pointer', borderRadius: 6, fontSize: 13,
                      display: 'flex', alignItems: 'center', justifyContent: 'center'
                    }}
                    title="分享会话"
                  >
                    ↗
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onShareToHub(session.id); }}
                    style={{
                      width: 28, height: 28, border: '1px solid rgba(245,158,11,0.3)',
                      background: 'rgba(245,158,11,0.1)', color: '#fbbf24',
                      cursor: 'pointer', borderRadius: 6, fontSize: 12,
                      display: 'flex', alignItems: 'center', justifyContent: 'center'
                    }}
                    title="分享到 Hub"
                  >
                    🚀
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDownloadSession(session.id, session.name); }}
                    disabled={downloadingId === session.id}
                    style={{
                      width: 28, height: 28, border: '1px solid rgba(16,185,129,0.3)',
                      background: 'rgba(16,185,129,0.1)', color: '#34d399',
                      cursor: downloadingId === session.id ? 'not-allowed' : 'pointer',
                      borderRadius: 6, fontSize: 13, opacity: downloadingId === session.id ? 0.5 : 1,
                      display: 'flex', alignItems: 'center', justifyContent: 'center'
                    }}
                    title="下载会话"
                  >
                    {downloadingId === session.id ? '…' : '↓'}
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onStartEditSession(session.id, session.name); }}
                    className="mobile-edit-btn"
                  >
                    编辑
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
