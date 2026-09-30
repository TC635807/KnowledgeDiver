import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { useAgent } from '../hooks/useAgent';
import './AgentDrawer.css';

interface Props {
  sessionId: string | null;
  onClose: () => void;
  initialMessage?: string;
  onTaskCreated?: (taskId: string, keyword: string, taskType: string) => void;
}

const AgentDrawer: React.FC<Props> = ({ sessionId, onClose, initialMessage, onTaskCreated }) => {
  const { messages, isStreaming, send, clear } = useAgent(sessionId, onTaskCreated);
  const [input, setInput] = useState('');
  const [closing, setClosing] = useState(false);
  const [animating, setAnimating] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const initialSent = useRef(false);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (isNearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    if (initialMessage && !initialSent.current) {
      initialSent.current = true;
      send(initialMessage);
    }
  }, [initialMessage, send]);

  useEffect(() => {
    inputRef.current?.focus();
    // 延迟一帧再触发动画，避免初始帧闪烁
    const raf = requestAnimationFrame(() => setAnimating(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const handleClose = () => {
    setClosing(true);
    setTimeout(onClose, 400);
  };

  const handleSend = () => {
    if (!input.trim()) return;
    send(input);
    setInput('');
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className={`agent-drawer${animating && !closing ? ' agent-drawer--open' : ''}${closing ? ' agent-drawer--closing' : ''}`} onClick={handleClose}>
      <div className="agent-drawer__panel" onClick={e => e.stopPropagation()}>
        <div className="agent-drawer__header">
          <span className="agent-drawer__title">💬 Agent 知识助手</span>
          <div className="agent-drawer__actions">
            <button className="agent-drawer__btn" onClick={clear} disabled={isStreaming}>
              清空
            </button>
            <button className="agent-drawer__btn" onClick={handleClose}>
              ✕
            </button>
          </div>
        </div>

        <div className="agent-drawer__messages" ref={messagesRef}>
          {messages.length === 0 && (
            <div style={{ color: 'rgba(255,255,255,0.3)', fontSize: 13, textAlign: 'center', marginTop: 24 }}>
              输入问题，Agent 会先在知识库中查找已有卡片
            </div>
          )}
          {messages.map(m => (
            <div
              key={m.id}
              className={`agent-msg agent-msg--${m.role}${m.isStreaming ? ' agent-msg--streaming' : ''}`}
            >
              {m.role === 'agent' ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                  components={{
                    table: ({ children }) => (
                      <div className="agent-msg__table-wrap"><table>{children}</table></div>
                    ),
                  }}
                >
                  {m.content}
                </ReactMarkdown>
              ) : (
                m.content
              )}
              {m.isStreaming && <span className="agent-msg__cursor" />}
            </div>
          ))}
        </div>

        <div className="agent-drawer__input-area">
          <input
            ref={inputRef}
            className="agent-drawer__input"
            placeholder="输入问题..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
          />
          <button
            className="agent-drawer__send"
            onClick={handleSend}
            disabled={!input.trim()}
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentDrawer;
