import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { useAgent } from '../hooks/useAgent';
import './AgentPanel.css';

interface Props {
  sessionId: string | null;
  initialMessage?: string;
  onTaskCreated?: (taskId: string, keyword: string, taskType: string) => void;
}

/**
 * Agent 常驻对话栏 — 最右侧完整对话面板（原图谱列位置）。
 * 与 AgentDrawer 共享 useAgent hook，但为嵌入式列布局，非覆盖式抽屉。
 */
const AgentPanel: React.FC<Props> = ({ sessionId, initialMessage, onTaskCreated }) => {
  const { messages, isStreaming, send, clear } = useAgent(sessionId, onTaskCreated);
  const [input, setInput] = useState('');
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
    <div className="agent-panel">
      <div className="agent-panel__header">
        <span className="agent-panel__title">💬 Agent 知识助手</span>
        <button className="agent-panel__btn" onClick={clear} disabled={isStreaming} title="清空对话">
          清空
        </button>
      </div>

      <div className="agent-panel__messages" ref={messagesRef}>
        {messages.length === 0 && (
          <div className="agent-panel__empty">
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

      <div className="agent-panel__input-area">
        <input
          ref={inputRef}
          className="agent-panel__input"
          placeholder="输入问题..."
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
        />
        <button
          className="agent-panel__send"
          onClick={handleSend}
          disabled={!input.trim()}
        >
          发送
        </button>
      </div>
    </div>
  );
};

export default AgentPanel;
