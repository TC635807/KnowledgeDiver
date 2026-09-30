import { ApiConfig, DEFAULT_CONFIG } from '../types/config';

export async function testConnection(config: ApiConfig): Promise<{ success: boolean; error?: string }> {
  try {
    const res = await fetch('/api/ai/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: text || `HTTP ${res.status}` };
    }

    const data = await res.json().catch(() => ({} as any));
    if (typeof data?.success === 'boolean') {
      if (data.success) return { success: true };
      return { success: false, error: data.error ?? '连接测试失败' };
    }

    return { success: true };
  } catch (err) {
    const message = (err as Error).message || '网络错误';
    return { success: false, error: message };
  }
}

export async function saveConfig(config: ApiConfig): Promise<{ success: boolean; error?: string }> {
  try {
    const res = await fetch('/api/ai/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: text || `HTTP ${res.status}` };
    }

    localStorage.setItem('knowledgeDiver.apiConfig', JSON.stringify(config));
    return { success: true };
  } catch (err) {
    const message = (err as Error).message || '网络错误';
    return { success: false, error: message };
  }
}

export async function loadConfig(): Promise<ApiConfig> {
  try {
    const res = await fetch('/api/ai/config');
    if (res.ok) {
      const data = await res.json();
      return {
        apiUrl: data.api_url,
        apiKey: data.api_key,
        model: data.model,
        temperature: 0.7,
        maxTokens: 2000,
        proxyPort: data.proxy_port ?? DEFAULT_CONFIG.proxyPort,
      };
    }
  } catch (err) {
    console.warn('Config API load failed:', err)
  }
  try {
    const raw = localStorage.getItem('knowledgeDiver.apiConfig');
    if (raw) {
      const parsed = JSON.parse(raw) as ApiConfig;
      return parsed;
    }
  } catch (err) {
    console.warn('Config localStorage parse failed:', err)
  }
  return DEFAULT_CONFIG;
}
