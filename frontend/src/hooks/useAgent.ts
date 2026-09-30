import { useCallback, useEffect, useRef, useState } from 'react';
import { agentChatSSE, AgentMessage, AgentSSEEvent, AgentToolResult, agentStreamSSE, clearAgentContext, injectLoopMessage, loadAgentHistory } from '../api/agent';

function toolSummary(r: AgentToolResult): string {
  const prefix = r.success ? '✅' : '❌';
  switch (r.tool) {
    case 'get_card_info': {
      const title = (r.data as Record<string, unknown> | undefined)?.title;
      return `${prefix} 已读取卡片「${title || '未知'}」`;
    }
    case 'search_similar_cards': {
      const count = (r.data as Record<string, unknown> | undefined)?.count;
      const cards = (r.data as Record<string, unknown> | undefined)?.cards as Array<{title: string}> | undefined;
      if (!count) return `${prefix} 未找到相关内容`;
      const titles = cards?.slice(0, 5).map(c => c.title).join(', ');
      return `${prefix} 找到 ${count} 条相关卡片${titles ? ': ' + titles : ''}${Number(count) > 5 ? '...' : ''}`;
    }
    case 'search_by_keyword': {
      const titles = (r.data as Record<string, unknown> | undefined)?.titles as string[] | undefined;
      const count = (r.data as Record<string, unknown> | undefined)?.cards_count;
      if (!count) return `${prefix} 搜索完成，未能生成有效卡片`;
      return `${prefix} 搜索完成，生成 ${count} 张卡片${titles ? ': ' + titles.join(', ') : ''}`;
    }
    case 'expand_from_card': {
      const titles = (r.data as Record<string, unknown> | undefined)?.titles as string[] | undefined;
      const count = (r.data as Record<string, unknown> | undefined)?.cards_count;
      if (!count) return `${prefix} 扩展完成，未能生成新卡片`;
      return `${prefix} 扩展完成，生成 ${count} 张卡片${titles ? ': ' + titles.join(', ') : ''}`;
    }
    case 'list_cards': {
      const total = (r.data as Record<string, unknown> | undefined)?.total;
      return `${prefix} 知识库共 ${total ?? '?'} 张卡片`;
    }
    case 'get_linked_cards': {
      const ids = (r.data as Record<string, unknown> | undefined)?.linked_ids as string[] | undefined;
      return `${prefix} ${ids?.length ? `关联 ${ids.length} 张卡片` : '该卡片没有关联卡片'}`;
    }
    default:
      return `${prefix} ${r.summary}`;
  }
}

