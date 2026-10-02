import { authFetchWithToken } from './auth';
import { getLocalToken } from './localAccount';

// SSE 流 300s 无任何数据视为卡死（后端 AGENT_TIMEOUT=300s 兜底 + 前端主动中止，时长对齐）
const AGENT_STALL_TIMEOUT_MS = 300_000;

export interface AgentToolCall {
  name: string;
  args: Record<string, unknown>;
}

export interface AgentToolResult {
  tool: string;
  success: boolean;
  summary: string;
  data?: Record<string, unknown>;
}

export type AgentSSEEvent =
  | { type: 'tool_call'; data: AgentToolCall }
  | { type: 'tool_result'; data: AgentToolResult }
  | { type: 'text'; data: { content: string } }
  | { type: 'complete'; data: { turns: number; tools_used: string[] } }
  | { type: 'error'; data: { message: string } };

export interface AgentMessage {
  id: string;
  role: 'user' | 'agent' | 'tool';
  content: string;
  toolName?: string;
  toolSuccess?: boolean;
  isStreaming?: boolean;
  timestamp: number;
}

export async function clearAgentContext(sessionId: string): Promise<boolean> {
  try {
    await authFetchWithToken('/api/agent/clear', {
      method: 'POST',
      body: JSON.stringify({ message: '', session_id: sessionId }),
    });
    return true;
  } catch (err) {
    console.warn('Agent clear failed:', err);
    return false;
  }
}

export async function injectLoopMessage(sessionId: string, message: string): Promise<boolean> {
  try {
    const token = getLocalToken();
    if (!token) return false;
    const resp = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if ((resp.headers.get('content-type') || '').includes('text/event-stream')) {
      return false;
    }
    if (!resp.ok) return false;
    const data = await resp.json();
    return data.injected === true;
  } catch {
    return false;
  }
}

interface RawMessage {
  role: string;
  content: string | null;
  tool_calls?: Array<{ function: { name: string; arguments: string } }>;
  tool_call_id?: string;
}

export async function loadAgentHistory(sessionId: string): Promise<AgentMessage[]> {
  try {
    const data = await authFetchWithToken<{ messages: RawMessage[] }>(
      `/api/agent/history?session_id=${encodeURIComponent(sessionId)}`
    );
    return convertHistory(data.messages);
  } catch {
    return [];
  }
}

function convertHistory(raw: RawMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  let ts = Date.now() - raw.length * 1000;

  for (const m of raw) {
    ts += 1000;
    if (m.role === 'system') continue;

    if (m.role === 'user') {
      result.push({ id: `h-${ts}`, role: 'user', content: m.content || '', timestamp: ts });
    } else if (m.role === 'assistant') {
      if (m.tool_calls) {
        for (const tc of m.tool_calls) {
          result.push({
            id: `h-${ts++}`,
            role: 'tool',
            content: `🔧 调用工具: ${tc.function.name}`,
            toolName: tc.function.name,
            timestamp: ts,
          });
        }
      } else if (m.content) {
        result.push({ id: `h-${ts}`, role: 'agent', content: m.content, timestamp: ts });
      }
    } else if (m.role === 'tool') {
      result.push({
        id: `h-${ts}`,
        role: 'tool',
        content: m.content || '',
        toolSuccess: true,
        timestamp: ts,
      });
    }
  }
  return result;
}

async function* readAgentSSEStream(
  response: Response,
  stallMs?: number,
  onStall?: () => void,
): AsyncGenerator<AgentSSEEvent> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';
  let stallFired = false;
  let stallTimer: number | undefined;
  const armStall = () => {
    if (!stallMs) return;
    clearTimeout(stallTimer);
    stallTimer = window.setTimeout(() => {
      stallFired = true;
      onStall?.();
    }, stallMs);
  };

  try {
    while (true) {
      armStall();
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        if (stallFired) throw new Error(`Agent 响应超时（${(stallMs ?? 0) / 1000}s 无数据），已自动中止`);
        throw err;
      }
      clearTimeout(stallTimer);

      const { done, value } = chunk;

      if (done) {
        buffer += decoder.decode();
        if (buffer) {
          const finalLines = buffer.split('\n');
          for (const line of finalLines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('data: ')) continue;
            try {
              yield JSON.parse(trimmed.slice(6)) as AgentSSEEvent;
            } catch {
              // skip
            }
          }
        }
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data: ')) continue;
        try {
          yield JSON.parse(trimmed.slice(6)) as AgentSSEEvent;
        } catch {
          // skip unparseable lines
        }
      }
    }
  } finally {
    clearTimeout(stallTimer);
  }
}

export async function* agentChatSSE(
  sessionId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<AgentSSEEvent> {
  const token = getLocalToken();
  if (!token) throw new Error('未认证');

  // 内部 controller：合并调用方 signal（取消/卸载）与卡死超时（300s 无数据）两种中止源
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort);
  if (signal?.aborted) controller.abort();

  try {
    const response = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ message, session_id: sessionId }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.text().catch(() => '');
      throw new Error(body || `HTTP ${response.status}`);
    }

    yield* readAgentSSEStream(response, AGENT_STALL_TIMEOUT_MS, () => controller.abort());
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}

export async function* agentStreamSSE(
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<AgentSSEEvent> {
  const token = getLocalToken();
  if (!token) throw new Error('未认证');

  const controller = new AbortController();
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort);
  if (signal?.aborted) controller.abort();

  try {
    const response = await fetch(
      `/api/agent/stream?session_id=${encodeURIComponent(sessionId)}`,
      {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    yield* readAgentSSEStream(response);
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}
