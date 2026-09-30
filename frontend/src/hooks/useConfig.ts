import React from 'react';
import { ApiConfig, DEFAULT_CONFIG } from '../types/config';
import { loadConfig as loadFromStorage, saveConfig as saveToStorage, testConnection as testConnectionApi } from '../api/config';
import { validateApiUrl, validateApiKey, validateModel, validateTemperature, validateMaxTokens } from '../utils/validation';

type ValidationResult = { valid: boolean; errors: string[] };
type TestState = 'idle' | 'loading' | 'success' | 'error';
type SaveState = 'idle' | 'saving' | 'saved' | 'error';

export function useConfig() {
  const [config, setConfig] = React.useState<ApiConfig>(DEFAULT_CONFIG);
  const [testState, setTestState] = React.useState<TestState>('idle');
  const [testError, setTestError] = React.useState<string | null>(null);
  const [saveState, setSaveState] = React.useState<SaveState>('idle');
  const [saveError, setSaveError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const loaded = await loadFromStorage();
        if (mounted) {
          setConfig(loaded ?? DEFAULT_CONFIG);
        }
      } catch (err) {
        console.warn('Config load on mount failed:', err)
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const update = (patch: Partial<ApiConfig>) => {
    setConfig(prev => ({ ...prev, ...patch }));
    setSaveState('idle');
  };

  const save = async (): Promise<{ success: boolean; error?: string }> => {
    setSaveState('saving');
    setSaveError(null);
    const result = await saveToStorage(config);
    if (result.success) {
      setSaveState('saved');
      setTimeout(() => setSaveState('idle'), 2000);
    } else {
      setSaveState('error');
      setSaveError(result.error ?? '保存失败');
    }
    return result;
  };

  const resetToDefaults = () => {
    setConfig(DEFAULT_CONFIG);
    setSaveState('idle');
  };

  const load = async () => {
    try {
      const loaded = await loadFromStorage();
      setConfig(loaded ?? DEFAULT_CONFIG);
    } catch (err) {
      console.warn('Config reload failed:', err)
    }
  };

  const validate = (): ValidationResult => {
    const errors: string[] = [];
    if (!validateApiUrl(config.apiUrl)) errors.push('API URL 无效');
    if (!validateApiKey(config.apiKey)) errors.push('需要填写 API Key');
    if (!validateModel(config.model)) errors.push('需要填写模型名称');
    if (!validateTemperature(config.temperature)) errors.push('Temperature 必须在 0 到 2 之间');
    if (!validateMaxTokens(config.maxTokens)) errors.push('Max Tokens 必须大于 0');
    return { valid: errors.length === 0, errors };
  };

  const testConnection = async (): Promise<{ success: boolean; error?: string }> => {
    setTestState('loading');
    setTestError(null);
    const result = await testConnectionApi(config);
    if (result.success) {
      setTestState('success');
      return result;
    } else {
      setTestState('error');
      setTestError(result.error ?? '连接测试失败');
      return result;
    }
  };

  return { config, setConfig, update, save, resetToDefaults, load, validate, testConnection, testState, testError, saveState, saveError };
}