export function useAgent(
  sessionId: string | null,
  onTaskCreated?: (taskId: string, keyword: string, taskType: string) => void,
) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const streamMsgRef = useRef<AgentMessage | null>(null);
  const loadedSession = useRef<string | null>(null);
  const textRafRef = useRef<number | null>(null);

  // 防止消息无限增长导致浏览器OOM
  useEffect(() => {
    if (messages.length > 100) {
      setMessages(prev => prev.slice(-100))
    }
  }, [messages.length > 100 ? messages.length : 0])

  const handleEvent = useCallback((event: AgentSSEEvent) => {
    switch (event.type) {
      case 'tool_call': {
        // 如果 tool_call 携带 task_id（预创建），立即通知收集器
        const tcTaskId = (event.data as unknown as Record<string, unknown> | undefined)?.task_id as string | undefined;
        if (tcTaskId && onTaskCreated) {
          onTaskCreated(tcTaskId, event.data.name, 'collect');
        }
        setMessages(prev => {
          const toolMsg: AgentMessage = {
            id: `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            role: 'tool',
            content: `🔧 正在使用工具: ${event.data.name}`,
            toolName: event.data.name,
            timestamp: Date.now(),
          };
          const streamIdx = prev.findIndex(m => m.isStreaming);
          if (streamIdx >= 0) {
            const updated = [...prev];
            updated.splice(streamIdx, 0, toolMsg);
            return updated;
          }
          return [...prev, toolMsg];
        });
        break;
      }

      case 'tool_result': {
        setMessages(prev => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last?.role === 'tool' && last.toolName === event.data.tool) {
            updated[updated.length - 1] = {
              ...last,
              content: toolSummary(event.data),
              toolSuccess: event.data.success,
            };
          }
          return updated;
        });
        const taskId = (event.data.data as Record<string, unknown> | undefined)?.task_id as string | undefined;
        if (taskId && onTaskCreated) {
          const taskType = ((event.data.data as Record<string, unknown> | undefined)?.task_type as string) || 'collect';
          const keyword = ((event.data.data as Record<string, unknown> | undefined)?.keyword as string) || event.data.tool;
          onTaskCreated(taskId, keyword, taskType);
        }
        break;
      }

      case 'text': {
        // 后台循环订阅场景：没有当前流式消息时先创建一条
        if (!streamMsgRef.current) {
          const agentMsg: AgentMessage = {
            id: `a-${Date.now()}`,
            role: 'agent',
            content: '',
            isStreaming: true,
            timestamp: Date.now(),
          };
          streamMsgRef.current = agentMsg;
          setMessages(prev => [...prev, agentMsg]);
        }
        streamMsgRef.current.content += event.data.content;
        // throttle to max 1 render per frame — ReactMarkdown re-parses
        // the full accumulated text on every render, crushing the browser
        if (!textRafRef.current) {
          textRafRef.current = requestAnimationFrame(() => {
            textRafRef.current = null;
            const msg = streamMsgRef.current;
            if (!msg) return;
            setMessages(prev => {
              const updated = [...prev];
              const idx = updated.findIndex(m => m.id === msg.id);
              if (idx >= 0) {
                updated[idx] = { ...updated[idx], content: msg.content };
              }
              return updated;
            });
          });
        }
        break;
      }

      case 'complete':
        setMessages(prev => {
          const updated = [...prev];
          const idx = updated.findIndex(m => m.id === streamMsgRef.current?.id);
          if (idx >= 0) {
            updated[idx] = { ...updated[idx], isStreaming: false };
          }
          return updated;
        });
        break;

      case 'error':
        setMessages(prev => [...prev, {
          id: `e-${Date.now()}`,
          role: 'agent',
          content: `❌ ${event.data.message}`,
          timestamp: Date.now(),
        }]);
        break;
    }
  }, [onTaskCreated]);

  // 刷新/进入页面时：订阅后台运行中的 Agent 循环（无循环则静默结束）
  const subscribeToRunningLoop = useCallback(async () => {
    if (!sessionId) return;
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    try {
      for await (const event of agentStreamSSE(sessionId, controller.signal)) {
        if (event.type === 'error') return; // 无运行中的循环
        if (!streamMsgRef.current && event.type !== 'text') {
          // 后台循环：创建占位流式消息承载工具事件
          const agentMsg: AgentMessage = {
            id: `a-${Date.now()}`,
            role: 'agent',
            content: '',
            isStreaming: true,
            timestamp: Date.now(),
          };
          streamMsgRef.current = agentMsg;
          setMessages(prev => [...prev, agentMsg]);
        }
        handleEvent(event);
      }
    } catch {
      // abort 或无循环：静默
    } finally {
      if (streamAbortRef.current === controller) streamAbortRef.current = null;
      setIsStreaming(false);
      streamMsgRef.current = null;
    }
  }, [sessionId, handleEvent]);

  useEffect(() => {
    if (!sessionId || sessionId === loadedSession.current) return;
    loadedSession.current = sessionId;
    loadAgentHistory(sessionId).then(setMessages);
    subscribeToRunningLoop();
  }, [sessionId, subscribeToRunningLoop]);

  // 组件卸载时中止正在进行的 SSE 流
  useEffect(() => () => {
    abortRef.current?.abort();
    streamAbortRef.current?.abort();
    if (textRafRef.current) cancelAnimationFrame(textRafRef.current);
  }, [])

  const send = useCallback(async (text: string) => {
    if (!sessionId || !text.trim()) return;

    // 流进行中 → 注入到现有流（后端 bridge 处理路由）
    if (isStreaming) {
      const userMsg: AgentMessage = {
        id: `u-${Date.now()}`,
        role: 'user',
        content: text.trim(),
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, userMsg]);
      const injected = await injectLoopMessage(sessionId, text.trim());
      if (injected) return;
      // 注入失败（loop 已退出），中止旧流，走正常路径重启
      abortRef.current?.abort();
    }

    const userMsg: AgentMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text.trim(),
      timestamp: Date.now(),
    };
    const agentMsg: AgentMessage = {
      id: `a-${Date.now()}`,
      role: 'agent',
      content: '',
      isStreaming: true,
      timestamp: Date.now(),
    };

    const controller = new AbortController();
    abortRef.current = controller;
    // 停止后台循环订阅，由本 chat 流接管队列（避免双订阅者抢事件）
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;

    setMessages(prev => [...prev, userMsg, agentMsg]);
    setIsStreaming(true);
    streamMsgRef.current = agentMsg;

    try {
      for await (const event of agentChatSSE(sessionId, text, controller.signal)) {
        handleEvent(event);
      }
    } catch (err) {
      // 主动取消（cancel/卸载/注入失败）→ 静默收尾，不弹错误
      if ((err as DOMException)?.name !== 'AbortError') {
        setMessages(prev => [...prev, {
          id: `e-${Date.now()}`,
          role: 'agent',
          content: `❌ ${(err as Error).message || '请求失败'}`,
          timestamp: Date.now(),
        }]);
      }
      // 无论成功/失败/取消，占位消息不再显示流式光标
      const placeholderId = streamMsgRef.current?.id;
      setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, isStreaming: false } : m));
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
      streamMsgRef.current = null;
    }
  }, [sessionId, isStreaming]);

  const clear = useCallback(async () => {
    if (!sessionId) return;
    await clearAgentContext(sessionId);
    setMessages([]);
    loadedSession.current = null;
  }, [sessionId]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { messages, isStreaming, send, clear, cancel };
}
